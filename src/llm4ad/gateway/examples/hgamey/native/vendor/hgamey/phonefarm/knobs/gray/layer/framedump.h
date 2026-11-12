// framedump.h — 逐帧读回送显图 (插帧/超分的训练与评测数据), vk_layer_refknobs.cpp 调用的接口。
//
// 开关: debug.knobs.framedump=1 (CreateInstance 时读一次)。关着时下面所有函数都是空操作,
// 不加任何钩子 —— 不影响 loadop/upop/npuop 等别的档位的 A/B。
// 抓哪一段: 运行中改 debug.knobs.framedump_go=<场景标签> (层每 10 帧看一次), 改成一个
// 新的非空、非 0 的值就开一段, 抓 debug.knobs.framedump_n 次 present (缺省 600)。
// 具体格式、落点见 framedump.cpp 开头。
#pragma once
#include <vulkan/vulkan.h>
#include <string>

typedef VkResult(VKAPI_PTR* PFN_rkSetDeviceLoaderData)(VkDevice, void*);

bool fd_init_from_props();   // CreateInstance 时调一次, 返回是否开启
bool fd_on();
void fd_on_device(VkInstance inst, VkPhysicalDevice phys, VkDevice dev, PFN_vkGetInstanceProcAddr gipa,
                  PFN_vkGetDeviceProcAddr gdpa, PFN_rkSetDeviceLoaderData sdld, uint32_t sole_family);
// sole_family: 建设备时只建了一个队列族就填它 (查不到 present 队列的族时兜底), 否则 UINT32_MAX
void fd_on_destroy_device(VkDevice dev);
// 建 swapchain 前: 能加就给 imageUsage 加 TRANSFER_SRC (读回要用)。返回是否加成了。
bool fd_patch_swapchain_ci(VkDevice dev, VkSwapchainCreateInfoKHR* ci);
void fd_on_swapchain(VkDevice dev, VkSwapchainKHR sc, const VkSwapchainCreateInfoKHR* ci, bool usage_ok);
// present 前: 若在抓帧窗口内, 提交一次拷贝并返回改过等待信号量的 present 信息 (写在 scratch 里);
// 否则原样返回 pi。frame 是本层的全局 present 序号。
const VkPresentInfoKHR* fd_on_present(VkQueue q, const VkPresentInfoKHR* pi, VkPresentInfoKHR* scratch,
                                      uint64_t frame);
// 本模块自己的钩子 (vkGetDeviceQueue / vkGetDeviceQueue2 / vkDestroySwapchainKHR), 没开或不是这几个名字返回空。
PFN_vkVoidFunction fd_hook(const char* name);
std::string fd_json();       // 写进 knobs_layer_out.json 的 "framedump" 一节
