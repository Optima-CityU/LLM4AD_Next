// framedump.cpp — 在 vkQueuePresentKHR 处把每一张送显图无损读回、落盘 (插帧/超分的数据采集)。
//
// 为什么在 present 处读: 插帧要的是「游戏真正渲染出来的每一帧」, 带 UI、带游戏自己的放大与后处理,
// 和屏幕上看到的一致, 且一帧不多一帧不少。present 是唯一同时满足「逐帧、无损、知道帧序号与时刻」的位置。
// screenrecord 是有损编码且按合成器节奏取样 (会重复/丢帧); SurfaceFlinger 截图一次一张, 跟不上 60 帧。
//
// 做法 (每个被抓的 present):
//   1. 在游戏 present 用的同一条队列上提交一个我们的命令缓冲: 等游戏给 present 的那些信号量,
//      把送显图 PRESENT_SRC -> TRANSFER_SRC, 整张拷进主机可见缓冲 (紧密排布), 再转回 PRESENT_SRC,
//      然后发一个我们自己的信号量 (按 swapchain 图序号一张图一个)。
//   2. 把 present 的等待信号量换成我们的那一个再转发。所以画面内容与送显时机都不变,
//      只是 present 多等一个很短的拷贝 (2141x969 RGBA8 约 8.3 MB, GPU 拷贝亚毫秒级, 推断)。
//   3. 写盘线程等这个槽的 fence, 把缓冲原样写成一个文件。槽位用完 (写盘跟不上) 就丢这一帧,
//      在索引里记 gap —— 训练只用帧号连续的三元组, 丢帧不会产生「假连续」。
//
// 落盘 (每一段一个新目录, 已存在就加 _2/_3, 文件一律 O_EXCL 新建, 不覆盖任何东西):
//   <base>/<标签>/session.json   开始时: 宽高、格式、preTransform、计划帧数、像素字节序说明
//   <base>/<标签>/f<帧号10位>.raw 原始像素, 无文件头, 行紧密排布 (w*bpp 字节一行)
//   <base>/<标签>/index.csv       每帧一行: seq,frame,t_ns,dt_ns,gap_before,file,write_us
//   <base>/<标签>/summary.json    结束时: 抓到/丢掉/跳过的帧数 —— 采集脚本以它出现为「这段完成」
//   <base>/<标签>/refused.json    磁盘空间不够等原因拒抓时写这个 (此时不会有 summary.json)
// <base> = debug.knobs.framedump_dir, 缺省 /data/data/<包名>/files/i3dump (应用自己的内部目录,
// 游戏进程一定写得进, 直接是 f2fs, 比 /sdcard 的 FUSE 快)。
//
// 属性 (全部 debug.knobs.framedump*):
//   framedump=1      总开关, CreateInstance 时读。0/缺省时本文件里的一切都不走, 也不挂额外钩子
//   framedump_go     场景标签, 运行中改一次开一段; 只取 [A-Za-z0-9_-]
//   framedump_n      每段 present 数, 缺省 600 (60 帧下 10 秒), 上限 36000
//   framedump_slots  读回槽数, 缺省 8, 上限 32 (每槽一帧大小的主机可见显存)
//   framedump_dir    覆盖 <base>
//   framedump_reserve_mb  抓之前要求剩余空间 >= 本段预计大小 + 这么多, 缺省 4096
#include "framedump.h"

#include <android/log.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/system_properties.h>
#include <time.h>
#include <unistd.h>

#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <thread>
#include <vector>

#define FD_LOG(...) __android_log_print(ANDROID_LOG_INFO, "refknobs", "framedump: " __VA_ARGS__)

namespace {

inline void* fkey(void* h) { return *reinterpret_cast<void**>(h); }

uint64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

std::string prop(const char* name) {
    char v[PROP_VALUE_MAX] = {0};
    if (__system_property_get(name, v) <= 0) return "";
    return v;
}
long prop_long(const char* name, long dflt, long lo, long hi) {
    std::string s = prop(name);
    if (s.empty()) return dflt;
    long v = strtol(s.c_str(), nullptr, 10);
    return v < lo ? lo : (v > hi ? hi : v);
}

std::string self_pkg() {
    char cmd[256] = {0};
    FILE* f = fopen("/proc/self/cmdline", "r");
    if (f) { size_t n = fread(cmd, 1, sizeof cmd - 1, f); cmd[n < sizeof cmd ? n : sizeof cmd - 1] = 0; fclose(f); }
    std::string pkg = cmd[0] ? std::string(cmd) : std::string("unknown");
    size_t c = pkg.find(':');
    if (c != std::string::npos) pkg = pkg.substr(0, c);
    return pkg;
}

// 每像素字节数; 只认常见的送显格式, 其余返回 0 (不抓, 如实记原因)
uint32_t bpp_of(uint32_t fmt) {
    switch (fmt) {
        case VK_FORMAT_R8G8B8A8_UNORM: case VK_FORMAT_R8G8B8A8_SRGB:
        case VK_FORMAT_B8G8R8A8_UNORM: case VK_FORMAT_B8G8R8A8_SRGB:
        case VK_FORMAT_A2B10G10R10_UNORM_PACK32: case VK_FORMAT_A2R10G10B10_UNORM_PACK32:
            return 4;
        case VK_FORMAT_R5G6B5_UNORM_PACK16: return 2;
        case VK_FORMAT_R16G16B16A16_SFLOAT: return 8;
        default: return 0;
    }
}

bool mkdir_p(const std::string& p) {
    std::string cur;
    for (size_t i = 0; i < p.size(); i++) {
        cur += p[i];
        if ((p[i] == '/' && i > 0) || i + 1 == p.size()) {
            if (mkdir(cur.c_str(), 0771) != 0 && errno != EEXIST) return false;
        }
    }
    return true;
}

// O_EXCL 新建并写完整个文本; 已存在就失败, 绝不覆盖
bool write_new(const std::string& path, const std::string& text) {
    int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (fd < 0) return false;
    size_t off = 0;
    while (off < text.size()) {
        ssize_t w = write(fd, text.data() + off, text.size() - off);
        if (w <= 0) { close(fd); return false; }
        off += (size_t)w;
    }
    fsync(fd);
    close(fd);
    return true;
}

struct Swap {
    uint32_t w = 0, h = 0, fmt = 0, pretransform = 0, present_mode = 0;
    bool usage_ok = false;
    std::vector<VkImage> imgs;
    std::vector<VkSemaphore> sems;   // 一张图一个: 再次 acquire 到这张图时, 上次 present 对它的等待已经结束
};

struct Slot {
    VkBuffer buf = VK_NULL_HANDLE;
    VkDeviceMemory mem = VK_NULL_HANDLE;
    void* map = nullptr;
    VkFence fence = VK_NULL_HANDLE;
    VkCommandBuffer cb = VK_NULL_HANDLE;
    bool busy = false;
    // 这一帧的元数据
    uint64_t frame = 0, t_ns = 0, dt_ns = 0, gap = 0;
    uint32_t seq = 0, w = 0, h = 0, bpp = 0;
};

struct Dev {
    VkDevice dev = VK_NULL_HANDLE;
    VkInstance inst = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    PFN_rkSetDeviceLoaderData sdld = nullptr;
    bool coherent = true;
#define FD_FN(n) PFN_vk##n n = nullptr;
    FD_FN(GetDeviceQueue) FD_FN(GetDeviceQueue2) FD_FN(DestroySwapchainKHR) FD_FN(GetSwapchainImagesKHR)
    FD_FN(CreateCommandPool) FD_FN(DestroyCommandPool) FD_FN(AllocateCommandBuffers) FD_FN(ResetCommandBuffer)
    FD_FN(BeginCommandBuffer) FD_FN(EndCommandBuffer) FD_FN(CmdPipelineBarrier) FD_FN(CmdCopyImageToBuffer)
    FD_FN(QueueSubmit) FD_FN(CreateFence) FD_FN(DestroyFence) FD_FN(ResetFences) FD_FN(WaitForFences)
    FD_FN(CreateSemaphore) FD_FN(DestroySemaphore) FD_FN(CreateBuffer) FD_FN(DestroyBuffer)
    FD_FN(GetBufferMemoryRequirements) FD_FN(AllocateMemory) FD_FN(FreeMemory) FD_FN(BindBufferMemory)
    FD_FN(MapMemory) FD_FN(UnmapMemory) FD_FN(InvalidateMappedMemoryRanges)
    FD_FN(GetPhysicalDeviceMemoryProperties) FD_FN(GetPhysicalDeviceSurfaceCapabilitiesKHR)
#undef FD_FN
    std::map<VkSwapchainKHR, Swap> swaps;
    std::vector<VkSemaphore> retired_sems;   // swapchain 销毁后它的信号量可能仍被 present 等着, 留到 device 销毁再收
    std::map<VkQueue, uint32_t> queue_family;
    VkCommandPool pool = VK_NULL_HANDLE;
    uint32_t pool_family = UINT32_MAX;
    uint32_t sole_family = UINT32_MAX;
    std::vector<Slot> slots;
    VkDeviceSize slot_bytes = 0;
};

// ── 全局状态 (g_m 保护, 写盘线程与 present 线程共用) ──
std::mutex g_m;
std::condition_variable g_cv;
bool g_on = false;
std::map<void*, Dev*> g_devs;
std::deque<std::pair<Dev*, int>> g_jobs;   // 待写盘的槽
std::thread g_writer;
bool g_writer_stop = false;
uint32_t g_inflight = 0;                   // 已提交未写完的槽数

// 段状态
std::string g_base, g_last_go, g_dir, g_label, g_err;
bool g_capturing = false, g_finalizing = false;
uint32_t g_plan = 600, g_left = 0, g_seq = 0;
uint64_t g_cap = 0, g_drop = 0, g_skip = 0, g_prev_cap_frame = 0, g_first_frame = 0, g_last_frame = 0;
uint64_t g_t0 = 0, g_t_last = 0, g_prev_present_t = 0, g_bytes = 0, g_write_us_max = 0;
uint64_t g_tot_cap = 0, g_tot_drop = 0, g_sessions = 0;
FILE* g_index = nullptr;

Dev* dev_of_key(void* k) {
    auto it = g_devs.find(k);
    if (it == g_devs.end() && g_devs.size() == 1) it = g_devs.begin();
    return it == g_devs.end() ? nullptr : it->second;
}

std::string sanitize(const std::string& s) {
    std::string o;
    for (char c : s)
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') o += c;
    return o.substr(0, 64);
}

// 段结束: 队列清空、没有在途槽之后写 summary.json。调用方持 g_m。
void maybe_finalize_locked() {
    if (!g_finalizing || !g_jobs.empty() || g_inflight) return;
    char b[768];
    double secs = g_t_last && g_t0 ? (double)(g_t_last - g_t0) / 1e9 : 0;
    snprintf(b, sizeof b,
             "{\"label\":\"%s\",\"planned_presents\":%u,\"captured\":%llu,\"dropped_slot_busy\":%llu,"
             "\"skipped_other\":%llu,\"first_frame\":%llu,\"last_frame\":%llu,\"span_s\":%.3f,"
             "\"bytes\":%llu,\"write_us_max\":%llu,\"err\":\"%s\"}\n",
             g_label.c_str(), g_plan, (unsigned long long)g_cap, (unsigned long long)g_drop,
             (unsigned long long)g_skip, (unsigned long long)g_first_frame, (unsigned long long)g_last_frame, secs,
             (unsigned long long)g_bytes, (unsigned long long)g_write_us_max, g_err.c_str());
    if (g_index) { fflush(g_index); fsync(fileno(g_index)); fclose(g_index); g_index = nullptr; }
    if (!write_new(g_dir + "/summary.json", b)) FD_LOG("summary.json 写不进 %s", g_dir.c_str());
    FD_LOG("段结束 %s", b);
    g_finalizing = false;
}

void writer_main() {
    std::unique_lock<std::mutex> lk(g_m);
    for (;;) {
        g_cv.wait(lk, [] { return g_writer_stop || !g_jobs.empty(); });
        if (g_jobs.empty() && g_writer_stop) return;
        auto job = g_jobs.front();
        g_jobs.pop_front();
        Dev* D = job.first;
        Slot& s = D->slots[job.second];
        std::string dir = g_dir;
        lk.unlock();

        VkResult fr;
        int tries = 0;
        do { fr = D->WaitForFences(D->dev, 1, &s.fence, VK_TRUE, 1000000000ull); } while (fr == VK_TIMEOUT && ++tries < 10);
        uint64_t t_w0 = now_ns();
        size_t bytes = (size_t)s.w * s.h * s.bpp;
        char name[32];
        snprintf(name, sizeof name, "f%010llu.raw", (unsigned long long)s.frame);
        bool ok = false;
        if (fr == VK_SUCCESS) {
            if (!D->coherent && D->InvalidateMappedMemoryRanges) {
                VkMappedMemoryRange r{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, nullptr, s.mem, 0, VK_WHOLE_SIZE};
                D->InvalidateMappedMemoryRanges(D->dev, 1, &r);
            }
            int fd = open((dir + "/" + name).c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
            if (fd >= 0) {
                size_t off = 0;
                const char* p = (const char*)s.map;
                while (off < bytes) {
                    ssize_t w = write(fd, p + off, bytes - off);
                    if (w <= 0) break;
                    off += (size_t)w;
                }
                ok = off == bytes;
                close(fd);
            }
        }
        uint64_t wus = (now_ns() - t_w0) / 1000;

        lk.lock();
        if (ok) {
            g_cap++; g_tot_cap++; g_bytes += bytes;
            if (wus > g_write_us_max) g_write_us_max = wus;
            if (g_index)
                fprintf(g_index, "%u,%llu,%llu,%llu,%llu,%s,%llu\n", s.seq, (unsigned long long)s.frame,
                        (unsigned long long)s.t_ns, (unsigned long long)s.dt_ns, (unsigned long long)s.gap, name,
                        (unsigned long long)wus);
        } else {
            g_drop++; g_tot_drop++;
            if (g_err.empty()) g_err = fr != VK_SUCCESS ? "fence_wait_failed" : "write_failed";
            if (g_index) fprintf(g_index, "%u,%llu,%llu,%llu,%llu,WRITE_FAILED,%llu\n", s.seq,
                                 (unsigned long long)s.frame, (unsigned long long)s.t_ns,
                                 (unsigned long long)s.dt_ns, (unsigned long long)s.gap, (unsigned long long)wus);
        }
        s.busy = false;
        g_inflight--;
        maybe_finalize_locked();
    }
}

uint32_t pick_mem(Dev* D, uint32_t bits, bool* coherent) {
    VkPhysicalDeviceMemoryProperties mp{};
    D->GetPhysicalDeviceMemoryProperties(D->phys, &mp);
    int best = -1, best_score = -1;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++) {
        if (!(bits & (1u << i))) continue;
        VkMemoryPropertyFlags f = mp.memoryTypes[i].propertyFlags;
        if (!(f & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)) continue;
        int score = 1 + ((f & VK_MEMORY_PROPERTY_HOST_CACHED_BIT) ? 4 : 0) + ((f & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) ? 2 : 0);
        if (score > best_score) { best = (int)i; best_score = score; }
    }
    if (best >= 0) *coherent = (mp.memoryTypes[best].propertyFlags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) != 0;
    return best < 0 ? UINT32_MAX : (uint32_t)best;
}

// 准备好 >= need 字节的槽与本队列族的命令池。调用方持 g_m, 且无在途槽。
bool ensure_slots(Dev* D, uint32_t family, VkDeviceSize need, uint32_t nslots) {
    if (D->pool != VK_NULL_HANDLE && D->pool_family == family && D->slot_bytes >= need && D->slots.size() == nslots)
        return true;
    if (g_inflight) return false;   // 有槽在写, 下一帧再说
    // 收掉旧的 (只是我们自己建的 GPU 对象)
    for (auto& s : D->slots) {
        if (s.map) D->UnmapMemory(D->dev, s.mem);
        if (s.buf) D->DestroyBuffer(D->dev, s.buf, nullptr);
        if (s.mem) D->FreeMemory(D->dev, s.mem, nullptr);
        if (s.fence) D->DestroyFence(D->dev, s.fence, nullptr);
    }
    D->slots.clear();
    if (D->pool) { D->DestroyCommandPool(D->dev, D->pool, nullptr); D->pool = VK_NULL_HANDLE; }

    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, nullptr,
                                VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, family};
    if (D->CreateCommandPool(D->dev, &pci, nullptr, &D->pool) != VK_SUCCESS) { g_err = "create_pool"; return false; }
    D->pool_family = family;
    D->slots.resize(nslots);
    for (auto& s : D->slots) {
        VkBufferCreateInfo bci{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
        bci.size = need; bci.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT; bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        if (D->CreateBuffer(D->dev, &bci, nullptr, &s.buf) != VK_SUCCESS) { g_err = "create_buffer"; return false; }
        VkMemoryRequirements mr{};
        D->GetBufferMemoryRequirements(D->dev, s.buf, &mr);
        bool coh = true;
        uint32_t mt = pick_mem(D, mr.memoryTypeBits, &coh);
        if (mt == UINT32_MAX) { g_err = "no_host_visible_mem"; return false; }
        D->coherent = coh;
        VkMemoryAllocateInfo mai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, mt};
        if (D->AllocateMemory(D->dev, &mai, nullptr, &s.mem) != VK_SUCCESS) { g_err = "alloc_mem"; return false; }
        D->BindBufferMemory(D->dev, s.buf, s.mem, 0);
        if (D->MapMemory(D->dev, s.mem, 0, VK_WHOLE_SIZE, 0, &s.map) != VK_SUCCESS) { g_err = "map_mem"; return false; }
        VkFenceCreateInfo fci{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        D->CreateFence(D->dev, &fci, nullptr, &s.fence);
        VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, nullptr, D->pool,
                                        VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
        if (D->AllocateCommandBuffers(D->dev, &cai, &s.cb) != VK_SUCCESS) { g_err = "alloc_cb"; return false; }
        // 层自己分配的可分发对象要由加载器补上分发表, 否则下层驱动/后续层查表会崩
        if (D->sdld) D->sdld(D->dev, s.cb);
        else *reinterpret_cast<void**>(s.cb) = *reinterpret_cast<void**>(D->dev);
    }
    D->slot_bytes = need;
    FD_LOG("槽就绪: %u 个 x %llu 字节, family=%u coherent=%d", nslots, (unsigned long long)need, family, (int)D->coherent);
    return true;
}

// 开一段。调用方持 g_m。
void start_session(const std::string& label, Dev* D, const Swap& S) {
    g_label = label; g_err.clear();
    g_plan = (uint32_t)prop_long("debug.knobs.framedump_n", 600, 1, 36000);
    uint64_t frame_bytes = (uint64_t)S.w * S.h * bpp_of(S.fmt);
    std::string dir = g_base + "/" + label;
    for (int k = 2; access(dir.c_str(), F_OK) == 0 && k < 1000; k++) dir = g_base + "/" + label + "_" + std::to_string(k);
    if (!mkdir_p(dir)) { FD_LOG("建目录失败 %s errno=%d", dir.c_str(), errno); return; }
    g_dir = dir;
    // 空间检查: 不够就拒抓, 不去动任何已有文件
    struct statvfs sv{};
    uint64_t avail = statvfs(dir.c_str(), &sv) == 0 ? (uint64_t)sv.f_bavail * sv.f_frsize : 0;
    uint64_t need = frame_bytes * g_plan + (uint64_t)prop_long("debug.knobs.framedump_reserve_mb", 4096, 0, 1 << 20) * 1048576ull;
    if (!frame_bytes || avail < need) {
        char b[256];
        snprintf(b, sizeof b, "{\"label\":\"%s\",\"reason\":\"%s\",\"avail_bytes\":%llu,\"need_bytes\":%llu}\n",
                 label.c_str(), frame_bytes ? "disk_space" : "unsupported_format", (unsigned long long)avail,
                 (unsigned long long)need);
        write_new(dir + "/refused.json", b);
        FD_LOG("拒抓 %s", b);
        return;
    }
    char b[1024];
    snprintf(b, sizeof b,
             "{\"label\":\"%s\",\"pkg\":\"%s\",\"w\":%u,\"h\":%u,\"vk_format\":%u,\"bytes_per_pixel\":%u,"
             "\"row_bytes\":%u,\"pre_transform\":%u,\"present_mode\":%u,\"swap_images\":%zu,\"planned_presents\":%u,"
             "\"slots\":%zu,\"clock\":\"CLOCK_MONOTONIC ns at vkQueuePresentKHR entry\","
             "\"layout\":\"raw, no header, top row first, tightly packed; vk_format 37=R8G8B8A8_UNORM 44=B8G8R8A8_UNORM 64=A2B10G10R10\","
             "\"avail_bytes_at_start\":%llu}\n",
             label.c_str(), self_pkg().c_str(), S.w, S.h, S.fmt, bpp_of(S.fmt), S.w * bpp_of(S.fmt), S.pretransform,
             S.present_mode, S.imgs.size(), g_plan, D->slots.size(), (unsigned long long)avail);
    if (!write_new(dir + "/session.json", b)) { FD_LOG("session.json 写不进 %s", dir.c_str()); return; }
    std::string idx = dir + "/index.csv";
    int fd = open(idx.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    g_index = fd >= 0 ? fdopen(fd, "w") : nullptr;
    if (g_index) fprintf(g_index, "seq,frame,t_ns,dt_ns,gap_before,file,write_us\n");
    g_capturing = true; g_left = g_plan; g_seq = 0;
    g_cap = g_drop = g_skip = g_prev_cap_frame = g_first_frame = g_last_frame = g_bytes = g_write_us_max = 0;
    g_t0 = g_t_last = 0;
    g_sessions++;
    FD_LOG("开段 %s -> %s, %u 帧, 每帧 %llu 字节, 剩余空间 %llu", label.c_str(), dir.c_str(), g_plan,
           (unsigned long long)frame_bytes, (unsigned long long)avail);
}

void end_session_locked() {
    g_capturing = false;
    g_finalizing = true;
    maybe_finalize_locked();
}

// ── 本模块自己的钩子 ──
VKAPI_ATTR void VKAPI_CALL fd_GetDeviceQueue(VkDevice dev, uint32_t fam, uint32_t idx, VkQueue* q) {
    Dev* D;
    { std::lock_guard<std::mutex> lk(g_m); D = dev_of_key(fkey(dev)); }
    if (!D || !D->GetDeviceQueue) return;
    D->GetDeviceQueue(dev, fam, idx, q);
    if (q && *q) { std::lock_guard<std::mutex> lk(g_m); D->queue_family[*q] = fam; }
}
VKAPI_ATTR void VKAPI_CALL fd_GetDeviceQueue2(VkDevice dev, const VkDeviceQueueInfo2* qi, VkQueue* q) {
    Dev* D;
    { std::lock_guard<std::mutex> lk(g_m); D = dev_of_key(fkey(dev)); }
    if (!D || !D->GetDeviceQueue2) return;
    D->GetDeviceQueue2(dev, qi, q);
    if (qi && q && *q) { std::lock_guard<std::mutex> lk(g_m); D->queue_family[*q] = qi->queueFamilyIndex; }
}
VKAPI_ATTR void VKAPI_CALL fd_DestroySwapchainKHR(VkDevice dev, VkSwapchainKHR sc, const VkAllocationCallbacks* a) {
    Dev* D;
    {
        std::lock_guard<std::mutex> lk(g_m);
        D = dev_of_key(fkey(dev));
        if (D) {
            auto it = D->swaps.find(sc);
            if (it != D->swaps.end()) {
                for (auto s : it->second.sems) D->retired_sems.push_back(s);
                D->swaps.erase(it);
            }
        }
    }
    if (D && D->DestroySwapchainKHR) D->DestroySwapchainKHR(dev, sc, a);
}

}  // namespace

bool fd_init_from_props() {
    std::string v = prop("debug.knobs.framedump");
    g_on = !v.empty() && (v[0] == '1' || v[0] == 't' || v[0] == 'y');
    if (!g_on) return false;
    g_base = prop("debug.knobs.framedump_dir");
    if (g_base.empty()) g_base = "/data/data/" + self_pkg() + "/files/i3dump";
    // 启动时的 go 值不算触发: 只有运行中「改成新值」才开段, 免得上一轮留下的属性一启动就开抓
    g_last_go = prop("debug.knobs.framedump_go");
    FD_LOG("开启, base=%s, 当前 go=[%s] (不触发)", g_base.c_str(), g_last_go.c_str());
    return true;
}

bool fd_on() { return g_on; }

void fd_on_device(VkInstance inst, VkPhysicalDevice phys, VkDevice dev, PFN_vkGetInstanceProcAddr gipa,
                  PFN_vkGetDeviceProcAddr gdpa, PFN_rkSetDeviceLoaderData sdld, uint32_t sole_family) {
    if (!g_on) return;
    Dev* D = new Dev();
    D->dev = dev; D->inst = inst; D->phys = phys; D->sdld = sdld; D->sole_family = sole_family;
#define FD_GD(n) D->n = (PFN_vk##n)gdpa(dev, "vk" #n);
    FD_GD(GetDeviceQueue) FD_GD(GetDeviceQueue2) FD_GD(DestroySwapchainKHR) FD_GD(GetSwapchainImagesKHR)
    FD_GD(CreateCommandPool) FD_GD(DestroyCommandPool) FD_GD(AllocateCommandBuffers) FD_GD(ResetCommandBuffer)
    FD_GD(BeginCommandBuffer) FD_GD(EndCommandBuffer) FD_GD(CmdPipelineBarrier) FD_GD(CmdCopyImageToBuffer)
    FD_GD(QueueSubmit) FD_GD(CreateFence) FD_GD(DestroyFence) FD_GD(ResetFences) FD_GD(WaitForFences)
    FD_GD(CreateSemaphore) FD_GD(DestroySemaphore) FD_GD(CreateBuffer) FD_GD(DestroyBuffer)
    FD_GD(GetBufferMemoryRequirements) FD_GD(AllocateMemory) FD_GD(FreeMemory) FD_GD(BindBufferMemory)
    FD_GD(MapMemory) FD_GD(UnmapMemory) FD_GD(InvalidateMappedMemoryRanges)
#undef FD_GD
    if (inst && gipa) {
        D->GetPhysicalDeviceMemoryProperties =
            (PFN_vkGetPhysicalDeviceMemoryProperties)gipa(inst, "vkGetPhysicalDeviceMemoryProperties");
        D->GetPhysicalDeviceSurfaceCapabilitiesKHR =
            (PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR)gipa(inst, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR");
    }
    bool complete = D->CreateCommandPool && D->AllocateCommandBuffers && D->BeginCommandBuffer && D->EndCommandBuffer &&
                    D->CmdPipelineBarrier && D->CmdCopyImageToBuffer && D->QueueSubmit && D->CreateFence &&
                    D->WaitForFences && D->ResetFences && D->CreateSemaphore && D->CreateBuffer && D->AllocateMemory &&
                    D->MapMemory && D->GetSwapchainImagesKHR && D->GetPhysicalDeviceMemoryProperties &&
                    D->ResetCommandBuffer;
    std::lock_guard<std::mutex> lk(g_m);
    if (!complete) { g_err = "missing_device_functions"; FD_LOG("下层函数不全, 不抓"); }
    g_devs[fkey(dev)] = D;
    if (!g_writer.joinable()) { g_writer_stop = false; g_writer = std::thread(writer_main); }
    FD_LOG("device 已登记, loader_data_callback=%d", sdld ? 1 : 0);
}

void fd_on_destroy_device(VkDevice dev) {
    if (!g_on) return;
    Dev* D;
    {
        std::lock_guard<std::mutex> lk(g_m);
        D = dev_of_key(fkey(dev));
        if (!D) return;
        if (g_capturing) { g_err = g_err.empty() ? "device_destroyed" : g_err; end_session_locked(); }
        g_writer_stop = true;
    }
    g_cv.notify_all();
    if (g_writer.joinable()) g_writer.join();   // 写盘线程会把队列里剩下的写完再退
    std::lock_guard<std::mutex> lk(g_m);
    maybe_finalize_locked();
    for (auto& s : D->slots) {
        if (s.fence && D->WaitForFences) D->WaitForFences(D->dev, 1, &s.fence, VK_TRUE, 1000000000ull);
        if (s.map) D->UnmapMemory(D->dev, s.mem);
        if (s.buf) D->DestroyBuffer(D->dev, s.buf, nullptr);
        if (s.mem) D->FreeMemory(D->dev, s.mem, nullptr);
        if (s.fence) D->DestroyFence(D->dev, s.fence, nullptr);
    }
    if (D->pool) D->DestroyCommandPool(D->dev, D->pool, nullptr);
    for (auto& kv : D->swaps) for (auto s : kv.second.sems) D->retired_sems.push_back(s);
    for (auto s : D->retired_sems) if (s && D->DestroySemaphore) D->DestroySemaphore(D->dev, s, nullptr);
    g_devs.erase(fkey(dev));
    delete D;
    g_writer_stop = false;
}

bool fd_patch_swapchain_ci(VkDevice dev, VkSwapchainCreateInfoKHR* ci) {
    if (!g_on || !ci) return false;
    Dev* D;
    { std::lock_guard<std::mutex> lk(g_m); D = dev_of_key(fkey(dev)); }
    if (!D) return false;
    if (ci->imageUsage & VK_IMAGE_USAGE_TRANSFER_SRC_BIT) return true;
    if (D->GetPhysicalDeviceSurfaceCapabilitiesKHR) {
        VkSurfaceCapabilitiesKHR caps{};
        if (D->GetPhysicalDeviceSurfaceCapabilitiesKHR(D->phys, ci->surface, &caps) == VK_SUCCESS &&
            !(caps.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_SRC_BIT)) {
            FD_LOG("surface 不支持 TRANSFER_SRC (supportedUsageFlags=0x%x), 不抓", caps.supportedUsageFlags);
            return false;
        }
    }
    ci->imageUsage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    return true;
}

void fd_on_swapchain(VkDevice dev, VkSwapchainKHR sc, const VkSwapchainCreateInfoKHR* ci, bool usage_ok) {
    if (!g_on || !ci) return;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D || !D->GetSwapchainImagesKHR) return;
    Swap S;
    S.w = ci->imageExtent.width; S.h = ci->imageExtent.height; S.fmt = (uint32_t)ci->imageFormat;
    S.pretransform = (uint32_t)ci->preTransform; S.present_mode = (uint32_t)ci->presentMode; S.usage_ok = usage_ok;
    uint32_t n = 0;
    D->GetSwapchainImagesKHR(dev, sc, &n, nullptr);
    S.imgs.resize(n);
    if (n) D->GetSwapchainImagesKHR(dev, sc, &n, S.imgs.data());
    S.sems.assign(n, VK_NULL_HANDLE);
    for (uint32_t i = 0; i < n; i++) {
        VkSemaphoreCreateInfo sci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        if (D->CreateSemaphore(dev, &sci, nullptr, &S.sems[i]) != VK_SUCCESS) { S.usage_ok = false; break; }
    }
    FD_LOG("swapchain %ux%u fmt=%u preTransform=%u presentMode=%u images=%u usage_ok=%d", S.w, S.h, S.fmt,
           S.pretransform, S.present_mode, n, (int)S.usage_ok);
    D->swaps[sc] = std::move(S);
}

const VkPresentInfoKHR* fd_on_present(VkQueue q, const VkPresentInfoKHR* pi, VkPresentInfoKHR* scratch,
                                      uint64_t frame) {
    if (!g_on || !pi) return pi;
    uint64_t t = now_ns();
    std::lock_guard<std::mutex> lk(g_m);
    uint64_t dt = g_prev_present_t ? t - g_prev_present_t : 0;
    g_prev_present_t = t;
    Dev* D = dev_of_key(fkey(q));
    if (!D || pi->swapchainCount == 0 || !pi->pSwapchains || !pi->pImageIndices) return pi;
    auto si = D->swaps.find(pi->pSwapchains[0]);
    if (si == D->swaps.end()) return pi;
    Swap& S = si->second;

    if (!g_capturing && !g_finalizing && frame % 10 == 0) {
        std::string go = prop("debug.knobs.framedump_go");
        if (go != g_last_go) {
            g_last_go = go;
            std::string label = sanitize(go);
            if (!label.empty() && label != "0") {
                if (!S.usage_ok) FD_LOG("swapchain 没有 TRANSFER_SRC, 不能抓 (%s)", label.c_str());
                else start_session(label, D, S);
            }
        }
    }
    if (!g_capturing) return pi;
    if (g_left == 0) { end_session_locked(); return pi; }
    g_left--;
    if (!g_t0) g_t0 = t;
    g_last_frame = frame;
    g_t_last = t;
    if (!g_first_frame) g_first_frame = frame;

    auto skip = [&](const char* why) {
        g_skip++;
        if (g_index) fprintf(g_index, "%u,%llu,%llu,%llu,0,SKIP_%s,0\n", g_seq, (unsigned long long)frame,
                             (unsigned long long)t, (unsigned long long)dt, why);
        g_seq++;
        if (g_left == 0) end_session_locked();
        return pi;
    };
    if (pi->swapchainCount != 1) return skip("multi_swapchain");
    uint32_t idx = pi->pImageIndices[0];
    if (!S.usage_ok || idx >= S.imgs.size() || !S.sems[idx]) return skip("swapchain");
    uint32_t bpp = bpp_of(S.fmt);
    if (!bpp) return skip("format");
    uint32_t family = D->sole_family;
    auto qf = D->queue_family.find(q);
    if (qf != D->queue_family.end()) family = qf->second;
    if (family == UINT32_MAX) return skip("queue_family_unknown");
    uint32_t nslots = (uint32_t)prop_long("debug.knobs.framedump_slots", 8, 2, 32);
    if (!ensure_slots(D, family, (VkDeviceSize)S.w * S.h * bpp, D->slots.empty() ? nslots : (uint32_t)D->slots.size()))
        return skip("slots");
    int slot = -1;
    for (size_t k = 0; k < D->slots.size(); k++) {
        size_t i = (g_seq + k) % D->slots.size();
        if (!D->slots[i].busy) { slot = (int)i; break; }
    }
    if (slot < 0) {   // 写盘跟不上: 丢这一帧, 帧号断开, 训练侧自然不会把它两边当成连续
        g_drop++; g_tot_drop++;
        if (g_index) fprintf(g_index, "%u,%llu,%llu,%llu,0,DROP_SLOT_BUSY,0\n", g_seq, (unsigned long long)frame,
                             (unsigned long long)t, (unsigned long long)dt);
        g_seq++;
        if (g_left == 0) end_session_locked();
        return pi;
    }
    Slot& s = D->slots[slot];
    VkImage img = S.imgs[idx];

    D->ResetCommandBuffer(s.cb, 0);
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, nullptr,
                                VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, nullptr};
    D->BeginCommandBuffer(s.cb, &bi);
    VkImageMemoryBarrier ib{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    ib.srcAccessMask = 0;   // 游戏的写入已由它给 present 的信号量 (我们在 TRANSFER 阶段等) 覆盖
    ib.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    ib.oldLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;   // 不是 UNDEFINED: 要保留内容
    ib.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    ib.srcQueueFamilyIndex = ib.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    ib.image = img;
    ib.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    D->CmdPipelineBarrier(s.cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0,
                          nullptr, 1, &ib);
    VkBufferImageCopy rg{};
    rg.bufferOffset = 0; rg.bufferRowLength = 0; rg.bufferImageHeight = 0;   // 紧密排布
    rg.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    rg.imageExtent = {S.w, S.h, 1};
    D->CmdCopyImageToBuffer(s.cb, img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, s.buf, 1, &rg);
    VkImageMemoryBarrier ib2 = ib;
    ib2.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT; ib2.dstAccessMask = 0;
    ib2.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL; ib2.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
    VkBufferMemoryBarrier bb{VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER};
    bb.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT; bb.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    bb.srcQueueFamilyIndex = bb.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    bb.buffer = s.buf; bb.offset = 0; bb.size = VK_WHOLE_SIZE;
    D->CmdPipelineBarrier(s.cb, VK_PIPELINE_STAGE_TRANSFER_BIT,
                          VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT | VK_PIPELINE_STAGE_HOST_BIT, 0, 0, nullptr, 1, &bb, 1,
                          &ib2);
    D->EndCommandBuffer(s.cb);

    std::vector<VkPipelineStageFlags> stages(pi->waitSemaphoreCount, VK_PIPELINE_STAGE_TRANSFER_BIT);
    VkSubmitInfo sub{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    sub.waitSemaphoreCount = pi->waitSemaphoreCount;
    sub.pWaitSemaphores = pi->pWaitSemaphores;
    sub.pWaitDstStageMask = stages.empty() ? nullptr : stages.data();
    sub.commandBufferCount = 1; sub.pCommandBuffers = &s.cb;
    sub.signalSemaphoreCount = 1; sub.pSignalSemaphores = &S.sems[idx];
    D->ResetFences(D->dev, 1, &s.fence);
    VkResult r = D->QueueSubmit(q, 1, &sub, s.fence);
    if (r != VK_SUCCESS) {
        // 提交失败时游戏的等待信号量没被消费, 原样 present 即可
        if (g_err.empty()) g_err = "queue_submit_" + std::to_string((int)r);
        return skip("submit");
    }
    s.busy = true;
    s.frame = frame; s.t_ns = t; s.dt_ns = dt; s.seq = g_seq++; s.w = S.w; s.h = S.h; s.bpp = bpp;
    s.gap = g_prev_cap_frame ? frame - g_prev_cap_frame - 1 : 0;
    g_prev_cap_frame = frame;
    g_inflight++;
    g_jobs.emplace_back(D, slot);
    g_cv.notify_one();

    *scratch = *pi;
    scratch->waitSemaphoreCount = 1;
    scratch->pWaitSemaphores = &S.sems[idx];
    if (g_left == 0) end_session_locked();
    return scratch;
}

PFN_vkVoidFunction fd_hook(const char* name) {
    if (!g_on || !name) return nullptr;
    if (!strcmp(name, "vkGetDeviceQueue")) return reinterpret_cast<PFN_vkVoidFunction>(fd_GetDeviceQueue);
    if (!strcmp(name, "vkGetDeviceQueue2")) return reinterpret_cast<PFN_vkVoidFunction>(fd_GetDeviceQueue2);
    if (!strcmp(name, "vkDestroySwapchainKHR")) return reinterpret_cast<PFN_vkVoidFunction>(fd_DestroySwapchainKHR);
    return nullptr;
}

std::string fd_json() {
    std::lock_guard<std::mutex> lk(g_m);
    char b[512];
    snprintf(b, sizeof b,
             "{\"on\":%s,\"base\":\"%s\",\"sessions\":%llu,\"captured_total\":%llu,\"dropped_total\":%llu,"
             "\"capturing\":%s,\"last_label\":\"%s\",\"last_dir\":\"%s\",\"err\":\"%s\"}",
             g_on ? "true" : "false", g_base.c_str(), (unsigned long long)g_sessions, (unsigned long long)g_tot_cap,
             (unsigned long long)g_tot_drop, g_capturing ? "true" : "false", g_label.c_str(), g_dir.c_str(),
             g_err.c_str());
    return b;
}
