// Actual intercepted hooks with a deterministic lower Vulkan driver fixture.
// No Android runtime/GPU; the fixture attests identities only in host builds.
#include "../layer/vk_layer_refknobs.cpp"
#include <algorithm>
#include <cassert>
#include <deque>
template <typename T> static T h(uintptr_t n) { return reinterpret_cast<T>(n); }
static void *dispatch_key = h<void *>(0x12345);
static void *device_word = dispatch_key;
static void *queue_word = dispatch_key;
static VkDevice device = reinterpret_cast<VkDevice>(&device_word);
static VkQueue queue = reinterpret_cast<VkQueue>(&queue_word);
static uintptr_t serial = 100;
static std::deque<void *> cb_words;
static VkResult callback_result = VK_SUCCESS;
static std::string fail;
static uint32_t cases = 0, forwards = 0, dispatch_count = 0;
static VkDeviceSize requirement = 4096;
static VkSampler reuse_sampler = VK_NULL_HANDLE;
static VkCommandBuffer reuse_cb = VK_NULL_HANDLE;
static std::set<uintptr_t> live;
using Element = std::pair<uint32_t, uint32_t>;
static std::map<VkDescriptorSet, std::map<Element, VkDescriptorImageInfo>> descriptors;
static std::set<VkDescriptorSet> frozen;
static std::map<VkDescriptorPool, std::vector<VkDescriptorSet>> pool_sets;
static std::map<VkCommandBuffer, VkDescriptorSet> compute_bound;
static std::map<VkCommandBuffer, VkDescriptorSet> graphics_bound;
static bool failure(const char *n) {
    if (fail == n) {
        fail.clear();
        return true;
    }
    return false;
}
template <typename T> static T create_handle() {
    auto out = h<T>(++serial);
    live.insert(serial);
    return out;
}
template <typename T> static void destroy_handle(T v) {
    assert(v && live.erase(reinterpret_cast<uintptr_t>(v)) == 1);
}
static VkResult VKAPI_CALL begin(VkCommandBuffer, const VkCommandBufferBeginInfo *) {
    ++forwards;
    return callback_result;
}
static VkResult VKAPI_CALL end_cb(VkCommandBuffer) {
    ++forwards;
    return callback_result;
}
static VkResult VKAPI_CALL reset(VkCommandBuffer, VkCommandBufferResetFlags) {
    ++forwards;
    return callback_result;
}
static VkResult VKAPI_CALL reset_pool(VkDevice, VkCommandPool, VkCommandPoolResetFlags) {
    ++forwards;
    return callback_result;
}
static void VKAPI_CALL free_cbs(VkDevice, VkCommandPool, uint32_t, const VkCommandBuffer *) {
    ++forwards;
}
static void VKAPI_CALL destroy_command_pool(VkDevice, VkCommandPool p,
                                            const VkAllocationCallbacks *) {
    ++forwards;
    destroy_handle(p);
}
static VkResult VKAPI_CALL create_command_pool(VkDevice, const VkCommandPoolCreateInfo *,
                                               const VkAllocationCallbacks *, VkCommandPool *out) {
    *out = create_handle<VkCommandPool>();
    return VK_SUCCESS;
}
static VkResult VKAPI_CALL allocate_cbs(VkDevice, const VkCommandBufferAllocateInfo *ci,
                                        VkCommandBuffer *out) {
    for (uint32_t i = 0; i < ci->commandBufferCount; ++i) {
        if (reuse_cb) {
            out[i] = reuse_cb;
            reuse_cb = VK_NULL_HANDLE;
            continue;
        }
        cb_words.push_back(dispatch_key);
        out[i] = reinterpret_cast<VkCommandBuffer>(&cb_words.back());
    }
    return VK_SUCCESS;
}
static VkResult VKAPI_CALL submit(VkQueue, uint32_t, const VkSubmitInfo *, VkFence) {
    ++forwards;
    return callback_result;
}
static void VKAPI_CALL execute(VkCommandBuffer, uint32_t, const VkCommandBuffer *) { ++forwards; }
static VkResult VKAPI_CALL shader(VkDevice, const VkShaderModuleCreateInfo *,
                                  const VkAllocationCallbacks *, VkShaderModule *out) {
    if (failure("shader")) {
        *out = h<VkShaderModule>(0xdeadbeef);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    *out = create_handle<VkShaderModule>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_shader(VkDevice, VkShaderModule v, const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL layout(VkDevice, const VkDescriptorSetLayoutCreateInfo *,
                                  const VkAllocationCallbacks *, VkDescriptorSetLayout *out) {
    if (failure("layout")) {
        *out = h<VkDescriptorSetLayout>(0xdeadbeef);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    *out = create_handle<VkDescriptorSetLayout>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_layout(VkDevice, VkDescriptorSetLayout v,
                                      const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL pipeline_layout(VkDevice, const VkPipelineLayoutCreateInfo *,
                                           const VkAllocationCallbacks *, VkPipelineLayout *out) {
    if (failure("pipeline_layout")) {
        *out = h<VkPipelineLayout>(0xdeadbeef);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    *out = create_handle<VkPipelineLayout>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_pipeline_layout(VkDevice, VkPipelineLayout v,
                                               const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL pipeline(VkDevice, VkPipelineCache, uint32_t,
                                    const VkComputePipelineCreateInfo *,
                                    const VkAllocationCallbacks *, VkPipeline *out) {
    if (failure("pipeline_partial")) {
        *out = create_handle<VkPipeline>();
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    if (failure("pipeline"))
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    *out = create_handle<VkPipeline>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_pipeline(VkDevice, VkPipeline v, const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL pool(VkDevice, const VkDescriptorPoolCreateInfo *,
                                const VkAllocationCallbacks *, VkDescriptorPool *out) {
    if (failure("pool")) {
        *out = h<VkDescriptorPool>(0xdeadbeef);
        return VK_ERROR_OUT_OF_POOL_MEMORY;
    }
    *out = create_handle<VkDescriptorPool>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_pool(VkDevice, VkDescriptorPool p, const VkAllocationCallbacks *) {
    for (auto s : pool_sets[p]) {
        frozen.erase(s);
        descriptors.erase(s);
        destroy_handle(s);
    }
    pool_sets.erase(p);
    destroy_handle(p);
}
static VkResult VKAPI_CALL alloc_sets(VkDevice, const VkDescriptorSetAllocateInfo *ai,
                                      VkDescriptorSet *out) {
    if (failure("set")) {
        *out = h<VkDescriptorSet>(0xdeadbeef);
        return VK_ERROR_OUT_OF_POOL_MEMORY;
    }
    for (uint32_t i = 0; i < ai->descriptorSetCount; ++i) {
        out[i] = create_handle<VkDescriptorSet>();
        pool_sets[ai->descriptorPool].push_back(out[i]);
    }
    return VK_SUCCESS;
}
static VkResult VKAPI_CALL free_sets(VkDevice, VkDescriptorPool p, uint32_t n,
                                     const VkDescriptorSet *set) {
    if (callback_result != VK_SUCCESS)
        return callback_result;
    for (uint32_t i = 0; i < n; ++i) {
        assert(!frozen.count(set[i]));
        auto &v = pool_sets[p];
        v.erase(std::remove(v.begin(), v.end(), set[i]), v.end());
        descriptors.erase(set[i]);
        destroy_handle(set[i]);
    }
    return VK_SUCCESS;
}
static VkResult VKAPI_CALL reset_descriptor_pool(VkDevice, VkDescriptorPool p,
                                                 VkDescriptorPoolResetFlags) {
    if (callback_result != VK_SUCCESS)
        return callback_result;
    for (auto s : pool_sets[p]) {
        assert(!frozen.count(s));
        descriptors.erase(s);
        destroy_handle(s);
    }
    pool_sets[p].clear();
    return VK_SUCCESS;
}
static void VKAPI_CALL update(VkDevice, uint32_t nw, const VkWriteDescriptorSet *w, uint32_t nc,
                              const VkCopyDescriptorSet *c) {
    ++forwards;
    for (uint32_t i = 0; i < nc; ++i) {
        assert(!frozen.count(c[i].dstSet));
        for (uint32_t j = 0; j < c[i].descriptorCount; ++j)
            descriptors[c[i].dstSet][{c[i].dstBinding, c[i].dstArrayElement + j}] =
                descriptors[c[i].srcSet][{c[i].srcBinding, c[i].srcArrayElement + j}];
    }
    for (uint32_t i = 0; i < nw; ++i) {
        assert(!frozen.count(w[i].dstSet));
        if (w[i].pImageInfo)
            for (uint32_t j = 0; j < w[i].descriptorCount; ++j)
                descriptors[w[i].dstSet][{w[i].dstBinding, w[i].dstArrayElement + j}] =
                    w[i].pImageInfo[j];
    }
}
static VkResult VKAPI_CALL image(VkDevice, const VkImageCreateInfo *, const VkAllocationCallbacks *,
                                 VkImage *out) {
    if (failure("image")) {
        *out = h<VkImage>(0xdeadbeef);
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    *out = create_handle<VkImage>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_image(VkDevice, VkImage v, const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL view(VkDevice, const VkImageViewCreateInfo *,
                                const VkAllocationCallbacks *, VkImageView *out) {
    if (failure("view")) {
        *out = h<VkImageView>(0xdeadbeef);
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
    *out = create_handle<VkImageView>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_view(VkDevice, VkImageView v, const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static void VKAPI_CALL memory_requirements(VkDevice, VkImage, VkMemoryRequirements *out) {
    out->size = requirement;
    out->memoryTypeBits = 1;
    out->alignment = 16;
}
static PFN_vkVoidFunction VKAPI_CALL gdpa(VkDevice, const char *name) {
    return !strcmp(name, "vkGetImageMemoryRequirements")
               ? reinterpret_cast<PFN_vkVoidFunction>(memory_requirements)
               : nullptr;
}
static void VKAPI_CALL memory_properties(VkPhysicalDevice, VkPhysicalDeviceMemoryProperties *out) {
    out->memoryTypeCount = 1;
    out->memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
}
static VkResult VKAPI_CALL allocate_memory(VkDevice, const VkMemoryAllocateInfo *,
                                           const VkAllocationCallbacks *, VkDeviceMemory *out) {
    if (failure("memory")) {
        *out = h<VkDeviceMemory>(0xdeadbeef);
        return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    }
    *out = create_handle<VkDeviceMemory>();
    return VK_SUCCESS;
}
static void VKAPI_CALL free_memory(VkDevice, VkDeviceMemory v, const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL bind_memory(VkDevice, VkImage, VkDeviceMemory, VkDeviceSize) {
    return failure("bind") ? VK_ERROR_OUT_OF_DEVICE_MEMORY : VK_SUCCESS;
}
static VkResult VKAPI_CALL sampler(VkDevice, const VkSamplerCreateInfo *,
                                   const VkAllocationCallbacks *, VkSampler *out) {
    if (reuse_sampler) {
        *out = reuse_sampler;
        live.insert(reinterpret_cast<uintptr_t>(*out));
        reuse_sampler = VK_NULL_HANDLE;
    } else
        *out = create_handle<VkSampler>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_sampler(VkDevice, VkSampler v, const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL render_pass(VkDevice, const VkRenderPassCreateInfo *,
                                       const VkAllocationCallbacks *, VkRenderPass *out) {
    *out = create_handle<VkRenderPass>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_render_pass(VkDevice, VkRenderPass v,
                                           const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static VkResult VKAPI_CALL framebuffer(VkDevice, const VkFramebufferCreateInfo *,
                                       const VkAllocationCallbacks *, VkFramebuffer *out) {
    *out = create_handle<VkFramebuffer>();
    return VK_SUCCESS;
}
static void VKAPI_CALL destroy_framebuffer(VkDevice, VkFramebuffer v,
                                           const VkAllocationCallbacks *) {
    destroy_handle(v);
}
static void VKAPI_CALL end_pass(VkCommandBuffer) { ++forwards; }
static void VKAPI_CALL begin_pass(VkCommandBuffer, const VkRenderPassBeginInfo *,
                                  VkSubpassContents) {
    ++forwards;
}
static void VKAPI_CALL barrier(VkCommandBuffer, VkPipelineStageFlags, VkPipelineStageFlags,
                               VkDependencyFlags, uint32_t, const VkMemoryBarrier *, uint32_t,
                               const VkBufferMemoryBarrier *, uint32_t,
                               const VkImageMemoryBarrier *) {}
static void VKAPI_CALL bind_pipeline(VkCommandBuffer, VkPipelineBindPoint, VkPipeline) {
    ++forwards;
}
static void VKAPI_CALL bind_sets(VkCommandBuffer cb, VkPipelineBindPoint point, VkPipelineLayout,
                                 uint32_t, uint32_t n, const VkDescriptorSet *sets, uint32_t,
                                 const uint32_t *) {
    ++forwards;
    if (n) {
        if (!rk_lifetime::sets.count({device, sets[0]}))
            frozen.insert(sets[0]);
        if (point == VK_PIPELINE_BIND_POINT_COMPUTE)
            compute_bound[cb] = sets[0];
        else
            graphics_bound[cb] = sets[0];
    }
}
static void VKAPI_CALL dispatch(VkCommandBuffer cb, uint32_t, uint32_t, uint32_t) {
    assert(compute_bound[cb]);
    ++dispatch_count;
}
static void VKAPI_CALL destroy_device(VkDevice, const VkAllocationCallbacks *) {
    ++forwards;
    assert(!g_cp.pipe);
    assert(rk_lifetime::live_memory == 0);
}
static void enable() {
    g_cp.disabled = false;
    callback_result = VK_SUCCESS;
}
static VkCommandPool command_pool(uint32_t family = 0) {
    VkCommandPoolCreateInfo ci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    ci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    ci.queueFamilyIndex = family;
    VkCommandPool out;
    assert(rk_CreateCommandPool(device, &ci, nullptr, &out) == VK_SUCCESS);
    return out;
}
static VkCommandBuffer cb_alloc(VkCommandPool p,
                                VkCommandBufferLevel level = VK_COMMAND_BUFFER_LEVEL_PRIMARY) {
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = p;
    ai.level = level;
    ai.commandBufferCount = 1;
    VkCommandBuffer out;
    assert(rk_AllocateCommandBuffers(device, &ai, &out) == VK_SUCCESS);
    return out;
}
static void start(VkCommandBuffer cb, VkCommandBufferUsageFlags flags = 0) {
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = flags;
    assert(rk_BeginCommandBuffer(cb, &bi) == VK_SUCCESS);
}
struct Fixture {
    VkImage src, tgt;
    VkImageView v, tv;
    VkSampler samp;
    VkRenderPass rp;
    VkFramebuffer before, target;
    VkDescriptorSetLayout l;
    VkDescriptorPool p;
    VkDescriptorSet game;
    uint32_t dimension;
    Fixture(uint32_t dim = 16) : dimension(dim) {
        VkImageCreateInfo ii{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
        ii.imageType = VK_IMAGE_TYPE_2D;
        ii.format = VK_FORMAT_R8G8B8A8_UNORM;
        ii.extent = {8, 8, 1};
        ii.mipLevels = ii.arrayLayers = 1;
        ii.samples = VK_SAMPLE_COUNT_1_BIT;
        ii.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
        assert(rk_CreateImage(device, &ii, nullptr, &src) == VK_SUCCESS);
        VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
        vi.image = src;
        vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
        vi.format = ii.format;
        vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        assert(rk_CreateImageView(device, &vi, nullptr, &v) == VK_SUCCESS);
        ii.extent = {dimension, dimension, 1};
        assert(rk_CreateImage(device, &ii, nullptr, &tgt) == VK_SUCCESS);
        vi.image = tgt;
        assert(rk_CreateImageView(device, &vi, nullptr, &tv) == VK_SUCCESS);
        VkSamplerCreateInfo si{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
        assert(rk_CreateSampler(device, &si, nullptr, &samp) == VK_SUCCESS);
        VkAttachmentDescription attachment{};
        attachment.format = ii.format;
        attachment.samples = VK_SAMPLE_COUNT_1_BIT;
        attachment.finalLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        attachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
        attachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
        VkAttachmentReference color{0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
        VkSubpassDescription pass{};
        pass.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS;
        pass.colorAttachmentCount = 1;
        pass.pColorAttachments = &color;
        VkRenderPassCreateInfo ri{VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO};
        ri.attachmentCount = 1;
        ri.pAttachments = &attachment;
        ri.subpassCount = 1;
        ri.pSubpasses = &pass;
        assert(rk_CreateRenderPass(device, &ri, nullptr, &rp) == VK_SUCCESS);
        VkFramebufferCreateInfo fi{VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO};
        fi.renderPass = rp;
        fi.attachmentCount = 1;
        fi.pAttachments = &v;
        fi.width = fi.height = 8;
        fi.layers = 1;
        assert(rk_CreateFramebuffer(device, &fi, nullptr, &before) == VK_SUCCESS);
        fi.width = fi.height = dimension;
        fi.pAttachments = &tv;
        assert(rk_CreateFramebuffer(device, &fi, nullptr, &target) == VK_SUCCESS);
        VkDescriptorSetLayoutBinding binding{0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1,
                                             VK_SHADER_STAGE_FRAGMENT_BIT, nullptr};
        VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        li.bindingCount = 1;
        li.pBindings = &binding;
        assert(rk_CreateDescriptorSetLayout(device, &li, nullptr, &l) == VK_SUCCESS);
        VkDescriptorPoolSize size{binding.descriptorType, 8};
        VkDescriptorPoolCreateInfo pi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
        pi.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
        pi.maxSets = 8;
        pi.poolSizeCount = 1;
        pi.pPoolSizes = &size;
        assert(rk_CreateDescriptorPool(device, &pi, nullptr, &p) == VK_SUCCESS);
        VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        ai.descriptorPool = p;
        ai.descriptorSetCount = 1;
        ai.pSetLayouts = &l;
        assert(rk_AllocateDescriptorSets(device, &ai, &game) == VK_SUCCESS);
        write();
    }
    void write() {
        VkDescriptorImageInfo di{samp, v, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
        VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        w.dstSet = game;
        w.descriptorCount = 1;
        w.descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        w.pImageInfo = &di;
        rk_UpdateDescriptorSets(device, 1, &w, 0, nullptr);
    }
    void record(VkCommandBuffer cb, VkSubpassContents contents = VK_SUBPASS_CONTENTS_INLINE) {
        if (rk_lifetime::cbs[cb].inside_pass)
            rk_CmdEndRenderPass(cb);
        VkRenderPassBeginInfo bi{VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO};
        bi.renderPass = rp;
        bi.framebuffer = before;
        bi.renderArea.extent = {8, 8};
        rk_CmdBeginRenderPass(cb, &bi, VK_SUBPASS_CONTENTS_INLINE);
        rk_CmdEndRenderPass(cb);
#ifdef RK_LIFETIME_HOST_FIXTURE
        assert(rk_lifetime::host_contract(cb, v, samp, rp, target));
#endif
        bi.framebuffer = target;
        bi.renderArea.extent = {dimension, dimension};
        rk_CmdBeginRenderPass(cb, &bi, contents);
    }
    VkDescriptorSet bind(VkCommandBuffer cb) {
        rk_CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_GRAPHICS, VK_NULL_HANDLE, 0, 1, &game,
                                 0, nullptr);
        return graphics_bound[cb];
    }
    ~Fixture() {
        frozen.erase(game);
        rk_DestroyDescriptorPool(device, p, nullptr);
        if (l)
            rk_DestroyDescriptorSetLayout(device, l, nullptr);
        rk_DestroyFramebuffer(device, before, nullptr);
        rk_DestroyFramebuffer(device, target, nullptr);
        rk_DestroyRenderPass(device, rp, nullptr);
        rk_DestroySampler(device, samp, nullptr);
        rk_DestroyImageView(device, v, nullptr);
        rk_DestroyImage(device, src, nullptr);
        rk_DestroyImageView(device, tv, nullptr);
        rk_DestroyImage(device, tgt, nullptr);
    }
};
int main() {
    DevDisp d;
    d.dev = device;
    d.gdpa = gdpa;
    d.queue_flags = {VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT};
    d.BeginCommandBuffer = begin;
    d.EndCommandBuffer = end_cb;
    d.ResetCommandBuffer = reset;
    d.ResetCommandPool = reset_pool;
    d.CreateCommandPool = create_command_pool;
    d.DestroyCommandPool = destroy_command_pool;
    d.AllocateCommandBuffers = allocate_cbs;
    d.FreeCommandBuffers = free_cbs;
    d.QueueSubmit = submit;
    d.CmdExecuteCommands = execute;
    d.CreateShaderModule = shader;
    d.DestroyShaderModule = destroy_shader;
    d.CreateDescriptorSetLayout = layout;
    d.DestroyDescriptorSetLayout = destroy_layout;
    d.CreatePipelineLayout = pipeline_layout;
    d.DestroyPipelineLayout = destroy_pipeline_layout;
    d.CreateComputePipelines = pipeline;
    d.DestroyPipeline = destroy_pipeline;
    d.CreateDescriptorPool = pool;
    d.DestroyDescriptorPool = destroy_pool;
    d.AllocateDescriptorSets = alloc_sets;
    d.FreeDescriptorSets = free_sets;
    d.ResetDescriptorPool = reset_descriptor_pool;
    d.UpdateDescriptorSets = update;
    d.CreateImage = image;
    d.DestroyImage = destroy_image;
    d.CreateImageView = view;
    d.DestroyImageView = destroy_view;
    d.GetPhysMemProps = memory_properties;
    d.AllocateMemory = allocate_memory;
    d.FreeMemory = free_memory;
    d.BindImageMemory = bind_memory;
    d.CreateSampler = sampler;
    d.DestroySampler = destroy_sampler;
    d.CreateRenderPass = render_pass;
    d.DestroyRenderPass = destroy_render_pass;
    d.CreateFramebuffer = framebuffer;
    d.DestroyFramebuffer = destroy_framebuffer;
    d.CmdBeginRenderPass = begin_pass;
    d.CmdEndRenderPass = end_pass;
    d.CmdPipelineBarrier = barrier;
    d.CmdBindPipeline = bind_pipeline;
    d.CmdBindDescriptorSets = bind_sets;
    d.CmdDispatch = dispatch;
    d.DestroyDevice = destroy_device;
    g_dev[dispatch_key] = d;
    g_upop = true;
    g_dump_passes = true;
    g_swap_w = g_swap_h = 16;
    auto cp = command_pool();
    auto a = cb_alloc(cp);
    auto b = cb_alloc(cp);
    {
        Fixture x;
        Fixture y;
        start(a);
        start(b);
#ifndef RK_LIFETIME_HOST_FIXTURE
        auto f = forwards;
        x.record(a);
        assert(forwards > f);
        assert(dispatch_count == 0 && g_cp.disabled &&
               rk_lifetime::refusal == "missing_source_layout_target_compute_contract");
        assert(x.bind(a) == x.game);
        ++cases;
#else
        x.record(a);
        auto ca = x.bind(a);
        auto va = descriptors[ca][{0, 0}].imageView;
        auto first = compute_bound[a];
        y.record(b);
        auto cb = y.bind(b);
        auto vb = descriptors[cb][{0, 0}].imageView;
        assert(va != vb && first != compute_bound[b]);
        assert(x.bind(a) == ca);
        ++cases;
        x.record(a);
        auto ca2 = x.bind(a);
        assert(
            (ca2 != ca && compute_bound[a] != first && descriptors[ca2][{0, 0}].imageView != va));
        assert(live.count(reinterpret_cast<uintptr_t>(va)));
        ++cases;
        x.write();
        auto ca3 = x.bind(a);
        assert(ca3 != ca2 && live.count(reinterpret_cast<uintptr_t>(ca2)));
        ++cases;
        VkCopyDescriptorSet copy{VK_STRUCTURE_TYPE_COPY_DESCRIPTOR_SET};
        copy.srcSet = copy.dstSet = x.game;
        copy.descriptorCount = 1;
        rk_UpdateDescriptorSets(device, 0, nullptr, 1, &copy);
        auto ca4 = x.bind(a);
        assert(ca4 != ca3);
        ++cases;
        g_swap_w = g_swap_h = 32;
        Fixture z(32);
        z.record(a);
        assert(live.count(reinterpret_cast<uintptr_t>(va)));
        assert(rk_lifetime::cbs[a].dispatches.back().w == 32);
        g_swap_w = g_swap_h = 16;
        ++cases;
        auto count = rk_lifetime::cbs[a].dispatches.size();
        auto bytes = rk_lifetime::live_memory;
        callback_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
        assert(rk_BeginCommandBuffer(a, &bi) == callback_result);
        assert(rk_lifetime::cbs[a].dispatches.size() == count && rk_lifetime::live_memory == bytes);
        assert(rk_ResetCommandBuffer(a, 0) == callback_result && rk_lifetime::live_memory == bytes);
        ++cases;
        callback_result = VK_SUCCESS;
        start(a);
        assert(rk_lifetime::cbs[a].dispatches.empty() &&
               !live.count(reinterpret_cast<uintptr_t>(va)));
        assert(!rk_lifetime::cbs[b].dispatches.empty());
        ++cases;
        VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
        si.commandBufferCount = 1;
        si.pCommandBuffers = &b;
        auto submitted = g_artifact_stats.queue_submitted;
        callback_result = VK_ERROR_DEVICE_LOST;
        rk_QueueSubmit(queue, 1, &si, VK_NULL_HANDLE);
        assert(g_artifact_stats.queue_submitted == submitted);
        callback_result = VK_SUCCESS;
        rk_QueueSubmit(queue, 1, &si, VK_NULL_HANDLE);
        rk_QueueSubmit(queue, 1, &si, VK_NULL_HANDLE);
        assert(g_artifact_stats.queue_submitted == submitted + 2 &&
               !rk_lifetime::cbs[b].dispatches.empty());
        ++cases;
        callback_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        assert(rk_ResetCommandPool(device, cp, 0) == callback_result);
        assert(!rk_lifetime::cbs[b].dispatches.empty());
        callback_result = VK_SUCCESS;
        rk_ResetCommandPool(device, cp, 0);
        assert(rk_lifetime::live_memory == 0);
        ++cases;
        // Partial allocations roll back, including device asset partial creation.
        for (const char *failure_name : {"image", "memory", "bind", "view", "pool", "set"}) {
            enable();
            start(a);
            size_t before = live.size();
            fail = failure_name;
            x.record(a);
            assert(g_cp.disabled && rk_lifetime::cbs[a].dispatches.empty() &&
                   live.size() == before && rk_lifetime::live_memory == 0);
            ++cases;
        }
        for (const char *failure_name :
             {"shader", "layout", "pipeline_layout", "pipeline", "pipeline_partial"}) {
            rk_lifetime::destroy_assets(d);
            enable();
            start(a);
            size_t before = live.size();
            fail = failure_name;
            x.record(a);
            assert(g_cp.disabled && !g_cp.pipe && !g_cp.sm && live.size() == before);
            ++cases;
        }
        enable();
        start(a);
        x.record(a);
        auto retained = live.size();
        fail = "layout";
        x.bind(a);
        assert(g_cp.disabled && live.size() == retained && !rk_lifetime::cbs[a].dispatches.empty());
        ++cases;
        enable();
        start(a);
        requirement = rk_lifetime::kMemoryLimit + 1;
        x.record(a);
        assert(g_cp.disabled && rk_lifetime::cbs[a].dispatches.empty() &&
               rk_lifetime::live_memory == 0);
        requirement = 4096;
        ++cases;
        enable();
        start(a, VK_COMMAND_BUFFER_USAGE_SIMULTANEOUS_USE_BIT);
        x.record(a);
        assert(g_cp.disabled && rk_lifetime::refusal == "simultaneous_use_unsupported");
        ++cases;
        enable();
        auto secondary = cb_alloc(cp, VK_COMMAND_BUFFER_LEVEL_SECONDARY);
        start(secondary);
        x.record(secondary);
        assert(g_cp.disabled && rk_lifetime::refusal == "secondary_unsupported");
        rk_FreeCommandBuffers(device, cp, 1, &secondary);
        ++cases;
        enable();
        auto unknown = command_pool(99);
        auto c = cb_alloc(unknown);
        start(c);
        x.record(c);
        assert(g_cp.disabled && rk_lifetime::refusal == "unknown_pool_or_queue_family");
        rk_DestroyCommandPool(device, unknown, nullptr);
        ++cases;
        enable();
        start(a);
        x.record(a, VK_SUBPASS_CONTENTS_SECONDARY_COMMAND_BUFFERS);
        assert(g_cp.disabled && rk_lifetime::refusal == "unknown_target_identity");
        ++cases;
        enable();
        start(a);
        rk_CmdBindPipeline(a, VK_PIPELINE_BIND_POINT_COMPUTE, h<VkPipeline>(123));
        x.record(a);
        assert(g_cp.disabled && rk_lifetime::refusal == "unverified_compute_bind_state");
        ++cases;
        // End failures cannot release an already injected group.
        enable();
        start(a);
        x.record(a);
        retained = live.size();
        callback_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        assert(rk_EndCommandBuffer(a) == callback_result);
        assert(live.size() == retained && !rk_lifetime::cbs[a].dispatches.empty());
        callback_result = VK_SUCCESS;
        start(a);
        assert(rk_lifetime::live_memory == 0);
        ++cases;
        enable();
        start(a);
        x.record(a);
        auto held = rk_lifetime::live_memory;
        rk_CmdEndRenderPass(a);
        assert(x.bind(a) == x.game && rk_lifetime::live_memory == held);
        rk_ResetCommandBuffer(a, 0);
        ++cases;
        enable();
        start(a);
        g_upop = false;
        g_copyprobe = true;
        auto old_dispatches = dispatch_count;
        x.record(a);
        assert(g_cp.disabled && dispatch_count == old_dispatches);
#ifdef RK_DYNAMIC_UPOP_BUILD
        assert(rk_lifetime::refusal == "dynamic_upop_not_requested");
#else
        assert(rk_lifetime::refusal == "operator_mode_changed");
#endif
        g_upop = true;
        g_copyprobe = false;
        rk_ResetCommandBuffer(a, 0);
        ++cases;
        // Hard caps retain every previously recorded group, never evict live work.
        enable();
        start(a);
        for (size_t i = 0; i < rk_lifetime::kDispatchLimit; ++i)
            x.record(a);
        auto old_memory = rk_lifetime::live_memory;
        x.record(a);
        assert(g_cp.disabled &&
               rk_lifetime::cbs[a].dispatches.size() == rk_lifetime::kDispatchLimit &&
               rk_lifetime::live_memory == old_memory);
        rk_ResetCommandBuffer(a, 0);
        ++cases;
        enable();
        start(a);
        x.record(a);
        for (size_t i = 0; i < rk_lifetime::kCloneLimit; ++i) {
            x.write();
            assert(x.bind(a) != x.game);
        }
        auto old_live = live.size();
        x.write();
        assert(x.bind(a) == x.game && g_cp.disabled && live.size() == old_live);
        rk_ResetCommandBuffer(a, 0);
        ++cases;
        enable();
        std::vector<VkCommandBuffer> many;
        for (size_t i = 0; i < rk_lifetime::kGenerationLimit; ++i) {
            auto c = cb_alloc(cp);
            many.push_back(c);
            start(c);
            x.record(c);
        }
        auto overflow = cb_alloc(cp);
        start(overflow);
        old_memory = rk_lifetime::live_memory;
        x.record(overflow);
        assert(g_cp.disabled && rk_lifetime::cbs[overflow].dispatches.empty() &&
               rk_lifetime::live_memory == old_memory);
        rk_ResetCommandPool(device, cp, 0);
        ++cases;
        // Descriptor extension contracts are rejected, with original binds forwarded.
        VkDescriptorSetLayoutBinding extra_binding{0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1,
                                                   VK_SHADER_STAGE_FRAGMENT_BIT, nullptr};
        VkDescriptorBindingFlags binding_flag = VK_DESCRIPTOR_BINDING_UPDATE_AFTER_BIND_BIT;
        VkDescriptorSetLayoutBindingFlagsCreateInfo extension{
            VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO};
        extension.bindingCount = 1;
        extension.pBindingFlags = &binding_flag;
        VkDescriptorSetLayoutCreateInfo extra{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        extra.pNext = &extension;
        extra.bindingCount = 1;
        extra.pBindings = &extra_binding;
        VkDescriptorSetLayout extended;
        rk_CreateDescriptorSetLayout(device, &extra, nullptr, &extended);
        VkDescriptorSetAllocateInfo extra_ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        extra_ai.descriptorPool = x.p;
        extra_ai.descriptorSetCount = 1;
        extra_ai.pSetLayouts = &extended;
        VkDescriptorSet extra_set;
        rk_AllocateDescriptorSets(device, &extra_ai, &extra_set);
        auto original = x.game;
        x.game = extra_set;
        x.write();
        enable();
        start(a);
        x.record(a);
        assert(x.bind(a) == extra_set && g_cp.disabled);
        rk_ResetCommandBuffer(a, 0);
        x.game = original;
        rk_DestroyDescriptorSetLayout(device, extended, nullptr);
        ++cases;
        // Free/reset application sets leave generation-owned clones alive until CB retirement.
        enable();
        start(a);
        x.record(a);
        auto owned = x.bind(a);
        auto saved = x.game;
        callback_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        assert(rk_FreeDescriptorSets(device, x.p, 1, &saved) == callback_result);
        assert(rk_lifetime::sets.count({device, saved}));
        callback_result = VK_SUCCESS;
        assert(rk_FreeDescriptorSets(device, x.p, 1, &saved) == VK_SUCCESS);
        assert(!rk_lifetime::sets.count({device, saved}) &&
               live.count(reinterpret_cast<uintptr_t>(owned)));
        rk_ResetCommandBuffer(a, 0);
        VkDescriptorSetAllocateInfo replace_ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        replace_ai.descriptorPool = x.p;
        replace_ai.descriptorSetCount = 1;
        replace_ai.pSetLayouts = &x.l;
        rk_AllocateDescriptorSets(device, &replace_ai, &x.game);
        x.write();
        ++cases;
        enable();
        start(a);
        x.record(a);
        owned = x.bind(a);
        callback_result = VK_ERROR_OUT_OF_HOST_MEMORY;
        assert(rk_ResetDescriptorPool(device, x.p, 0) == callback_result);
        assert(rk_lifetime::sets.count({device, x.game}));
        callback_result = VK_SUCCESS;
        rk_ResetDescriptorPool(device, x.p, 0);
        assert(!rk_lifetime::sets.count({device, x.game}) &&
               live.count(reinterpret_cast<uintptr_t>(owned)));
        rk_ResetCommandBuffer(a, 0);
        rk_AllocateDescriptorSets(device, &replace_ai, &x.game);
        x.write();
        ++cases;
        // Frozen layout metadata survives legitimate application layout destruction.
        rk_DestroyDescriptorSetLayout(device, x.l, nullptr);
        x.l = VK_NULL_HANDLE;
        enable();
        start(a);
        x.record(a);
        assert(x.bind(a) != x.game);
        rk_ResetCommandBuffer(a, 0);
        ++cases;
        // A destroyed/recreated sampler with equal numeric handle has a new epoch.
        auto sampler_epoch = rk_lifetime::samplers[{device, x.samp}].epoch;
        rk_DestroySampler(device, x.samp, nullptr);
        reuse_sampler = x.samp;
        VkSamplerCreateInfo recreated{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
        rk_CreateSampler(device, &recreated, nullptr, &x.samp);
        assert((rk_lifetime::samplers[{device, x.samp}].epoch != sampler_epoch));
        enable();
        start(a);
        x.record(a);
        assert(x.bind(a) == x.game && g_cp.disabled);
        rk_ResetCommandBuffer(a, 0);
        x.write();
        ++cases;
        enable();
        start(a);
        x.record(a);
        assert(rk_lifetime::live_memory);
        auto allocation_epoch = rk_lifetime::cbs[a].allocation;
        reuse_cb = a;
        rk_FreeCommandBuffers(device, cp, 1, &a);
        assert(!rk_lifetime::cbs.count(a) && rk_lifetime::live_memory == 0);
        a = cb_alloc(cp);
        assert(rk_lifetime::cbs[a].allocation != allocation_epoch);
        ++cases;
#endif
    }
    enable();
    rk_DestroyCommandPool(device, cp, nullptr);
    assert(rk_lifetime::cbs.empty() && rk_lifetime::live_memory == 0);
    rk_DestroyDevice(device, nullptr);
    assert(live.empty());
    g_dump_passes = false;
    write_marker();
    printf("{\"native_marker\":%s,\"ownership_cases\":%u,\"dispatch_recorded\":%u,\"original_calls_"
           "forwarded\":%u,"
           "\"deployable\":false,\"artifact_id\":\"%s\"}\n",
           rk_host_marker.c_str(), cases, dispatch_count, forwards, kArtifactId);
}
