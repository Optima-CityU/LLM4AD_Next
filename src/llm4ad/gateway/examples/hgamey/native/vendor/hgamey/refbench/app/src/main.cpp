// refbench — 白盒基准靶场, 单文件 Vulkan NativeActivity (hasCode=false, 零 Java)
//
// 渲染内容不依赖采样或判定。不读时钟决定内容、无随机数。
// 内容只是 frame_index 的函数。refbench_out JSON 只有配置事实与提交计数。
// 可选质量捕获的独立清单记录采集身份时钟。可选 GPU query 原始证据另存 sidecar，
// 不改变内容、不计算帧率或统计，不混入 refbench_out 的配置事实。
//
// 生命周期: 解析 intent extras → 起 Vulkan → 渲染固定帧数 (每帧恰好一次
// vkQueueSubmit, 提交线程名 RefbenchRender) → 写 JSON + fsync → 进程自退。
// harness 用 pidof 存活轮询判断结束。

#include <android/log.h>
#include <android/native_activity.h>
#include <android/window.h>
#include <android_native_app_glue.h>
#include <jni.h>
#include <dirent.h>
#include <pthread.h>
#include <unistd.h>
#include <sys/stat.h>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#define VK_USE_PLATFORM_ANDROID_KHR 1
#include <vulkan/vulkan.h>

#include "gen/fullscreen_vert.h"
#include "gen/pingpong_frag.h"
#include "gen/alu_frag.h"
#include "gen/blit_frag.h"
#include "gen/tri_vert.h"
#include "gen/tri_frag.h"
#include "paired_capture.h"
#include "output_json.h"
#include "gpu_timestamps.h"

#define TAG "refbench"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

static const char* APP_VERSION = "0.1.2-mali-whitebox";
static const char* RENDER_THREAD_NAME = "RefbenchRender";
// Adreno Vulkan 驱动在自己进程内的内部线程 ("Thread-N") 上发 kgsl 提交, 不在调用线程。
// 同线程组可写 /proc/self/task/<tid>/comm, 把它们统一改名 → ftrace 过滤有了稳定契约。
static const char* DRIVER_THREAD_NAME = "RefbenchDrv";
// Capture readback has a finite wait; the existing general shutdown path still
// has vkDeviceWaitIdle and is not a bounded process-lifetime guarantee.
static constexpr uint64_t CAPTURE_SUBMISSION_WAIT_NS = 10000000000ull;

// ── 配置 (来自 intent extras, 全部字符串) ──────────────────────────────

struct Cfg {
    std::string scene = "bw_pingpong";
    std::string run_id = "unset";
    int frames = 3600;
    int intensity = 8;
    std::string knob_loadop = "off";  // 只作用于 bw_pingpong
    // 只作用于 sr_pipeline: 管线尾部的 compute 后处理算子
    //   off = 不挂算子, present blit 直接采样低分辨率 RT (驱动双线性放大) —— A 臂
    //   on  = 挂 postfx_shader 指定的 SPIR-V, 低分辨率 RT -> 高分辨率 RT       —— B 臂
    std::string knob_postfx = "off";
    std::string postfx_shader;  // 外部 candidate.spv 的设备侧绝对路径
    std::string capture_native_pairs = "off";
    std::string capture_gpu_timestamps = "off";
    std::string capture_bw_hash = "off";
    int capture_start = 0, capture_count = 4, capture_stride = 1;
    std::string capture_rules_path, capture_rules_sha256;
};

struct CaptureBuffer {
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkDeviceSize bytes = 0;
    uint32_t width = 0, height = 0;
    std::string role;
};

struct PassInfo {
    std::string name, target, load_op, store_op, format;
    uint32_t w = 0, h = 0;
};

struct State {
    android_app* app = nullptr;
    Cfg cfg;
    bool window_ready = false;
    bool window_lost = false;

    // vulkan
    VkInstance instance = VK_NULL_HANDLE;
    VkSurfaceKHR surface = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    VkDevice dev = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t qfam = 0;
    std::string gpu_name;

    VkSwapchainKHR swapchain = VK_NULL_HANDLE;
    VkFormat swap_format = VK_FORMAT_UNDEFINED;
    VkExtent2D swap_extent{};
    VkPresentModeKHR present_mode = VK_PRESENT_MODE_FIFO_KHR;
    VkSurfaceTransformFlagBitsKHR pre_transform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR;
    std::vector<VkImage> swap_images;
    std::vector<VkImageView> swap_views;
    std::vector<VkFramebuffer> swap_fbs;

    // sr_pipeline: 游戏以低分辨率渲染, 算子在管线尾部重构到送显分辨率
    static const uint32_t SR_LO_W = 1280, SR_LO_H = 720;
    static const uint32_t SR_HI_W = 1920, SR_HI_H = 1080;
    VkImage imgLo = VK_NULL_HANDLE, imgHi = VK_NULL_HANDLE;
    VkDeviceMemory memLo = VK_NULL_HANDLE, memHi = VK_NULL_HANDLE;
    VkImageView viewLo = VK_NULL_HANDLE, viewHi = VK_NULL_HANDLE;
    VkFramebuffer fbLo = VK_NULL_HANDLE;
    VkDescriptorSet setLo = VK_NULL_HANDLE, setHi = VK_NULL_HANDLE;
    bool hi_initialised = false;  // imgHi 首次使用前需要一次 layout 过渡
    // Independent native HR is never imgHi (which is candidate compute output).
    VkImage imgNativeHR = VK_NULL_HANDLE;
    VkDeviceMemory memNativeHR = VK_NULL_HANDLE;
    VkImageView viewNativeHR = VK_NULL_HANDLE;
    VkFramebuffer fbNativeHR = VK_NULL_HANDLE;
    VkPipeline pipeNativeHR = VK_NULL_HANDLE;
    std::vector<CaptureBuffer> capture_buffers;
    paired_capture::Sink capture_sink;
    bool capture_initialized = false;
    int capture_frames_written = 0;
    uint64_t capture_recording_timestamp_ns = 0;
    std::string candidate_sha256;

    // 外部装入的 compute 后处理算子
    VkShaderModule postfx_module = VK_NULL_HANDLE;
    VkDescriptorSetLayout postfx_dsl = VK_NULL_HANDLE;
    VkDescriptorPool postfx_dpool = VK_NULL_HANDLE;
    VkDescriptorSet postfx_set = VK_NULL_HANDLE;
    VkPipelineLayout postfx_playout = VK_NULL_HANDLE;
    VkPipeline postfx_pipe = VK_NULL_HANDLE;
    long postfx_bytes = 0;
    bool postfx_active = false;

    // 离屏
    static const uint32_t BW_RT = 2048, ALU_RT = 1024;
    VkImage imgA = VK_NULL_HANDLE, imgB = VK_NULL_HANDLE, imgC = VK_NULL_HANDLE;
    VkDeviceMemory memA = VK_NULL_HANDLE, memB = VK_NULL_HANDLE, memC = VK_NULL_HANDLE;
    VkImageView viewA = VK_NULL_HANDLE, viewB = VK_NULL_HANDLE, viewC = VK_NULL_HANDLE;
    VkFramebuffer fbA = VK_NULL_HANDLE, fbB = VK_NULL_HANDLE, fbC = VK_NULL_HANDLE;

    VkRenderPass rp_off_load = VK_NULL_HANDLE;   // RGBA16F, loadOp=LOAD
    VkRenderPass rp_off_dont = VK_NULL_HANDLE;   // RGBA16F, loadOp=DONT_CARE
    VkRenderPass rp_alu = VK_NULL_HANDLE;        // RGBA8,   loadOp=DONT_CARE
    VkRenderPass rp_swap_dont = VK_NULL_HANDLE;  // swapchain, DONT_CARE (blit 全覆写)
    VkRenderPass rp_swap_clear = VK_NULL_HANDLE; // swapchain, CLEAR (idle_cap)

    VkSampler sampler = VK_NULL_HANDLE;
    VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
    VkDescriptorPool dpool = VK_NULL_HANDLE;
    VkDescriptorSet setA = VK_NULL_HANDLE, setB = VK_NULL_HANDLE, setC = VK_NULL_HANDLE;
    VkPipelineLayout playout = VK_NULL_HANDLE;
    VkPipeline pipe_ping = VK_NULL_HANDLE, pipe_alu = VK_NULL_HANDLE,
               pipe_blit = VK_NULL_HANDLE, pipe_tri = VK_NULL_HANDLE;

    VkCommandPool cmdpool = VK_NULL_HANDLE;
    static const int FLIGHTS = 2;
    VkCommandBuffer cmd[FLIGHTS] = {};
    VkFence fence[FLIGHTS] = {};
    VkQueryPool timestamp_pool = VK_NULL_HANDLE;
    int timestamp_frames[FLIGHTS] = {-1, -1};
    unsigned timestamp_bits = 0;
    double timestamp_period_ns = 0;
    refbench_timestamps::Capture gpu_timestamps;
    VkSemaphore sem_acq[FLIGHTS] = {};
    std::vector<VkSemaphore> sem_ren;  // 每 swapchain image 一个

    // debug utils (可选)
    bool debug_labels = false;
    PFN_vkCmdBeginDebugUtilsLabelEXT beginLabel = nullptr;
    PFN_vkCmdEndDebugUtilsLabelEXT endLabel = nullptr;

    // ground truth 记录
    std::vector<PassInfo> passes;
    int frames_submitted = 0;
    int renamed_driver_threads = 0;
    bool clean_exit = false;
};

static State g;

// ── JSON 输出 (退出前唯一一次落盘) ─────────────────────────────────────

static const char* present_mode_name(VkPresentModeKHR m) {
    switch (m) {
        case VK_PRESENT_MODE_FIFO_KHR: return "FIFO";
        case VK_PRESENT_MODE_MAILBOX_KHR: return "MAILBOX";
        case VK_PRESENT_MODE_IMMEDIATE_KHR: return "IMMEDIATE";
        case VK_PRESENT_MODE_FIFO_RELAXED_KHR: return "FIFO_RELAXED";
        default: return "OTHER";
    }
}

static const char* format_name(VkFormat f) {
    switch (f) {
        case VK_FORMAT_R8G8B8A8_UNORM: return "R8G8B8A8_UNORM";
        case VK_FORMAT_B8G8R8A8_UNORM: return "B8G8R8A8_UNORM";
        case VK_FORMAT_R8G8B8A8_SRGB: return "R8G8B8A8_SRGB";
        case VK_FORMAT_B8G8R8A8_SRGB: return "B8G8R8A8_SRGB";
        case VK_FORMAT_R16G16B16A16_SFLOAT: return "R16G16B16A16_SFLOAT";
        default: { static char b[16]; snprintf(b, sizeof b, "VK_%d", (int)f); return b; }
    }
}

static void mkdirs(const std::string& path) {
    std::string p;
    for (size_t i = 0; i < path.size(); i++) {
        p += path[i];
        if (path[i] == '/' && i > 0) mkdir(p.c_str(), 0771);
    }
    mkdir(path.c_str(), 0771);
}

static std::string output_json() {
    return refbench_output::serialize(g, APP_VERSION, RENDER_THREAD_NAME,
                                     DRIVER_THREAD_NAME, present_mode_name(g.present_mode),
                                     format_name(g.swap_format));
}

static void write_output_json() {
    const char* base = g.app && g.app->activity && g.app->activity->externalDataPath
                           ? g.app->activity->externalDataPath
                           : (g.app && g.app->activity ? g.app->activity->internalDataPath : nullptr);
    if (!base) { LOGE("no data path, json not written"); return; }
    mkdirs(base);
    std::string path = std::string(base) + "/refbench_out.json";
    std::string j = output_json();
    FILE* f = fopen(path.c_str(), "w");
    if (!f) { LOGE("open %s failed: %s", path.c_str(), strerror(errno)); return; }
    fwrite(j.data(), 1, j.size(), f);
    fflush(f);
    fsync(fileno(f));
    fclose(f);
    LOGI("REFBENCH_JSON %s", path.c_str());
}

// 渲染循环开始前落地的标记文件 — harness 用它轮询"已进入渲染"。
// 设备端 logcat 不可靠 (实测会整个哑掉), 所以进度信号全走文件系统。
static void write_started_marker() {
    const char* base = g.app && g.app->activity && g.app->activity->externalDataPath
                           ? g.app->activity->externalDataPath
                           : (g.app && g.app->activity ? g.app->activity->internalDataPath : nullptr);
    if (!base) return;
    mkdirs(base);
    std::string path = std::string(base) + "/refbench_started";
    FILE* f = fopen(path.c_str(), "w");
    if (!f) return;
    fprintf(f, "%s %s\n", g.cfg.scene.c_str(), g.cfg.run_id.c_str());
    fflush(f);
    fsync(fileno(f));
    fclose(f);
}

static bool collect_gpu_timestamp(int slot) {
    const int frame = g.timestamp_frames[slot];
    if (!g.timestamp_pool || frame < 0) return true;
    uint64_t ticks[2] = {};
    VkResult result = vkGetQueryPoolResults(g.dev, g.timestamp_pool, slot * 2, 2,
        sizeof(ticks), ticks, sizeof(uint64_t), VK_QUERY_RESULT_64_BIT);
    if (result != VK_SUCCESS || !g.gpu_timestamps.record(frame, ticks[0], ticks[1])) {
        LOGE("GPU timestamp query unavailable/duplicate at frame %d", frame);
        return false;
    }
    g.timestamp_frames[slot] = -1;
    return true;
}

static bool write_gpu_timestamps() {
    const char* base = g.app && g.app->activity ? g.app->activity->externalDataPath : nullptr;
    if (!base) return false;
    mkdirs(base);
    FILE* file = fopen((std::string(base) + "/refbench_gpu_timestamps.json").c_str(), "w");
    if (!file) return false;
    const std::string raw = g.gpu_timestamps.json(g.cfg.run_id);
    bool ok = fwrite(raw.data(), 1, raw.size(), file) == raw.size();
    ok = fflush(file) == 0 && ok;
    ok = fsync(fileno(file)) == 0 && ok;
    ok = fclose(file) == 0 && ok;
    return ok;
}

static bool write_bw_hash();

static void finish_and_exit(int code) {
    if (g.capture_initialized && (!g.clean_exit ||
            g.capture_frames_written != g.cfg.capture_count)) {
        g.clean_exit = false;
        code = 2;
        g.capture_sink.fail("run_aborted_or_incomplete");
    }
    if (g.dev) {
        VkResult idle = vkDeviceWaitIdle(g.dev);
        if (g.capture_initialized && idle != VK_SUCCESS) {
            g.clean_exit = false;
            code = 2;
            g.capture_sink.fail("device_idle_failed");
        }
    }
    if (g.cfg.capture_gpu_timestamps == "on") {
        for (int slot = 0; slot < State::FLIGHTS; ++slot) {
            if (!collect_gpu_timestamp(slot)) { g.clean_exit = false; code = 2; }
        }
        if (!g.gpu_timestamps.complete() || !write_gpu_timestamps()) {
            g.clean_exit = false;
            code = 2;
        }
    }
    if (g.cfg.capture_bw_hash == "on" && !write_bw_hash()) {
        g.clean_exit = false;
        code = 2;
    }
    if (g.capture_initialized) {
        if (!g.capture_sink.finish(output_json(), g.frames_submitted, g.clean_exit)) {
            g.clean_exit = false;
            code = 2;
            // Best effort only: durable storage failure may also prevent this
            // negative checkpoint. The completion contract still rejects it.
            g.capture_sink.failure_output(output_json());
        }
    } else if (g.cfg.capture_native_pairs != "on") {
        // Keep the historical performance output path and behavior unchanged.
        write_output_json();
    }
    LOGI("REFBENCH_DONE code=%d scene=%s run_id=%s frames=%d clean=%d",
         code, g.cfg.scene.c_str(), g.cfg.run_id.c_str(), g.frames_submitted, (int)g.clean_exit);
    // 礼貌收尾: 请求 finish, 短暂排空事件; 无论成败, 保证进程退出 (harness 靠 pidof 判结束)
    if (g.app && g.app->activity) ANativeActivity_finish(g.app->activity);
    for (int i = 0; i < 20 && g.app && !g.app->destroyRequested; i++) {
        int events; android_poll_source* src;
        while (ALooper_pollOnce(25, nullptr, &events, (void**)&src) >= 0) {
            if (src) src->process(g.app, src);
            if (g.app->destroyRequested) break;
        }
    }
    _exit(code);
}

#define VKC(x)                                                        \
    do {                                                              \
        VkResult r_ = (x);                                            \
        if (r_ != VK_SUCCESS) {                                       \
            LOGE("vk error %d at %s:%d", (int)r_, __FILE__, __LINE__);\
            g.clean_exit = false;                                     \
            finish_and_exit(2);                                       \
        }                                                             \
    } while (0)

// ── intent extras → Cfg ────────────────────────────────────────────────

static void read_cfg_from_intent(android_app* app) {
    JavaVM* vm = app->activity->vm;
    JNIEnv* env = nullptr;
    if (vm->AttachCurrentThread(&env, nullptr) != JNI_OK) { LOGE("jni attach failed"); return; }
    jobject act = app->activity->clazz;
    jclass actCls = env->GetObjectClass(act);
    jmethodID midGetIntent = env->GetMethodID(actCls, "getIntent", "()Landroid/content/Intent;");
    jobject intent = env->CallObjectMethod(act, midGetIntent);
    if (intent) {
        jclass intentCls = env->GetObjectClass(intent);
        jmethodID midGse = env->GetMethodID(intentCls, "getStringExtra",
                                            "(Ljava/lang/String;)Ljava/lang/String;");
        auto getS = [&](const char* key, const std::string& def) -> std::string {
            jstring jk = env->NewStringUTF(key);
            jstring jv = (jstring)env->CallObjectMethod(intent, midGse, jk);
            env->DeleteLocalRef(jk);
            if (!jv) return def;
            const char* c = env->GetStringUTFChars(jv, nullptr);
            std::string s = c ? c : def;
            env->ReleaseStringUTFChars(jv, c);
            env->DeleteLocalRef(jv);
            return s;
        };
        g.cfg.scene = getS("scene", g.cfg.scene);
        g.cfg.run_id = getS("run_id", g.cfg.run_id);
        std::string fr = getS("frames", ""), in = getS("intensity", "");
        if (!fr.empty()) g.cfg.frames = atoi(fr.c_str());
        if (!in.empty()) g.cfg.intensity = atoi(in.c_str());
        g.cfg.knob_loadop = getS("knob.loadop", g.cfg.knob_loadop);
        g.cfg.knob_postfx = getS("knob.postfx", g.cfg.knob_postfx);
        g.cfg.postfx_shader = getS("postfx.shader", g.cfg.postfx_shader);
        g.cfg.capture_native_pairs = getS("capture.native_pairs", "off");
        g.cfg.capture_gpu_timestamps = getS("capture.gpu_timestamps", "off");
        g.cfg.capture_bw_hash = getS("capture.bw_hash", "off");
        auto captureInt = [&](const char* key, int fallback) {
            std::string value = getS(key, std::to_string(fallback));
            if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos || value.size() > 6) return -1;
            return atoi(value.c_str());
        };
        g.cfg.capture_start = captureInt("capture.start", 0);
        g.cfg.capture_count = captureInt("capture.count", 4);
        g.cfg.capture_stride = captureInt("capture.stride", 1);
        g.cfg.capture_rules_path = getS("capture.rules_path", "");
        g.cfg.capture_rules_sha256 = getS("capture.rules_sha256", "");
    }
    vm->DetachCurrentThread();
    if (g.cfg.frames < 60) g.cfg.frames = 60;
    if (g.cfg.frames > 100000) g.cfg.frames = 100000;
    if (g.cfg.intensity < 1) g.cfg.intensity = 1;
    if (g.cfg.intensity > 64) g.cfg.intensity = 64;
    if (g.cfg.scene != "bw_pingpong" && g.cfg.scene != "frag_alu" &&
        g.cfg.scene != "idle_cap" && g.cfg.scene != "sr_pipeline") {
        LOGE("unknown scene '%s', falling back to bw_pingpong", g.cfg.scene.c_str());
        g.cfg.scene = "bw_pingpong";
    }
    if (g.cfg.knob_loadop != "on" && g.cfg.knob_loadop != "off") g.cfg.knob_loadop = "off";
    if (g.cfg.knob_postfx != "on" && g.cfg.knob_postfx != "off") g.cfg.knob_postfx = "off";
    if ((g.cfg.capture_gpu_timestamps != "off" && g.cfg.capture_gpu_timestamps != "on") ||
            (g.cfg.capture_gpu_timestamps == "on" && g.cfg.capture_native_pairs == "on")) {
        LOGE("invalid GPU timestamp mode or simultaneous quality capture");
        finish_and_exit(2);
    }
    if ((g.cfg.capture_bw_hash != "off" && g.cfg.capture_bw_hash != "on") ||
        (g.cfg.capture_bw_hash == "on" && (g.cfg.scene != "bw_pingpong" ||
         g.cfg.capture_gpu_timestamps != "off" || g.cfg.capture_native_pairs != "off"))) {
        LOGE("invalid bandwidth readback configuration"); finish_and_exit(2);
    }
    if (g.cfg.capture_native_pairs != "off" && g.cfg.capture_native_pairs != "on") {
        LOGE("invalid capture.native_pairs"); finish_and_exit(2);
    }
    if (g.cfg.capture_native_pairs == "on" &&
        (g.cfg.scene != "sr_pipeline" || !paired_capture::name_ok(g.cfg.run_id) ||
         !paired_capture::hash_ok(g.cfg.capture_rules_sha256) || g.cfg.capture_rules_path.empty() ||
         g.cfg.capture_start < 0 || g.cfg.capture_count < 1 || g.cfg.capture_count > 16 ||
         g.cfg.capture_stride < 1 || g.cfg.capture_stride > 16 ||
         int64_t(g.cfg.capture_start) + int64_t(g.cfg.capture_count - 1) * g.cfg.capture_stride >= g.cfg.frames)) {
        LOGE("invalid native paired capture configuration; refusing silent fallback"); finish_and_exit(2);
    }
    LOGI("REFBENCH_CFG scene=%s run_id=%s frames=%d intensity=%d loadop=%s postfx=%s shader=%s",
         g.cfg.scene.c_str(), g.cfg.run_id.c_str(), g.cfg.frames, g.cfg.intensity,
         g.cfg.knob_loadop.c_str(), g.cfg.knob_postfx.c_str(),
         g.cfg.postfx_shader.empty() ? "-" : g.cfg.postfx_shader.c_str());
}

// ── vulkan 搭建 ───────────────────────────────────────────────────────

static VkShaderModule make_shader(const unsigned char* data, unsigned len) {
    std::vector<uint32_t> words((len + 3) / 4, 0);
    memcpy(words.data(), data, len);
    VkShaderModuleCreateInfo ci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    ci.codeSize = len;
    ci.pCode = words.data();
    VkShaderModule m;
    VKC(vkCreateShaderModule(g.dev, &ci, nullptr, &m));
    return m;
}

static uint32_t find_mem_type(uint32_t bits, VkMemoryPropertyFlags props) {
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(g.phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & props) == props) return i;
    LOGE("no memory type for bits=0x%x props=0x%x", bits, props);
    finish_and_exit(2);
    return 0;
}

static void make_rt(uint32_t w, uint32_t h, VkFormat fmt, VkImage* img, VkDeviceMemory* mem,
                    VkImageView* view) {
    VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ic.imageType = VK_IMAGE_TYPE_2D;
    ic.format = fmt;
    ic.extent = {w, h, 1};
    ic.mipLevels = 1;
    ic.arrayLayers = 1;
    ic.samples = VK_SAMPLE_COUNT_1_BIT;
    ic.tiling = VK_IMAGE_TILING_OPTIMAL;
    ic.usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT |
               VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (g.cfg.capture_bw_hash == "on") ic.usage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    ic.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ic.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VKC(vkCreateImage(g.dev, &ic, nullptr, img));
    VkMemoryRequirements mr;
    vkGetImageMemoryRequirements(g.dev, *img, &mr);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = mr.size;
    ai.memoryTypeIndex = find_mem_type(mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    VKC(vkAllocateMemory(g.dev, &ai, nullptr, mem));
    VKC(vkBindImageMemory(g.dev, *img, *mem, 0));
    VkImageViewCreateInfo vc{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vc.image = *img;
    vc.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vc.format = fmt;
    vc.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VKC(vkCreateImageView(g.dev, &vc, nullptr, view));
}

// 与 make_rt 同, 额外带 STORAGE usage: compute 后处理要把它当 image2D 读写。
static void make_storage_rt(uint32_t w, uint32_t h, VkFormat fmt, bool color_attachment,
                            VkImage* img, VkDeviceMemory* mem, VkImageView* view) {
    VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ic.imageType = VK_IMAGE_TYPE_2D;
    ic.format = fmt;
    ic.extent = {w, h, 1};
    ic.mipLevels = 1;
    ic.arrayLayers = 1;
    ic.samples = VK_SAMPLE_COUNT_1_BIT;
    ic.tiling = VK_IMAGE_TILING_OPTIMAL;
    ic.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT |
               VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (g.cfg.capture_native_pairs == "on") ic.usage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    if (color_attachment) ic.usage |= VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    ic.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ic.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VKC(vkCreateImage(g.dev, &ic, nullptr, img));
    VkMemoryRequirements mr;
    vkGetImageMemoryRequirements(g.dev, *img, &mr);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = mr.size;
    ai.memoryTypeIndex = find_mem_type(mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    VKC(vkAllocateMemory(g.dev, &ai, nullptr, mem));
    VKC(vkBindImageMemory(g.dev, *img, *mem, 0));
    VkImageViewCreateInfo vc{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vc.image = *img;
    vc.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vc.format = fmt;
    vc.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VKC(vkCreateImageView(g.dev, &vc, nullptr, view));
}

// 从设备文件系统装入外部 SPIR-V (candidate.spv)。
//
// 失败**不静默降级**: postfx 开着却装不上算子, 就直接让整局失败。
// 悄悄退回 off 的话, A/B 两臂会变成 A 和 A, 而上层还以为自己在对照算子 —— 
// 那比跑不起来危险得多。
static bool load_external_spirv(const std::string& path, VkShaderModule* out, long* bytes) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) { LOGE("postfx: 打不开 %s", path.c_str()); return false; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz <= 0 || (sz % 4) != 0) {
        LOGE("postfx: %s 长度 %ld 非法 (必须为正且是 4 的倍数)", path.c_str(), sz);
        fclose(f);
        return false;
    }
    std::vector<uint32_t> words((size_t)sz / 4);
    size_t got = fread(words.data(), 1, (size_t)sz, f);
    fclose(f);
    if (got != (size_t)sz) { LOGE("postfx: %s 读取不全", path.c_str()); return false; }
    if (words[0] != 0x07230203u) { LOGE("postfx: %s 魔数不对", path.c_str()); return false; }

    VkShaderModuleCreateInfo sc{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    sc.codeSize = (size_t)sz;
    sc.pCode = words.data();
    if (vkCreateShaderModule(g.dev, &sc, nullptr, out) != VK_SUCCESS) {
        LOGE("postfx: vkCreateShaderModule 失败 (%s)", path.c_str());
        return false;
    }
    *bytes = sz;
    g.candidate_sha256 = paired_capture::digest(words.data(), size_t(sz));
    return true;
}

// 离屏 renderpass: 可选 LOAD / DONT_CARE, final=SHADER_READ_ONLY, 带前后外部依赖
static VkRenderPass make_offscreen_rp(VkFormat fmt, bool load) {
    VkAttachmentDescription att{};
    att.format = fmt;
    att.samples = VK_SAMPLE_COUNT_1_BIT;
    att.loadOp = load ? VK_ATTACHMENT_LOAD_OP_LOAD : VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    att.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    att.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    att.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    att.initialLayout = load ? VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
    att.finalLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    VkAttachmentReference ref{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription sp{};
    sp.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    sp.colorAttachmentCount = 1;
    sp.pColorAttachments = &ref;
    VkSubpassDependency deps[2]{};
    deps[0].srcSubpass = VK_SUBPASS_EXTERNAL;
    deps[0].dstSubpass = 0;
    deps[0].srcStageMask = VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT | VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    deps[0].srcAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    deps[0].dstStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    deps[0].dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_COLOR_ATTACHMENT_READ_BIT;
    deps[1].srcSubpass = 0;
    deps[1].dstSubpass = VK_SUBPASS_EXTERNAL;
    deps[1].srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    deps[1].srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    deps[1].dstStageMask = VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT;
    deps[1].dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    VkRenderPassCreateInfo rc{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};
    rc.attachmentCount = 1;
    rc.pAttachments = &att;
    rc.subpassCount = 1;
    rc.pSubpasses = &sp;
    rc.dependencyCount = 2;
    rc.pDependencies = deps;
    VkRenderPass rp;
    VKC(vkCreateRenderPass(g.dev, &rc, nullptr, &rp));
    return rp;
}

static VkRenderPass make_swap_rp(bool clear) {
    VkAttachmentDescription att{};
    att.format = g.swap_format;
    att.samples = VK_SAMPLE_COUNT_1_BIT;
    att.loadOp = clear ? VK_ATTACHMENT_LOAD_OP_CLEAR : VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    att.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
    att.stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
    att.stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
    att.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    att.finalLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    VkAttachmentReference ref{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    VkSubpassDescription sp{};
    sp.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
    sp.colorAttachmentCount = 1;
    sp.pColorAttachments = &ref;
    VkSubpassDependency dep{};
    dep.srcSubpass = VK_SUBPASS_EXTERNAL;
    dep.dstSubpass = 0;
    dep.srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    dep.srcAccessMask = 0;
    dep.dstStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
    dep.dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
    VkRenderPassCreateInfo rc{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};
    rc.attachmentCount = 1;
    rc.pAttachments = &att;
    rc.subpassCount = 1;
    rc.pSubpasses = &sp;
    rc.dependencyCount = 1;
    rc.pDependencies = &dep;
    VkRenderPass rp;
    VKC(vkCreateRenderPass(g.dev, &rc, nullptr, &rp));
    return rp;
}

static VkFramebuffer make_fb(VkRenderPass rp, VkImageView view, uint32_t w, uint32_t h) {
    VkFramebufferCreateInfo fc{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO};
    fc.renderPass = rp;
    fc.attachmentCount = 1;
    fc.pAttachments = &view;
    fc.width = w;
    fc.height = h;
    fc.layers = 1;
    VkFramebuffer fb;
    VKC(vkCreateFramebuffer(g.dev, &fc, nullptr, &fb));
    return fb;
}

static VkPipeline make_pipeline(VkShaderModule vs, VkShaderModule fs, VkRenderPass rp,
                                uint32_t w, uint32_t h) {
    VkPipelineShaderStageCreateInfo st[2]{};
    st[0].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    st[0].stage = VK_SHADER_STAGE_VERTEX_BIT;
    st[0].module = vs;
    st[0].pName = "main";
    st[1].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    st[1].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
    st[1].module = fs;
    st[1].pName = "main";
    VkPipelineVertexInputStateCreateInfo vi{VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
    VkPipelineInputAssemblyStateCreateInfo ia{VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO};
    ia.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
    VkViewport vp{0, 0, (float)w, (float)h, 0.0f, 1.0f};
    VkRect2D sc{{0, 0}, {w, h}};
    VkPipelineViewportStateCreateInfo vps{VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO};
    vps.viewportCount = 1;
    vps.pViewports = &vp;
    vps.scissorCount = 1;
    vps.pScissors = &sc;
    VkPipelineRasterizationStateCreateInfo rs{VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO};
    rs.polygonMode = VK_POLYGON_MODE_FILL;
    rs.cullMode = VK_CULL_MODE_NONE;
    rs.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
    rs.lineWidth = 1.0f;
    VkPipelineMultisampleStateCreateInfo ms{VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO};
    ms.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
    VkPipelineColorBlendAttachmentState ba{};
    ba.blendEnable = VK_FALSE;
    ba.colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT |
                        VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT;
    VkPipelineColorBlendStateCreateInfo bs{VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
    bs.attachmentCount = 1;
    bs.pAttachments = &ba;
    VkGraphicsPipelineCreateInfo pc{VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO};
    pc.stageCount = 2;
    pc.pStages = st;
    pc.pVertexInputState = &vi;
    pc.pInputAssemblyState = &ia;
    pc.pViewportState = &vps;
    pc.pRasterizationState = &rs;
    pc.pMultisampleState = &ms;
    pc.pColorBlendState = &bs;
    pc.layout = g.playout;
    pc.renderPass = rp;
    pc.subpass = 0;
    VkPipeline p;
    VKC(vkCreateGraphicsPipelines(g.dev, VK_NULL_HANDLE, 1, &pc, nullptr, &p));
    return p;
}

static void init_vulkan() {
    // instance
    uint32_t next = 0;
    vkEnumerateInstanceExtensionProperties(nullptr, &next, nullptr);
    std::vector<VkExtensionProperties> exts(next);
    vkEnumerateInstanceExtensionProperties(nullptr, &next, exts.data());
    bool has_debug = false;
    for (auto& e : exts)
        if (!strcmp(e.extensionName, VK_EXT_DEBUG_UTILS_EXTENSION_NAME)) has_debug = true;
    std::vector<const char*> ienames = {"VK_KHR_surface", "VK_KHR_android_surface"};
    if (has_debug) ienames.push_back(VK_EXT_DEBUG_UTILS_EXTENSION_NAME);
    VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    ai.pApplicationName = "refbench";
    ai.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ic{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ic.pApplicationInfo = &ai;
    ic.enabledExtensionCount = (uint32_t)ienames.size();
    ic.ppEnabledExtensionNames = ienames.data();
    VKC(vkCreateInstance(&ic, nullptr, &g.instance));

    // surface
    VkAndroidSurfaceCreateInfoKHR sc{VK_STRUCTURE_TYPE_ANDROID_SURFACE_CREATE_INFO_KHR};
    sc.window = g.app->window;
    VKC(vkCreateAndroidSurfaceKHR(g.instance, &sc, nullptr, &g.surface));

    // physical device + queue family
    uint32_t nphys = 0;
    vkEnumeratePhysicalDevices(g.instance, &nphys, nullptr);
    std::vector<VkPhysicalDevice> phys(nphys);
    vkEnumeratePhysicalDevices(g.instance, &nphys, phys.data());
    for (auto p : phys) {
        uint32_t nq = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(p, &nq, nullptr);
        std::vector<VkQueueFamilyProperties> qf(nq);
        vkGetPhysicalDeviceQueueFamilyProperties(p, &nq, qf.data());
        for (uint32_t i = 0; i < nq; i++) {
            VkBool32 pres = VK_FALSE;
            vkGetPhysicalDeviceSurfaceSupportKHR(p, i, g.surface, &pres);
            if ((qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) && pres) {
                g.phys = p;
                g.qfam = i;
                g.timestamp_bits = qf[i].timestampValidBits;
                break;
            }
        }
        if (g.phys) break;
    }
    if (!g.phys) { LOGE("no suitable gpu"); finish_and_exit(2); }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(g.phys, &props);
    g.gpu_name = props.deviceName;
    g.timestamp_period_ns = props.limits.timestampPeriod;
    LOGI("gpu: %s", g.gpu_name.c_str());

    // device
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qc{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qc.queueFamilyIndex = g.qfam;
    qc.queueCount = 1;
    qc.pQueuePriorities = &prio;
    const char* denames[] = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
    VkDeviceCreateInfo dc{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dc.queueCreateInfoCount = 1;
    dc.pQueueCreateInfos = &qc;
    dc.enabledExtensionCount = 1;
    dc.ppEnabledExtensionNames = denames;
    VKC(vkCreateDevice(g.phys, &dc, nullptr, &g.dev));
    vkGetDeviceQueue(g.dev, g.qfam, 0, &g.queue);

    if (has_debug) {
        g.beginLabel = (PFN_vkCmdBeginDebugUtilsLabelEXT)vkGetInstanceProcAddr(
            g.instance, "vkCmdBeginDebugUtilsLabelEXT");
        g.endLabel = (PFN_vkCmdEndDebugUtilsLabelEXT)vkGetInstanceProcAddr(
            g.instance, "vkCmdEndDebugUtilsLabelEXT");
        g.debug_labels = g.beginLabel && g.endLabel;
    }

    // swapchain
    VkSurfaceCapabilitiesKHR caps;
    VKC(vkGetPhysicalDeviceSurfaceCapabilitiesKHR(g.phys, g.surface, &caps));
    uint32_t nfmt = 0;
    vkGetPhysicalDeviceSurfaceFormatsKHR(g.phys, g.surface, &nfmt, nullptr);
    std::vector<VkSurfaceFormatKHR> fmts(nfmt);
    vkGetPhysicalDeviceSurfaceFormatsKHR(g.phys, g.surface, &nfmt, fmts.data());
    VkSurfaceFormatKHR pick = fmts[0];
    for (auto& f : fmts)
        if (f.format == VK_FORMAT_R8G8B8A8_UNORM) { pick = f; break; }
    if (pick.format != VK_FORMAT_R8G8B8A8_UNORM)
        for (auto& f : fmts)
            if (f.format == VK_FORMAT_B8G8R8A8_UNORM) { pick = f; break; }
    g.swap_format = pick.format;

    uint32_t nmode = 0;
    vkGetPhysicalDeviceSurfacePresentModesKHR(g.phys, g.surface, &nmode, nullptr);
    std::vector<VkPresentModeKHR> modes(nmode);
    vkGetPhysicalDeviceSurfacePresentModesKHR(g.phys, g.surface, &nmode, modes.data());
    auto supported = [&](VkPresentModeKHR m) {
        for (auto x : modes) if (x == m) return true;
        return false;
    };
    if (g.cfg.scene == "idle_cap") {
        g.present_mode = VK_PRESENT_MODE_FIFO_KHR;  // 强制: 该场景的标准答案就是 vsync 钉住
    } else if (supported(VK_PRESENT_MODE_MAILBOX_KHR)) {
        g.present_mode = VK_PRESENT_MODE_MAILBOX_KHR;
    } else if (supported(VK_PRESENT_MODE_IMMEDIATE_KHR)) {
        g.present_mode = VK_PRESENT_MODE_IMMEDIATE_KHR;
    } else {
        g.present_mode = VK_PRESENT_MODE_FIFO_KHR;
    }

    g.swap_extent = caps.currentExtent;
    g.pre_transform = caps.currentTransform;
    uint32_t imgcount = caps.minImageCount + 1;
    if (g.present_mode == VK_PRESENT_MODE_MAILBOX_KHR && imgcount < 3) imgcount = 3;
    if (caps.maxImageCount > 0 && imgcount > caps.maxImageCount) imgcount = caps.maxImageCount;

    VkCompositeAlphaFlagBitsKHR calpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    if (!(caps.supportedCompositeAlpha & calpha))
        calpha = (VkCompositeAlphaFlagBitsKHR)(1u << __builtin_ctz(caps.supportedCompositeAlpha));

    VkSwapchainCreateInfoKHR swc{VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
    swc.surface = g.surface;
    swc.minImageCount = imgcount;
    swc.imageFormat = g.swap_format;
    swc.imageColorSpace = pick.colorSpace;
    swc.imageExtent = g.swap_extent;
    swc.imageArrayLayers = 1;
    swc.imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    swc.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
    swc.preTransform = g.pre_transform;
    swc.compositeAlpha = calpha;
    swc.presentMode = g.present_mode;
    swc.clipped = VK_TRUE;
    VKC(vkCreateSwapchainKHR(g.dev, &swc, nullptr, &g.swapchain));

    uint32_t nsimg = 0;
    vkGetSwapchainImagesKHR(g.dev, g.swapchain, &nsimg, nullptr);
    g.swap_images.resize(nsimg);
    vkGetSwapchainImagesKHR(g.dev, g.swapchain, &nsimg, g.swap_images.data());
    g.swap_views.resize(nsimg);
    for (uint32_t i = 0; i < nsimg; i++) {
        VkImageViewCreateInfo vc{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
        vc.image = g.swap_images[i];
        vc.viewType = VK_IMAGE_VIEW_TYPE_2D;
        vc.format = g.swap_format;
        vc.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        VKC(vkCreateImageView(g.dev, &vc, nullptr, &g.swap_views[i]));
    }
    LOGI("swapchain: %ux%u %s %s x%u", g.swap_extent.width, g.swap_extent.height,
         format_name(g.swap_format), present_mode_name(g.present_mode), nsimg);

    // 布局/描述符
    VkSamplerCreateInfo smc{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    smc.magFilter = VK_FILTER_LINEAR;
    smc.minFilter = VK_FILTER_LINEAR;
    smc.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
    smc.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    smc.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    smc.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    VKC(vkCreateSampler(g.dev, &smc, nullptr, &g.sampler));

    VkDescriptorSetLayoutBinding bind{};
    bind.binding = 0;
    bind.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    bind.descriptorCount = 1;
    bind.stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT;
    VkDescriptorSetLayoutCreateInfo dlc{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dlc.bindingCount = 1;
    dlc.pBindings = &bind;
    VKC(vkCreateDescriptorSetLayout(g.dev, &dlc, nullptr, &g.dsl));

    VkPushConstantRange pcr{VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8};
    VkPipelineLayoutCreateInfo plc{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    plc.setLayoutCount = 1;
    plc.pSetLayouts = &g.dsl;
    plc.pushConstantRangeCount = 1;
    plc.pPushConstantRanges = &pcr;
    VKC(vkCreatePipelineLayout(g.dev, &plc, nullptr, &g.playout));

    VkDescriptorPoolSize psz{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 3};
    VkDescriptorPoolCreateInfo dpc{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpc.maxSets = 3;
    dpc.poolSizeCount = 1;
    dpc.pPoolSizes = &psz;
    VKC(vkCreateDescriptorPool(g.dev, &dpc, nullptr, &g.dpool));

    // 命令池 + 同步原语
    VkCommandPoolCreateInfo cpc{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpc.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    cpc.queueFamilyIndex = g.qfam;
    VKC(vkCreateCommandPool(g.dev, &cpc, nullptr, &g.cmdpool));
    VkCommandBufferAllocateInfo cba{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cba.commandPool = g.cmdpool;
    cba.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cba.commandBufferCount = State::FLIGHTS;
    VKC(vkAllocateCommandBuffers(g.dev, &cba, g.cmd));
    for (int i = 0; i < State::FLIGHTS; i++) {
        VkFenceCreateInfo fc{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        fc.flags = VK_FENCE_CREATE_SIGNALED_BIT;
        VKC(vkCreateFence(g.dev, &fc, nullptr, &g.fence[i]));
        VkSemaphoreCreateInfo sc2{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        VKC(vkCreateSemaphore(g.dev, &sc2, nullptr, &g.sem_acq[i]));
    }
    g.sem_ren.resize(g.swap_images.size());
    for (auto& s : g.sem_ren) {
        VkSemaphoreCreateInfo sc2{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        VKC(vkCreateSemaphore(g.dev, &sc2, nullptr, &s));
    }

    if (g.cfg.capture_gpu_timestamps == "on") {
        if (!g.gpu_timestamps.configure(g.cfg.frames, g.timestamp_bits, g.timestamp_period_ns)) {
            LOGE("GPU timestamp capabilities unavailable");
            finish_and_exit(2);
        }
        VkQueryPoolCreateInfo query{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
        query.queryType = VK_QUERY_TYPE_TIMESTAMP;
        query.queryCount = State::FLIGHTS * 2;
        VKC(vkCreateQueryPool(g.dev, &query, nullptr, &g.timestamp_pool));
    }

    // renderpass (swap 侧两种 loadOp 变体共享 framebuffer, 属兼容 renderpass)
    g.rp_swap_dont = make_swap_rp(false);
    g.rp_swap_clear = make_swap_rp(true);
    g.swap_fbs.resize(g.swap_images.size());
    for (size_t i = 0; i < g.swap_images.size(); i++)
        g.swap_fbs[i] = make_fb(g.rp_swap_dont, g.swap_views[i], g.swap_extent.width, g.swap_extent.height);

    VkShaderModule vs_full = make_shader(g_fullscreen_vert, g_fullscreen_vert_len);
    VkShaderModule fs_blit = make_shader(g_blit_frag, g_blit_frag_len);

    auto write_set = [&](VkDescriptorSet set, VkImageView view) {
        VkDescriptorImageInfo ii{g.sampler, view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        w.dstSet = set;
        w.dstBinding = 0;
        w.descriptorCount = 1;
        w.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        w.pImageInfo = &ii;
        vkUpdateDescriptorSets(g.dev, 1, &w, 0, nullptr);
    };
    auto alloc_set = [&]() {
        VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        da.descriptorPool = g.dpool;
        da.descriptorSetCount = 1;
        da.pSetLayouts = &g.dsl;
        VkDescriptorSet s;
        VKC(vkAllocateDescriptorSets(g.dev, &da, &s));
        return s;
    };

    // 初始清屏 + 布局迁移 (一次性): 让 LOAD 臂第一帧读到确定内容
    auto init_clear = [&](std::vector<VkImage> imgs) {
        VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        VKC(vkBeginCommandBuffer(g.cmd[0], &bi));
        float cols[3][4] = {{0.5f, 0.25f, 0.75f, 1.0f}, {0.3f, 0.6f, 0.2f, 1.0f}, {0.1f, 0.1f, 0.1f, 1.0f}};
        int ci2 = 0;
        for (VkImage im : imgs) {
            VkImageMemoryBarrier b1{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
            b1.srcAccessMask = 0;
            b1.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            b1.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
            b1.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            b1.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            b1.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
            b1.image = im;
            b1.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            vkCmdPipelineBarrier(g.cmd[0], VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                                 VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &b1);
            VkClearColorValue cv;
            memcpy(cv.float32, cols[ci2 % 3], sizeof cv.float32);
            ci2++;
            VkImageSubresourceRange r{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            vkCmdClearColorImage(g.cmd[0], im, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &cv, 1, &r);
            VkImageMemoryBarrier b2 = b1;
            b2.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
            b2.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_READ_BIT;
            b2.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
            b2.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
            vkCmdPipelineBarrier(g.cmd[0], VK_PIPELINE_STAGE_TRANSFER_BIT,
                                 VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT | VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                                 0, 0, nullptr, 0, nullptr, 1, &b2);
        }
        VKC(vkEndCommandBuffer(g.cmd[0]));
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.commandBufferCount = 1;
        si.pCommandBuffers = &g.cmd[0];
        VKC(vkQueueSubmit(g.queue, 1, &si, VK_NULL_HANDLE));
        VKC(vkQueueWaitIdle(g.queue));
    };

    // ── 场景专属搭建 + ground truth 记录 ──
    if (g.cfg.scene == "bw_pingpong") {
        const VkFormat F = VK_FORMAT_R16G16B16A16_SFLOAT;
        make_rt(State::BW_RT, State::BW_RT, F, &g.imgA, &g.memA, &g.viewA);
        make_rt(State::BW_RT, State::BW_RT, F, &g.imgB, &g.memB, &g.viewB);
        g.rp_off_load = make_offscreen_rp(F, true);
        g.rp_off_dont = make_offscreen_rp(F, false);
        g.fbA = make_fb(g.rp_off_load, g.viewA, State::BW_RT, State::BW_RT);
        g.fbB = make_fb(g.rp_off_load, g.viewB, State::BW_RT, State::BW_RT);
        g.setA = alloc_set(); write_set(g.setA, g.viewA);
        g.setB = alloc_set(); write_set(g.setB, g.viewB);
        VkShaderModule fs_ping = make_shader(g_pingpong_frag, g_pingpong_frag_len);
        g.pipe_ping = make_pipeline(vs_full, fs_ping, g.rp_off_load, State::BW_RT, State::BW_RT);
        g.pipe_blit = make_pipeline(vs_full, fs_blit, g.rp_swap_dont,
                                    g.swap_extent.width, g.swap_extent.height);
        vkDestroyShaderModule(g.dev, fs_ping, nullptr);
        init_clear({g.imgA, g.imgB});
        bool on = g.cfg.knob_loadop == "on";
        for (int i = 0; i < g.cfg.intensity; i++) {
            PassInfo p;
            char nm[32]; snprintf(nm, sizeof nm, "ping_%d", i);
            p.name = nm;
            p.target = ((i + 1) % 2 == 0) ? "rtA" : "rtB";
            p.load_op = on ? "DONT_CARE" : "LOAD";
            p.store_op = "STORE";
            p.w = p.h = State::BW_RT;
            p.format = "R16G16B16A16_SFLOAT";
            g.passes.push_back(p);
        }
        g.passes.push_back({"present_blit", "swapchain", "DONT_CARE", "STORE",
                            format_name(g.swap_format), g.swap_extent.width, g.swap_extent.height});
    } else if (g.cfg.scene == "frag_alu") {
        const VkFormat F = VK_FORMAT_R8G8B8A8_UNORM;
        make_rt(State::ALU_RT, State::ALU_RT, F, &g.imgC, &g.memC, &g.viewC);
        g.rp_alu = make_offscreen_rp(F, false);
        g.fbC = make_fb(g.rp_alu, g.viewC, State::ALU_RT, State::ALU_RT);
        g.setC = alloc_set(); write_set(g.setC, g.viewC);
        VkShaderModule fs_alu = make_shader(g_alu_frag, g_alu_frag_len);
        g.pipe_alu = make_pipeline(vs_full, fs_alu, g.rp_alu, State::ALU_RT, State::ALU_RT);
        g.pipe_blit = make_pipeline(vs_full, fs_blit, g.rp_swap_dont,
                                    g.swap_extent.width, g.swap_extent.height);
        vkDestroyShaderModule(g.dev, fs_alu, nullptr);
        init_clear({g.imgC});
        g.passes.push_back({"alu_main", "rtC", "DONT_CARE", "STORE",
                            "R8G8B8A8_UNORM", State::ALU_RT, State::ALU_RT});
        g.passes.push_back({"present_blit", "swapchain", "DONT_CARE", "STORE",
                            format_name(g.swap_format), g.swap_extent.width, g.swap_extent.height});
    } else if (g.cfg.scene == "sr_pipeline") {
        // 游戏以低分辨率渲染 -> 管线尾部 compute 算子重构到送显分辨率 -> 呈现。
        // 这一支的存在意义: 算子不再是孤立跑在空设备上的一次 dispatch,
        // 而是挂在一整套真实渲染负载的尾巴上, 和场景 pass 抢同一份带宽、
        // 同一份功耗预算、同一条呈现时序。
        const VkFormat F = VK_FORMAT_R8G8B8A8_UNORM;
        make_storage_rt(State::SR_LO_W, State::SR_LO_H, F, true, &g.imgLo, &g.memLo, &g.viewLo);
        make_storage_rt(State::SR_HI_W, State::SR_HI_H, F, false, &g.imgHi, &g.memHi, &g.viewHi);
        g.rp_alu = make_offscreen_rp(F, false);
        g.fbLo = make_fb(g.rp_alu, g.viewLo, State::SR_LO_W, State::SR_LO_H);
        g.setLo = alloc_set(); write_set(g.setLo, g.viewLo);
        g.setHi = alloc_set(); write_set(g.setHi, g.viewHi);

        VkShaderModule fs_alu = make_shader(g_alu_frag, g_alu_frag_len);
        g.pipe_alu = make_pipeline(vs_full, fs_alu, g.rp_alu, State::SR_LO_W, State::SR_LO_H);
        if (g.cfg.capture_native_pairs == "on") {
            make_storage_rt(State::SR_HI_W, State::SR_HI_H, F, true,
                            &g.imgNativeHR, &g.memNativeHR, &g.viewNativeHR);
            g.fbNativeHR = make_fb(g.rp_alu, g.viewNativeHR, State::SR_HI_W, State::SR_HI_H);
            g.pipeNativeHR = make_pipeline(vs_full, fs_alu, g.rp_alu, State::SR_HI_W, State::SR_HI_H);
        }
        g.pipe_blit = make_pipeline(vs_full, fs_blit, g.rp_swap_dont,
                                    g.swap_extent.width, g.swap_extent.height);
        vkDestroyShaderModule(g.dev, fs_alu, nullptr);
        init_clear({g.imgLo, g.imgHi});

        g.passes.push_back({"scene_lowres", "rtLo", "DONT_CARE", "STORE",
                            "R8G8B8A8_UNORM", State::SR_LO_W, State::SR_LO_H});
        if (g.cfg.capture_native_pairs == "on") {
            g.passes.push_back({"scene_native_hr_capture_only", "nativeHR", "DONT_CARE", "STORE",
                               "R8G8B8A8_UNORM", State::SR_HI_W, State::SR_HI_H});
        }

        if (g.cfg.knob_postfx == "on") {
            if (g.cfg.postfx_shader.empty()) {
                LOGE("postfx=on 但没给 postfx.shader");
                g.clean_exit = false;
                finish_and_exit(2);
            }
            if (!load_external_spirv(g.cfg.postfx_shader, &g.postfx_module, &g.postfx_bytes)) {
                // 不静默降级: 悄悄退回 off 会让 A/B 变成 A 和 A
                g.clean_exit = false;
                finish_and_exit(2);
            }
            // 两个 storage image, 与上游赛道契约一致 (binding 0 进 / 1 出)
            VkDescriptorSetLayoutBinding cb[2]{};
            for (int i = 0; i < 2; i++) {
                cb[i].binding = (uint32_t)i;
                cb[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
                cb[i].descriptorCount = 1;
                cb[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
            }
            VkDescriptorSetLayoutCreateInfo cl{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
            cl.bindingCount = 2;
            cl.pBindings = cb;
            VKC(vkCreateDescriptorSetLayout(g.dev, &cl, nullptr, &g.postfx_dsl));

            VkDescriptorPoolSize cps{VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 2};
            VkDescriptorPoolCreateInfo cpc2{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
            cpc2.maxSets = 1;
            cpc2.poolSizeCount = 1;
            cpc2.pPoolSizes = &cps;
            VKC(vkCreateDescriptorPool(g.dev, &cpc2, nullptr, &g.postfx_dpool));

            VkDescriptorSetAllocateInfo cda{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
            cda.descriptorPool = g.postfx_dpool;
            cda.descriptorSetCount = 1;
            cda.pSetLayouts = &g.postfx_dsl;
            VKC(vkAllocateDescriptorSets(g.dev, &cda, &g.postfx_set));

            VkDescriptorImageInfo cii[2]{};
            cii[0].imageView = g.viewLo;
            cii[0].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
            cii[1].imageView = g.viewHi;
            cii[1].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
            VkWriteDescriptorSet cw[2]{};
            for (int i = 0; i < 2; i++) {
                cw[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
                cw[i].dstSet = g.postfx_set;
                cw[i].dstBinding = (uint32_t)i;
                cw[i].descriptorCount = 1;
                cw[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
                cw[i].pImageInfo = &cii[i];
            }
            vkUpdateDescriptorSets(g.dev, 2, cw, 0, nullptr);

            VkPipelineLayoutCreateInfo cpl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
            cpl.setLayoutCount = 1;
            cpl.pSetLayouts = &g.postfx_dsl;
            VKC(vkCreatePipelineLayout(g.dev, &cpl, nullptr, &g.postfx_playout));

            VkComputePipelineCreateInfo ccp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
            ccp.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
            ccp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
            ccp.stage.module = g.postfx_module;
            ccp.stage.pName = "main";
            ccp.layout = g.postfx_playout;
            if (vkCreateComputePipelines(g.dev, VK_NULL_HANDLE, 1, &ccp, nullptr, &g.postfx_pipe) != VK_SUCCESS) {
                LOGE("postfx: vkCreateComputePipelines 失败");
                g.clean_exit = false;
                finish_and_exit(2);
            }
            g.postfx_active = true;
            g.passes.push_back({"postfx_compute", "rtHi", "NONE", "STORE",
                                "R8G8B8A8_UNORM", State::SR_HI_W, State::SR_HI_H});
        }

        g.passes.push_back({"present_blit", "swapchain", "DONT_CARE", "STORE",
                            format_name(g.swap_format), g.swap_extent.width, g.swap_extent.height});
    } else {  // idle_cap
        VkShaderModule vs_tri = make_shader(g_tri_vert, g_tri_vert_len);
        VkShaderModule fs_tri = make_shader(g_tri_frag, g_tri_frag_len);
        g.pipe_tri = make_pipeline(vs_tri, fs_tri, g.rp_swap_clear,
                                   g.swap_extent.width, g.swap_extent.height);
        vkDestroyShaderModule(g.dev, vs_tri, nullptr);
        vkDestroyShaderModule(g.dev, fs_tri, nullptr);
        g.passes.push_back({"idle_tri", "swapchain", "CLEAR", "STORE",
                            format_name(g.swap_format), g.swap_extent.width, g.swap_extent.height});
    }
    vkDestroyShaderModule(g.dev, vs_full, nullptr);
    vkDestroyShaderModule(g.dev, fs_blit, nullptr);
}

// ── 帧循环 ────────────────────────────────────────────────────────────

// 把本进程内驱动匿名线程 ("Thread-N") 改名为 RefbenchDrv。幂等; 返回本次改名数。
// 驱动可能延迟建线程, 所以帧循环里周期性重扫。
static int rename_driver_threads() {
    DIR* d = opendir("/proc/self/task");
    if (!d) return 0;
    int renamed = 0;
    dirent* e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        char path[64], comm[32] = {0};
        snprintf(path, sizeof path, "/proc/self/task/%s/comm", e->d_name);
        FILE* f = fopen(path, "r");
        if (!f) continue;
        size_t n = fread(comm, 1, sizeof comm - 1, f);
        fclose(f);
        while (n > 0 && (comm[n - 1] == '\n' || comm[n - 1] == '\r')) comm[--n] = 0;
        if (strncmp(comm, "Thread-", 7) != 0) continue;
        FILE* w = fopen(path, "w");
        if (!w) continue;
        fputs(DRIVER_THREAD_NAME, w);
        fclose(w);
        renamed++;
    }
    closedir(d);
    return renamed;
}

struct PushC { float phase; int32_t iters; };

static void init_capture() {
    if (g.cfg.capture_native_pairs != "on")
        return;
    std::string rules_bytes, rules_hash;
    uint64_t frozen_at = 0;
    if (!paired_capture::file_hash(g.cfg.capture_rules_path, &rules_hash, &rules_bytes) ||
        rules_hash != g.cfg.capture_rules_sha256 ||
        !paired_capture::FrozenTimeParser(rules_bytes).parse(&frozen_at)) {
        LOGE("capture rules file/hash/freeze invalid");
        finish_and_exit(2);
    }
    const char *base = g.app->activity->externalDataPath ? g.app->activity->externalDataPath
                                                         : g.app->activity->internalDataPath;
    if (!base) {
        LOGE("capture data path missing");
        finish_and_exit(2);
    }
    mkdirs(base);
    paired_capture::Config cfg;
    cfg.run_id = g.cfg.run_id;
    cfg.total_frames = g.cfg.frames;
    cfg.intensity = g.cfg.intensity;
    cfg.start = g.cfg.capture_start;
    cfg.count = g.cfg.capture_count;
    cfg.stride = g.cfg.capture_stride;
    cfg.rules_sha256 = rules_hash;
    cfg.frozen_at_ms = frozen_at;
    cfg.scene_shader_sha256 = paired_capture::digest(g_alu_frag, g_alu_frag_len);
    cfg.vertex_shader_sha256 = paired_capture::digest(g_fullscreen_vert, g_fullscreen_vert_len);
    cfg.candidate_sha256 = g.postfx_active ? g.candidate_sha256 : "";
    if (!g.capture_sink.begin(base, cfg)) {
        LOGE("capture begin failed (rules not frozen or output already exists)");
        finish_and_exit(2);
    }
    g.capture_initialized = true;
    for (const auto &role :
         std::vector<std::string>{"native_lr", "native_hr", "postfx_candidate"}) {
        if (role == "postfx_candidate" && !g.postfx_active)
            continue;
        CaptureBuffer b;
        b.role = role;
        b.width = role == "native_lr" ? State::SR_LO_W : State::SR_HI_W;
        b.height = role == "native_lr" ? State::SR_LO_H : State::SR_HI_H;
        b.bytes = VkDeviceSize(b.width) * b.height * 4;
        VkBufferCreateInfo bc{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        bc.size = b.bytes;
        bc.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
        bc.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        VKC(vkCreateBuffer(g.dev, &bc, nullptr, &b.buffer));
        VkMemoryRequirements requirements;
        vkGetBufferMemoryRequirements(g.dev, b.buffer, &requirements);
        VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
        ma.allocationSize = requirements.size;
        ma.memoryTypeIndex =
            find_mem_type(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                                           VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        VKC(vkAllocateMemory(g.dev, &ma, nullptr, &b.memory));
        VKC(vkBindBufferMemory(g.dev, b.buffer, b.memory, 0));
        g.capture_buffers.push_back(b);
    }
}

static void record_capture_copies(VkCommandBuffer cb, int frame_idx) {
    if (!g.capture_initialized || !g.capture_sink.selected(frame_idx))
        return;
    for (const auto &b : g.capture_buffers) {
        VkImage image =
            b.role == "native_lr" ? g.imgLo : (b.role == "native_hr" ? g.imgNativeHR : g.imgHi);
        VkImageMemoryBarrier to{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
        to.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_WRITE_BIT |
                           VK_ACCESS_SHADER_READ_BIT;
        to.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        to.oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        to.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        to.srcQueueFamilyIndex = to.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        to.image = image;
        to.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        vkCmdPipelineBarrier(cb,
                             VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT |
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT |
                                 VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,
                             VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &to);
        VkBufferImageCopy copy{};
        copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        copy.imageExtent = {b.width, b.height, 1};
        vkCmdCopyImageToBuffer(cb, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, b.buffer, 1, &copy);
        VkImageMemoryBarrier back = to;
        back.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
        back.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
        back.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
        back.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT |
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT |
                                 VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                             0, 0, nullptr, 0, nullptr, 1, &back);
        VkBufferMemoryBarrier host{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};
        host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        host.srcQueueFamilyIndex = host.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        host.buffer = b.buffer;
        host.size = b.bytes;
        vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0,
                             nullptr, 1, &host, 0, nullptr);
    }
}

// Quality-only full offscreen RGBA16F readback. No timing samples are taken.
static bool write_bw_hash() {
    if (!g.dev || !g.imgA || !g.clean_exit || g.frames_submitted != g.cfg.frames) return false;
    VkDeviceSize bytes = VkDeviceSize(State::BW_RT) * State::BW_RT * 8;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bi.size = bytes; bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    if (vkCreateBuffer(g.dev, &bi, nullptr, &buffer) != VK_SUCCESS) return false;
    VkMemoryRequirements mr{}; vkGetBufferMemoryRequirements(g.dev, buffer, &mr);
    VkMemoryAllocateInfo ma{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ma.allocationSize = mr.size;
    ma.memoryTypeIndex = find_mem_type(mr.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (vkAllocateMemory(g.dev, &ma, nullptr, &memory) != VK_SUCCESS ||
        vkBindBufferMemory(g.dev, buffer, memory, 0) != VK_SUCCESS) return false;
    VkCommandBuffer cmd = g.cmd[0];
    if (vkResetCommandBuffer(cmd, 0) != VK_SUCCESS) return false;
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    if (vkBeginCommandBuffer(cmd, &begin) != VK_SUCCESS) return false;
    VkImageMemoryBarrier barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    barrier.srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT;
    barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    barrier.oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = g.cfg.intensity % 2 == 0 ? g.imgA : g.imgB;
    barrier.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
        0, 0, nullptr, 0, nullptr, 1, &barrier);
    VkBufferImageCopy copy{};
    copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    copy.imageExtent = {State::BW_RT, State::BW_RT, 1};
    vkCmdCopyImageToBuffer(cmd, barrier.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &copy);
    VkBufferMemoryBarrier host{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};
    host.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; host.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    host.srcQueueFamilyIndex = host.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    host.buffer = buffer; host.size = bytes;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
        0, 0, nullptr, 1, &host, 0, nullptr);
    if (vkEndCommandBuffer(cmd) != VK_SUCCESS) return false;
    VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    submit.commandBufferCount = 1; submit.pCommandBuffers = &cmd;
    if (vkQueueSubmit(g.queue, 1, &submit, VK_NULL_HANDLE) != VK_SUCCESS ||
        vkQueueWaitIdle(g.queue) != VK_SUCCESS) return false;
    void* mapped = nullptr;
    if (vkMapMemory(g.dev, memory, 0, bytes, 0, &mapped) != VK_SUCCESS) return false;
    std::string hash = paired_capture::digest(mapped, size_t(bytes));
    vkUnmapMemory(g.dev, memory);
    vkDestroyBuffer(g.dev, buffer, nullptr); vkFreeMemory(g.dev, memory, nullptr);
    const char* base = g.app->activity->externalDataPath;
    if (!base) return false;
    std::string body = "{\"schema\":\"refbench/bw-output-hash@1\",\"run_id\":" +
        refbench_output::quote(g.cfg.run_id) + ",\"complete\":true,\"format\":\"R16G16B16A16_SFLOAT\",\"width\":2048,\"height\":2048,\"bytes\":" +
        std::to_string(bytes) + ",\"frames\":" + std::to_string(g.frames_submitted) +
        ",\"sha256\":" + refbench_output::quote(hash) + "}";
    FILE* file = fopen((std::string(base) + "/refbench_bw_hash.json").c_str(), "w");
    if (!file) return false;
    bool ok = fwrite(body.data(), 1, body.size(), file) == body.size();
    ok = fflush(file) == 0 && ok; ok = fsync(fileno(file)) == 0 && ok;
    return fclose(file) == 0 && ok;
}

static void write_capture_frame(int frame_idx) {
    std::vector<paired_capture::Raw> raw;
    for (const auto &b : g.capture_buffers) {
        void *ptr = nullptr;
        VKC(vkMapMemory(g.dev, b.memory, 0, b.bytes, 0, &ptr));
        raw.push_back({b.role, b.width, b.height, ptr});
    }
    bool ok = g.capture_sink.frame(frame_idx, paired_capture::phase(frame_idx, g.cfg.frames),
                                   g.capture_recording_timestamp_ns, raw);
    for (const auto &b : g.capture_buffers)
        vkUnmapMemory(g.dev, b.memory);
    if (!ok) {
        LOGE("capture readback write failed");
        g.clean_exit = false;
        g.capture_sink.fail("readback_write_failed");
        finish_and_exit(2);
    }
    g.capture_frames_written++;
}
static void label_begin(VkCommandBuffer cb, const char* name) {
    if (!g.debug_labels) return;
    VkDebugUtilsLabelEXT l{VK_STRUCTURE_TYPE_DEBUG_UTILS_LABEL_EXT};
    l.pLabelName = name;
    g.beginLabel(cb, &l);
}
static void label_end(VkCommandBuffer cb) {
    if (g.debug_labels) g.endLabel(cb);
}

static void record_frame(VkCommandBuffer cb, uint32_t image_idx, int frame_idx, int slot) {
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VKC(vkBeginCommandBuffer(cb, &bi));
    if (g.timestamp_pool) {
        vkCmdResetQueryPool(cb, g.timestamp_pool, slot * 2, 2);
        vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, g.timestamp_pool, slot * 2);
    }

    PushC pc;
    pc.phase = paired_capture::phase(frame_idx,g.cfg.frames);
    pc.iters = 0;

    if (g.cfg.scene == "bw_pingpong") {
        bool on = g.cfg.knob_loadop == "on";
        VkRenderPass rp = on ? g.rp_off_dont : g.rp_off_load;
        int n = g.cfg.intensity;
        for (int i = 0; i < n; i++) {
            VkFramebuffer fb = ((i + 1) % 2 == 0) ? g.fbA : g.fbB;   // pass i 写 img[(i+1)%2]
            VkDescriptorSet rd = (i % 2 == 0) ? g.setA : g.setB;     // pass i 读 img[i%2]
            label_begin(cb, g.passes[i].name.c_str());
            VkRenderPassBeginInfo rb{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
            rb.renderPass = rp;
            rb.framebuffer = fb;
            rb.renderArea = {{0, 0}, {State::BW_RT, State::BW_RT}};
            vkCmdBeginRenderPass(cb, &rb, VK_SUBPASS_CONTENTS_INLINE);
            vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_ping);
            vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.playout, 0, 1, &rd, 0, nullptr);
            vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
            vkCmdDraw(cb, 3, 1, 0, 0);
            vkCmdEndRenderPass(cb);
            label_end(cb);
        }
        // blit: 读最后写入的 img[n%2]
        VkDescriptorSet last = (n % 2 == 0) ? g.setA : g.setB;
        label_begin(cb, "present_blit");
        VkRenderPassBeginInfo rb{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        rb.renderPass = g.rp_swap_dont;
        rb.framebuffer = g.swap_fbs[image_idx];
        rb.renderArea = {{0, 0}, g.swap_extent};
        vkCmdBeginRenderPass(cb, &rb, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_blit);
        vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.playout, 0, 1, &last, 0, nullptr);
        vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
        vkCmdDraw(cb, 3, 1, 0, 0);
        vkCmdEndRenderPass(cb);
        label_end(cb);
    } else if (g.cfg.scene == "frag_alu") {
        pc.iters = g.cfg.intensity * 64;  // 实测 NX809J: 512 iter ≈ 17.7ms, 384 ≈ 13.3ms
        label_begin(cb, "alu_main");
        VkRenderPassBeginInfo rb{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        rb.renderPass = g.rp_alu;
        rb.framebuffer = g.fbC;
        rb.renderArea = {{0, 0}, {State::ALU_RT, State::ALU_RT}};
        vkCmdBeginRenderPass(cb, &rb, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_alu);
        vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
        vkCmdDraw(cb, 3, 1, 0, 0);
        vkCmdEndRenderPass(cb);
        label_end(cb);
        label_begin(cb, "present_blit");
        VkRenderPassBeginInfo rb2{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        rb2.renderPass = g.rp_swap_dont;
        rb2.framebuffer = g.swap_fbs[image_idx];
        rb2.renderArea = {{0, 0}, g.swap_extent};
        vkCmdBeginRenderPass(cb, &rb2, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_blit);
        vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.playout, 0, 1, &g.setC, 0, nullptr);
        vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
        vkCmdDraw(cb, 3, 1, 0, 0);
        vkCmdEndRenderPass(cb);
        label_end(cb);
    } else if (g.cfg.scene == "sr_pipeline") {
        pc.iters = g.cfg.intensity * 64;  // 低分辨率场景 pass 的 fragment 负载

        // 1) 场景以低分辨率渲染 (这是"游戏内部降载到 720p")
        label_begin(cb, "scene_lowres");
        VkRenderPassBeginInfo rb{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        rb.renderPass = g.rp_alu;
        rb.framebuffer = g.fbLo;
        rb.renderArea = {{0, 0}, {State::SR_LO_W, State::SR_LO_H}};
        vkCmdBeginRenderPass(cb, &rb, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_alu);
        vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
        vkCmdDraw(cb, 3, 1, 0, 0);
        vkCmdEndRenderPass(cb);
        label_end(cb);

        VkDescriptorSet present_src = g.setLo;  // postfx=off 时直接放大低分辨率图
        if (g.capture_initialized && g.capture_sink.selected(frame_idx)) {
            label_begin(cb,"scene_native_hr_capture_only");
            VkRenderPassBeginInfo hr{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};hr.renderPass=g.rp_alu;hr.framebuffer=g.fbNativeHR;
            hr.renderArea={{0,0},{State::SR_HI_W,State::SR_HI_H}};
            vkCmdBeginRenderPass(cb,&hr,VK_SUBPASS_CONTENTS_INLINE);vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_GRAPHICS,g.pipeNativeHR);
            vkCmdPushConstants(cb,g.playout,VK_SHADER_STAGE_VERTEX_BIT|VK_SHADER_STAGE_FRAGMENT_BIT,0,8,&pc);
            vkCmdDraw(cb,3,1,0,0);vkCmdEndRenderPass(cb);label_end(cb);
        }

        if (g.postfx_active) {
            // 2) 管线尾部的 compute 算子: rtLo -> rtHi, 全程在显存里
            label_begin(cb, "postfx_compute");

            // rtLo: renderpass 结束时是 SHADER_READ_ONLY, compute 要当 storage image 读
            VkImageMemoryBarrier tb[2]{};
            for (int i = 0; i < 2; i++) {
                tb[i].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                tb[i].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                tb[i].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                tb[i].subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
            }
            tb[0].image = g.imgLo;
            tb[0].oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
            tb[0].newLayout = VK_IMAGE_LAYOUT_GENERAL;
            tb[0].srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT;
            tb[0].dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            // rtHi: 上一帧末尾是 SHADER_READ_ONLY (被 blit 采样过), 首帧是 init_clear 之后的同一 layout
            tb[1].image = g.imgHi;
            tb[1].oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
            tb[1].newLayout = VK_IMAGE_LAYOUT_GENERAL;
            tb[1].srcAccessMask = VK_ACCESS_SHADER_READ_BIT;
            tb[1].dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
            vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT | VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,
                                 VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 2, tb);

            vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, g.postfx_pipe);
            vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, g.postfx_playout,
                                    0, 1, &g.postfx_set, 0, nullptr);
            // 工作组 16x16, 与上游赛道契约一致; 向上取整覆盖整幅输出
            vkCmdDispatch(cb, (State::SR_HI_W + 15) / 16, (State::SR_HI_H + 15) / 16, 1);

            // rtHi 交还给 blit 采样; rtLo 回到 renderpass 期望的 initialLayout
            VkImageMemoryBarrier bb[2]{};
            for (int i = 0; i < 2; i++) {
                bb[i].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
                bb[i].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                bb[i].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
                bb[i].subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
                bb[i].oldLayout = VK_IMAGE_LAYOUT_GENERAL;
                bb[i].newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
            }
            bb[0].image = g.imgHi;
            bb[0].srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
            bb[0].dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            bb[1].image = g.imgLo;
            bb[1].srcAccessMask = VK_ACCESS_SHADER_READ_BIT;
            bb[1].dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
            vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                 VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 2, bb);
            label_end(cb);
            present_src = g.setHi;
        }

        // 3) 呈现
        label_begin(cb, "present_blit");
        VkRenderPassBeginInfo rb2{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        rb2.renderPass = g.rp_swap_dont;
        rb2.framebuffer = g.swap_fbs[image_idx];
        rb2.renderArea = {{0, 0}, g.swap_extent};
        vkCmdBeginRenderPass(cb, &rb2, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_blit);
        vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.playout, 0, 1, &present_src, 0, nullptr);
        vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
        vkCmdDraw(cb, 3, 1, 0, 0);
        vkCmdEndRenderPass(cb);
        label_end(cb);
    } else {  // idle_cap
        label_begin(cb, "idle_tri");
        VkClearValue cv;
        cv.color = {{0.05f, 0.05f, 0.08f, 1.0f}};
        VkRenderPassBeginInfo rb{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        rb.renderPass = g.rp_swap_clear;
        rb.framebuffer = g.swap_fbs[image_idx];
        rb.renderArea = {{0, 0}, g.swap_extent};
        rb.clearValueCount = 1;
        rb.pClearValues = &cv;
        vkCmdBeginRenderPass(cb, &rb, VK_SUBPASS_CONTENTS_INLINE);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, g.pipe_tri);
        vkCmdPushConstants(cb, g.playout, VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0, 8, &pc);
        vkCmdDraw(cb, 3, 1, 0, 0);
        vkCmdEndRenderPass(cb);
        label_end(cb);
    }
    if (g.timestamp_pool)
        vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, g.timestamp_pool, slot * 2 + 1);
    record_capture_copies(cb,frame_idx);
    VKC(vkEndCommandBuffer(cb));
}

static void render_all_frames() {
    int f = 0;
    g.renamed_driver_threads += rename_driver_threads();
    for (int frame = 0; frame < g.cfg.frames; frame++) {
        if (frame % 256 == 0 && frame > 0)
            g.renamed_driver_threads += rename_driver_threads();
        // 事件泵: 不阻塞
        int events;
        android_poll_source* src;
        while (ALooper_pollOnce(0, nullptr, &events, (void**)&src) >= 0) {
            if (src) src->process(g.app, src);
            if (g.app->destroyRequested || g.window_lost) {
                LOGE("window lost / destroy at frame %d", frame);
                g.clean_exit = false;
                finish_and_exit(2);
            }
        }

        VKC(vkWaitForFences(g.dev, 1, &g.fence[f], VK_TRUE, UINT64_MAX));
        if (!collect_gpu_timestamp(f)) { g.clean_exit = false; finish_and_exit(2); }
        VKC(vkResetFences(g.dev, 1, &g.fence[f]));

        uint32_t idx = 0;
        VkResult ar = vkAcquireNextImageKHR(g.dev, g.swapchain, UINT64_MAX, g.sem_acq[f],
                                            VK_NULL_HANDLE, &idx);
        if (ar != VK_SUCCESS && ar != VK_SUBOPTIMAL_KHR) {
            LOGE("acquire failed %d at frame %d", (int)ar, frame);
            g.clean_exit = false;
            finish_and_exit(2);
        }

        if(g.capture_initialized)g.capture_recording_timestamp_ns=paired_capture::clock_ns(CLOCK_MONOTONIC);
        record_frame(g.cmd[f], idx, frame, f);

        VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT;
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.waitSemaphoreCount = 1;
        si.pWaitSemaphores = &g.sem_acq[f];
        si.pWaitDstStageMask = &wait_stage;
        si.commandBufferCount = 1;
        si.pCommandBuffers = &g.cmd[f];
        si.signalSemaphoreCount = 1;
        si.pSignalSemaphores = &g.sem_ren[idx];
        VKC(vkQueueSubmit(g.queue, 1, &si, g.fence[f]));
        if (g.timestamp_pool) g.timestamp_frames[f] = frame;

        VkPresentInfoKHR pi{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};
        pi.waitSemaphoreCount = 1;
        pi.pWaitSemaphores = &g.sem_ren[idx];
        pi.swapchainCount = 1;
        pi.pSwapchains = &g.swapchain;
        pi.pImageIndices = &idx;
        VkResult pr = vkQueuePresentKHR(g.queue, &pi);
        if (pr != VK_SUCCESS && pr != VK_SUBOPTIMAL_KHR) {
            LOGE("present failed %d at frame %d", (int)pr, frame);
            g.clean_exit = false;
            finish_and_exit(2);
        }
        g.frames_submitted++;
        if(g.capture_initialized && g.capture_sink.selected(frame)){
            VkResult capture_wait=vkWaitForFences(g.dev,1,&g.fence[f],VK_TRUE,CAPTURE_SUBMISSION_WAIT_NS);
            if(capture_wait!=VK_SUCCESS){
                g.clean_exit=false;
                g.capture_sink.fail(capture_wait==VK_TIMEOUT?"submission_fence_timeout":"submission_fence_error");
                LOGE("capture submission wait failed %d",int(capture_wait));
                finish_and_exit(2);
            }
            write_capture_frame(frame);
        }
        f = (f + 1) % State::FLIGHTS;
    }
    g.clean_exit = true;
}

// ── app 生命周期 ──────────────────────────────────────────────────────

static void on_app_cmd(android_app* app, int32_t cmd) {
    switch (cmd) {
        case APP_CMD_INIT_WINDOW:
            if (app->window) g.window_ready = true;
            break;
        case APP_CMD_TERM_WINDOW:
            g.window_lost = true;
            break;
        default:
            break;
    }
}

void android_main(android_app* app) {
    pthread_setname_np(pthread_self(), RENDER_THREAD_NAME);
    g.app = app;
    app->onAppCmd = on_app_cmd;
    ANativeActivity_setWindowFlags(app->activity,
                                   AWINDOW_FLAG_KEEP_SCREEN_ON | AWINDOW_FLAG_FULLSCREEN, 0);

    read_cfg_from_intent(app);

    // 等窗口就绪 (阻塞泵)
    while (!g.window_ready) {
        int events;
        android_poll_source* src;
        if (ALooper_pollOnce(-1, nullptr, &events, (void**)&src) >= 0) {
            if (src) src->process(app, src);
            if (app->destroyRequested) {
                LOGE("destroyed before window ready");
                finish_and_exit(2);
            }
        }
    }

    init_vulkan();
    init_capture();
    write_started_marker();
    LOGI("REFBENCH_START scene=%s frames=%d", g.cfg.scene.c_str(), g.cfg.frames);
    render_all_frames();
    finish_and_exit(0);
}
