// interp.h — 层内多 present (插帧线 I6): 游戏每 present 一帧, 层在两帧之间多送显一张合成帧。
//
// 开关: debug.knobs.interp=1 (CreateInstance 时读一次)。关着时本文件所有函数都是空操作, 不挂钩子、
// 不改 swapchain 参数、不加 device 扩展 —— 其它档位 (loadop/upop/framedump/npuop) 的 A/B 不受影响。
//
// 属性 (全部 debug.knobs.interp*), 除 interp 本身外都在 CreateInstance 时读一次:
//   interp=1            总开关
//   interp_mode         合成帧怎么来 (缺省 dup):
//                         dup     = 原样复制上一张真实帧 (B0 臂: 零计算, 只多一次 GPU 拷贝 + 一次送显)
//                         dupcur  = 原样复制当前这张真实帧 (连历史图都不用, 最便宜的「双 present」)
//                         real    = 不插帧, 只回读真实帧上屏时刻 (插帧延迟的对照)
//                         bm      = 块匹配运动补偿中间帧 (interp_bm.cpp)
//                         <其它>  = 查 ip_register_synth 登记的名字 (I5 的 GPU 着色器 / NPU 调用挂在这里);
//                                   没登记就退回 dup 并在 marker 里记 fallbacks.unknown_mode
//   interp_pacing       1 (缺省) = 用 VK_GOOGLE_display_timing 的 desiredPresentTime 定节奏;
//                       0 = 不设时间戳, 只靠 FIFO 连续送两张 (要求屏幕 60 Hz 才会是 30→60)
//   interp_lead_ns      合成帧目标显示时刻至少在「现在 + 这么多」之后, 缺省 = 一个刷新周期
//   interp_extra_images swapchain 多要几张图给层用, 缺省 2 (受 surface maxImageCount 限制)
//   interp_acquire_ms   层自己 acquire 合成图的最长等待, 缺省 4 ms; 超时这一帧只送真实帧
//   interp_csv          1 = 每次 present 追加一行到 <files>/knobs_interp_frames.csv (缺省 1)
//
// 节奏 (interp_pacing=1): 游戏 present 第 N 帧时, 层在同一次调用、同一线程里 (不开线程, 原神会杀层线程):
//   1. acquire 一张层自己的 swapchain 图 L;
//   2. 提交一个命令缓冲: 等游戏给 present 的信号量 → 合成 (dup: 历史图 H → L) → 把 N 拷进 H → 发层信号量;
//   3. present L, desiredPresentTime = 下一个可达 vsync (t); 再 present N, desiredPresentTime = t + 半个游戏周期。
//   半个游戏周期按最近 present 间隔的滑动平均取整到刷新周期的整数倍。实际显示时刻用
//   vkGetPastPresentationTimingGOOGLE 回读, 算显示帧间隔 p50/p95 进 marker。
// 扩展不可用 / 开不起来时退回 interp_pacing=0 的做法, 原因写进 marker 的 pacing_fallback_reason。
#pragma once
#include <vulkan/vulkan.h>
#include <string>

typedef VkResult(VKAPI_PTR* PFN_rkSetDeviceLoaderData)(VkDevice, void*);

// I5 插入点: 在层的命令缓冲里录合成命令 (GPU 路径), 或在本线程里同步做主机侧工作 (NPU 路径)。
// 调用时三张图的布局: prev (历史图 H, 层自有, 非 swapchain 图) = TRANSFER_SRC_OPTIMAL;
//   cur (游戏刚 present 的 swapchain 图 N) = TRANSFER_SRC_OPTIMAL; dst (层 acquire 的 swapchain 图 L) = TRANSFER_DST_OPTIMAL。
// 回调结束时三张图必须回到上述布局 (层之后把 dst 转成 PRESENT_SRC, 把 cur 拷进 prev)。
// prev 第一帧可能是 VK_NULL_HANDLE (还没有历史); usage 字段说明 swapchain 图实际拿到了哪些 usage 位
// (SAMPLED / STORAGE 不一定给)。返回 false = 本帧退回 dup。
struct IpSynthCtx {
    VkDevice dev = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;       // 游戏 present 用的队列 (同一条)
    VkCommandBuffer cb = VK_NULL_HANDLE;  // 已 Begin, 由层 End + 提交
    VkImage prev = VK_NULL_HANDLE, cur = VK_NULL_HANDLE, dst = VK_NULL_HANDLE;
    uint32_t w = 0, h = 0, fmt = 0;       // VkFormat
    uint32_t swap_usage = 0;              // swapchain 图实际的 VkImageUsageFlags
    uint32_t queue_family = 0;
    uint64_t frame = 0;                   // 本层的全局 present 序号
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
};
typedef bool (*IpSynthFn)(const IpSynthCtx& c, void* user);
// 登记一种合成方式 (名字对应 debug.knobs.interp_mode)。随时可调 (静态初始化或 rk_CreateInstance 里)。
void ip_register_synth(const char* mode, IpSynthFn fn, void* user);

bool ip_init_from_props();   // CreateInstance 时调一次, 返回是否开启
bool ip_on();
// CreateDevice 前: 要加的 device 扩展名 (VK_GOOGLE_display_timing), 没开或不要加时返回空。
// supported 由调用方用下层 EnumerateDeviceExtensionProperties 查出来后传入。
const char* ip_wanted_device_ext(bool supported);
void ip_on_device(VkInstance inst, VkPhysicalDevice phys, VkDevice dev, PFN_vkGetInstanceProcAddr gipa,
                  PFN_vkGetDeviceProcAddr gdpa, PFN_rkSetDeviceLoaderData sdld, uint32_t sole_family,
                  bool ext_enabled);
void ip_on_destroy_device(VkDevice dev);
// 建 swapchain 前: 多要几张图、加 TRANSFER_SRC|TRANSFER_DST (+SAMPLED/STORAGE 若支持)、强制 FIFO。返回是否改了。
bool ip_patch_swapchain_ci(VkDevice dev, VkSwapchainCreateInfoKHR* ci);
void ip_on_swapchain(VkDevice dev, VkSwapchainKHR sc, const VkSwapchainCreateInfoKHR* used_ci, bool patched);
// present: 开着时由本函数负责调下层 (可能调两次: 合成帧 + 真实帧), 返回给游戏的结果。
VkResult ip_on_present(VkQueue q, const VkPresentInfoKHR* pi, PFN_vkQueuePresentKHR next, uint64_t frame);
// 本模块自己的钩子 (vkGetDeviceQueue / vkGetDeviceQueue2 / vkDestroySwapchainKHR); 没开或不是这几个名字返回空。
PFN_vkVoidFunction ip_hook(const char* name);
// debug.knobs.interp_fps>0 时挂 vkCreateAndroidSurfaceKHR: 表面建好后对游戏窗口调
// ANativeWindow_setFrameRate(fps, FIXED_SOURCE) —— 应用请求高刷的标准接口, 插帧需要屏幕跑在游戏帧率两倍
struct ANativeWindow;
bool ip_wants_surface_hook();
void ip_on_android_surface(ANativeWindow* w);
std::string ip_json();       // 写进 knobs_layer_out.json 的 "interp" 一节
