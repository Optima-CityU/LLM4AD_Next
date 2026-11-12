// npuprobe: 游戏进程 (普通应用, 无 root) 里的 NPU (HTP) 运行时。两个用户:
//   - debug.knobs.npuprobe=1: 只验证能不能跑 —— 第 INIT_FRAME 帧初始化, 之后 RUN_FRAMES 帧每帧推理一次并计时。
//     =2 只开设备写日志, 不加载任何库; =3 只写日志 (排查游戏为什么退出)。
//   - npuop (vk_layer_refknobs.cpp): 用 npu_rt_init / npu_rt_bind / npu_rt_invoke (零拷贝)
//     或 npu_rt_run (拷贝) 真正替换游戏的放大。
// 不开线程: 原神会杀掉层自己创建的线程 (2026-10-20 实测, 只开线程写日志也会 SIGSEGV)。
// 所以全部工作在游戏线程的 vkQueuePresentKHR 里同步做。
//
// 需要和层放在同一个 nativeLibraryDir 的文件 (游戏沙箱只读得到自己的目录):
//   libtensorflowlite_jni.so      TFLite 运行时 (tensorflow-lite 2.16.1 AAR)
//   libQnnTFLiteDelegate.so libQnnHtp.so libQnnHtpV81Stub.so libQnnSystem.so libQnnHtpPrepare.so
//   libQnnHtpV81Skel.so libQnnHtpV81.so    DSP 侧, 由 ADSP_LIBRARY_PATH 找到
//   libnpusr_model.so              超分模型 (.tflite 改名, 只有 lib*.so 会被安装器保留权限)
//
// 全部经 dlopen/dlsym 调用: 文件不在时只打日志, 层照常工作。
// 结果写 logcat (tag refknobs-npu) 和应用外部目录的 npuprobe.txt
// (有的 ROM 把 log.tag 设成 S, logcat 全部静音)。
#include <android/log.h>
#include <stdarg.h>
#include <stdio.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/system_properties.h>
#include <time.h>
#include <unistd.h>
#include <string>

// 与 vk_layer_refknobs.cpp 共用的接口
#include "npu_rt.h"

namespace {

FILE* g_out = nullptr;
bool g_out_tried = false;
int g_mode = 0;

void open_out() {
    if (g_out_tried) return;
    g_out_tried = true;
    char pkg[256] = {0};
    FILE* f = fopen("/proc/self/cmdline", "r");
    if (f) { fread(pkg, 1, sizeof pkg - 1, f); fclose(f); }
    std::string path = std::string("/storage/emulated/0/Android/data/") + pkg + "/files/npuprobe.txt";
    g_out = fopen(path.c_str(), "a");
}
#define NLOG(...) npu_log(__VA_ARGS__)

double now_ms() {
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}

std::string self_dir() {
    Dl_info info{};
    if (!dladdr((void*)&self_dir, &info) || !info.dli_fname) return "";
    std::string p = info.dli_fname;
    return p.substr(0, p.rfind('/'));
}

template <typename T>
bool sym(void* h, const char* name, T& out) {
    out = reinterpret_cast<T>(dlsym(h, name));
    if (!out) NLOG("缺符号 %s", name);
    return out != nullptr;
}

struct QuantParams { float scale; int32_t zero_point; };
void* g_it = nullptr;
int (*g_invoke)(void*) = nullptr;
void* (*g_get_in)(void*, int) = nullptr;
const void* (*g_get_out)(void*, int) = nullptr;
size_t (*g_bytes)(const void*) = nullptr;
int (*g_copy_from)(void*, const void*, size_t) = nullptr;
int (*g_copy_to)(const void*, void*, size_t) = nullptr;
QuantParams (*g_qparams)(const void*) = nullptr;
int (*g_alloc)(void*) = nullptr;
int (*g_ndims)(const void*) = nullptr;
int (*g_dim)(const void*, int) = nullptr;
int (*g_type)(const void*) = nullptr;
int (*g_in_index)(const void*, int) = nullptr;
int (*g_out_index)(const void*, int) = nullptr;
struct CustomAlloc { void* data; size_t bytes; };   // = TfLiteCustomAllocation
int (*g_set_custom)(void*, int, const CustomAlloc*, int64_t) = nullptr;
void* g_bound_in = nullptr;
void* g_bound_out = nullptr;

// 加载运行库, 建好解释器 (HTP 平衡档)。成功后 g_it 非空。
bool rt_load() {
    open_out();
    std::string dir = self_dir();
    NLOG("开始, 目录 %s, uid %d", dir.c_str(), getuid());

    if (g_mode == 3) { NLOG("模式 3, 不碰设备"); return false; }
    int fd = open("/dev/fastrpc-cdsp", O_RDONLY | O_NONBLOCK);
    NLOG("open /dev/fastrpc-cdsp: %s", fd >= 0 ? "成功" : strerror(errno));
    if (fd >= 0) close(fd);
    if (g_mode == 2) { NLOG("模式 2, 到此为止"); return false; }

    std::string adsp = dir + ";/vendor/lib/rfsa/adsp;/vendor/dsp/cdsp";
    setenv("ADSP_LIBRARY_PATH", adsp.c_str(), 1);

    void* tfl = dlopen((dir + "/libtensorflowlite_jni.so").c_str(), RTLD_NOW | RTLD_LOCAL);
    void* qd = dlopen((dir + "/libQnnTFLiteDelegate.so").c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!tfl || !qd) { NLOG("dlopen 失败: %s", dlerror()); return false; }

    using CreateDel = void* (*)(char**, char**, size_t, void (*)(const char*));
    using ModelCreate = void* (*)(const char*);
    using OptCreate = void* (*)();
    using OptAddDel = void (*)(void*, void*);
    using InterpCreate = void* (*)(void*, void*);
    using Status = int (*)(void*);
    CreateDel create_del; ModelCreate model_create; OptCreate opt_create; OptAddDel opt_add;
    InterpCreate interp_create; Status alloc;
    if (!sym(qd, "tflite_plugin_create_delegate", create_del) ||
        !sym(tfl, "TfLiteModelCreateFromFile", model_create) ||
        !sym(tfl, "TfLiteInterpreterOptionsCreate", opt_create) ||
        !sym(tfl, "TfLiteInterpreterOptionsAddDelegate", opt_add) ||
        !sym(tfl, "TfLiteInterpreterCreate", interp_create) ||
        !sym(tfl, "TfLiteInterpreterAllocateTensors", alloc) ||
        !sym(tfl, "TfLiteInterpreterInvoke", g_invoke) ||
        !sym(tfl, "TfLiteInterpreterGetInputTensor", g_get_in) ||
        !sym(tfl, "TfLiteInterpreterGetOutputTensor", g_get_out) ||
        !sym(tfl, "TfLiteTensorByteSize", g_bytes) ||
        !sym(tfl, "TfLiteTensorCopyFromBuffer", g_copy_from) ||
        !sym(tfl, "TfLiteTensorCopyToBuffer", g_copy_to) ||
        !sym(tfl, "TfLiteTensorQuantizationParams", g_qparams) ||
        !sym(tfl, "TfLiteTensorNumDims", g_ndims) ||
        !sym(tfl, "TfLiteTensorDim", g_dim) ||
        !sym(tfl, "TfLiteTensorType", g_type))
        return false;
    g_alloc = alloc;
    // 零拷贝要的三个是可选的: 缺了只是 npuop 退回拷贝模式
    g_in_index = reinterpret_cast<int (*)(const void*, int)>(dlsym(tfl, "TfLiteInterpreterGetInputTensorIndex"));
    g_out_index = reinterpret_cast<int (*)(const void*, int)>(dlsym(tfl, "TfLiteInterpreterGetOutputTensorIndex"));
    g_set_custom = reinterpret_cast<int (*)(void*, int, const CustomAlloc*, int64_t)>(
        dlsym(tfl, "TfLiteInterpreterSetCustomAllocationForTensor"));

    std::string lib = dir + "/libQnnHtp.so";
    const char* keys[] = {"backend_type", "library_path", "skel_library_dir", "htp_performance_mode"};
    const char* vals[] = {"htp", lib.c_str(), dir.c_str(), "8"};   // 8 = balanced
    double t0 = now_ms();
    void* del = create_del((char**)keys, (char**)vals, 4, nullptr);
    void* model = model_create((dir + "/libnpusr_model.so").c_str());
    if (!del || !model) { NLOG("代理或模型创建失败 del=%p model=%p", del, model); return false; }
    void* opt = opt_create();
    opt_add(opt, del);
    void* it = interp_create(model, opt);
    if (!it || alloc(it) != 0) { NLOG("解释器创建失败"); return false; }
    NLOG("初始化 %.0f ms", now_ms() - t0);
    g_it = it;
    return true;   // 不释放: 退出时析构会 abort (见 tools 里的 handoff 测试)
}

const uint64_t INIT_FRAME = 300, RUN_FRAMES = 60;
double g_sum = 0, g_max = 0;
int g_runs = 0;

__attribute__((constructor)) void npuprobe_init() {
    char v[PROP_VALUE_MAX] = {0};
    if (__system_property_get("debug.knobs.npuprobe", v) <= 0) return;
    g_mode = atoi(v);
    if (g_mode < 1 || g_mode > 3) g_mode = 0;
}

}  // namespace

void npu_log(const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    __android_log_print(ANDROID_LOG_INFO, "refknobs-npu", "%s", buf);
    open_out();
    if (g_out) { fprintf(g_out, "%s\n", buf); fflush(g_out); }
}

// npuop 用: 建好运行时并报出输入输出的字节数与量化参数
bool npu_rt_init(NpuIo* io) {
    if (!g_it && !rt_load()) return false;
    const void* ti = g_get_in(g_it, 0);
    const void* to = g_get_out(g_it, 0);
    QuantParams qi = g_qparams(ti), qo = g_qparams(to);
    io->in_bytes = g_bytes(ti); io->out_bytes = g_bytes(to);
    io->in_scale = qi.scale; io->in_zp = qi.zero_point;
    io->out_scale = qo.scale; io->out_zp = qo.zero_point;
    io->in_ndims = g_ndims(ti); io->out_ndims = g_ndims(to);
    for (int i = 0; i < 4; i++) {
        io->in_dims[i] = i < io->in_ndims ? g_dim(ti, i) : 0;
        io->out_dims[i] = i < io->out_ndims ? g_dim(to, i) : 0;
    }
    io->in_type = g_type(ti); io->out_type = g_type(to);
    io->can_bind = g_set_custom && g_in_index && g_out_index;
    NLOG("模型: 输入 [%d,%d,%d,%d] type %d %zu B (scale %.6f zp %d), 输出 [%d,%d,%d,%d] type %d %zu B (scale %.6f zp %d), 零拷贝接口 %s",
         io->in_dims[0], io->in_dims[1], io->in_dims[2], io->in_dims[3], io->in_type,
         io->in_bytes, io->in_scale, io->in_zp,
         io->out_dims[0], io->out_dims[1], io->out_dims[2], io->out_dims[3], io->out_type,
         io->out_bytes, io->out_scale, io->out_zp, io->can_bind ? "有" : "无");
    return true;
}

// npuop 零拷贝: 输入/输出张量改用调用方的内存。AllocateTensors 在已分配过的图上直接返回,
// 只是让 TFLite 校验新指针 (官方要求 SetCustomAllocation 之后调一次)。
bool npu_rt_bind(void* in, size_t in_cap, void* out, size_t out_cap) {
    if (!g_it || !g_set_custom || !g_in_index || !g_out_index) return false;
    if (in == g_bound_in && out == g_bound_out) return true;
    CustomAlloc ai{in, in_cap}, ao{out, out_cap};
    if (g_set_custom(g_it, g_in_index(g_it, 0), &ai, 0) != 0) return false;
    if (g_set_custom(g_it, g_out_index(g_it, 0), &ao, 0) != 0) return false;
    if (g_alloc(g_it) != 0) return false;
    g_bound_in = in; g_bound_out = out;
    return true;
}

bool npu_rt_invoke() {
    return g_it && g_bound_in && g_invoke(g_it) == 0;
}

// npuop 拷贝模式: 一次推理, in/out 为打包好的 NHWC 张量字节 (通道数按模型)
bool npu_rt_run(const void* in, size_t in_bytes, void* out, size_t out_bytes) {
    if (!g_it) return false;
    if (g_copy_from(g_get_in(g_it, 0), in, in_bytes) != 0) return false;
    if (g_invoke(g_it) != 0) return false;
    return g_copy_to(g_get_out(g_it, 0), out, out_bytes) == 0;
}

// vk_layer_refknobs.cpp 的 rk_QueuePresentKHR 每帧调用
void npuprobe_on_present(uint64_t frame) {
    if (!g_mode || frame < INIT_FRAME) return;
    if (frame == INIT_FRAME) {
        double t0 = now_ms();
        if (rt_load()) {
            void* in = g_get_in(g_it, 0);
            std::string zeros(g_bytes(in), (char)128);
            g_copy_from(in, zeros.data(), zeros.size());
        }
        NLOG("第 %llu 帧初始化, 卡了 %.0f ms", (unsigned long long)frame, now_ms() - t0);
        return;
    }
    if (!g_it || g_runs >= (int)RUN_FRAMES) return;
    double a = now_ms();
    if (g_invoke(g_it) != 0) { NLOG("推理失败"); g_it = nullptr; return; }
    double d = now_ms() - a;
    g_sum += d; if (d > g_max) g_max = d;
    if (++g_runs == (int)RUN_FRAMES)
        NLOG("游戏线程内 NPU 推理成功: %d 帧, 平均 %.2f ms, 最长 %.2f ms", g_runs, g_sum / g_runs, g_max);
}
