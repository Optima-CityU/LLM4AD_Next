// interp.cpp — 层内多 present: 游戏每 present 一帧 N, 层在 N 之前多送显一张合成帧 L (接口与属性见 interp.h)。
//
// 一次 vkQueuePresentKHR 里发生的事 (全在游戏的 present 线程, 不开线程):
//   1. 取一个空闲槽 (命令缓冲 + fence + acquire 信号量, 共 3 个轮用; 上上上次的 fence 没完就本帧放弃合成);
//   2. vkAcquireNextImageKHR 要一张层自己的 swapchain 图 L (最多等 interp_acquire_ms, 超时放弃合成);
//   3. 录命令: N PRESENT_SRC→TRANSFER_SRC, L UNDEFINED→TRANSFER_DST; 合成 (dup: 历史图 H→L; dupcur: N→L;
//      其它: 登记的回调); L→PRESENT_SRC; N→H (历史); N→PRESENT_SRC。提交: 等游戏给 present 的信号量 +
//      acquire 信号量, 发 L 与 N 各自的信号量 (一张 swapchain 图一个, 同 framedump 的做法);
//   4. present L (等 L 的信号量), 再 present N (等 N 的信号量, 其余字段照游戏的)。
//      开 pacing 时两次 present 都带 VkPresentTimesInfoGOOGLE: L 的 desiredPresentTime = 下一个可达 vsync 的
//      前半个周期 (SurfaceFlinger 的 latch 规则是 desired < 预期 vsync 才送, 取中点最不含糊), N 的 = 再加半个
//      游戏周期。游戏自己若带了 PresentTimesInfoGOOGLE (Swappy), 排在 pNext 链头的被跳过, 更深的记一次不动。
//   5. 回读 vkGetPastPresentationTimingGOOGLE: 真实显示时刻 → vsync 相位 (下次算目标用) + 显示帧间隔统计。
// 任何一步放弃合成都只送真实帧 N (与不开层时相同), 原因计数进 marker 的 fallbacks。
#include "interp.h"
#include "framedump.h"

#include <android/log.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <sys/system_properties.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <vector>

#define IP_LOG(...) __android_log_print(ANDROID_LOG_INFO, "refknobs", "interp: " __VA_ARGS__)
#define IP_LOG_ONCE(...) do { static bool said = false; if (!said) { said = true; IP_LOG(__VA_ARGS__); } } while (0)

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

// 固定容量环: 只存最近 N 个样本, 算分位数时拷出来排序
struct Ring {
    std::vector<uint64_t> v;
    size_t cap = 4096, head = 0;
    uint64_t n = 0, mx = 0;
    void push(uint64_t x) {
        if (v.size() < cap) v.push_back(x);
        else { v[head] = x; head = (head + 1) % cap; }
        n++;
        if (x > mx) mx = x;
    }
    std::string json() const {
        if (v.empty()) return "{\"n\":0}";
        std::vector<uint64_t> c = v;
        std::sort(c.begin(), c.end());
        auto pct = [&](double p) { size_t i = (size_t)(p * (c.size() - 1) + 0.5); return c[i < c.size() ? i : c.size() - 1]; };
        char b[200];
        snprintf(b, sizeof b, "{\"n\":%llu,\"p50\":%llu,\"p95\":%llu,\"p99\":%llu,\"min\":%llu,\"max\":%llu}",
                 (unsigned long long)n, (unsigned long long)pct(0.5), (unsigned long long)pct(0.95),
                 (unsigned long long)pct(0.99), (unsigned long long)c.front(), (unsigned long long)mx);
        return b;
    }
};

enum Kind : uint8_t { K_REAL = 0, K_SYNTH = 1 };
struct Pending { uint32_t id; uint8_t kind; uint64_t frame, desired, target, call; };   // call = 游戏调 present 的时刻

struct Swap {
    uint32_t w = 0, h = 0, fmt = 0, usage = 0, n_images = 0;
    uint32_t min_req = 0, min_used = 0, present_mode_orig = 0, present_mode_used = 0;
    bool patched = false;
    std::vector<VkImage> imgs;
    std::vector<VkSemaphore> sems;        // 一张图一个: 层提交 → 这张图的 present
    VkImage hist = VK_NULL_HANDLE;        // 历史图 H (上一张真实帧), 层自有
    VkDeviceMemory hist_mem = VK_NULL_HANDLE;
    bool hist_valid = false;              // H 里有没有内容 (第一帧没有)
    // 节奏
    bool timing_ok = false;               // 扩展开着且 GetRefreshCycleDuration 成功
    uint64_t refresh_ns = 0;
    bool phase_known = false;
    uint64_t phase_ns = 0;                // 最近一次真实显示时刻 mod refresh
    uint64_t last_target_n = 0;           // 上一张真实帧的目标显示时刻
    uint32_t next_id = 1;
    std::deque<Pending> pending;
    uint32_t last_id = 0; uint8_t last_kind = K_REAL; uint64_t last_actual = 0;
    // 游戏 present 调用节奏
    uint64_t prev_call_t = 0; double ema_ns = 0;
    uint64_t synth_count = 0;
};

struct Slot {
    VkCommandBuffer cb = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkSemaphore acq = VK_NULL_HANDLE;
    bool busy = false, acq_dirty = false;
};

struct Dev {
    VkDevice dev = VK_NULL_HANDLE;
    VkInstance inst = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    PFN_rkSetDeviceLoaderData sdld = nullptr;
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
    bool ext_enabled = false;
#define IP_FN(n) PFN_vk##n n = nullptr;
    IP_FN(GetDeviceQueue) IP_FN(GetDeviceQueue2) IP_FN(DestroySwapchainKHR) IP_FN(GetSwapchainImagesKHR)
    IP_FN(AcquireNextImageKHR) IP_FN(CreateCommandPool) IP_FN(DestroyCommandPool) IP_FN(AllocateCommandBuffers)
    IP_FN(ResetCommandBuffer) IP_FN(BeginCommandBuffer) IP_FN(EndCommandBuffer) IP_FN(CmdPipelineBarrier)
    IP_FN(CmdCopyImage) IP_FN(QueueSubmit) IP_FN(CreateFence) IP_FN(DestroyFence) IP_FN(ResetFences)
    IP_FN(WaitForFences) IP_FN(GetFenceStatus) IP_FN(CreateSemaphore) IP_FN(DestroySemaphore)
    IP_FN(CreateImage) IP_FN(DestroyImage) IP_FN(GetImageMemoryRequirements) IP_FN(AllocateMemory) IP_FN(FreeMemory)
    IP_FN(BindImageMemory) IP_FN(GetRefreshCycleDurationGOOGLE) IP_FN(GetPastPresentationTimingGOOGLE)
    IP_FN(GetPhysicalDeviceMemoryProperties) IP_FN(GetPhysicalDeviceSurfaceCapabilitiesKHR)
    IP_FN(GetPhysicalDeviceFormatProperties)
#undef IP_FN
    std::map<VkSwapchainKHR, Swap> swaps;
    std::vector<VkSemaphore> retired_sems;
    std::map<VkQueue, uint32_t> queue_family;
    VkCommandPool pool = VK_NULL_HANDLE;
    uint32_t pool_family = UINT32_MAX;
    uint32_t sole_family = UINT32_MAX;
    Slot slots[3];
    bool complete = false;
    // patch_swapchain_ci 时记下原值, on_swapchain 时取走
    uint32_t pend_min_req = 0, pend_mode_orig = 0;
};

struct Synth { IpSynthFn fn; void* user; };
std::map<std::string, Synth>& registry() { static std::map<std::string, Synth> r; return r; }

// ── 全局状态 (g_m 保护) ──
std::mutex g_m;
bool g_on = false;
std::string g_mode = "dup";
bool g_pacing = true;
long g_lead_ns = -1;       // -1 = 一个刷新周期
long g_extra_images = 2;
long g_acquire_ms = 4;
bool g_csv = true;
long g_fps_vote = 0;                   // debug.knobs.interp_fps: 给游戏窗口投的帧率, 0 = 不投
std::string g_fps_vote_result;         // 投票结果 (marker)
std::map<void*, Dev*> g_devs;
FILE* g_csv_f = nullptr;
uint32_t g_csv_rows = 0;
std::string g_pacing_reason;          // 退回 FIFO 的原因 (空 = pacing 在用)
std::string g_swap_reason;            // swapchain 没改成的原因
// 计数
uint64_t g_real = 0, g_synth = 0, g_real_only = 0;
std::map<std::string, uint64_t> g_fb;  // fallback 原因
uint64_t g_late = 0, g_early = 0, g_timing_samples = 0;
uint64_t g_pair_rs = 0, g_pair_sr = 0, g_pair_rr = 0, g_pair_ss = 0;   // 相邻显示帧的种类组合
Ring g_disp_int, g_disp_int_rs, g_disp_int_sr, g_call_int, g_cpu_us, g_margin;
Ring g_lat_real;   // 真实帧: 游戏调 present → 实际上屏 (插帧带来的显示延迟看它与 real 模式之差)
uint64_t g_last_half_ns = 0, g_last_period_ns = 0;
uint32_t g_dup_src_cur = 0;            // dup 模式下因没有历史而拷当前帧的次数

Dev* dev_of_key(void* k) {
    auto it = g_devs.find(k);
    if (it == g_devs.end() && g_devs.size() == 1) it = g_devs.begin();
    return it == g_devs.end() ? nullptr : it->second;
}

void csv_open_locked() {
    if (g_csv_f || !g_csv) return;
    std::string pkg = self_pkg();
    const std::string cands[] = {"/storage/emulated/0/Android/data/" + pkg + "/files/knobs_interp_frames.csv",
                                 "/data/data/" + pkg + "/files/knobs_interp_frames.csv",
                                 "/data/local/tmp/knobs_interp_frames." + pkg + ".csv"};
    for (const auto& p : cands) {
        int fd = open(p.c_str(), O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);   // 只追加, 不截断
        if (fd < 0) continue;
        g_csv_f = fdopen(fd, "a");
        if (!g_csv_f) { close(fd); continue; }
        off_t sz = lseek(fd, 0, SEEK_END);
        fprintf(g_csv_f, "%s# session pid=%d t0_ns=%llu mode=%s\n", sz > 0 ? "" : "event,present_id,kind,frame,t_ns,desired_ns,target_ns,actual_ns,earliest_ns,margin_ns\n",
                (int)getpid(), (unsigned long long)now_ns(), g_mode.c_str());
        IP_LOG("csv -> %s", p.c_str());
        return;
    }
    g_csv = false;
    IP_LOG("csv 三个落点都写不进, 关掉 csv");
}
void csv_row(const char* ev, uint32_t id, uint8_t kind, uint64_t frame, uint64_t t, uint64_t desired, uint64_t target,
             uint64_t actual, uint64_t earliest, int64_t margin) {
    if (!g_csv) return;
    csv_open_locked();
    if (!g_csv_f) return;
    fprintf(g_csv_f, "%s,%u,%s,%llu,%llu,%llu,%llu,%llu,%llu,%lld\n", ev, id, kind == K_REAL ? "real" : "synth",
            (unsigned long long)frame, (unsigned long long)t, (unsigned long long)desired, (unsigned long long)target,
            (unsigned long long)actual, (unsigned long long)earliest, (long long)margin);
    if (++g_csv_rows % 64 == 0) fflush(g_csv_f);
}

bool ensure_slots(Dev* D, uint32_t family) {
    if (D->pool != VK_NULL_HANDLE && D->pool_family == family) return true;
    if (D->pool != VK_NULL_HANDLE) {   // 队列族变了 (极少见): 等在途的完成再重建
        for (auto& s : D->slots) if (s.busy && s.fence) D->WaitForFences(D->dev, 1, &s.fence, VK_TRUE, 1000000000ull);
        for (auto& s : D->slots) {
            if (s.fence) D->DestroyFence(D->dev, s.fence, nullptr);
            if (s.acq) D->DestroySemaphore(D->dev, s.acq, nullptr);
            s = Slot{};
        }
        D->DestroyCommandPool(D->dev, D->pool, nullptr);
        D->pool = VK_NULL_HANDLE;
    }
    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, nullptr,
                                VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, family};
    if (D->CreateCommandPool(D->dev, &pci, nullptr, &D->pool) != VK_SUCCESS) return false;
    D->pool_family = family;
    for (auto& s : D->slots) {
        VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, nullptr, D->pool,
                                        VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
        if (D->AllocateCommandBuffers(D->dev, &cai, &s.cb) != VK_SUCCESS) return false;
        if (D->sdld) D->sdld(D->dev, s.cb);
        else *reinterpret_cast<void**>(s.cb) = *reinterpret_cast<void**>(D->dev);
        VkFenceCreateInfo fci{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        if (D->CreateFence(D->dev, &fci, nullptr, &s.fence) != VK_SUCCESS) return false;
        VkSemaphoreCreateInfo sci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        if (D->CreateSemaphore(D->dev, &sci, nullptr, &s.acq) != VK_SUCCESS) return false;
    }
    IP_LOG("槽就绪 x3, family=%u", family);
    return true;
}

uint32_t pick_device_local(Dev* D, uint32_t bits) {
    VkPhysicalDeviceMemoryProperties mp{};
    D->GetPhysicalDeviceMemoryProperties(D->phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) return i;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++) if (bits & (1u << i)) return i;
    return UINT32_MAX;
}

bool create_hist(Dev* D, Swap& S) {
    VkImageCreateInfo ici{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ici.imageType = VK_IMAGE_TYPE_2D; ici.format = (VkFormat)S.fmt; ici.extent = {S.w, S.h, 1};
    ici.mipLevels = 1; ici.arrayLayers = 1; ici.samples = VK_SAMPLE_COUNT_1_BIT; ici.tiling = VK_IMAGE_TILING_OPTIMAL;
    ici.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    if (D->GetPhysicalDeviceFormatProperties) {
        VkFormatProperties fp{};
        D->GetPhysicalDeviceFormatProperties(D->phys, (VkFormat)S.fmt, &fp);
        if (fp.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT) ici.usage |= VK_IMAGE_USAGE_STORAGE_BIT;
    }
    ici.sharingMode = VK_SHARING_MODE_EXCLUSIVE; ici.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (D->CreateImage(D->dev, &ici, nullptr, &S.hist) != VK_SUCCESS) { S.hist = VK_NULL_HANDLE; return false; }
    VkMemoryRequirements mr{};
    D->GetImageMemoryRequirements(D->dev, S.hist, &mr);
    uint32_t mt = pick_device_local(D, mr.memoryTypeBits);
    VkMemoryAllocateInfo mai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, mt};
    if (mt == UINT32_MAX || D->AllocateMemory(D->dev, &mai, nullptr, &S.hist_mem) != VK_SUCCESS ||
        D->BindImageMemory(D->dev, S.hist, S.hist_mem, 0) != VK_SUCCESS) {
        D->DestroyImage(D->dev, S.hist, nullptr); S.hist = VK_NULL_HANDLE;
        if (S.hist_mem) { D->FreeMemory(D->dev, S.hist_mem, nullptr); S.hist_mem = VK_NULL_HANDLE; }
        return false;
    }
    S.hist_valid = false;
    return true;
}

// 等所有在途槽完成后收掉一张 swapchain 的层自有对象 (信号量留到 device 销毁)。调用方持 g_m。
void release_swap_locked(Dev* D, Swap& S) {
    for (auto& s : D->slots) if (s.busy && s.fence) { D->WaitForFences(D->dev, 1, &s.fence, VK_TRUE, 1000000000ull); s.busy = false; }
    if (S.hist) { D->DestroyImage(D->dev, S.hist, nullptr); S.hist = VK_NULL_HANDLE; }
    if (S.hist_mem) { D->FreeMemory(D->dev, S.hist_mem, nullptr); S.hist_mem = VK_NULL_HANDLE; }
    for (auto s : S.sems) if (s) D->retired_sems.push_back(s);
    S.sems.clear();
}

void image_barrier(Dev* D, VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to, VkAccessFlags sa,
                   VkAccessFlags da, VkPipelineStageFlags ss, VkPipelineStageFlags ds) {
    VkImageMemoryBarrier ib{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    ib.srcAccessMask = sa; ib.dstAccessMask = da; ib.oldLayout = from; ib.newLayout = to;
    ib.srcQueueFamilyIndex = ib.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    ib.image = img; ib.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    D->CmdPipelineBarrier(cb, ss, ds, 0, 0, nullptr, 0, nullptr, 1, &ib);
}
void copy_full(Dev* D, VkCommandBuffer cb, VkImage src, VkImage dst, uint32_t w, uint32_t h) {
    VkImageCopy rg{};
    rg.srcSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    rg.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    rg.extent = {w, h, 1};
    D->CmdCopyImage(cb, src, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &rg);
}

// 回读真实显示时刻 (只在 pacing 下有记录)。调用方持 g_m。
void poll_timing_locked(Dev* D, VkSwapchainKHR sc, Swap& S) {
    if (!S.timing_ok || !D->GetPastPresentationTimingGOOGLE) return;
    uint32_t n = 0;
    if (D->GetPastPresentationTimingGOOGLE(D->dev, sc, &n, nullptr) < 0 || n == 0) return;
    std::vector<VkPastPresentationTimingGOOGLE> v(n);
    VkResult r = D->GetPastPresentationTimingGOOGLE(D->dev, sc, &n, v.data());
    if (r != VK_SUCCESS && r != VK_INCOMPLETE) return;
    const uint64_t R = S.refresh_ns;
    for (uint32_t i = 0; i < n; i++) {
        const auto& e = v[i];
        if (!e.actualPresentTime) continue;
        Pending p{e.presentID, K_REAL, 0, 0, 0, 0};
        bool found = false;
        while (!S.pending.empty() && S.pending.front().id < e.presentID) S.pending.pop_front();   // 回读丢了的
        if (!S.pending.empty() && S.pending.front().id == e.presentID) { p = S.pending.front(); S.pending.pop_front(); found = true; }
        if (!found) continue;
        g_timing_samples++;
        if (R) { S.phase_ns = e.actualPresentTime % R; S.phase_known = true; }
        if (S.last_id && e.presentID == S.last_id + 1 && e.actualPresentTime >= S.last_actual) {
            uint64_t d = e.actualPresentTime - S.last_actual;
            g_disp_int.push(d);
            if (S.last_kind == K_REAL && p.kind == K_SYNTH) { g_pair_rs++; g_disp_int_rs.push(d); }
            else if (S.last_kind == K_SYNTH && p.kind == K_REAL) { g_pair_sr++; g_disp_int_sr.push(d); }
            else if (S.last_kind == K_REAL) g_pair_rr++;
            else g_pair_ss++;
        }
        S.last_id = e.presentID; S.last_actual = e.actualPresentTime; S.last_kind = p.kind;
        if (p.target && R) {
            if (e.actualPresentTime > p.target + R / 4) g_late++;
            else if (e.actualPresentTime + R / 4 < p.target) g_early++;
        }
        g_margin.push((uint64_t)(e.presentMargin < 0 ? 0 : e.presentMargin));
        if (p.kind == K_REAL && p.call && e.actualPresentTime >= p.call) g_lat_real.push(e.actualPresentTime - p.call);
        csv_row("timing", e.presentID, p.kind, p.frame, 0, e.desiredPresentTime, p.target, e.actualPresentTime,
                e.earliestPresentTime, e.presentMargin);
    }
}

// ── 本模块自己的钩子 ──
VKAPI_ATTR void VKAPI_CALL ip_GetDeviceQueue(VkDevice dev, uint32_t fam, uint32_t idx, VkQueue* q) {
    Dev* D;
    { std::lock_guard<std::mutex> lk(g_m); D = dev_of_key(fkey(dev)); }
    if (!D || !D->GetDeviceQueue) return;
    // framedump 同时开着时它也要记队列族: 本钩子排在前面, 往下调它的版本 (它再调下层)
    if (auto fdn = reinterpret_cast<PFN_vkGetDeviceQueue>(fd_hook("vkGetDeviceQueue"))) fdn(dev, fam, idx, q);
    else D->GetDeviceQueue(dev, fam, idx, q);
    if (q && *q) { std::lock_guard<std::mutex> lk(g_m); D->queue_family[*q] = fam; }
}
VKAPI_ATTR void VKAPI_CALL ip_GetDeviceQueue2(VkDevice dev, const VkDeviceQueueInfo2* qi, VkQueue* q) {
    Dev* D;
    { std::lock_guard<std::mutex> lk(g_m); D = dev_of_key(fkey(dev)); }
    if (!D || !D->GetDeviceQueue2) return;
    if (auto fdn = reinterpret_cast<PFN_vkGetDeviceQueue2>(fd_hook("vkGetDeviceQueue2"))) fdn(dev, qi, q);
    else D->GetDeviceQueue2(dev, qi, q);
    if (qi && q && *q) { std::lock_guard<std::mutex> lk(g_m); D->queue_family[*q] = qi->queueFamilyIndex; }
}
VKAPI_ATTR void VKAPI_CALL ip_DestroySwapchainKHR(VkDevice dev, VkSwapchainKHR sc, const VkAllocationCallbacks* a) {
    Dev* D;
    {
        std::lock_guard<std::mutex> lk(g_m);
        D = dev_of_key(fkey(dev));
        if (D) {
            auto it = D->swaps.find(sc);
            if (it != D->swaps.end()) { release_swap_locked(D, it->second); D->swaps.erase(it); }
        }
    }
    // 先收本模块的, 再交给 framedump (开着时) 或下层
    if (auto fdn = reinterpret_cast<PFN_vkDestroySwapchainKHR>(fd_hook("vkDestroySwapchainKHR"))) fdn(dev, sc, a);
    else if (D && D->DestroySwapchainKHR) D->DestroySwapchainKHR(dev, sc, a);
}

}  // namespace

void ip_register_synth(const char* mode, IpSynthFn fn, void* user) {
    if (!mode || !fn) return;
    std::lock_guard<std::mutex> lk(g_m);
    registry()[mode] = Synth{fn, user};
}

bool ip_init_from_props() {
    std::string v = prop("debug.knobs.interp");
    g_on = !v.empty() && (v[0] == '1' || v[0] == 't' || v[0] == 'y');
    if (!g_on) return false;
    std::string m = prop("debug.knobs.interp_mode");
    g_mode = m.empty() ? "dup" : m;
    std::string p = prop("debug.knobs.interp_pacing");
    g_pacing = p.empty() || p[0] == '1' || p[0] == 't' || p[0] == 'y';
    g_lead_ns = prop_long("debug.knobs.interp_lead_ns", -1, -1, 200000000);
    g_extra_images = prop_long("debug.knobs.interp_extra_images", 2, 1, 4);
    g_acquire_ms = prop_long("debug.knobs.interp_acquire_ms", 4, 0, 100);
    g_fps_vote = prop_long("debug.knobs.interp_fps", 0, 0, 240);
    std::string c = prop("debug.knobs.interp_csv");
    g_csv = c.empty() || c[0] == '1';
    if (!g_pacing) g_pacing_reason = "disabled_by_prop";
    IP_LOG("开启: mode=%s pacing=%d lead_ns=%ld extra_images=%ld acquire_ms=%ld csv=%d", g_mode.c_str(), (int)g_pacing,
           g_lead_ns, g_extra_images, g_acquire_ms, (int)g_csv);
    return true;
}

bool ip_on() { return g_on; }

bool ip_wants_surface_hook() { return g_on && g_fps_vote > 0; }

void ip_on_android_surface(ANativeWindow* w) {
    if (!g_on || g_fps_vote <= 0 || !w) return;
    typedef int32_t (*SetFrameRate)(ANativeWindow*, float, int8_t);
    static SetFrameRate fn = nullptr;
    if (!fn) {
        void* h = dlopen("libandroid.so", RTLD_NOW | RTLD_NOLOAD);
        if (!h) h = dlopen("libandroid.so", RTLD_NOW);
        if (h) fn = (SetFrameRate)dlsym(h, "ANativeWindow_setFrameRate");
    }
    std::lock_guard<std::mutex> lk(g_m);
    if (!fn) { g_fps_vote_result = "no_ANativeWindow_setFrameRate"; IP_LOG("没有 ANativeWindow_setFrameRate"); return; }
    int32_t r = fn(w, (float)g_fps_vote, 1 /* ANATIVEWINDOW_FRAME_RATE_COMPATIBILITY_FIXED_SOURCE */);
    g_fps_vote_result = "setFrameRate(" + std::to_string(g_fps_vote) + ")=" + std::to_string(r);
    IP_LOG("给游戏窗口投帧率 %ld: %d", g_fps_vote, (int)r);
}

const char* ip_wanted_device_ext(bool supported) {
    if (!g_on || !g_pacing) return nullptr;
    if (!supported) {
        std::lock_guard<std::mutex> lk(g_m);
        if (g_pacing_reason.empty()) g_pacing_reason = "ext_unsupported";
        return nullptr;
    }
    return VK_GOOGLE_DISPLAY_TIMING_EXTENSION_NAME;
}

void ip_on_device(VkInstance inst, VkPhysicalDevice phys, VkDevice dev, PFN_vkGetInstanceProcAddr gipa,
                  PFN_vkGetDeviceProcAddr gdpa, PFN_rkSetDeviceLoaderData sdld, uint32_t sole_family, bool ext_enabled) {
    if (!g_on) return;
    Dev* D = new Dev();
    D->dev = dev; D->inst = inst; D->phys = phys; D->sdld = sdld; D->gdpa = gdpa; D->sole_family = sole_family;
    D->ext_enabled = ext_enabled;
#define IP_GD(n) D->n = (PFN_vk##n)gdpa(dev, "vk" #n);
    IP_GD(GetDeviceQueue) IP_GD(GetDeviceQueue2) IP_GD(DestroySwapchainKHR) IP_GD(GetSwapchainImagesKHR)
    IP_GD(AcquireNextImageKHR) IP_GD(CreateCommandPool) IP_GD(DestroyCommandPool) IP_GD(AllocateCommandBuffers)
    IP_GD(ResetCommandBuffer) IP_GD(BeginCommandBuffer) IP_GD(EndCommandBuffer) IP_GD(CmdPipelineBarrier)
    IP_GD(CmdCopyImage) IP_GD(QueueSubmit) IP_GD(CreateFence) IP_GD(DestroyFence) IP_GD(ResetFences)
    IP_GD(WaitForFences) IP_GD(GetFenceStatus) IP_GD(CreateSemaphore) IP_GD(DestroySemaphore)
    IP_GD(CreateImage) IP_GD(DestroyImage) IP_GD(GetImageMemoryRequirements) IP_GD(AllocateMemory) IP_GD(FreeMemory)
    IP_GD(BindImageMemory)
    if (ext_enabled) { IP_GD(GetRefreshCycleDurationGOOGLE) IP_GD(GetPastPresentationTimingGOOGLE) }
#undef IP_GD
    if (inst && gipa) {
        D->GetPhysicalDeviceMemoryProperties = (PFN_vkGetPhysicalDeviceMemoryProperties)gipa(inst, "vkGetPhysicalDeviceMemoryProperties");
        D->GetPhysicalDeviceSurfaceCapabilitiesKHR = (PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR)gipa(inst, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR");
        D->GetPhysicalDeviceFormatProperties = (PFN_vkGetPhysicalDeviceFormatProperties)gipa(inst, "vkGetPhysicalDeviceFormatProperties");
    }
    D->complete = D->GetSwapchainImagesKHR && D->AcquireNextImageKHR && D->CreateCommandPool && D->AllocateCommandBuffers &&
                  D->ResetCommandBuffer && D->BeginCommandBuffer && D->EndCommandBuffer && D->CmdPipelineBarrier &&
                  D->CmdCopyImage && D->QueueSubmit && D->CreateFence && D->ResetFences && D->WaitForFences &&
                  D->CreateSemaphore && D->CreateImage && D->GetImageMemoryRequirements && D->AllocateMemory &&
                  D->BindImageMemory && D->GetPhysicalDeviceMemoryProperties;
    std::lock_guard<std::mutex> lk(g_m);
    if (!D->complete) g_swap_reason = "missing_device_functions";
    if (g_pacing && ext_enabled && !(D->GetRefreshCycleDurationGOOGLE && D->GetPastPresentationTimingGOOGLE) && g_pacing_reason.empty())
        g_pacing_reason = "ext_functions_missing";
    if (g_pacing && !ext_enabled && g_pacing_reason.empty()) g_pacing_reason = "ext_not_enabled";
    g_devs[fkey(dev)] = D;
    IP_LOG("device 已登记: complete=%d display_timing=%d loader_data_callback=%d", (int)D->complete, (int)ext_enabled, sdld ? 1 : 0);
}

void ip_on_destroy_device(VkDevice dev) {
    if (!g_on) return;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D) return;
    for (auto& kv : D->swaps) release_swap_locked(D, kv.second);
    D->swaps.clear();
    for (auto& s : D->slots) {
        if (s.fence) D->DestroyFence(D->dev, s.fence, nullptr);
        if (s.acq) D->DestroySemaphore(D->dev, s.acq, nullptr);
    }
    if (D->pool) D->DestroyCommandPool(D->dev, D->pool, nullptr);
    for (auto s : D->retired_sems) if (s && D->DestroySemaphore) D->DestroySemaphore(D->dev, s, nullptr);
    g_devs.erase(fkey(dev));
    delete D;
    if (g_csv_f) { fflush(g_csv_f); fclose(g_csv_f); g_csv_f = nullptr; }
}

bool ip_patch_swapchain_ci(VkDevice dev, VkSwapchainCreateInfoKHR* ci) {
    if (!g_on || !ci) return false;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D || !D->complete) return false;
    VkSurfaceCapabilitiesKHR caps{};
    bool have_caps = D->GetPhysicalDeviceSurfaceCapabilitiesKHR &&
                     D->GetPhysicalDeviceSurfaceCapabilitiesKHR(D->phys, ci->surface, &caps) == VK_SUCCESS;
    const VkImageUsageFlags need = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (have_caps && (caps.supportedUsageFlags & need) != need) {
        g_swap_reason = "usage_unsupported"; IP_LOG("surface 不支持 TRANSFER_SRC|DST (0x%x), 不插", caps.supportedUsageFlags);
        return false;
    }
    uint32_t want = ci->minImageCount + (uint32_t)g_extra_images;
    if (have_caps && caps.maxImageCount && want > caps.maxImageCount) want = caps.maxImageCount;
    if (want <= ci->minImageCount) { g_swap_reason = "no_extra_images"; IP_LOG("surface 不给多余的图 (max=%u), 不插", caps.maxImageCount); return false; }
    D->pend_min_req = ci->minImageCount; D->pend_mode_orig = (uint32_t)ci->presentMode;
    ci->minImageCount = want;
    ci->imageUsage |= need;
    if (have_caps) {
        if (caps.supportedUsageFlags & VK_IMAGE_USAGE_SAMPLED_BIT) ci->imageUsage |= VK_IMAGE_USAGE_SAMPLED_BIT;
        if (caps.supportedUsageFlags & VK_IMAGE_USAGE_STORAGE_BIT) ci->imageUsage |= VK_IMAGE_USAGE_STORAGE_BIT;
    }
    // 节奏靠 FIFO 队列顺序 (合成帧先、真实帧后), MAILBOX 会把合成帧直接替换掉
    if (ci->presentMode != VK_PRESENT_MODE_FIFO_KHR && ci->presentMode != VK_PRESENT_MODE_FIFO_RELAXED_KHR)
        ci->presentMode = VK_PRESENT_MODE_FIFO_KHR;
    g_swap_reason.clear();
    return true;
}

void ip_on_swapchain(VkDevice dev, VkSwapchainKHR sc, const VkSwapchainCreateInfoKHR* ci, bool patched) {
    if (!g_on || !ci) return;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D) return;
    auto old = D->swaps.find(sc);
    if (old != D->swaps.end()) { release_swap_locked(D, old->second); D->swaps.erase(old); }
    Swap S;
    S.w = ci->imageExtent.width; S.h = ci->imageExtent.height; S.fmt = (uint32_t)ci->imageFormat; S.usage = (uint32_t)ci->imageUsage;
    S.min_used = ci->minImageCount; S.present_mode_used = (uint32_t)ci->presentMode;
    S.min_req = patched ? D->pend_min_req : ci->minImageCount;
    S.present_mode_orig = patched ? D->pend_mode_orig : (uint32_t)ci->presentMode;
    S.patched = patched;
    uint32_t n = 0;
    if (D->GetSwapchainImagesKHR) D->GetSwapchainImagesKHR(dev, sc, &n, nullptr);
    S.imgs.resize(n);
    if (n) D->GetSwapchainImagesKHR(dev, sc, &n, S.imgs.data());
    S.n_images = n;
    S.sems.assign(n, VK_NULL_HANDLE);
    for (uint32_t i = 0; i < n && patched; i++) {
        VkSemaphoreCreateInfo sci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        if (D->CreateSemaphore(dev, &sci, nullptr, &S.sems[i]) != VK_SUCCESS) { S.patched = false; g_swap_reason = "create_semaphore"; break; }
    }
    if (S.patched && g_mode != "dupcur" && !create_hist(D, S)) { S.patched = false; g_swap_reason = "create_history_image"; }
    if (g_pacing && D->ext_enabled && D->GetRefreshCycleDurationGOOGLE) {
        VkRefreshCycleDurationGOOGLE rc{};
        if (D->GetRefreshCycleDurationGOOGLE(dev, sc, &rc) == VK_SUCCESS && rc.refreshDuration > 1000000 && rc.refreshDuration < 100000000) {
            S.refresh_ns = rc.refreshDuration; S.timing_ok = true; g_pacing_reason.clear();
        } else if (g_pacing_reason.empty()) g_pacing_reason = "refresh_cycle_query_failed";
    }
    if (!S.refresh_ns) S.refresh_ns = 16666667;   // 没扩展时按 60 Hz 估 (只用于日志/半周期取整, 不设时间戳)
    IP_LOG("swapchain %ux%u fmt=%u usage=0x%x images=%u (minImageCount %u->%u) presentMode %u->%u patched=%d timing=%d refresh=%llu ns",
           S.w, S.h, S.fmt, S.usage, n, S.min_req, S.min_used, S.present_mode_orig, S.present_mode_used, (int)S.patched,
           (int)S.timing_ok, (unsigned long long)S.refresh_ns);
    D->swaps[sc] = std::move(S);
}

VkResult ip_on_present(VkQueue q, const VkPresentInfoKHR* pi, PFN_vkQueuePresentKHR next, uint64_t frame) {
    if (!g_on || !pi) return next(q, pi);
    const uint64_t t = now_ns();
    std::unique_lock<std::mutex> lk(g_m);
    auto bail = [&](const char* why) {
        g_real++; g_real_only++; g_fb[why]++;
        csv_row("present", 0, K_REAL, frame, t, 0, 0, 0, 0, 0);
        lk.unlock();
        return next(q, pi);
    };
    Dev* D = dev_of_key(fkey(q));
    if (!D) return bail("device_unknown");
    if (pi->swapchainCount != 1 || !pi->pSwapchains || !pi->pImageIndices) return bail("multi_swapchain");
    VkSwapchainKHR sc = pi->pSwapchains[0];
    auto si = D->swaps.find(sc);
    if (si == D->swaps.end()) return bail("swapchain_unknown");
    Swap& S = si->second;
    // 游戏 present 调用节奏 (滑动平均, 用来定半个周期)
    if (S.prev_call_t) {
        uint64_t dt = t - S.prev_call_t;
        if (dt > 500000 && dt < 500000000) { g_call_int.push(dt); S.ema_ns = S.ema_ns > 0 ? S.ema_ns * 0.8 + dt * 0.2 : (double)dt; }
    }
    S.prev_call_t = t;
    if (!S.patched) return bail("swapchain_not_patched");
    if (g_mode == "real") {   // 对照: 不插帧, 只给真实帧带 presentID 回读上屏时刻 (延迟口径与插帧相同)
        poll_timing_locked(D, sc, S);
        bool timed = g_pacing && S.timing_ok;
        uint32_t id = S.next_id++;
        if (timed) {
            S.pending.push_back(Pending{id, K_REAL, frame, 0, 0, t});
            while (S.pending.size() > 512) S.pending.pop_front();
        }
        g_real++; g_real_only++;
        lk.unlock();
        if (!timed) return next(q, pi);
        struct Hdr { VkStructureType sType; const void* pNext; };
        const void* chain = pi->pNext;
        while (chain && reinterpret_cast<const Hdr*>(chain)->sType == VK_STRUCTURE_TYPE_PRESENT_TIMES_INFO_GOOGLE)
            chain = reinterpret_cast<const Hdr*>(chain)->pNext;   // 游戏自己的时间戳在链头: 换成我们的 (desired=0 即尽快)
        VkPresentTimeGOOGLE tm{id, 0};
        VkPresentTimesInfoGOOGLE pt{VK_STRUCTURE_TYPE_PRESENT_TIMES_INFO_GOOGLE, chain, 1, &tm};
        VkPresentInfoKHR pn = *pi;
        pn.pNext = &pt;
        return next(q, &pn);
    }
    uint32_t idxN = pi->pImageIndices[0];
    if (idxN >= S.imgs.size() || !S.sems[idxN]) return bail("image_index");
    uint32_t family = D->sole_family;
    auto qf = D->queue_family.find(q);
    if (qf != D->queue_family.end()) family = qf->second;
    if (family == UINT32_MAX) return bail("queue_family_unknown");
    if (!ensure_slots(D, family)) return bail("slots");
    Slot& sl = D->slots[S.synth_count % 3];
    if (sl.busy) {
        VkResult fr = D->WaitForFences(D->dev, 1, &sl.fence, VK_TRUE, 2000000ull);   // 上上上帧的拷贝还没完: 最多等 2 ms
        if (fr != VK_SUCCESS) return bail("slot_busy");
        sl.busy = false;
    }
    if (sl.acq_dirty) {   // 上次 acquire 成功但提交失败, 信号量停在已发状态: 换一个新的
        D->DestroySemaphore(D->dev, sl.acq, nullptr);
        VkSemaphoreCreateInfo sci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        if (D->CreateSemaphore(D->dev, &sci, nullptr, &sl.acq) != VK_SUCCESS) { sl.acq = VK_NULL_HANDLE; return bail("semaphore"); }
        sl.acq_dirty = false;
    }
    poll_timing_locked(D, sc, S);
    // 2. 层自己 acquire 一张图
    uint32_t idxL = UINT32_MAX;
    VkResult ar = D->AcquireNextImageKHR(D->dev, sc, (uint64_t)g_acquire_ms * 1000000ull, sl.acq, VK_NULL_HANDLE, &idxL);
    if (ar == VK_TIMEOUT || ar == VK_NOT_READY) return bail("acquire_timeout");
    if (ar != VK_SUCCESS && ar != VK_SUBOPTIMAL_KHR) return bail(ar == VK_ERROR_OUT_OF_DATE_KHR ? "acquire_out_of_date" : "acquire_fail");
    if (idxL >= S.imgs.size() || idxL == idxN || !S.sems[idxL]) { sl.acq_dirty = true; return bail("acquire_index"); }
    // 3. 录命令
    VkImage N = S.imgs[idxN], L = S.imgs[idxL];
    D->ResetCommandBuffer(sl.cb, 0);
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, nullptr, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, nullptr};
    D->BeginCommandBuffer(sl.cb, &bi);
    const VkPipelineStageFlags TR = VK_PIPELINE_STAGE_TRANSFER_BIT;
    image_barrier(D, sl.cb, N, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, 0, VK_ACCESS_TRANSFER_READ_BIT, TR, TR);
    image_barrier(D, sl.cb, L, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT, TR, TR);
    bool use_hist = S.hist != VK_NULL_HANDLE;
    bool custom = false;
    if (g_mode != "dup" && g_mode != "dupcur") {
        auto it = registry().find(g_mode);
        if (it == registry().end()) { g_fb["unknown_mode"]++; }
        else {
            IpSynthCtx c;
            c.dev = D->dev; c.queue = q; c.cb = sl.cb; c.prev = S.hist_valid ? S.hist : VK_NULL_HANDLE; c.cur = N; c.dst = L;
            c.w = S.w; c.h = S.h; c.fmt = S.fmt; c.swap_usage = S.usage; c.queue_family = family; c.frame = frame; c.gdpa = D->gdpa;
            custom = it->second.fn(c, it->second.user);
            if (!custom) g_fb["synth_declined"]++;
        }
    }
    if (!custom) {   // dup / dupcur / 退回
        VkImage src = (g_mode != "dupcur" && S.hist_valid) ? S.hist : N;
        if (g_mode != "dupcur" && !S.hist_valid) g_dup_src_cur++;
        copy_full(D, sl.cb, src, L, S.w, S.h);
    }
    image_barrier(D, sl.cb, L, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_ACCESS_TRANSFER_WRITE_BIT, 0, TR, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    if (use_hist) {   // N → H, 下一帧当「上一帧」
        image_barrier(D, sl.cb, S.hist, S.hist_valid ? VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED,
                      VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_ACCESS_TRANSFER_READ_BIT, VK_ACCESS_TRANSFER_WRITE_BIT, TR, TR);
        copy_full(D, sl.cb, N, S.hist, S.w, S.h);
        image_barrier(D, sl.cb, S.hist, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, TR, TR);
    }
    image_barrier(D, sl.cb, N, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_ACCESS_TRANSFER_READ_BIT, 0, TR, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    D->EndCommandBuffer(sl.cb);
    std::vector<VkSemaphore> waits(pi->pWaitSemaphores, pi->pWaitSemaphores + pi->waitSemaphoreCount);
    waits.push_back(sl.acq);
    std::vector<VkPipelineStageFlags> stages(waits.size(), TR);
    VkSemaphore signals[2] = {S.sems[idxL], S.sems[idxN]};
    VkSubmitInfo sub{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    sub.waitSemaphoreCount = (uint32_t)waits.size(); sub.pWaitSemaphores = waits.data(); sub.pWaitDstStageMask = stages.data();
    sub.commandBufferCount = 1; sub.pCommandBuffers = &sl.cb;
    sub.signalSemaphoreCount = 2; sub.pSignalSemaphores = signals;
    D->ResetFences(D->dev, 1, &sl.fence);
    VkResult sr = D->QueueSubmit(q, 1, &sub, sl.fence);
    bool submitted = sr == VK_SUCCESS;
    if (!submitted) { g_fb["submit_fail"]++; sl.acq_dirty = true; }   // L 已 acquire, 仍要送回去 (内容未定义), 否则游戏可能拿不到图
    else { sl.busy = true; S.hist_valid = use_hist; }
    S.synth_count++;
    // 4. 节奏
    const uint64_t R = S.refresh_ns;
    uint64_t period = S.ema_ns > 0 ? (uint64_t)S.ema_ns : 2 * R;
    if (period < R) period = R;
    if (period > 10 * R) period = 10 * R;
    uint64_t half = ((period / 2 + R / 2) / R) * R;
    if (half < R) half = R;
    g_last_half_ns = half; g_last_period_ns = period;
    bool pace = g_pacing && S.timing_ok;
    uint64_t desiredL = 0, desiredN = 0, targetL = 0, targetN = 0;
    if (pace) {
        if (S.phase_known) {
            uint64_t lead = g_lead_ns >= 0 ? (uint64_t)g_lead_ns : R;
            uint64_t from = t + lead;
            uint64_t k = (from > S.phase_ns ? (from - S.phase_ns + R - 1) / R : 0);
            targetL = S.phase_ns + k * R;
            if (S.last_target_n && S.last_target_n + half > targetL && S.last_target_n + half < t + 10 * R) targetL = S.last_target_n + half;
            targetN = targetL + half;
            desiredL = targetL - R / 2; desiredN = targetN - R / 2;
            S.last_target_n = targetN;
        } else g_fb["no_phase_yet"]++;   // 还没回读到真实显示时刻: 这一对不设时间戳 (ASAP), 但带 presentID 以便回读
    }
    uint32_t idL = S.next_id++, idN = S.next_id++;
    S.pending.push_back(Pending{idL, K_SYNTH, frame, desiredL, targetL, 0});
    S.pending.push_back(Pending{idN, K_REAL, frame, desiredN, targetN, t});
    while (S.pending.size() > 512) S.pending.pop_front();
    csv_row("present", idL, K_SYNTH, frame, t, desiredL, targetL, 0, 0, 0);
    csv_row("present", idN, K_REAL, frame, t, desiredN, targetN, 0, 0, 0);
    g_real++; g_synth++;
    g_cpu_us.push((now_ns() - t) / 1000);
    VkSemaphore semL = S.sems[idxL], semN = S.sems[idxN];
    lk.unlock();   // 下层 present 可能阻塞 (FIFO 满), 不持锁

    VkPresentTimeGOOGLE tL{idL, desiredL}, tN{idN, desiredN};
    VkPresentTimesInfoGOOGLE ptL{VK_STRUCTURE_TYPE_PRESENT_TIMES_INFO_GOOGLE, nullptr, 1, &tL};
    VkPresentTimesInfoGOOGLE ptN{VK_STRUCTURE_TYPE_PRESENT_TIMES_INFO_GOOGLE, nullptr, 1, &tN};
    VkPresentInfoKHR pl{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};
    pl.pNext = pace ? &ptL : nullptr;
    pl.waitSemaphoreCount = submitted ? 1 : 0; pl.pWaitSemaphores = &semL;
    pl.swapchainCount = 1; pl.pSwapchains = &sc; pl.pImageIndices = &idxL; pl.pResults = nullptr;
    VkResult rL = next(q, &pl);

    VkPresentInfoKHR pn = *pi;
    if (submitted) { pn.waitSemaphoreCount = 1; pn.pWaitSemaphores = &semN; }
    const void* chain = pi->pNext;
    struct Hdr { VkStructureType sType; const void* pNext; };
    bool deep_times = false;
    if (pace) {
        while (chain && reinterpret_cast<const Hdr*>(chain)->sType == VK_STRUCTURE_TYPE_PRESENT_TIMES_INFO_GOOGLE)
            chain = reinterpret_cast<const Hdr*>(chain)->pNext;   // 游戏自己的时间戳 (Swappy) 在链头: 跳过, 用我们的
        for (const Hdr* h = reinterpret_cast<const Hdr*>(chain); h; h = reinterpret_cast<const Hdr*>(h->pNext))
            if (h->sType == VK_STRUCTURE_TYPE_PRESENT_TIMES_INFO_GOOGLE) { deep_times = true; break; }
        if (!deep_times) { ptN.pNext = chain; pn.pNext = &ptN; }
    }
    VkResult rN = next(q, &pn);

    lk.lock();
    if (deep_times) { g_fb["game_present_times_deep"]++; IP_LOG_ONCE("游戏的 VkPresentTimesInfoGOOGLE 不在 pNext 链头, 这一帧真实帧没设时间戳"); }
    if (rL != VK_SUCCESS && rL != VK_SUBOPTIMAL_KHR) g_fb[rL == VK_ERROR_OUT_OF_DATE_KHR ? "present_l_out_of_date" : "present_l_fail"]++;
    if (rN != VK_SUCCESS && rN != VK_SUBOPTIMAL_KHR) g_fb["present_n_fail"]++;
    return rN;
}

PFN_vkVoidFunction ip_hook(const char* name) {
    if (!g_on || !name) return nullptr;
    if (!strcmp(name, "vkGetDeviceQueue")) return reinterpret_cast<PFN_vkVoidFunction>(ip_GetDeviceQueue);
    if (!strcmp(name, "vkGetDeviceQueue2")) return reinterpret_cast<PFN_vkVoidFunction>(ip_GetDeviceQueue2);
    if (!strcmp(name, "vkDestroySwapchainKHR")) return reinterpret_cast<PFN_vkVoidFunction>(ip_DestroySwapchainKHR);
    return nullptr;
}

std::string ip_json() {
    std::lock_guard<std::mutex> lk(g_m);
    std::string fb;
    for (auto& kv : g_fb) { if (!fb.empty()) fb += ","; fb += "\"" + kv.first + "\":" + std::to_string(kv.second); }
    std::string swap = "null";
    bool pacing_live = false;
    for (auto& dv : g_devs)
        for (auto& kv : dv.second->swaps) {
            const Swap& S = kv.second;
            char b[512];
            snprintf(b, sizeof b,
                     "{\"w\":%u,\"h\":%u,\"fmt\":%u,\"usage\":%u,\"images\":%u,\"min_image_count_req\":%u,\"min_image_count_used\":%u,"
                     "\"present_mode_orig\":%u,\"present_mode_used\":%u,\"patched\":%s,\"refresh_ns\":%llu,\"timing_ok\":%s,"
                     "\"phase_known\":%s,\"game_period_ns_ema\":%llu,\"history_image\":%s}",
                     S.w, S.h, S.fmt, S.usage, S.n_images, S.min_req, S.min_used, S.present_mode_orig, S.present_mode_used,
                     S.patched ? "true" : "false", (unsigned long long)S.refresh_ns, S.timing_ok ? "true" : "false",
                     S.phase_known ? "true" : "false", (unsigned long long)S.ema_ns, S.hist ? "true" : "false");
            swap = b;
            pacing_live = pacing_live || (S.timing_ok && g_pacing);
        }
    char head[512];
    snprintf(head, sizeof head,
             "{\"on\":true,\"mode\":\"%s\",\"pacing\":\"%s\",\"pacing_fallback_reason\":\"%s\",\"swapchain_fallback_reason\":\"%s\","
             "\"half_period_ns\":%llu,\"period_ns\":%llu,\"frames\":{\"real\":%llu,\"synth\":%llu,\"real_only\":%llu,\"dup_without_history\":%u},"
             "\"timing\":{\"samples\":%llu,\"late\":%llu,\"early\":%llu,\"pairs\":{\"real_synth\":%llu,\"synth_real\":%llu,\"real_real\":%llu,\"synth_synth\":%llu}}",
             g_mode.c_str(), pacing_live ? "google_display_timing" : "fifo_plain", g_pacing_reason.c_str(), g_swap_reason.c_str(),
             (unsigned long long)g_last_half_ns, (unsigned long long)g_last_period_ns, (unsigned long long)g_real,
             (unsigned long long)g_synth, (unsigned long long)g_real_only, g_dup_src_cur, (unsigned long long)g_timing_samples,
             (unsigned long long)g_late, (unsigned long long)g_early, (unsigned long long)g_pair_rs, (unsigned long long)g_pair_sr,
             (unsigned long long)g_pair_rr, (unsigned long long)g_pair_ss);
    std::string j = head;
    j += ",\"displayed_interval_ns\":" + g_disp_int.json();
    j += ",\"displayed_interval_real_to_synth_ns\":" + g_disp_int_rs.json();
    j += ",\"displayed_interval_synth_to_real_ns\":" + g_disp_int_sr.json();
    j += ",\"present_margin_ns\":" + g_margin.json();
    j += ",\"real_present_to_display_ns\":" + g_lat_real.json();
    j += ",\"present_call_interval_ns\":" + g_call_int.json();
    j += ",\"layer_cpu_us\":" + g_cpu_us.json();
    j += ",\"fallbacks\":{" + fb + "}";
    j += ",\"fps_vote\":\"" + g_fps_vote_result + "\"";
    j += ",\"swapchain\":" + swap;
    j += std::string(",\"csv\":") + (g_csv_f ? "true" : "false");
    j += "}";
    return j;
}
