// selfsr.h — 送显前超分 (自研超分线): 游戏按小分辨率出图, 层在 vkQueuePresentKHR 前把它放大到送显分辨率。
//
// 开关: debug.knobs.selfsr = "<宽>x<高>[:<锐化强度 0..1>]" (CreateInstance 时读一次), 例如 2141x969:0.8; 空或 "0" = 关。
// 关着时本文件所有函数都是空操作, 不改 swapchain 参数 —— 其它档位的 A/B 不受影响。
//
// 做法 (游戏不知道这件事, 也不依赖游戏的 pass 结构):
//   1. vkCreateSwapchainKHR: 游戏要的是 w0 x h0 (比如 Android Game Mode 降分辨率后的小窗口),
//      层把 imageExtent 换成 <宽>x<高> 并加 TRANSFER_SRC|TRANSFER_DST。游戏的帧缓冲仍是 w0 x h0
//      (帧缓冲允许小于图像), 所以它只画在每张 swapchain 图的左上角 w0 x h0。
//   2. vkQueuePresentKHR: 等游戏的信号量 → 左上角 w0 x h0 拷进层自有的源图 T → 计算着色器 (selfsr_up.comp,
//      Lanczos2 + 2x2 去振铃) 写层自有的目标图 O (<宽>x<高>) → [锐化>0 时 selfsr_rcas.comp: O → P]
//      → 拷回 swapchain 图 → 送显。
//   游戏要的尺寸本来就等于 <宽>x<高> 时不改 swapchain, 只计数 (same_size), 画面与不开时相同。
// 任何一步不成立都退回原样送显, 原因计数进 marker 的 selfsr.fallbacks。
// 与 interp 同时开时 (第 5 步): 先超分, 再由 interp 在超分后的画面上插帧 (见 rk_QueuePresentKHR)。
#pragma once
#include <vulkan/vulkan.h>
#include <string>
#include "framedump.h"   // PFN_rkSetDeviceLoaderData

bool sr_init_from_props();
bool sr_on();
// Native mode keeps the canvas extent and dispatches only with selfsr_live=1.
// The parent must supply an actual sole, unprotected, compute-capable queue.
void sr_on_device(VkInstance inst, VkPhysicalDevice phys, VkDevice dev, PFN_vkGetInstanceProcAddr gipa,
                  PFN_vkGetDeviceProcAddr gdpa, PFN_rkSetDeviceLoaderData sdld, uint32_t sole_family,
                  VkQueue sole_queue = VK_NULL_HANDLE);
// False means layer work is not known complete; do not forward DestroyDevice.
bool sr_on_destroy_device(VkDevice dev);
// 建 swapchain 前: 放大 imageExtent 并加用途位。返回是否改了。
bool sr_patch_swapchain_ci(VkDevice dev, VkSwapchainCreateInfoKHR* ci);
// 建好后: orig = 游戏原本要的参数, used = 实际用的参数
void sr_on_swapchain(VkDevice dev, VkSwapchainKHR sc, const VkSwapchainCreateInfoKHR* orig,
                     const VkSwapchainCreateInfoKHR* used, bool patched);
// 本模块自己的钩子 (vkDestroySwapchainKHR: 先收层自建对象再往下转); 没开或不是这个名字返回空。
PFN_vkVoidFunction sr_hook(const char* name);
// present: 开着时由本函数调下层, 返回给游戏的结果
VkResult sr_on_present(VkQueue q, const VkPresentInfoKHR* pi, PFN_vkQueuePresentKHR next, uint64_t frame);
std::string sr_json();
