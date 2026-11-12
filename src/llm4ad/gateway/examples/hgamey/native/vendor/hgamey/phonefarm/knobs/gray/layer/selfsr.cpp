// selfsr.cpp — 送显前超分 (接口与做法见 selfsr.h)。
//
// 一次 vkQueuePresentKHR 里 (游戏的 present 线程, 不开线程 —— 原神会杀层线程):
//   1. 取一个空闲槽 (命令缓冲 + fence, 3 个轮用; 上上上次没完最多等 2 ms, 等不到本帧原样送显);
//   2. 录命令: 先一道全量屏障 (之前提交里对 T/O 的读写全部完成); N PRESENT_SRC→TRANSFER_SRC;
//      N 左上 w0 x h0 → T; T→SHADER_READ; O→GENERAL; dispatch; O→TRANSFER_SRC; N→TRANSFER_DST; O → N;
//      N→PRESENT_SRC。提交: 等游戏给 present 的信号量, 发这张图自己的信号量;
//   3. present: 照游戏的参数, 只把等待的信号量换成我们的。
#include "selfsr.h"
#include "interp.h"

#include <android/log.h>
#include <sys/system_properties.h>
#include <time.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <vector>

#if __has_include("selfsr_spv.h")
#include "selfsr_spv.h"   // build_layer.sh 生成
#else
// gray-artifact 等只生成 copy_spv.h 的构建: 没有超分着色器, 管线创建时报 shader_missing, 本模块不启用
static const unsigned char kSelfsrUpSpv[] = {0};
static const unsigned int kSelfsrUpSpvLen = 0;
static const unsigned char kSelfsrRcasSpv[] = {0};
static const unsigned int kSelfsrRcasSpvLen = 0;
#endif

#if __has_include("selfsr_candidate_metadata.h")
#include "selfsr_candidate_metadata.h"
#else
static const char kSelfsrCandidateSourceSha256[] = "";
static const char kSelfsrCandidateSpirvSha256[] = "";
static const char kSelfsrCandidateKind[] = "unspecified";
#endif

#define SR_LOG(...) __android_log_print(ANDROID_LOG_INFO, "refknobs", "selfsr: " __VA_ARGS__)

namespace {

inline void* fkey(void* h) { return *reinterpret_cast<void**>(h); }

uint64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

struct Swap {
    uint32_t in_w = 0, in_h = 0;     // 游戏要的 (= 游戏实际画的区域)
    uint32_t out_w = 0, out_h = 0;   // 实际 swapchain 尺寸
    VkFormat fmt = VK_FORMAT_UNDEFINED;
    bool active = false;             // 尺寸不同且资源齐全: 每帧做超分
    bool destroy_pending = false;
    bool custom_destroy_allocator = false;
    std::vector<VkImage> imgs;
    std::vector<VkSemaphore> sems;   // 一张图一个: 层提交 → 这张图的 present
    VkImage T = VK_NULL_HANDLE, O = VK_NULL_HANDLE, P = VK_NULL_HANDLE;   // P: 锐化后 (开 RCAS 时)
    VkDeviceMemory T_mem = VK_NULL_HANDLE, O_mem = VK_NULL_HANDLE, P_mem = VK_NULL_HANDLE;
    VkImageView T_view = VK_NULL_HANDLE, O_view = VK_NULL_HANDLE, P_view = VK_NULL_HANDLE;
    VkDescriptorSet set = VK_NULL_HANDLE, set2 = VK_NULL_HANDLE;
    bool T_init = false, O_init = false;
    uint64_t frames = 0;
};

struct Slot {
    VkCommandBuffer cb = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    bool busy = false;
};

struct Dev {
    VkDevice dev = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    PFN_rkSetDeviceLoaderData sdld = nullptr;
    uint32_t family = UINT32_MAX;
    VkQueue queue = VK_NULL_HANDLE;
    VkResult terminal = VK_SUCCESS;
    bool submission_uncertain = false;
#define SR_FN(n) PFN_vk##n n = nullptr;
    SR_FN(GetSwapchainImagesKHR) SR_FN(CreateCommandPool) SR_FN(DestroyCommandPool) SR_FN(AllocateCommandBuffers)
    SR_FN(ResetCommandBuffer) SR_FN(BeginCommandBuffer) SR_FN(EndCommandBuffer) SR_FN(CmdPipelineBarrier)
    SR_FN(CmdCopyImage) SR_FN(CmdBindPipeline) SR_FN(CmdBindDescriptorSets) SR_FN(CmdPushConstants) SR_FN(CmdDispatch)
    SR_FN(QueueSubmit) SR_FN(CreateFence) SR_FN(DestroyFence) SR_FN(ResetFences) SR_FN(WaitForFences)
    SR_FN(CreateSemaphore) SR_FN(DestroySemaphore) SR_FN(CreateImage) SR_FN(DestroyImage)
    SR_FN(GetImageMemoryRequirements) SR_FN(AllocateMemory) SR_FN(FreeMemory) SR_FN(BindImageMemory)
    SR_FN(CreateImageView) SR_FN(DestroyImageView) SR_FN(CreateSampler) SR_FN(DestroySampler)
    SR_FN(CreateDescriptorSetLayout) SR_FN(DestroyDescriptorSetLayout) SR_FN(CreateDescriptorPool)
    SR_FN(DestroyDescriptorPool) SR_FN(AllocateDescriptorSets) SR_FN(FreeDescriptorSets) SR_FN(UpdateDescriptorSets)
    SR_FN(CreatePipelineLayout) SR_FN(DestroyPipelineLayout) SR_FN(CreateShaderModule) SR_FN(DestroyShaderModule)
    SR_FN(CreateComputePipelines) SR_FN(DestroyPipeline) SR_FN(DeviceWaitIdle) SR_FN(DestroySwapchainKHR)
    SR_FN(GetPhysicalDeviceMemoryProperties) SR_FN(GetPhysicalDeviceSurfaceCapabilitiesKHR)
    SR_FN(GetPhysicalDeviceFormatProperties)
    SR_FN(GetPhysicalDeviceQueueFamilyProperties)
#undef SR_FN
    bool complete = false;
    VkCommandPool pool = VK_NULL_HANDLE;
    Slot slots[3];
    uint64_t next_slot = 0;
    VkSampler sampler = VK_NULL_HANDLE;
    VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
    VkDescriptorPool dpool = VK_NULL_HANDLE;
    VkPipelineLayout pl = VK_NULL_HANDLE;
    VkPipeline pipe = VK_NULL_HANDLE, pipe_rcas = VK_NULL_HANDLE;
    bool pipe_tried = false;
    std::map<VkSwapchainKHR, Swap> swaps;
    std::vector<VkSemaphore> retired_sems;
};

std::mutex g_m;
bool g_on = false;
bool g_native = false, g_live = false;
uint32_t g_want_w = 0, g_want_h = 0;
float g_sharp = 0.f;                        // RCAS 强度, 0 = 不锐化
std::map<void*, Dev*> g_devs;
std::string g_reason;                       // 最近一次没能启用的原因
uint64_t g_up = 0, g_passthrough = 0, g_same_size = 0;
uint64_t g_dispatches = 0, g_completed = 0, g_presents = 0;
VkResult g_last_result = VK_SUCCESS;
std::map<std::string, uint64_t> g_fb;
uint32_t g_last_in_w = 0, g_last_in_h = 0, g_last_out_w = 0, g_last_out_h = 0;
std::vector<uint64_t> g_cpu_us;             // 录制+提交的 CPU 耗时 (最近 1024 个)

Dev* dev_of_key(void* k) {
    auto it = g_devs.find(k);
    if (!g_native && it == g_devs.end() && g_devs.size() == 1) it = g_devs.begin();
    return it == g_devs.end() ? nullptr : it->second;
}

void failure(const char* reason, VkResult result) {
    g_reason = reason;
    g_last_result = result;
    ++g_fb[reason];
    SR_LOG("%s: VkResult=%d", reason, static_cast<int>(result));
}

bool live_enabled() {
    if (!g_native) return true;
    char value[PROP_VALUE_MAX]{};
    __system_property_get("debug.knobs.selfsr_live", value);
    return strcmp(value, "1") == 0;
}

VkResult complete_slot(Dev* D, Slot& slot, uint64_t timeout) {
    if (!slot.busy) return VK_SUCCESS;
    VkResult result = D->WaitForFences(D->dev, 1, &slot.fence, VK_TRUE, timeout);
    if (result == VK_SUCCESS) {
        slot.busy = false;
        ++g_completed;
    }
    return result;
}

uint32_t pick_device_local(Dev* D, uint32_t bits) {
    VkPhysicalDeviceMemoryProperties mp{};
    D->GetPhysicalDeviceMemoryProperties(D->phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) return i;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++) if (bits & (1u << i)) return i;
    return UINT32_MAX;
}

bool make_image(Dev* D, uint32_t w, uint32_t h, VkFormat fmt, VkImageUsageFlags usage, VkImage* img,
                VkDeviceMemory* mem, VkImageView* view) {
    VkImageCreateInfo ici{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ici.imageType = VK_IMAGE_TYPE_2D; ici.format = fmt; ici.extent = {w, h, 1};
    ici.mipLevels = 1; ici.arrayLayers = 1; ici.samples = VK_SAMPLE_COUNT_1_BIT;
    ici.tiling = VK_IMAGE_TILING_OPTIMAL; ici.usage = usage; ici.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ici.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (D->CreateImage(D->dev, &ici, nullptr, img) != VK_SUCCESS) return false;
    VkMemoryRequirements mr{};
    D->GetImageMemoryRequirements(D->dev, *img, &mr);
    uint32_t mt = pick_device_local(D, mr.memoryTypeBits);
    VkMemoryAllocateInfo mai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, mt};
    if (mt == UINT32_MAX || D->AllocateMemory(D->dev, &mai, nullptr, mem) != VK_SUCCESS) return false;
    if (D->BindImageMemory(D->dev, *img, *mem, 0) != VK_SUCCESS) return false;
    VkImageViewCreateInfo vci{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vci.image = *img; vci.viewType = VK_IMAGE_VIEW_TYPE_2D; vci.format = fmt;
    vci.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    return D->CreateImageView(D->dev, &vci, nullptr, view) == VK_SUCCESS;
}

// 设备级对象: 命令池/槽、采样器、描述符布局/池、管线。第一次用到时建。调用方持 g_m。
bool ensure_pipeline(Dev* D) {
    if (D->pipe) return true;
    if (D->pipe_tried) return false;
    D->pipe_tried = true;
    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, nullptr,
                                VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, D->family};
    if (D->CreateCommandPool(D->dev, &pci, nullptr, &D->pool) != VK_SUCCESS) { g_reason = "command_pool"; return false; }
    for (auto& s : D->slots) {
        VkCommandBufferAllocateInfo cai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, nullptr, D->pool,
                                        VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
        if (D->AllocateCommandBuffers(D->dev, &cai, &s.cb) != VK_SUCCESS) { g_reason = "command_buffer"; return false; }
        if (D->sdld) {
            if (D->sdld(D->dev, s.cb) != VK_SUCCESS) { g_reason = "loader_data"; return false; }
        }
        else *reinterpret_cast<void**>(s.cb) = *reinterpret_cast<void**>(D->dev);
        VkFenceCreateInfo fci{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        if (D->CreateFence(D->dev, &fci, nullptr, &s.fence) != VK_SUCCESS) { g_reason = "fence"; return false; }
    }
    VkSamplerCreateInfo sci{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    sci.magFilter = sci.minFilter = VK_FILTER_NEAREST;
    sci.addressModeU = sci.addressModeV = sci.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    if (D->CreateSampler(D->dev, &sci, nullptr, &D->sampler) != VK_SUCCESS) { g_reason = "sampler"; return false; }
    VkDescriptorSetLayoutBinding b[2] = {
        {0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr},
        {1, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr}};
    VkDescriptorSetLayoutCreateInfo dli{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, nullptr, 0, 2, b};
    if (D->CreateDescriptorSetLayout(D->dev, &dli, nullptr, &D->dsl) != VK_SUCCESS) { g_reason = "descriptor_layout"; return false; }
    VkDescriptorPoolSize ps[2] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 8}, {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 8}};
    VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, nullptr,
                                   VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT, 8, 2, ps};
    if (D->CreateDescriptorPool(D->dev, &dpi, nullptr, &D->dpool) != VK_SUCCESS) { g_reason = "descriptor_pool"; return false; }
    VkPushConstantRange pcr{VK_SHADER_STAGE_COMPUTE_BIT, 0, 16};
    VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, nullptr, 0, 1, &D->dsl, 1, &pcr};
    if (D->CreatePipelineLayout(D->dev, &pli, nullptr, &D->pl) != VK_SUCCESS) { g_reason = "pipeline_layout"; return false; }
    if (kSelfsrUpSpvLen < 20 || (g_sharp > 0 && kSelfsrRcasSpvLen < 20)) { g_reason = "shader_missing"; return false; }
    auto make = [&](const unsigned char* spv, unsigned len, VkPipeline* out) {
        VkShaderModuleCreateInfo smi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, nullptr, 0, len,
                                     reinterpret_cast<const uint32_t*>(spv)};
        VkShaderModule sm = VK_NULL_HANDLE;
        if (D->CreateShaderModule(D->dev, &smi, nullptr, &sm) != VK_SUCCESS) return false;
        VkComputePipelineCreateInfo cpi{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        cpi.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, sm, "main", nullptr};
        cpi.layout = D->pl;
        VkResult r = D->CreateComputePipelines(D->dev, VK_NULL_HANDLE, 1, &cpi, nullptr, out);
        D->DestroyShaderModule(D->dev, sm, nullptr);
        if (r != VK_SUCCESS) { *out = VK_NULL_HANDLE; return false; }
        return true;
    };
    if (!make(kSelfsrUpSpv, kSelfsrUpSpvLen, &D->pipe)) { g_reason = "compute_pipeline"; return false; }
    if (g_sharp > 0 && !make(kSelfsrRcasSpv, kSelfsrRcasSpvLen, &D->pipe_rcas)) {
        D->DestroyPipeline(D->dev, D->pipe, nullptr); D->pipe = VK_NULL_HANDLE;
        g_reason = "compute_pipeline_rcas"; return false;
    }
    SR_LOG("管线就绪 family=%u rcas=%d", D->family, D->pipe_rcas ? 1 : 0);
    return true;
}

bool release_swap_locked(Dev* D, Swap& S) {
    if (D->submission_uncertain) {
        failure("cleanup_submission_uncertain", D->terminal);
        return false;
    }
    for (auto& s : D->slots) {
        VkResult result = complete_slot(D, s, 1000000000ull);
        if (result != VK_SUCCESS) {
            failure("cleanup_incomplete", result);
            return false;
        }
    }
    if (S.set) D->FreeDescriptorSets(D->dev, D->dpool, 1, &S.set);
    if (S.set2) D->FreeDescriptorSets(D->dev, D->dpool, 1, &S.set2);
    for (VkImageView v : {S.T_view, S.O_view, S.P_view}) if (v) D->DestroyImageView(D->dev, v, nullptr);
    for (VkImage i : {S.T, S.O, S.P}) if (i) D->DestroyImage(D->dev, i, nullptr);
    for (VkDeviceMemory m : {S.T_mem, S.O_mem, S.P_mem}) if (m) D->FreeMemory(D->dev, m, nullptr);
    for (auto s : S.sems) if (s) D->retired_sems.push_back(s);
    S = Swap{};
    return true;
}

void barrier(Dev* D, VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to, VkAccessFlags sa,
             VkAccessFlags da, VkPipelineStageFlags ss, VkPipelineStageFlags ds) {
    VkImageMemoryBarrier ib{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    ib.srcAccessMask = sa; ib.dstAccessMask = da; ib.oldLayout = from; ib.newLayout = to;
    ib.srcQueueFamilyIndex = ib.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    ib.image = img; ib.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    D->CmdPipelineBarrier(cb, ss, ds, 0, 0, nullptr, 0, nullptr, 1, &ib);
}

VKAPI_ATTR void VKAPI_CALL sr_DestroySwapchainKHR(VkDevice dev, VkSwapchainKHR sc, const VkAllocationCallbacks* a) {
    Dev* D;
    {
        std::lock_guard<std::mutex> lk(g_m);
        D = dev_of_key(fkey(dev));
        if (D) {
            auto it = D->swaps.find(sc);
            if (it != D->swaps.end()) {
                if (!release_swap_locked(D, it->second)) {
                    it->second.active = false;
                    it->second.destroy_pending = true;
                    it->second.custom_destroy_allocator = a != nullptr;
                    return;
                }
                D->swaps.erase(it);
            }
        }
    }
    // interp 同时开着时它也要收这条 swapchain 的对象: 往下转给它的钩子 (它再转 framedump / 下层)
    if (auto ipn = reinterpret_cast<PFN_vkDestroySwapchainKHR>(ip_hook("vkDestroySwapchainKHR"))) ipn(dev, sc, a);
    else if (D && D->DestroySwapchainKHR) D->DestroySwapchainKHR(dev, sc, a);
}

}  // namespace

PFN_vkVoidFunction sr_hook(const char* name) {
    if (!g_on || !name) return nullptr;
    if (!strcmp(name, "vkDestroySwapchainKHR")) return reinterpret_cast<PFN_vkVoidFunction>(sr_DestroySwapchainKHR);
    return nullptr;
}

bool sr_init_from_props() {
    char v[PROP_VALUE_MAX] = {0};
    __system_property_get("debug.knobs.selfsr", v);
    if (strcmp(v, "native") == 0) {
        g_on = g_native = true;
        g_live = live_enabled();
        g_sharp = 0.f;
        SR_LOG("native experimental postprocessing enabled; live=%d", static_cast<int>(g_live));
        return true;
    }
    unsigned w = 0, h = 0;
    float sharp = 0.f;
    int n = sscanf(v, "%ux%u:%f", &w, &h, &sharp);
    if (n >= 2 && w >= 64 && h >= 64 && w <= 4096 && h <= 4096) {
        g_on = true; g_want_w = w; g_want_h = h;
        g_sharp = (n == 3 && sharp > 0.f) ? (sharp > 1.f ? 1.f : sharp) : 0.f;
        SR_LOG("开启: 送显分辨率 %ux%u 锐化 %.2f", w, h, g_sharp);
    }
    return g_on;
}

bool sr_on() { return g_on; }

void sr_on_device(VkInstance inst, VkPhysicalDevice phys, VkDevice dev, PFN_vkGetInstanceProcAddr gipa,
                  PFN_vkGetDeviceProcAddr gdpa, PFN_rkSetDeviceLoaderData sdld, uint32_t sole_family,
                  VkQueue sole_queue) {
    if (!g_on) return;
    Dev* D = new Dev();
    D->dev = dev; D->phys = phys; D->sdld = sdld; D->family = sole_family;
    D->queue = sole_queue;
#define SR_GD(n) D->n = (PFN_vk##n)gdpa(dev, "vk" #n);
    SR_GD(GetSwapchainImagesKHR) SR_GD(CreateCommandPool) SR_GD(DestroyCommandPool) SR_GD(AllocateCommandBuffers)
    SR_GD(ResetCommandBuffer) SR_GD(BeginCommandBuffer) SR_GD(EndCommandBuffer) SR_GD(CmdPipelineBarrier)
    SR_GD(CmdCopyImage) SR_GD(CmdBindPipeline) SR_GD(CmdBindDescriptorSets) SR_GD(CmdPushConstants) SR_GD(CmdDispatch)
    SR_GD(QueueSubmit) SR_GD(CreateFence) SR_GD(DestroyFence) SR_GD(ResetFences) SR_GD(WaitForFences)
    SR_GD(CreateSemaphore) SR_GD(DestroySemaphore) SR_GD(CreateImage) SR_GD(DestroyImage)
    SR_GD(GetImageMemoryRequirements) SR_GD(AllocateMemory) SR_GD(FreeMemory) SR_GD(BindImageMemory)
    SR_GD(CreateImageView) SR_GD(DestroyImageView) SR_GD(CreateSampler) SR_GD(DestroySampler)
    SR_GD(CreateDescriptorSetLayout) SR_GD(DestroyDescriptorSetLayout) SR_GD(CreateDescriptorPool)
    SR_GD(DestroyDescriptorPool) SR_GD(AllocateDescriptorSets) SR_GD(FreeDescriptorSets) SR_GD(UpdateDescriptorSets)
    SR_GD(CreatePipelineLayout) SR_GD(DestroyPipelineLayout) SR_GD(CreateShaderModule) SR_GD(DestroyShaderModule)
    SR_GD(CreateComputePipelines) SR_GD(DestroyPipeline) SR_GD(DeviceWaitIdle) SR_GD(DestroySwapchainKHR)
#undef SR_GD
    if (inst && gipa) {
        D->GetPhysicalDeviceMemoryProperties = (PFN_vkGetPhysicalDeviceMemoryProperties)gipa(inst, "vkGetPhysicalDeviceMemoryProperties");
        D->GetPhysicalDeviceSurfaceCapabilitiesKHR = (PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR)gipa(inst, "vkGetPhysicalDeviceSurfaceCapabilitiesKHR");
        D->GetPhysicalDeviceFormatProperties = (PFN_vkGetPhysicalDeviceFormatProperties)gipa(inst, "vkGetPhysicalDeviceFormatProperties");
        D->GetPhysicalDeviceQueueFamilyProperties = (PFN_vkGetPhysicalDeviceQueueFamilyProperties)gipa(inst, "vkGetPhysicalDeviceQueueFamilyProperties");
    }
    D->complete = D->GetSwapchainImagesKHR && D->CreateCommandPool && D->AllocateCommandBuffers && D->ResetCommandBuffer &&
                  D->BeginCommandBuffer && D->EndCommandBuffer && D->CmdPipelineBarrier && D->CmdCopyImage &&
                  D->CmdBindPipeline && D->CmdBindDescriptorSets && D->CmdPushConstants && D->CmdDispatch &&
                  D->QueueSubmit && D->CreateFence && D->ResetFences && D->WaitForFences && D->CreateSemaphore &&
                  D->CreateImage && D->GetImageMemoryRequirements && D->AllocateMemory && D->BindImageMemory &&
                  D->CreateImageView && D->CreateSampler && D->CreateDescriptorSetLayout && D->CreateDescriptorPool &&
                  D->AllocateDescriptorSets && D->UpdateDescriptorSets && D->CreatePipelineLayout &&
                  D->CreateShaderModule && D->CreateComputePipelines && D->GetPhysicalDeviceMemoryProperties &&
                  D->GetPhysicalDeviceFormatProperties;
    // Native mode accepts only the exact queue attested by the parent device hook.
    if (g_native) {
        bool compute = false;
        if (sole_queue && sole_family != UINT32_MAX && D->GetPhysicalDeviceQueueFamilyProperties) {
            uint32_t count = 0;
            D->GetPhysicalDeviceQueueFamilyProperties(phys, &count, nullptr);
            if (count && count <= 256 && sole_family < count) {
                std::vector<VkQueueFamilyProperties> families(count);
                D->GetPhysicalDeviceQueueFamilyProperties(phys, &count, families.data());
                compute = sole_family < count && (families[sole_family].queueFlags & VK_QUEUE_COMPUTE_BIT);
            }
        }
        if (!compute) D->family = UINT32_MAX;
        D->complete = D->complete && D->sdld && D->GetPhysicalDeviceSurfaceCapabilitiesKHR &&
                      D->DestroyCommandPool && D->DestroyFence && D->DestroySemaphore &&
                      D->DestroyImage && D->FreeMemory && D->DestroyImageView && D->DestroySampler &&
                      D->DestroyDescriptorSetLayout && D->DestroyDescriptorPool && D->FreeDescriptorSets &&
                      D->DestroyPipelineLayout && D->DestroyShaderModule && D->DestroyPipeline && D->DestroySwapchainKHR;
    }
    std::lock_guard<std::mutex> lk(g_m);
    if (!D->complete) g_reason = "missing_device_functions";
    if (D->family == UINT32_MAX) g_reason = "queue_family_ambiguous";
    g_devs[fkey(dev)] = D;
    SR_LOG("device 已登记: complete=%d family=%u", (int)D->complete, D->family);
}

bool sr_on_destroy_device(VkDevice dev) {
    if (!g_on) return true;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D) return true;
    for (auto& kv : D->swaps) {
        // An allocator's user data cannot be assumed alive after a deferred destroy.
        if (kv.second.destroy_pending && kv.second.custom_destroy_allocator) {
            failure("deferred_allocator_lifetime_unknown", VK_NOT_READY);
            return false;
        }
        bool pending = kv.second.destroy_pending;
        if (!release_swap_locked(D, kv.second)) return false;
        if (pending) D->DestroySwapchainKHR(dev, kv.first, nullptr);
    }
    D->swaps.clear();
    for (auto& s : D->slots) if (s.fence) D->DestroyFence(D->dev, s.fence, nullptr);
    if (D->pool) D->DestroyCommandPool(D->dev, D->pool, nullptr);
    if (D->pipe) D->DestroyPipeline(D->dev, D->pipe, nullptr);
    if (D->pipe_rcas) D->DestroyPipeline(D->dev, D->pipe_rcas, nullptr);
    if (D->pl) D->DestroyPipelineLayout(D->dev, D->pl, nullptr);
    if (D->dpool) D->DestroyDescriptorPool(D->dev, D->dpool, nullptr);
    if (D->dsl) D->DestroyDescriptorSetLayout(D->dev, D->dsl, nullptr);
    if (D->sampler) D->DestroySampler(D->dev, D->sampler, nullptr);
    for (auto s : D->retired_sems) if (s) D->DestroySemaphore(D->dev, s, nullptr);
    g_devs.erase(fkey(dev));
    delete D;
    return true;
}

bool sr_patch_swapchain_ci(VkDevice dev, VkSwapchainCreateInfoKHR* ci) {
    if (!g_on || !ci) return false;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D || !D->complete || D->family == UINT32_MAX) return false;
    if (g_native) {
        if (!D->queue || ci->flags || ci->pNext || ci->imageSharingMode != VK_SHARING_MODE_EXCLUSIVE ||
            ci->imageArrayLayers != 1 || ci->imageFormat != VK_FORMAT_R8G8B8A8_UNORM ||
            ci->presentMode == VK_PRESENT_MODE_SHARED_DEMAND_REFRESH_KHR ||
            ci->presentMode == VK_PRESENT_MODE_SHARED_CONTINUOUS_REFRESH_KHR) {
            g_reason = "native_swapchain_unsupported";
            return false;
        }
        for (const auto& entry : D->swaps) {
            if (entry.second.active || entry.second.destroy_pending) {
                g_reason = "native_multiple_swapchains";
                return false;
            }
        }
        VkFormatProperties fp{};
        D->GetPhysicalDeviceFormatProperties(D->phys, VK_FORMAT_R8G8B8A8_UNORM, &fp);
        const VkFormatFeatureFlags need = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT |
                                          VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
        if ((fp.optimalTilingFeatures & need) != need) { g_reason = "native_format_features"; return false; }
    } else if (ci->imageExtent.width == g_want_w && ci->imageExtent.height == g_want_h) return false;
    if (ci->imageFormat != VK_FORMAT_R8G8B8A8_UNORM && ci->imageFormat != VK_FORMAT_R8G8B8A8_SRGB) {
        g_reason = "format_" + std::to_string((int)ci->imageFormat); return false;
    }
    if (ci->imageArrayLayers != 1) { g_reason = "array_layers"; return false; }
    VkSurfaceCapabilitiesKHR caps{};
    VkResult capability = D->GetPhysicalDeviceSurfaceCapabilitiesKHR
        ? D->GetPhysicalDeviceSurfaceCapabilitiesKHR(D->phys, ci->surface, &caps) : VK_ERROR_FEATURE_NOT_PRESENT;
    if (g_native && capability != VK_SUCCESS) { failure("surface_capabilities", capability); return false; }
    if (capability == VK_SUCCESS) {
        const VkImageUsageFlags need = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
        if ((caps.supportedUsageFlags & need) != need) { g_reason = "usage_unsupported"; return false; }
        uint32_t width = g_native ? ci->imageExtent.width : g_want_w;
        uint32_t height = g_native ? ci->imageExtent.height : g_want_h;
        if (!width || !height || width > caps.maxImageExtent.width || height > caps.maxImageExtent.height ||
            width < caps.minImageExtent.width || height < caps.minImageExtent.height) {
            g_reason = "extent_out_of_range"; return false;
        }
    }
    if (!g_native) ci->imageExtent = {g_want_w, g_want_h};
    ci->imageUsage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    return true;
}

void sr_on_swapchain(VkDevice dev, VkSwapchainKHR sc, const VkSwapchainCreateInfoKHR* orig,
                     const VkSwapchainCreateInfoKHR* used, bool patched) {
    if (!g_on || !orig || !used) return;
    std::lock_guard<std::mutex> lk(g_m);
    Dev* D = dev_of_key(fkey(dev));
    if (!D) return;
    auto old = D->swaps.find(sc);
    if (old != D->swaps.end()) {
        if (!release_swap_locked(D, old->second)) return;
        D->swaps.erase(old);
    }
    Swap S;
    S.in_w = orig->imageExtent.width; S.in_h = orig->imageExtent.height;
    S.out_w = used->imageExtent.width; S.out_h = used->imageExtent.height;
    S.fmt = used->imageFormat;
    g_last_in_w = S.in_w; g_last_in_h = S.in_h; g_last_out_w = S.out_w; g_last_out_h = S.out_h;
    const VkImageUsageFlags transfer = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (g_native && (used->imageExtent.width != orig->imageExtent.width || used->imageExtent.height != orig->imageExtent.height ||
                     (used->imageUsage & transfer) != transfer || used->imageFormat != VK_FORMAT_R8G8B8A8_UNORM ||
                     used->imageSharingMode != VK_SHARING_MODE_EXCLUSIVE || used->flags || used->pNext ||
                     used->imageArrayLayers != 1 || !D->queue || D->family == UINT32_MAX || !D->complete)) {
        g_reason = "native_actual_swapchain_unsupported";
        patched = false;
    }
    if (!patched) {
        SR_LOG("swapchain %ux%u 未改 (原因: %s)", S.in_w, S.in_h, g_reason.empty() ? "same_size" : g_reason.c_str());
        D->swaps[sc] = S;
        return;
    }
    bool ok = ensure_pipeline(D);
    uint32_t n = 0;
    VkResult images_result = D->GetSwapchainImagesKHR(dev, sc, &n, nullptr);
    if (images_result != VK_SUCCESS || !n || n > 64) { ok = false; g_reason = "swapchain_image_count"; }
    if (ok) {
        S.imgs.resize(n);
        images_result = D->GetSwapchainImagesKHR(dev, sc, &n, S.imgs.data());
        if (images_result != VK_SUCCESS || !n || n > S.imgs.size()) { ok = false; g_reason = "swapchain_images"; }
        else S.imgs.resize(n);
    }
    if (!ok) n = 0;
    S.sems.assign(n, VK_NULL_HANDLE);
    for (uint32_t i = 0; !g_native && ok && i < n; i++) {
        VkSemaphoreCreateInfo sci{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
        if (D->CreateSemaphore(dev, &sci, nullptr, &S.sems[i]) != VK_SUCCESS) { ok = false; g_reason = "semaphore"; }
    }
    // T: 游戏画的那块 (采样读); O: 计算着色器写的整张 (storage, 再拷回 swapchain)。都用 UNORM: 拷贝是逐位的。
    const VkFormat F = VK_FORMAT_R8G8B8A8_UNORM;
    VkFormatProperties fp{};
    D->GetPhysicalDeviceFormatProperties(D->phys, F, &fp);
    if (ok && !(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT)) { ok = false; g_reason = "storage_unsupported"; }
    if (ok && !make_image(D, S.in_w, S.in_h, F, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT, &S.T, &S.T_mem, &S.T_view)) { ok = false; g_reason = "image_T"; }
    const bool rc = D->pipe_rcas != VK_NULL_HANDLE;
    if (ok && !make_image(D, S.out_w, S.out_h, F, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_SAMPLED_BIT, &S.O, &S.O_mem, &S.O_view)) { ok = false; g_reason = "image_O"; }
    if (ok && rc && !make_image(D, S.out_w, S.out_h, F, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT, &S.P, &S.P_mem, &S.P_view)) { ok = false; g_reason = "image_P"; }
    // set: T(采样) → O(写); set2: O(采样) → P(写)
    auto bind = [&](VkDescriptorSet* set, VkImageView in, VkImageView outv) {
        VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, nullptr, D->dpool, 1, &D->dsl};
        if (D->AllocateDescriptorSets(dev, &dai, set) != VK_SUCCESS) return false;
        VkDescriptorImageInfo ti{D->sampler, in, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
        VkDescriptorImageInfo oi{VK_NULL_HANDLE, outv, VK_IMAGE_LAYOUT_GENERAL};
        VkWriteDescriptorSet w[2] = {};
        w[0] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, *set, 0, 0, 1, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, &ti, nullptr, nullptr};
        w[1] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, *set, 1, 0, 1, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, &oi, nullptr, nullptr};
        D->UpdateDescriptorSets(dev, 2, w, 0, nullptr);
        return true;
    };
    if (ok && !bind(&S.set, S.T_view, S.O_view)) { ok = false; g_reason = "descriptor_set"; }
    if (ok && rc && !bind(&S.set2, S.O_view, S.P_view)) { ok = false; g_reason = "descriptor_set2"; }
    S.active = ok;
    SR_LOG("swapchain 游戏要 %ux%u → 实际 %ux%u fmt=%u images=%u active=%d%s%s", S.in_w, S.in_h, S.out_w, S.out_h,
           (unsigned)S.fmt, n, (int)ok, ok ? "" : " 原因=", ok ? "" : g_reason.c_str());
    D->swaps[sc] = std::move(S);
}

VkResult sr_on_present(VkQueue q, const VkPresentInfoKHR* pi, PFN_vkQueuePresentKHR next, uint64_t frame) {
    if (!g_on || !pi) return next(q, pi);
    const uint64_t t = now_ns();
    std::unique_lock<std::mutex> lk(g_m);
    auto bail = [&](const char* why) { g_passthrough++; g_fb[why]++; lk.unlock(); return next(q, pi); };
    Dev* D = dev_of_key(fkey(q));
    if (!D) return bail("device_unknown");
    if (g_native && q != D->queue) return bail("unverified_present_queue");
    if (D->terminal != VK_SUCCESS) return D->terminal;
    // Poll while disabled too, so a live toggle never hides completion evidence.
    for (auto& slot : D->slots) {
        VkResult result = complete_slot(D, slot, 0);
        if (result != VK_SUCCESS && result != VK_TIMEOUT) {
            failure("fence_status", result);
            D->terminal = result;
            return result;
        }
    }
    g_live = live_enabled();
    if (!g_live) return bail("live_off");
    if (pi->swapchainCount != 1 || !pi->pSwapchains || !pi->pImageIndices) return bail("multi_swapchain");
    if (g_native && pi->pNext) return bail("native_present_extensions");
    VkSwapchainKHR sc = pi->pSwapchains[0];
    auto si = D->swaps.find(sc);
    if (si == D->swaps.end()) return bail("swapchain_unknown");
    Swap& S = si->second;
    if (!S.active) {
        if (S.in_w == S.out_w && S.in_h == S.out_h) { g_same_size++; lk.unlock(); return next(q, pi); }
        return bail("swapchain_inactive");
    }
    uint32_t idx = pi->pImageIndices[0];
    if (idx >= S.imgs.size() || (!g_native && !S.sems[idx])) return bail("image_index");
    Slot& sl = D->slots[D->next_slot % 3];
    if (complete_slot(D, sl, 2000000ull) != VK_SUCCESS) return bail("slot_busy");
    auto before_submit_failure = [&](const char* why, VkResult result) {
        failure(why, result);
        if (result == VK_ERROR_DEVICE_LOST) { D->terminal = result; return result; }
        ++g_passthrough;
        lk.unlock();
        return next(q, pi);
    };
    VkImage N = S.imgs[idx];
    VkCommandBuffer cb = sl.cb;
    VkResult result = D->ResetCommandBuffer(cb, 0);
    if (result != VK_SUCCESS) return before_submit_failure("reset_command_buffer", result);
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, nullptr, VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, nullptr};
    result = D->BeginCommandBuffer(cb, &bi);
    if (result != VK_SUCCESS) return before_submit_failure("begin_command_buffer", result);
    const VkPipelineStageFlags TR = VK_PIPELINE_STAGE_TRANSFER_BIT, CS = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
    // 之前提交里对 T/O 的读写 (上一帧的超分) 全部完成, 才动它们
    VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr,
                       VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT,
                       VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT};
    D->CmdPipelineBarrier(cb, TR | CS, TR | CS, 0, 1, &mb, 0, nullptr, 0, nullptr);
    barrier(D, cb, N, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, 0, VK_ACCESS_TRANSFER_READ_BIT, TR, TR);
    barrier(D, cb, S.T, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT, TR, TR);
    VkImageCopy rg{};
    rg.srcSubresource = rg.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    rg.extent = {S.in_w, S.in_h, 1};
    D->CmdCopyImage(cb, N, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, S.T, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &rg);
    barrier(D, cb, S.T, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, TR, CS);
    barrier(D, cb, S.O, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, VK_ACCESS_SHADER_WRITE_BIT, TR, CS);
    D->CmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, D->pipe);
    D->CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, D->pl, 0, 1, &S.set, 0, nullptr);
    int32_t pc[4] = {(int32_t)S.in_w, (int32_t)S.in_h, (int32_t)S.out_w, (int32_t)S.out_h};
    D->CmdPushConstants(cb, D->pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc, pc);
    D->CmdDispatch(cb, (S.out_w + 15) / 16, (S.out_h + 15) / 16, 1);
    VkImage R = S.O;   // 拷回 swapchain 的那张
    if (S.set2) {
        barrier(D, cb, S.O, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, CS, CS);
        barrier(D, cb, S.P, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, VK_ACCESS_SHADER_WRITE_BIT, CS, CS);
        D->CmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, D->pipe_rcas);
        D->CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, D->pl, 0, 1, &S.set2, 0, nullptr);
        struct { int32_t w, h; float sharp; int32_t pad; } pc2{(int32_t)S.out_w, (int32_t)S.out_h, g_sharp, 0};
        D->CmdPushConstants(cb, D->pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc2, &pc2);
        D->CmdDispatch(cb, (S.out_w + 15) / 16, (S.out_h + 15) / 16, 1);
        R = S.P;
    }
    barrier(D, cb, R, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, CS, TR);
    barrier(D, cb, N, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_ACCESS_TRANSFER_READ_BIT, VK_ACCESS_TRANSFER_WRITE_BIT, TR, TR);
    VkImageCopy full{};
    full.srcSubresource = full.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    full.extent = {S.out_w, S.out_h, 1};
    D->CmdCopyImage(cb, R, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, N, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &full);
    barrier(D, cb, N, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, VK_ACCESS_TRANSFER_WRITE_BIT, 0, TR, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT);
    result = D->EndCommandBuffer(cb);
    if (result != VK_SUCCESS) return before_submit_failure("end_command_buffer", result);
    std::vector<VkPipelineStageFlags> stages(pi->waitSemaphoreCount, TR);
    VkSubmitInfo sub{VK_STRUCTURE_TYPE_SUBMIT_INFO};
    sub.waitSemaphoreCount = pi->waitSemaphoreCount; sub.pWaitSemaphores = pi->pWaitSemaphores; sub.pWaitDstStageMask = stages.data();
    sub.commandBufferCount = 1; sub.pCommandBuffers = &cb;
    sub.signalSemaphoreCount = g_native ? 0 : 1;
    sub.pSignalSemaphores = g_native ? nullptr : &S.sems[idx];
    result = D->ResetFences(D->dev, 1, &sl.fence);
    if (result != VK_SUCCESS) return before_submit_failure("reset_fence", result);
    result = D->QueueSubmit(q, 1, &sub, sl.fence);
    if (result != VK_SUCCESS) {
        // Never retry presentation with waits belonging to a failed submission.
        failure("submit_failed", result);
        D->terminal = result;
        D->submission_uncertain = result != VK_ERROR_OUT_OF_HOST_MEMORY && result != VK_ERROR_OUT_OF_DEVICE_MEMORY;
        return result;
    }
    sl.busy = true;
    D->next_slot++;
    S.frames++;
    g_up++;
    g_dispatches += S.set2 ? 2 : 1;
    if (g_native) {
        // This experimental path deliberately stalls the host, avoiding present
        // semaphore lifetime assumptions. It is not a performance benchmark.
        result = complete_slot(D, sl, 1000000000ull);
        if (result != VK_SUCCESS) {
            failure("submission_completion_failed", result);
            // A timeout is not device loss. Ask the application to recreate its
            // swapchain; keep the old image and all layer resources until done.
            D->terminal = result == VK_TIMEOUT ? VK_ERROR_OUT_OF_DATE_KHR : result;
            return D->terminal;
        }
    }
    if (g_cpu_us.size() < 1024) g_cpu_us.push_back((now_ns() - t) / 1000);
    else g_cpu_us[g_up % 1024] = (now_ns() - t) / 1000;
    VkSemaphore sem = g_native ? VK_NULL_HANDLE : S.sems[idx];
    lk.unlock();
    VkPresentInfoKHR pn = *pi;
    pn.waitSemaphoreCount = g_native ? 0 : 1;
    pn.pWaitSemaphores = g_native ? nullptr : &sem;
    (void)frame;
    result = next(q, &pn);
    lk.lock();
    VkResult per_swap = pn.pResults ? pn.pResults[0] : result;
    if ((result == VK_SUCCESS || result == VK_SUBOPTIMAL_KHR) &&
        (per_swap == VK_SUCCESS || per_swap == VK_SUBOPTIMAL_KHR)) ++g_presents;
    else failure("present_failed", result != VK_SUCCESS ? result : per_swap);
    return result;
}

std::string sr_json() {
    std::lock_guard<std::mutex> lk(g_m);
    std::string fb;
    for (auto& kv : g_fb) fb += (fb.empty() ? "" : ",") + ("\"" + kv.first + "\":" + std::to_string(kv.second));
    std::vector<uint64_t> c = g_cpu_us;
    std::sort(c.begin(), c.end());
    auto pct = [&](double p) -> unsigned long long { return c.empty() ? 0 : c[(size_t)(p * (c.size() - 1) + 0.5)]; };
    char b[640];
    snprintf(b, sizeof b,
             "{\"want\":[%u,%u],\"sharp\":%.2f,\"game_extent\":[%u,%u],\"swap_extent\":[%u,%u],\"upscaled\":%llu,\"same_size\":%llu,"
             "\"passthrough\":%llu,\"reason\":\"%s\",\"cpu_us_p50\":%llu,\"cpu_us_p95\":%llu,\"fallbacks\":{",
             g_want_w, g_want_h, g_sharp, g_last_in_w, g_last_in_h, g_last_out_w, g_last_out_h, (unsigned long long)g_up,
             (unsigned long long)g_same_size, (unsigned long long)g_passthrough, g_reason.c_str(), pct(0.5), pct(0.95));
    std::string json = std::string(b) + fb + "}";
    json += std::string(",\"mode\":\"") + (g_native ? "native" : "resize") + "\"";
    json += std::string(",\"live\":") + (g_live ? "true" : "false");
    json += ",\"scope\":\"experimental_present_postprocessing\",\"display_completion_proven\":false";
    json += ",\"dispatches_accepted\":" + std::to_string(g_dispatches);
    json += ",\"submissions_accepted\":" + std::to_string(g_up);
    json += ",\"submissions_completed\":" + std::to_string(g_completed);
    json += ",\"presents_succeeded\":" + std::to_string(g_presents);
    json += ",\"last_result\":" + std::to_string(static_cast<int>(g_last_result));
    if (g_native || strlen(kSelfsrCandidateSourceSha256) == 64) {
        json += std::string(",\"candidate_source_sha256\":\"") + kSelfsrCandidateSourceSha256 + "\"";
        json += std::string(",\"candidate_spirv_sha256\":\"") + kSelfsrCandidateSpirvSha256 + "\"";
        json += std::string(",\"candidate_kind\":\"") + kSelfsrCandidateKind + "\"";
    }
    return json + "}";
}
