// These tests execute production hooks; the driver records calls without a GPU.
#include "selfsr.cpp"
#include <cassert>
#include <set>

namespace mock {
static std::string mode = "native", live;
static void* word = reinterpret_cast<void*>(0x1234);
static void* queue_word = word;
static void* other_queue_word = word;
static VkDevice device = reinterpret_cast<VkDevice>(&word);
static VkQueue queue = reinterpret_cast<VkQueue>(&queue_word);
static VkQueue other_queue = reinterpret_cast<VkQueue>(&other_queue_word);
template<class T> T handle(uintptr_t value) { return reinterpret_cast<T>(value); }
static uintptr_t serial = 100;
static std::set<uintptr_t> objects;
template<class T> VkResult create(T* out) {
    *out = handle<T>(++serial);
    objects.insert(serial);
    return VK_SUCCESS;
}
template<class T> void destroy(T object) {
    assert(objects.erase(reinterpret_cast<uintptr_t>(object)) == 1);
}
static VkResult reset_result = VK_SUCCESS, begin_result = VK_SUCCESS, end_result = VK_SUCCESS;
static VkResult fence_reset_result = VK_SUCCESS, submit_result = VK_SUCCESS, wait_result = VK_SUCCESS;
static VkResult present_result = VK_SUCCESS, caps_result = VK_SUCCESS;
static VkQueueFlags queue_flags = VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT;
static VkFormatFeatureFlags format_flags = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT |
    VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
static VkImageUsageFlags surface_usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
static uint32_t submits = 0, presents = 0, dispatches = 0, destroys = 0, copies = 0;
static uint32_t signal_count = 99, present_waits = 99;
static VkSemaphore game_wait = handle<VkSemaphore>(7);
static bool fence_pending = false;
static VkResult VKAPI_CALL loader(VkDevice, void*) { return VK_SUCCESS; }
static void VKAPI_CALL families(VkPhysicalDevice, uint32_t* n, VkQueueFamilyProperties* p) {
    if (p) { p[0] = {}; p[0].queueFlags = queue_flags; p[0].queueCount = 1; }
    *n = 1;
}
static void VKAPI_CALL formats(VkPhysicalDevice, VkFormat, VkFormatProperties* p) {
    *p = {}; p->optimalTilingFeatures = format_flags;
}
static VkResult VKAPI_CALL caps(VkPhysicalDevice, VkSurfaceKHR, VkSurfaceCapabilitiesKHR* p) {
    *p = {}; p->supportedUsageFlags = surface_usage;
    p->minImageExtent = {1, 1}; p->maxImageExtent = {4096, 4096};
    return caps_result;
}
static void VKAPI_CALL memory_props(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties* p) {
    *p = {}; p->memoryTypeCount = 1; p->memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
}
#define FACTORY(name, Info, Type) \
static VkResult VKAPI_CALL name(VkDevice, const Info*, const VkAllocationCallbacks*, Type* out) { return create(out); }
#define DESTROY(name, Type) \
static void VKAPI_CALL name(VkDevice, Type object, const VkAllocationCallbacks*) { assert(!fence_pending); destroy(object); }
FACTORY(create_pool, VkCommandPoolCreateInfo, VkCommandPool)
FACTORY(create_fence, VkFenceCreateInfo, VkFence)
FACTORY(create_sem, VkSemaphoreCreateInfo, VkSemaphore)
FACTORY(create_sampler, VkSamplerCreateInfo, VkSampler)
FACTORY(create_dsl, VkDescriptorSetLayoutCreateInfo, VkDescriptorSetLayout)
FACTORY(create_dpool, VkDescriptorPoolCreateInfo, VkDescriptorPool)
FACTORY(create_pl, VkPipelineLayoutCreateInfo, VkPipelineLayout)
FACTORY(create_shader, VkShaderModuleCreateInfo, VkShaderModule)
FACTORY(create_image, VkImageCreateInfo, VkImage)
FACTORY(create_view, VkImageViewCreateInfo, VkImageView)
FACTORY(allocate_memory, VkMemoryAllocateInfo, VkDeviceMemory)
DESTROY(destroy_pool, VkCommandPool)
DESTROY(destroy_fence, VkFence)
DESTROY(destroy_sem, VkSemaphore)
DESTROY(destroy_sampler, VkSampler)
DESTROY(destroy_dsl, VkDescriptorSetLayout)
DESTROY(destroy_dpool, VkDescriptorPool)
DESTROY(destroy_pl, VkPipelineLayout)
DESTROY(destroy_shader, VkShaderModule)
DESTROY(destroy_pipeline, VkPipeline)
DESTROY(destroy_image, VkImage)
DESTROY(destroy_view, VkImageView)
DESTROY(free_memory, VkDeviceMemory)
#undef FACTORY
#undef DESTROY
static VkResult VKAPI_CALL allocate_cbs(VkDevice, const VkCommandBufferAllocateInfo*, VkCommandBuffer* p) {
    *p = handle<VkCommandBuffer>(++serial); return VK_SUCCESS;
}
static VkResult VKAPI_CALL allocate_sets(VkDevice, const VkDescriptorSetAllocateInfo*, VkDescriptorSet* p) { return create(p); }
static VkResult VKAPI_CALL free_sets(VkDevice, VkDescriptorPool, uint32_t n, const VkDescriptorSet* p) {
    assert(!fence_pending); for (uint32_t i = 0; i < n; ++i) destroy(p[i]); return VK_SUCCESS;
}
static VkResult VKAPI_CALL pipeline(VkDevice, VkPipelineCache, uint32_t, const VkComputePipelineCreateInfo*,
                                   const VkAllocationCallbacks*, VkPipeline* p) { return create(p); }
static void VKAPI_CALL requirements(VkDevice, VkImage, VkMemoryRequirements* p) { *p = {4096, 16, 1}; }
static VkResult VKAPI_CALL bind_memory(VkDevice, VkImage, VkDeviceMemory, VkDeviceSize) { return VK_SUCCESS; }
static void VKAPI_CALL update(VkDevice, uint32_t n, const VkWriteDescriptorSet* w, uint32_t, const VkCopyDescriptorSet*) {
    assert(n == 2 && w[0].descriptorType == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER);
    assert(w[0].pImageInfo->imageLayout == VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    assert(w[1].descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE && w[1].pImageInfo->imageLayout == VK_IMAGE_LAYOUT_GENERAL);
}
static VkResult VKAPI_CALL images(VkDevice, VkSwapchainKHR, uint32_t* n, VkImage* p) {
    if (p) { p[0] = handle<VkImage>(10); p[1] = handle<VkImage>(11); } *n = 2; return VK_SUCCESS;
}
static void VKAPI_CALL destroy_swap(VkDevice, VkSwapchainKHR, const VkAllocationCallbacks*) {
    assert(!fence_pending); ++destroys;
}
static VkResult VKAPI_CALL reset(VkCommandBuffer, VkCommandBufferResetFlags) { return reset_result; }
static VkResult VKAPI_CALL begin(VkCommandBuffer, const VkCommandBufferBeginInfo*) { return begin_result; }
static VkResult VKAPI_CALL end(VkCommandBuffer) { return end_result; }
static VkResult VKAPI_CALL reset_fences(VkDevice, uint32_t, const VkFence*) { assert(!fence_pending); return fence_reset_result; }
static VkResult VKAPI_CALL wait(VkDevice, uint32_t, const VkFence*, VkBool32, uint64_t) {
    if (wait_result == VK_SUCCESS) fence_pending = false;
    return wait_result;
}
static VkResult VKAPI_CALL submit(VkQueue q, uint32_t, const VkSubmitInfo* p, VkFence) {
    assert(q == queue && p->waitSemaphoreCount == 1 && p->pWaitSemaphores[0] == game_wait);
    ++submits; signal_count = p->signalSemaphoreCount;
    if (submit_result == VK_SUCCESS) fence_pending = true;
    return submit_result;
}
static VkResult VKAPI_CALL present(VkQueue, const VkPresentInfoKHR* p) {
    ++presents; present_waits = p->waitSemaphoreCount;
    if (mode == "native" && present_waits == 0) assert(!fence_pending);
    return present_result;
}
static void VKAPI_CALL barrier(VkCommandBuffer, VkPipelineStageFlags, VkPipelineStageFlags, VkDependencyFlags,
    uint32_t, const VkMemoryBarrier*, uint32_t, const VkBufferMemoryBarrier*, uint32_t, const VkImageMemoryBarrier*) {}
static void VKAPI_CALL copy(VkCommandBuffer, VkImage, VkImageLayout, VkImage, VkImageLayout, uint32_t, const VkImageCopy* p) {
    assert(p->extent.width && p->extent.height); ++copies;
}
static void VKAPI_CALL bind_pipeline(VkCommandBuffer, VkPipelineBindPoint, VkPipeline) {}
static void VKAPI_CALL bind_sets(VkCommandBuffer, VkPipelineBindPoint, VkPipelineLayout, uint32_t, uint32_t,
                                const VkDescriptorSet*, uint32_t, const uint32_t*) {}
static void VKAPI_CALL push(VkCommandBuffer, VkPipelineLayout, VkShaderStageFlags, uint32_t, uint32_t, const void*) {}
static void VKAPI_CALL dispatch(VkCommandBuffer, uint32_t x, uint32_t y, uint32_t z) {
    assert(x == (mode == "native" ? 134u : 175u) && y == (mode == "native" ? 61u : 80u) && z == 1); ++dispatches;
}
static PFN_vkVoidFunction VKAPI_CALL gipa(VkInstance, const char* name) {
#define FN(api, fn) if (!strcmp(name, "vk" #api)) return reinterpret_cast<PFN_vkVoidFunction>(fn)
    FN(GetPhysicalDeviceQueueFamilyProperties, families); FN(GetPhysicalDeviceFormatProperties, formats);
    FN(GetPhysicalDeviceSurfaceCapabilitiesKHR, caps); FN(GetPhysicalDeviceMemoryProperties, memory_props);
    return nullptr;
}
static PFN_vkVoidFunction VKAPI_CALL gdpa(VkDevice, const char* name) {
    FN(CreateCommandPool, create_pool); FN(DestroyCommandPool, destroy_pool); FN(AllocateCommandBuffers, allocate_cbs);
    FN(CreateFence, create_fence); FN(DestroyFence, destroy_fence); FN(CreateSemaphore, create_sem); FN(DestroySemaphore, destroy_sem);
    FN(CreateSampler, create_sampler); FN(DestroySampler, destroy_sampler); FN(CreateDescriptorSetLayout, create_dsl);
    FN(DestroyDescriptorSetLayout, destroy_dsl); FN(CreateDescriptorPool, create_dpool); FN(DestroyDescriptorPool, destroy_dpool);
    FN(CreatePipelineLayout, create_pl); FN(DestroyPipelineLayout, destroy_pl); FN(CreateShaderModule, create_shader);
    FN(DestroyShaderModule, destroy_shader); FN(CreateComputePipelines, pipeline); FN(DestroyPipeline, destroy_pipeline);
    FN(CreateImage, create_image); FN(DestroyImage, destroy_image); FN(CreateImageView, create_view); FN(DestroyImageView, destroy_view);
    FN(AllocateMemory, allocate_memory); FN(FreeMemory, free_memory); FN(GetImageMemoryRequirements, requirements);
    FN(BindImageMemory, bind_memory); FN(AllocateDescriptorSets, allocate_sets); FN(FreeDescriptorSets, free_sets);
    FN(UpdateDescriptorSets, update); FN(GetSwapchainImagesKHR, images); FN(DestroySwapchainKHR, destroy_swap);
    FN(ResetCommandBuffer, reset); FN(BeginCommandBuffer, begin); FN(EndCommandBuffer, end); FN(ResetFences, reset_fences);
    FN(WaitForFences, wait); FN(QueueSubmit, submit); FN(CmdPipelineBarrier, barrier); FN(CmdCopyImage, copy);
    FN(CmdBindPipeline, bind_pipeline); FN(CmdBindDescriptorSets, bind_sets); FN(CmdPushConstants, push); FN(CmdDispatch, dispatch);
    return nullptr;
#undef FN
}
}

int __system_property_get(const char* name, char* value) {
    const std::string& text = !strcmp(name, "debug.knobs.selfsr") ? mock::mode : mock::live;
    strcpy(value, text.c_str()); return static_cast<int>(text.size());
}
PFN_vkVoidFunction ip_hook(const char*) { return nullptr; }

int main(int argc, char** argv) {
    assert(argc == 2);
    using namespace mock;
    std::string test = argv[1];
    assert(g_dispatches == 0 && g_presents == 0);
    if (test == "legacy") mode = "2800x1272";
    assert(sr_init_from_props());
    if (test == "family") queue_flags = VK_QUEUE_GRAPHICS_BIT;
    sr_on_device(handle<VkInstance>(1), handle<VkPhysicalDevice>(2), device, gipa, gdpa, loader,
                 test == "queue" ? UINT32_MAX : 0, test == "queue" ? VK_NULL_HANDLE : queue);
    VkSwapchainCreateInfoKHR ci{VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR};
    ci.imageExtent = {2136, 971}; ci.imageFormat = VK_FORMAT_R8G8B8A8_UNORM;
    ci.imageArrayLayers = 1; ci.imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    ci.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE; ci.presentMode = VK_PRESENT_MODE_FIFO_KHR;
    if (test == "format") ci.imageFormat = VK_FORMAT_B8G8R8A8_UNORM;
    if (test == "protected") ci.flags = VK_SWAPCHAIN_CREATE_PROTECTED_BIT_KHR;
    if (test == "shared") ci.imageSharingMode = VK_SHARING_MODE_CONCURRENT;
    if (test == "usage") surface_usage = 0;
    if (test == "caps") caps_result = VK_ERROR_SURFACE_LOST_KHR;
    auto original = ci;
    bool patched = sr_patch_swapchain_ci(device, &ci);
    if (test == "queue" || test == "family" || test == "format" || test == "protected" ||
        test == "shared" || test == "usage" || test == "caps") {
        assert(!patched && ci.imageExtent.width == 2136 && ci.imageUsage == original.imageUsage);
        assert(sr_on_destroy_device(device)); return 0;
    }
    assert(patched && ci.imageExtent.width == (mode == "native" ? 2136u : 2800u));
    auto sc = handle<VkSwapchainKHR>(20);
    sr_on_swapchain(device, sc, &original, &ci, patched);
    assert(g_devs.at(word)->swaps.at(sc).active);
    uint32_t index = 0;
    VkPresentInfoKHR pi{VK_STRUCTURE_TYPE_PRESENT_INFO_KHR};
    pi.swapchainCount = 1; pi.pSwapchains = &sc; pi.pImageIndices = &index;
    pi.waitSemaphoreCount = 1; pi.pWaitSemaphores = &game_wait;
    if (test == "multi") {
        live = "1"; pi.swapchainCount = 2;
        assert(sr_on_present(queue, &pi, present, 1) == VK_SUCCESS && submits == 0);
    } else if (test == "live") {
        assert(sr_on_present(queue, &pi, present, 1) == VK_SUCCESS && submits == 0 && present_waits == 1);
        live = "1";
        assert(sr_on_present(other_queue, &pi, present, 2) == VK_SUCCESS && submits == 0);
        assert(sr_on_present(queue, &pi, present, 3) == VK_SUCCESS && submits == 1 && present_waits == 0);
        live = "0";
        assert(sr_on_present(queue, &pi, present, 4) == VK_SUCCESS && submits == 1 && present_waits == 1);
    } else {
        live = "1";
        if (test == "reset") reset_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        if (test == "begin") begin_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        if (test == "end") end_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        if (test == "reset_fence") fence_reset_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        if (test == "submit") submit_result = VK_ERROR_DEVICE_LOST;
        if (test == "submit_oom") submit_result = VK_ERROR_OUT_OF_DEVICE_MEMORY;
        if (test == "fence_timeout" || test == "cleanup") wait_result = VK_TIMEOUT;
        if (test == "present") present_result = VK_ERROR_OUT_OF_DATE_KHR;
        VkResult result = sr_on_present(queue, &pi, present, 1);
        if (test == "reset" || test == "begin" || test == "end" || test == "reset_fence") {
            assert(result == VK_SUCCESS && submits == 0 && presents == 1 && present_waits == 1);
        } else if (test == "submit" || test == "submit_oom") {
            assert(result == submit_result && submits == 1 && presents == 0);
            assert(sr_on_present(queue, &pi, present, 2) == submit_result && submits == 1 && presents == 0);
            if (test == "submit") { assert(!sr_on_destroy_device(device)); return 0; }
        } else if (test == "fence_timeout" || test == "cleanup") {
            assert(result < 0 && submits == 1 && presents == 0 && fence_pending);
            auto held = objects.size();
            sr_DestroySwapchainKHR(device, sc, nullptr);
            assert(objects.size() == held && destroys == 0 && fence_pending);
            assert(!sr_on_destroy_device(device) && objects.size() == held);
            wait_result = VK_SUCCESS;
            assert(sr_on_destroy_device(device) && objects.empty() && destroys == 1);
            return 0;
        } else if (test == "legacy") {
            assert(result == VK_SUCCESS && submits == 1 && signal_count == 1 && present_waits == 1);
        } else {
            assert(result == present_result && submits == 1 && signal_count == 0 && present_waits == 0);
            auto marker = sr_json();
            assert(marker.find("\"dispatches_accepted\":1") != std::string::npos);
            assert(marker.find("\"submissions_completed\":1") != std::string::npos);
            assert(marker.find(test == "present" ? "\"presents_succeeded\":0" : "\"presents_succeeded\":1") != std::string::npos);
            assert(marker.find("adapted-fixture") != std::string::npos);
        }
    }
    assert(sr_on_destroy_device(device));
    assert(objects.empty());
}
