#pragma once
#if defined(RK_LIFETIME_HOST_FIXTURE) && defined(__ANDROID__)
#error "Host lifetime contracts are forbidden in Android artifacts"
#endif
// Included by the actual layer after its dispatch tables and observer maps.
// All functions require g_mtx. Object lifetime follows legal Vulkan CB retirement,
// not submission counters. Host fixtures supply evidence only, never a different algorithm.
namespace rk_lifetime {
using Epoch = uint64_t;
using ImageKey = std::pair<VkDevice, VkImage>;
using ViewKey = std::pair<VkDevice, VkImageView>;
using Element = std::pair<uint32_t, uint32_t>;
static constexpr size_t kIdentityLimit = 4096, kGenerationLimit = 64;
static constexpr size_t kDispatchLimit = 64, kCloneLimit = 256;
static constexpr VkDeviceSize kMemoryLimit = 256ull * 1024 * 1024;
static Epoch next_epoch = 0;
static VkDeviceSize live_memory = 0;
static std::string refusal = "missing_source_layout_target_compute_contract";
struct Image {
    Epoch epoch;
    uint32_t w, h;
    VkFormat fmt;
    VkImageUsageFlags usage;
    bool supported;
};
struct View {
    Epoch epoch, image_epoch;
    VkImage image;
    bool supported;
};
struct Sampler {
    Epoch epoch;
    bool supported;
};
struct Framebuffer {
    Epoch epoch;
    VkRenderPass rp;
    Epoch rp_epoch;
    bool supported;
};
struct Layout {
    Epoch epoch = 0;
    bool supported = false;
    std::vector<VkDescriptorSetLayoutBinding> bindings;
};
struct Descriptor {
    VkImageView view = VK_NULL_HANDLE;
    VkSampler sampler = VK_NULL_HANDLE;
    VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
    Epoch view_epoch = 0, sampler_epoch = 0;
};
struct Set {
    Epoch epoch = 0, pool_epoch = 0, writes = 0;
    VkDescriptorPool pool = VK_NULL_HANDLE;
    Layout layout;
    bool known = true;
    std::map<Element, Descriptor> images;
};
struct AppPool {
    Epoch epoch;
    bool supported;
};
struct CommandPool {
    Epoch epoch;
    VkCommandPoolCreateFlags flags;
    bool supported;
};
struct Contract {
    enum class Origin { Missing, HostFixture } origin = Origin::Missing;
    Epoch allocation = 0, recording = 0, image_epoch = 0, view_epoch = 0, sampler_epoch = 0;
    Epoch render_pass_epoch = 0, framebuffer_epoch = 0, compute_mutation = 0;
    VkImage image = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkSampler sampler = VK_NULL_HANDLE;
    VkRenderPass render_pass = VK_NULL_HANDLE;
    VkFramebuffer framebuffer = VK_NULL_HANDLE;
    VkImageLayout source_layout = VK_IMAGE_LAYOUT_UNDEFINED;
};
struct GraphicsClone {
    VkDescriptorPool pool = VK_NULL_HANDLE;
    VkDescriptorSetLayout layout = VK_NULL_HANDLE;
    VkDescriptorSet set = VK_NULL_HANDLE;
    VkDescriptorSet original = VK_NULL_HANDLE;
    Epoch set_epoch = 0, writes = 0;
};
struct Dispatch {
    VkDescriptorPool pool = VK_NULL_HANDLE;
    VkDescriptorSet compute = VK_NULL_HANDLE;
    VkImage output = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkDeviceSize bytes = 0;
    uint32_t w = 0, h = 0;
    Contract contract;
    std::vector<GraphicsClone> clones;
};
struct CB {
    VkDevice dev = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    Epoch pool_epoch = 0, allocation = 0, recording_epoch = 0;
    VkCommandBufferLevel level = VK_COMMAND_BUFFER_LEVEL_SECONDARY;
    VkCommandBufferUsageFlags flags = 0;
    bool recording = false, uncertain = false;
    Epoch compute_mutation = 0;
    Contract contract;
    VkFramebuffer target_framebuffer = VK_NULL_HANDLE;
    bool inline_contents = false, inside_pass = false;
    size_t active = SIZE_MAX;
    std::vector<Dispatch> dispatches;
};
static std::map<ImageKey, Image> images;
static std::map<ViewKey, View> views;
static std::map<std::pair<VkDevice, VkSampler>, Sampler> samplers;
static std::map<std::pair<VkDevice, VkDescriptorSetLayout>, Layout> layouts;
static std::map<std::pair<VkDevice, VkDescriptorSet>, Set> sets;
static std::map<std::pair<VkDevice, VkDescriptorPool>, AppPool> app_pools;
static std::map<std::pair<VkDevice, VkCommandPool>, CommandPool> command_pools;
static std::map<std::pair<VkDevice, VkRenderPass>, Epoch> render_passes;
static std::map<std::pair<VkDevice, VkFramebuffer>, Framebuffer> framebuffers;
static std::map<VkCommandBuffer, CB> cbs;
static void reject(const char *reason) {
    refusal = reason;
    // Stop future work, but never reclaim commands already recorded.
    cp_disable(reason);
}
template <class M, class K, class V> static void remember(M &m, const K &key, const V &value) {
    if (m.find(key) != m.end()) {
        reject("live_handle_reused_without_retirement");
        return;
    }
    if (m.size() >= kIdentityLimit) {
        reject("identity_limit");
        return;
    }
    m.emplace(key, value);
}
static bool descriptor_type(VkDescriptorType t) {
    return t == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER ||
           t == VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE || t == VK_DESCRIPTOR_TYPE_STORAGE_IMAGE ||
           t == VK_DESCRIPTOR_TYPE_SAMPLER || t == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER ||
           t == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
           t == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC ||
           t == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC ||
           t == VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT;
}
static void created_image(VkDevice dev, VkImage out, const VkImageCreateInfo &ci) {
    bool supported = !ci.pNext && ci.flags == 0 && ci.imageType == VK_IMAGE_TYPE_2D &&
                     ci.extent.depth == 1 && ci.mipLevels == 1 && ci.arrayLayers == 1 &&
                     ci.samples == VK_SAMPLE_COUNT_1_BIT && ci.format == VK_FORMAT_R8G8B8A8_UNORM &&
                     ci.sharingMode == VK_SHARING_MODE_EXCLUSIVE;
    remember(
        images, ImageKey{dev, out},
        Image{++next_epoch, ci.extent.width, ci.extent.height, ci.format, ci.usage, supported});
}
static void created_view(VkDevice dev, VkImageView out, const VkImageViewCreateInfo &ci) {
    auto image = images.find({dev, ci.image});
    bool full = !ci.pNext && !ci.flags && ci.viewType == VK_IMAGE_VIEW_TYPE_2D &&
                ci.format == VK_FORMAT_R8G8B8A8_UNORM &&
                ci.components.r == VK_COMPONENT_SWIZZLE_IDENTITY &&
                ci.components.g == VK_COMPONENT_SWIZZLE_IDENTITY &&
                ci.components.b == VK_COMPONENT_SWIZZLE_IDENTITY &&
                ci.components.a == VK_COMPONENT_SWIZZLE_IDENTITY &&
                ci.subresourceRange.aspectMask == VK_IMAGE_ASPECT_COLOR_BIT &&
                ci.subresourceRange.baseMipLevel == 0 && ci.subresourceRange.levelCount == 1 &&
                ci.subresourceRange.baseArrayLayer == 0 && ci.subresourceRange.layerCount == 1;
    remember(views, ViewKey{dev, out},
             View{++next_epoch, image == images.end() ? 0 : image->second.epoch, ci.image,
                  full && image != images.end() && image->second.supported});
}
static void created_layout(VkDevice dev, VkDescriptorSetLayout out,
                           const VkDescriptorSetLayoutCreateInfo &ci) {
    Layout layout;
    layout.epoch = ++next_epoch;
    layout.supported = !ci.pNext && !ci.flags && ci.bindingCount <= 64;
    if (ci.bindingCount <= 64)
        for (uint32_t i = 0; i < ci.bindingCount; ++i) {
            auto b = ci.pBindings[i];
            if (b.pImmutableSamplers || b.descriptorCount > 256 || !b.descriptorCount ||
                !descriptor_type(b.descriptorType))
                layout.supported = false;
            b.pImmutableSamplers = nullptr;
            layout.bindings.push_back(b);
        }
    remember(layouts, std::make_pair(dev, out), layout);
}
static void allocated_sets(VkDevice dev, const VkDescriptorSetAllocateInfo &ai,
                           const VkDescriptorSet *out) {
    if (ai.descriptorSetCount > kIdentityLimit) {
        reject("identity_limit");
        return;
    }
    for (uint32_t i = 0; i < ai.descriptorSetCount; ++i) {
        Set s;
        s.epoch = ++next_epoch;
        s.pool = ai.descriptorPool;
        auto p = app_pools.find({dev, ai.descriptorPool});
        auto l = layouts.find({dev, ai.pSetLayouts[i]});
        if (p != app_pools.end())
            s.pool_epoch = p->second.epoch;
        if (l != layouts.end())
            s.layout = l->second;
        s.known = !ai.pNext && p != app_pools.end() && p->second.supported && l != layouts.end() &&
                  l->second.supported;
        remember(sets, std::make_pair(dev, out[i]), s);
    }
}
static const VkDescriptorSetLayoutBinding *binding(const Layout &l, uint32_t n) {
    for (const auto &b : l.bindings)
        if (b.binding == n)
            return &b;
    return nullptr;
}
static void updated_sets(VkDevice dev, uint32_t nw, const VkWriteDescriptorSet *w, uint32_t nc,
                         const VkCopyDescriptorSet *copies) {
    // Supported scalar/image writes. Buffer writes are copied by Vulkan into new clones;
    // image identities must be known. Spill across bindings is conservatively refused.
    if (nw > kIdentityLimit || nc > kIdentityLimit) {
        reject("descriptor_update_limit");
        return;
    }
    for (uint32_t i = 0; i < nw; ++i) {
        auto it = sets.find({dev, w[i].dstSet});
        if (it == sets.end())
            continue;
        Set &set = it->second;
        ++set.writes;
        const auto *b = binding(set.layout, w[i].dstBinding);
        if (w[i].pNext || !b || b->descriptorType != w[i].descriptorType ||
            w[i].dstArrayElement > b->descriptorCount ||
            w[i].descriptorCount > b->descriptorCount - w[i].dstArrayElement) {
            set.known = false;
            continue;
        }
        if (!w[i].pImageInfo)
            continue;
        for (uint32_t j = 0; j < w[i].descriptorCount; ++j) {
            const auto &data = w[i].pImageInfo[j];
            Descriptor d;
            d.view = data.imageView;
            d.sampler = data.sampler;
            d.layout = data.imageLayout;
            auto v = views.find({dev, d.view});
            if (v != views.end())
                d.view_epoch = v->second.epoch;
            auto s = samplers.find({dev, d.sampler});
            if (s != samplers.end())
                d.sampler_epoch = s->second.epoch;
            set.images[{w[i].dstBinding, w[i].dstArrayElement + j}] = d;
        }
    }
    for (uint32_t i = 0; i < nc; ++i) {
        const auto &c = copies[i];
        auto dst = sets.find({dev, c.dstSet});
        auto src = sets.find({dev, c.srcSet});
        if (dst == sets.end())
            continue;
        ++dst->second.writes;
        const auto *db = binding(dst->second.layout, c.dstBinding);
        const auto *sb = src == sets.end() ? nullptr : binding(src->second.layout, c.srcBinding);
        if (c.pNext || !sb || !db || !src->second.known ||
            sb->descriptorType != db->descriptorType || c.srcArrayElement > sb->descriptorCount ||
            c.descriptorCount > sb->descriptorCount - c.srcArrayElement ||
            c.dstArrayElement > db->descriptorCount ||
            c.descriptorCount > db->descriptorCount - c.dstArrayElement) {
            dst->second.known = false;
            continue;
        }
        if (sb->descriptorType == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER ||
            sb->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
            sb->descriptorType == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC ||
            sb->descriptorType == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC)
            continue;
        std::vector<Descriptor> snapshot;
        for (uint32_t j = 0; j < c.descriptorCount; ++j) {
            auto di = src->second.images.find({c.srcBinding, c.srcArrayElement + j});
            snapshot.push_back(di == src->second.images.end() ? Descriptor{} : di->second);
        }
        for (uint32_t j = 0; j < c.descriptorCount; ++j)
            dst->second.images[{c.dstBinding, c.dstArrayElement + j}] = snapshot[j];
    }
}
static void invalidate_pool(VkDevice dev, VkDescriptorPool p, bool destroy) {
    for (auto it = sets.begin(); it != sets.end();)
        if (it->first.first == dev && it->second.pool == p)
            it = sets.erase(it);
        else
            ++it;
    if (destroy)
        app_pools.erase({dev, p});
    else {
        auto it = app_pools.find({dev, p});
        if (it != app_pools.end())
            it->second.epoch = ++next_epoch;
    }
}
static void destroy_dispatch(const DevDisp &d, Dispatch &g) {
    // Only called after legal generation retirement or for an unreferenced transaction.
    for (auto &c : g.clones) {
        if (c.pool)
            d.DestroyDescriptorPool(d.dev, c.pool, nullptr);
        if (c.layout)
            d.DestroyDescriptorSetLayout(d.dev, c.layout, nullptr);
    }
    if (g.pool)
        d.DestroyDescriptorPool(d.dev, g.pool, nullptr);
    if (g.view)
        d.DestroyImageView(d.dev, g.view, nullptr);
    if (g.output)
        d.DestroyImage(d.dev, g.output, nullptr);
    if (g.memory)
        d.FreeMemory(d.dev, g.memory, nullptr);
    if (g.bytes <= live_memory)
        live_memory -= g.bytes;
    g = Dispatch{};
}
static void retire(CB &cb, const DevDisp &d) {
    for (auto &g : cb.dispatches)
        destroy_dispatch(d, g);
    cb.dispatches.clear();
    cb.active = SIZE_MAX;
    cb.contract = Contract{};
    cb.recording = false;
    cb.uncertain = false;
    cb.compute_mutation = 0;
    cb.inside_pass = false;
    cb.inline_contents = false;
    cb.target_framebuffer = VK_NULL_HANDLE;
}
static void destroy_assets(const DevDisp &d) {
    if (g_cp.pipe)
        d.DestroyPipeline(d.dev, g_cp.pipe, nullptr);
    if (g_cp.pl)
        d.DestroyPipelineLayout(d.dev, g_cp.pl, nullptr);
    if (g_cp.dsl)
        d.DestroyDescriptorSetLayout(d.dev, g_cp.dsl, nullptr);
    if (g_cp.sm)
        d.DestroyShaderModule(d.dev, g_cp.sm, nullptr);
    g_cp.pipe = VK_NULL_HANDLE;
    g_cp.pl = VK_NULL_HANDLE;
    g_cp.dsl = VK_NULL_HANDLE;
    g_cp.sm = VK_NULL_HANDLE;
}
static void destroy_device(VkDevice dev, const DevDisp &d) {
    for (auto it = cbs.begin(); it != cbs.end();)
        if (it->second.dev == dev) {
            retire(it->second, d);
            g_artifact_stats.free(reinterpret_cast<uintptr_t>(it->first));
            it = cbs.erase(it);
        } else
            ++it;
    auto erase_device = [dev](auto &m) {
        for (auto it = m.begin(); it != m.end();)
            if (it->first.first == dev)
                it = m.erase(it);
            else
                ++it;
    };
    erase_device(images);
    erase_device(views);
    erase_device(samplers);
    erase_device(layouts);
    erase_device(sets);
    erase_device(app_pools);
    erase_device(command_pools);
    erase_device(render_passes);
    erase_device(framebuffers);
}
template <class Handle, class Fn> static VkResult create_owned(Handle &owned, Fn call) {
    Handle temporary = VK_NULL_HANDLE;
    VkResult result = call(&temporary);
    if (result == VK_SUCCESS)
        owned = temporary;
    // General runtime failures can leave output undefined. Never destroy that output.
    return result;
}
static bool assets(const DevDisp &d) {
    if (g_cp.dev && g_cp.dev != d.dev) {
        reject("multiple_vulkan_devices_unsupported");
        return false;
    }
#ifdef RK_DYNAMIC_UPOP_BUILD
    if (!g_upop) {
        reject("dynamic_upop_not_requested");
        return false;
    }
#endif
    if (g_cp.pipe) {
        if (g_cp.asset_mode_upop != g_upop) {
            reject("operator_mode_changed");
            return false;
        }
        return true;
    }
    if (!d.CreateShaderModule || !d.DestroyShaderModule || !d.CreateDescriptorSetLayout ||
        !d.DestroyDescriptorSetLayout || !d.CreatePipelineLayout || !d.DestroyPipelineLayout ||
        !d.CreateComputePipelines || !d.DestroyPipeline || !d.CreateDescriptorPool ||
        !d.DestroyDescriptorPool || !d.AllocateDescriptorSets || !d.CreateImage ||
        !d.DestroyImage || !d.CreateImageView || !d.DestroyImageView || !d.AllocateMemory ||
        !d.FreeMemory || !d.BindImageMemory || !d.GetPhysMemProps || !d.gdpa ||
        !d.UpdateDescriptorSets || !d.CmdBindPipeline || !d.CmdBindDescriptorSets ||
        !d.CmdPipelineBarrier || !d.CmdDispatch) {
        reject("missing_lower_resource_callbacks");
        return false;
    }
#ifdef RK_DYNAMIC_UPOP_BUILD
    if (!g_upop) {
        reject("dynamic_upop_not_requested");
        return false;
    }
#endif
    const unsigned char *code = g_upop ? kUpopSpv : kCopySpv;
    unsigned int len = g_upop ? kUpopSpvLen : kCopySpvLen;
    if (!len || len % 4) {
        reject("empty_spirv");
        return false;
    }
    g_cp.dev = d.dev; // Own partial creations from the first object.
    g_cp.asset_mode_upop = g_upop;
    VkShaderModuleCreateInfo sm{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    sm.codeSize = len;
    sm.pCode = reinterpret_cast<const uint32_t *>(code);
    VkResult r = create_owned(g_cp.sm, [&](VkShaderModule *out) {
        return d.CreateShaderModule(d.dev, &sm, nullptr, out);
    });
    if (r == VK_SUCCESS) {
        VkDescriptorSetLayoutBinding b[2]{};
        b[0] = {0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT,
                nullptr};
        b[1] = {1, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr};
        VkDescriptorSetLayoutCreateInfo l{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
        l.bindingCount = 2;
        l.pBindings = b;
        r = create_owned(g_cp.dsl, [&](VkDescriptorSetLayout *out) {
            return d.CreateDescriptorSetLayout(d.dev, &l, nullptr, out);
        });
    }
    if (r == VK_SUCCESS) {
        VkPipelineLayoutCreateInfo p{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
        p.setLayoutCount = 1;
        p.pSetLayouts = &g_cp.dsl;
        r = create_owned(g_cp.pl, [&](VkPipelineLayout *out) {
            return d.CreatePipelineLayout(d.dev, &p, nullptr, out);
        });
    }
    if (r == VK_SUCCESS) {
        VkComputePipelineCreateInfo p{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
        p.layout = g_cp.pl;
        p.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        p.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        p.stage.module = g_cp.sm;
        p.stage.pName = "main";
        VkPipeline returned = VK_NULL_HANDLE;
        r = d.CreateComputePipelines(d.dev, VK_NULL_HANDLE, 1, &p, nullptr, &returned);
        g_cp.pipe = returned; // Pipeline API's specified partial-success handle contract.

        if (r == VK_SUCCESS && g_upop)
            ++g_artifact_stats.pipeline_created;
    }
    if (r != VK_SUCCESS) {
        destroy_assets(d);
        reject("device_asset_creation_failed");
        return false;
    }
    return true;
}
static bool supported_cb(CB &cb) {
    auto p = command_pools.find({cb.dev, cb.pool});
    if (p == command_pools.end() || p->second.epoch != cb.pool_epoch || !p->second.supported) {
        reject("unknown_pool_or_queue_family");
        return false;
    }
    if (cb.level != VK_COMMAND_BUFFER_LEVEL_PRIMARY) {
        reject("secondary_unsupported");
        return false;
    }
    if (cb.flags & VK_COMMAND_BUFFER_USAGE_SIMULTANEOUS_USE_BIT) {
        reject("simultaneous_use_unsupported");
        return false;
    }
    if (!cb.recording || cb.uncertain) {
        reject("unknown_recording_state");
        return false;
    }
    return true;
}
static bool contract_valid(VkCommandBuffer handle, CB &cb, VkRenderPass rp,
                           const std::vector<VkImageView> &attachments) {
    if (!supported_cb(cb))
        return false;
    const auto &c = cb.contract;
#ifndef RK_LIFETIME_HOST_FIXTURE
    (void)handle;
    if (c.origin != Contract::Origin::Missing) {
        reject("unsupported_contract_origin");
        return false;
    }
    reject("missing_source_layout_target_compute_contract");
    return false;
#else
    (void)handle;
    if (c.origin != Contract::Origin::HostFixture) {
        reject("missing_source_layout_target_compute_contract");
        return false;
    }
#endif
    auto i = images.find({cb.dev, c.image});
    auto v = views.find({cb.dev, c.view});
    auto s = samplers.find({cb.dev, c.sampler});
    auto r = render_passes.find({cb.dev, rp});
    auto f = framebuffers.find({cb.dev, c.framebuffer});
    if (c.allocation != cb.allocation || c.recording != cb.recording_epoch ||
        attachments.size() != 1 || attachments[0] != c.view || c.render_pass != rp ||
        r == render_passes.end() || r->second != c.render_pass_epoch || f == framebuffers.end() ||
        f->second.epoch != c.framebuffer_epoch || !f->second.supported || f->second.rp != rp ||
        f->second.rp_epoch != c.render_pass_epoch || cb.target_framebuffer != c.framebuffer ||
        !cb.inline_contents) {
        reject("unknown_target_identity");
        return false;
    }
    if (i == images.end() || v == views.end() || s == samplers.end() ||
        i->second.epoch != c.image_epoch || v->second.epoch != c.view_epoch ||
        v->second.image_epoch != c.image_epoch || s->second.epoch != c.sampler_epoch ||
        !s->second.supported || !i->second.supported || !v->second.supported ||
        !(i->second.usage & VK_IMAGE_USAGE_SAMPLED_BIT) || !i->second.w || !i->second.h ||
        c.source_layout != VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL) {
        reject("unknown_source_identity_or_layout");
        return false;
    }
    if (c.compute_mutation != cb.compute_mutation || cb.compute_mutation != 0) {
        reject("unverified_compute_bind_state");
        return false;
    }
    return true;
}
static bool output(const DevDisp &d, Dispatch &g) {
    VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ci.imageType = VK_IMAGE_TYPE_2D;
    ci.format = VK_FORMAT_R8G8B8A8_UNORM;
    ci.extent = {g.w, g.h, 1};
    ci.mipLevels = ci.arrayLayers = 1;
    ci.samples = VK_SAMPLE_COUNT_1_BIT;
    ci.tiling = VK_IMAGE_TILING_OPTIMAL;
    ci.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    ci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    if (create_owned(g.output, [&](VkImage *out) {
            return d.CreateImage(d.dev, &ci, nullptr, out);
        }) != VK_SUCCESS)
        return false;
    auto req = (PFN_vkGetImageMemoryRequirements)d.gdpa(d.dev, "vkGetImageMemoryRequirements");
    if (!req)
        return false;
    VkMemoryRequirements mr{};
    req(d.dev, g.output, &mr);
    if (!mr.size || mr.size > kMemoryLimit || live_memory > kMemoryLimit - mr.size)
        return false;
    VkPhysicalDeviceMemoryProperties mp{};
    d.GetPhysMemProps(d.phys, &mp);
    uint32_t index = UINT32_MAX;
    for (uint32_t i = 0; i < mp.memoryTypeCount && i < 32; ++i)
        if ((mr.memoryTypeBits & (1u << i)) &&
            (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) {
            index = i;
            break;
        }
    if (index == UINT32_MAX)
        return false;
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = mr.size;
    ai.memoryTypeIndex = index;
    if (create_owned(g.memory, [&](VkDeviceMemory *out) {
            return d.AllocateMemory(d.dev, &ai, nullptr, out);
        }) != VK_SUCCESS)
        return false;
    g.bytes = mr.size;
    live_memory += mr.size;
    if (d.BindImageMemory(d.dev, g.output, g.memory, 0) != VK_SUCCESS)
        return false;
    VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vi.image = g.output;
    vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
    vi.format = ci.format;
    vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    return create_owned(g.view, [&](VkImageView *out) {
               return d.CreateImageView(d.dev, &vi, nullptr, out);
           }) == VK_SUCCESS;
}
static bool descriptor_pool(const DevDisp &d,
                            const std::vector<VkDescriptorSetLayoutBinding> &bindings,
                            VkDescriptorPool &out) {
    std::map<VkDescriptorType, uint32_t> totals;
    for (const auto &b : bindings)
        totals[b.descriptorType] += b.descriptorCount;
    std::vector<VkDescriptorPoolSize> sizes;
    for (const auto &t : totals)
        sizes.push_back({t.first, t.second});
    VkDescriptorPoolCreateInfo ci{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    ci.maxSets = 1;
    ci.poolSizeCount = static_cast<uint32_t>(sizes.size());
    ci.pPoolSizes = sizes.data();
    return create_owned(out, [&](VkDescriptorPool *temp) {
               return d.CreateDescriptorPool(d.dev, &ci, nullptr, temp);
           }) == VK_SUCCESS;
}
static void inject(VkCommandBuffer handle, const DevDisp &d, VkRenderPass rp,
                   const std::vector<VkImageView> &attachments) {
    auto it = cbs.find(handle);
    if (it == cbs.end()) {
        reject("unknown_command_buffer_identity");
        return;
    }
    CB &cb = it->second;
    cb.active = SIZE_MAX;
    if (g_cp.disabled)
        return;
    if (cb.dev != d.dev) {
        reject("command_buffer_device_mismatch");
        return;
    }
    if (!contract_valid(handle, cb, rp, attachments))
        return;
    size_t retained = 0;
    for (const auto &x : cbs)
        if (!x.second.dispatches.empty())
            ++retained;
    if (cb.dispatches.size() >= kDispatchLimit ||
        (cb.dispatches.empty() && retained >= kGenerationLimit)) {
        reject("recording_resource_limit");
        return;
    }
    if (!assets(d))
        return;
    Dispatch g;
    g.contract = cb.contract;
    auto image = images.find({d.dev, g.contract.image});
    g.w = g_upop ? g_swap_w : image->second.w;
    g.h = g_upop ? g_swap_h : image->second.h;
    if (!g.w || !g.h || g.w > 16384 || g.h > 16384) {
        reject("unsupported_output_extent");
        return;
    }
    std::vector<VkDescriptorSetLayoutBinding> b{
        {0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr},
        {1, VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr}};
    bool ok = output(d, g) && descriptor_pool(d, b, g.pool);
    if (ok) {
        VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        ai.descriptorPool = g.pool;
        ai.descriptorSetCount = 1;
        ai.pSetLayouts = &g_cp.dsl;
        ok = create_owned(g.compute, [&](VkDescriptorSet *out) {
                 return d.AllocateDescriptorSets(d.dev, &ai, out);
             }) == VK_SUCCESS;
    }
    if (!ok) {
        destroy_dispatch(d, g);
        reject("dispatch_resource_creation_failed_or_budget");
        return;
    }
    VkDescriptorImageInfo di[2]{{g.contract.sampler, g.contract.view, g.contract.source_layout},
                                {VK_NULL_HANDLE, g.view, VK_IMAGE_LAYOUT_GENERAL}};
    VkWriteDescriptorSet w[2]{};
    for (uint32_t i = 0; i < 2; ++i) {
        w[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        w[i].dstSet = g.compute;
        w[i].dstBinding = i;
        w[i].descriptorCount = 1;
        w[i].descriptorType =
            i ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        w[i].pImageInfo = &di[i];
    }
    d.UpdateDescriptorSets(d.dev, 2, w, 0, nullptr); // Immutable after this call.
    cb.dispatches.push_back(std::move(g));
    cb.active = cb.dispatches.size() - 1;
    Dispatch &owned = cb.dispatches.back();
    // npuop (debug.knobs.npuop=1): NPU 结果就绪时由它把超分结果解包进本次 dispatch 自有的输出图,
    // 并打包本帧画面给下一帧推理; 没就绪/本帧解包集已用掉时返回 false, 照常走下面的 upop。
    if (g_npuop) {
        auto src_info = images.find({d.dev, owned.contract.image});
        uint32_t sw = src_info != images.end() ? src_info->second.w : 0;
        uint32_t sh = src_info != images.end() ? src_info->second.h : 0;
        if (npuop_record(handle, d, owned.contract.image, owned.contract.view, owned.contract.sampler, sw, sh,
                         owned.output, owned.view, owned.w, owned.h)) {
            if (g_upop)
                g_artifact_stats.record(reinterpret_cast<uintptr_t>(handle));
            g_cp.w = owned.w;
            g_cp.h = owned.h;
            g_cp.fmt = VK_FORMAT_R8G8B8A8_UNORM;
            ++g_cp.copies;
            return;
        }
    }
    VkImageMemoryBarrier barriers[2]{};
    barriers[0].sType = barriers[1].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    barriers[0].srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT;
    barriers[0].dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    barriers[0].oldLayout = barriers[0].newLayout = owned.contract.source_layout;
    barriers[0].image = owned.contract.image;
    barriers[1].dstAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    barriers[1].oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    barriers[1].newLayout = VK_IMAGE_LAYOUT_GENERAL;
    barriers[1].image = owned.output;
    for (auto &b : barriers) {
        b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
        b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    }
    d.CmdPipelineBarrier(handle, VK_PIPELINE_STAGE_ALL_GRAPHICS_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 2,
                         barriers);
    d.CmdBindPipeline(handle, VK_PIPELINE_BIND_POINT_COMPUTE, g_cp.pipe);
    d.CmdBindDescriptorSets(handle, VK_PIPELINE_BIND_POINT_COMPUTE, g_cp.pl, 0, 1, &owned.compute,
                            0, nullptr);
    d.CmdDispatch(handle, (owned.w + 15) / 16, (owned.h + 15) / 16, 1);
    if (g_upop)
        g_artifact_stats.record(reinterpret_cast<uintptr_t>(handle));
    VkImageMemoryBarrier end = barriers[1];
    end.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    end.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    end.oldLayout = VK_IMAGE_LAYOUT_GENERAL;
    end.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    d.CmdPipelineBarrier(handle, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 1, &end);
    g_cp.w = owned.w;
    g_cp.h = owned.h;
    g_cp.fmt = VK_FORMAT_R8G8B8A8_UNORM;
    ++g_cp.copies;
}
static VkDescriptorSet clone(VkCommandBuffer handle, const DevDisp &d, VkDescriptorSet game) {
    auto cb_it = cbs.find(handle);
    if (g_cp.disabled || cb_it == cbs.end())
        return game;
    CB &cb = cb_it->second;
    if (cb.active >= cb.dispatches.size())
        return game;
    auto si = sets.find({d.dev, game});
    if (si == sets.end() || !si->second.known) {
        reject("unknown_descriptor_set_identity");
        return game;
    }
    Set &set = si->second;
    Dispatch &g = cb.dispatches[cb.active];
    Element target{};
    bool found = false;
    for (const auto &entry : set.images) {
        const auto *layout_binding = binding(set.layout, entry.first.first);
        const auto &di = entry.second;
        if (!layout_binding) {
            reject("unknown_descriptor_binding");
            return game;
        }
        if (layout_binding->descriptorType != VK_DESCRIPTOR_TYPE_SAMPLER) {
            auto view = views.find({d.dev, di.view});
            if (view == views.end() || view->second.epoch != di.view_epoch ||
                !images.count({d.dev, view->second.image}) ||
                images.find({d.dev, view->second.image})->second.epoch !=
                    view->second.image_epoch) {
                reject("stale_descriptor_resource_identity");
                return game;
            }
        }
        if (layout_binding->descriptorType == VK_DESCRIPTOR_TYPE_SAMPLER ||
            layout_binding->descriptorType == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER) {
            auto sampler = samplers.find({d.dev, di.sampler});
            if (sampler == samplers.end() || sampler->second.epoch != di.sampler_epoch) {
                reject("stale_descriptor_resource_identity");
                return game;
            }
        }
    }
    for (const auto &kv : set.images)
        if (kv.second.view == g.contract.view && kv.second.view_epoch == g.contract.view_epoch) {
            if (found) {
                reject("ambiguous_source_binding");
                return game;
            }
            target = kv.first;
            found = true;
        }
    if (!found)
        return game; // Other/UI sets are forwarded unchanged.
    auto pool = app_pools.find({d.dev, set.pool});
    auto v = views.find({d.dev, g.contract.view});
    auto s = samplers.find({d.dev, g.contract.sampler});
    const auto *b = binding(set.layout, target.first);
    const auto &di = set.images[target];
    if (cb.dev != d.dev || !b || b->descriptorType != VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER ||
        b->descriptorCount != 1 || pool == app_pools.end() ||
        pool->second.epoch != set.pool_epoch || v == views.end() ||
        v->second.epoch != g.contract.view_epoch || s == samplers.end() ||
        s->second.epoch != g.contract.sampler_epoch || di.sampler != g.contract.sampler ||
        di.sampler_epoch != g.contract.sampler_epoch || di.layout != g.contract.source_layout) {
        reject("unsupported_or_stale_target_descriptor");
        return game;
    }
    for (const auto &c : g.clones)
        if (c.original == game && c.set_epoch == set.epoch && c.writes == set.writes) {
            ++g_cp.subs;
            return c.set;
        }
    size_t count = 0;
    for (const auto &group : cb.dispatches)
        count += group.clones.size();
    if (count >= kCloneLimit) {
        reject("descriptor_clone_limit");
        return game;
    }
    GraphicsClone c;
    c.original = game;
    c.set_epoch = set.epoch;
    c.writes = set.writes;
    VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    li.bindingCount = static_cast<uint32_t>(set.layout.bindings.size());
    li.pBindings = set.layout.bindings.data();
    bool ok = create_owned(c.layout,
                           [&](VkDescriptorSetLayout *out) {
                               return d.CreateDescriptorSetLayout(d.dev, &li, nullptr, out);
                           }) == VK_SUCCESS &&
              descriptor_pool(d, set.layout.bindings, c.pool);
    if (ok) {
        VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
        ai.descriptorPool = c.pool;
        ai.descriptorSetCount = 1;
        ai.pSetLayouts = &c.layout;
        ok = create_owned(c.set, [&](VkDescriptorSet *out) {
                 return d.AllocateDescriptorSets(d.dev, &ai, out);
             }) == VK_SUCCESS;
    }
    if (!ok) {
        if (c.pool)
            d.DestroyDescriptorPool(d.dev, c.pool, nullptr);
        if (c.layout)
            d.DestroyDescriptorSetLayout(d.dev, c.layout, nullptr);
        reject("graphics_clone_creation_failed");
        return game;
    }
    std::vector<VkCopyDescriptorSet> copies;
    for (const auto &binding : set.layout.bindings) {
        VkCopyDescriptorSet copy{VK_STRUCTURE_TYPE_COPY_DESCRIPTOR_SET};
        copy.srcSet = game;
        copy.dstSet = c.set;
        copy.srcBinding = copy.dstBinding = binding.binding;
        copy.descriptorCount = binding.descriptorCount;
        copies.push_back(copy);
    }
    d.UpdateDescriptorSets(d.dev, 0, nullptr, static_cast<uint32_t>(copies.size()), copies.data());
    VkDescriptorImageInfo image{di.sampler, g.view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
    VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    w.dstSet = c.set;
    w.dstBinding = target.first;
    w.dstArrayElement = target.second;
    w.descriptorCount = 1;
    w.descriptorType = b->descriptorType;
    w.pImageInfo = &image;
    d.UpdateDescriptorSets(d.dev, 1, &w, 0, nullptr);
    g.clones.push_back(c);
    ++g_cp.subs;
    return c.set;
}
#ifdef RK_LIFETIME_HOST_FIXTURE
// Only the host test translation unit defines this macro. No properties/environment
// flags can enable it. Inputs still come from actual intercepted identity callbacks.
static bool host_contract(VkCommandBuffer handle, VkImageView view, VkSampler sampler,
                          VkRenderPass rp, VkFramebuffer fb) {
    auto cb = cbs.find(handle);
    if (cb == cbs.end())
        return false;
    auto v = views.find({cb->second.dev, view});
    auto s = samplers.find({cb->second.dev, sampler});
    auto r = render_passes.find({cb->second.dev, rp});
    auto f = framebuffers.find({cb->second.dev, fb});
    if (v == views.end() || s == samplers.end() || r == render_passes.end() ||
        f == framebuffers.end())
        return false;
    Contract c;
    c.origin = Contract::Origin::HostFixture;
    c.allocation = cb->second.allocation;
    c.recording = cb->second.recording_epoch;
    c.view = view;
    c.view_epoch = v->second.epoch;
    c.image = v->second.image;
    c.image_epoch = v->second.image_epoch;
    c.sampler = sampler;
    c.sampler_epoch = s->second.epoch;
    c.render_pass = rp;
    c.render_pass_epoch = r->second;
    c.framebuffer = fb;
    c.framebuffer_epoch = f->second.epoch;
    c.compute_mutation = cb->second.compute_mutation;
    c.source_layout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    cb->second.contract = c;
    return true;
}
#endif
} // namespace rk_lifetime
