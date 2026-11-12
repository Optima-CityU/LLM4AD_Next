// interp_bm.cpp — 插帧合成方式 "bm" (debug.knobs.interp_mode=bm): 路线 B 的块匹配运动补偿, 不用 NPU 残差。
//
// interp.cpp 每次 present 在真实帧 N 之前多送一张 L, 这里负责算 L = 前一帧 H 与当前帧 N 在 t=0.5 的中间帧:
//   N (swapchain 图) → C (本模块的采样图; swapchain 图不一定给 SAMPLED);
//   interp_b_luma   H, C → 1/4 分辨率整数亮度 L0, L1
//   interp_b_bm     L0, L1 → 每 8x8 块 (全分辨率 32x32) 的整数位移 (±16 个 1/4 像素 = ±64 像素)
//   interp_b_smooth 位移场 3x3 中值 + 5x5 二项式平滑, 换算成全分辨率像素
//   interp_b_compose 两帧沿运动场反向采样到 t=0.5 取平均 (残差缓冲全零 = 不加 NPU 残差) → O
//   O → L (拷贝; swapchain 图不一定给 STORAGE)。
// 资源按 (device, 宽, 高) 建一次; 缺函数、建不成或第一帧没有 H 时返回 false, interp.cpp 退回 dup 并计数。
#include "interp.h"

#include <android/log.h>

#include <cstring>
#include <map>
#include <mutex>

#if __has_include("interp_spv.h")
#include "interp_spv.h"   // build_layer.sh 生成
#define BM_HAVE_SPV 1
#else
#define BM_HAVE_SPV 0
#endif

#define BM_LOG(...) __android_log_print(ANDROID_LOG_INFO, "refknobs", "interp_bm: " __VA_ARGS__)

namespace {

struct Fn {
#define BM_FN(n) PFN_vk##n n = nullptr;
    BM_FN(CreateImage) BM_FN(GetImageMemoryRequirements) BM_FN(AllocateMemory) BM_FN(BindImageMemory)
    BM_FN(CreateImageView) BM_FN(CreateBuffer) BM_FN(GetBufferMemoryRequirements) BM_FN(BindBufferMemory)
    BM_FN(CreateSampler) BM_FN(CreateDescriptorSetLayout) BM_FN(CreateDescriptorPool) BM_FN(AllocateDescriptorSets)
    BM_FN(UpdateDescriptorSets) BM_FN(CreatePipelineLayout) BM_FN(CreateShaderModule) BM_FN(DestroyShaderModule)
    BM_FN(CreateComputePipelines) BM_FN(CmdPipelineBarrier) BM_FN(CmdCopyImage) BM_FN(CmdBindPipeline)
    BM_FN(CmdBindDescriptorSets) BM_FN(CmdPushConstants) BM_FN(CmdDispatch) BM_FN(CmdFillBuffer)
#undef BM_FN
    PFN_vkGetPhysicalDeviceMemoryProperties GetPhysicalDeviceMemoryProperties = nullptr;
};

struct Stage {
    VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
    VkPipelineLayout pl = VK_NULL_HANDLE;
    VkPipeline pipe = VK_NULL_HANDLE;
    VkDescriptorSet set = VK_NULL_HANDLE;
};

struct Res {
    bool tried = false, ok = false, zeroed = false;
    uint32_t W = 0, H = 0, qw = 0, qh = 0, gw = 0, gh = 0;
    VkImage C = VK_NULL_HANDLE, O = VK_NULL_HANDLE;
    VkImageView C_view = VK_NULL_HANDLE, O_view = VK_NULL_HANDLE;
    VkImageView H_view = VK_NULL_HANDLE;   // 历史图 H 的视图 (H 变了就重建)
    VkImage H_for_view = VK_NULL_HANDLE;
    VkBuffer l0 = VK_NULL_HANDLE, l1 = VK_NULL_HANDLE, mv = VK_NULL_HANDLE, flow = VK_NULL_HANDLE,
             res = VK_NULL_HANDLE, hud = VK_NULL_HANDLE;
    VkSampler sampler = VK_NULL_HANDLE;
    VkDescriptorPool pool = VK_NULL_HANDLE;
    Stage luma, bm, smooth, compose;
};

std::mutex g_m;
std::map<VkDevice, Fn> g_fn;
std::map<VkDevice, Res> g_res;
uint64_t g_ok = 0, g_declined = 0;

bool load(VkDevice dev, PFN_vkGetDeviceProcAddr gdpa, Fn& f) {
#define BM_GD(n) f.n = (PFN_vk##n)gdpa(dev, "vk" #n); if (!f.n) return false;
    BM_GD(CreateImage) BM_GD(GetImageMemoryRequirements) BM_GD(AllocateMemory) BM_GD(BindImageMemory)
    BM_GD(CreateImageView) BM_GD(CreateBuffer) BM_GD(GetBufferMemoryRequirements) BM_GD(BindBufferMemory)
    BM_GD(CreateSampler) BM_GD(CreateDescriptorSetLayout) BM_GD(CreateDescriptorPool) BM_GD(AllocateDescriptorSets)
    BM_GD(UpdateDescriptorSets) BM_GD(CreatePipelineLayout) BM_GD(CreateShaderModule) BM_GD(DestroyShaderModule)
    BM_GD(CreateComputePipelines) BM_GD(CmdPipelineBarrier) BM_GD(CmdCopyImage) BM_GD(CmdBindPipeline)
    BM_GD(CmdBindDescriptorSets) BM_GD(CmdPushConstants) BM_GD(CmdDispatch) BM_GD(CmdFillBuffer)
#undef BM_GD
    return true;
}

// 显存类型: 物理设备在 interp.cpp 里, 这里拿不到 —— 选 memoryTypeBits 里第一个 (Adreno 上统一内存, 都可用)
uint32_t first_type(uint32_t bits) {
    for (uint32_t i = 0; i < 32; i++) if (bits & (1u << i)) return i;
    return UINT32_MAX;
}

bool alloc_bind_image(VkDevice dev, Fn& f, VkImage img) {
    VkMemoryRequirements mr{};
    f.GetImageMemoryRequirements(dev, img, &mr);
    VkMemoryAllocateInfo mai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, first_type(mr.memoryTypeBits)};
    VkDeviceMemory m = VK_NULL_HANDLE;
    return mai.memoryTypeIndex != UINT32_MAX && f.AllocateMemory(dev, &mai, nullptr, &m) == VK_SUCCESS &&
           f.BindImageMemory(dev, img, m, 0) == VK_SUCCESS;
}

bool make_image(VkDevice dev, Fn& f, uint32_t w, uint32_t h, VkImageUsageFlags usage, VkImage* img, VkImageView* view) {
    VkImageCreateInfo ici{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ici.imageType = VK_IMAGE_TYPE_2D; ici.format = VK_FORMAT_R8G8B8A8_UNORM; ici.extent = {w, h, 1};
    ici.mipLevels = 1; ici.arrayLayers = 1; ici.samples = VK_SAMPLE_COUNT_1_BIT; ici.tiling = VK_IMAGE_TILING_OPTIMAL;
    ici.usage = usage; ici.sharingMode = VK_SHARING_MODE_EXCLUSIVE; ici.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (f.CreateImage(dev, &ici, nullptr, img) != VK_SUCCESS || !alloc_bind_image(dev, f, *img)) return false;
    VkImageViewCreateInfo vci{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vci.image = *img; vci.viewType = VK_IMAGE_VIEW_TYPE_2D; vci.format = VK_FORMAT_R8G8B8A8_UNORM;
    vci.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    return f.CreateImageView(dev, &vci, nullptr, view) == VK_SUCCESS;
}

bool make_buffer(VkDevice dev, Fn& f, VkDeviceSize size, VkBufferUsageFlags usage, VkBuffer* b) {
    VkBufferCreateInfo bci{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bci.size = size; bci.usage = usage; bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    if (f.CreateBuffer(dev, &bci, nullptr, b) != VK_SUCCESS) return false;
    VkMemoryRequirements mr{};
    f.GetBufferMemoryRequirements(dev, *b, &mr);
    VkMemoryAllocateInfo mai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, nullptr, mr.size, first_type(mr.memoryTypeBits)};
    VkDeviceMemory m = VK_NULL_HANDLE;
    return mai.memoryTypeIndex != UINT32_MAX && f.AllocateMemory(dev, &mai, nullptr, &m) == VK_SUCCESS &&
           f.BindBufferMemory(dev, *b, m, 0) == VK_SUCCESS;
}

// types: 每个 binding 的描述符类型, 下标即 binding 号; VK_DESCRIPTOR_TYPE_MAX_ENUM = 该号不用
bool make_stage(VkDevice dev, Fn& f, VkDescriptorPool pool, const VkDescriptorType* types, int n, uint32_t pc_size,
                const unsigned char* spv, unsigned spv_len, Stage& s) {
    VkDescriptorSetLayoutBinding b[8];
    int m = 0;
    for (int i = 0; i < n; i++)
        if (types[i] != VK_DESCRIPTOR_TYPE_MAX_ENUM) b[m++] = {(uint32_t)i, types[i], 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr};
    VkDescriptorSetLayoutCreateInfo dli{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, nullptr, 0, (uint32_t)m, b};
    if (f.CreateDescriptorSetLayout(dev, &dli, nullptr, &s.dsl) != VK_SUCCESS) return false;
    VkPushConstantRange pcr{VK_SHADER_STAGE_COMPUTE_BIT, 0, pc_size};
    VkPipelineLayoutCreateInfo pli{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, nullptr, 0, 1, &s.dsl, 1, &pcr};
    if (f.CreatePipelineLayout(dev, &pli, nullptr, &s.pl) != VK_SUCCESS) return false;
    VkShaderModuleCreateInfo smi{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, nullptr, 0, spv_len, reinterpret_cast<const uint32_t*>(spv)};
    VkShaderModule sm = VK_NULL_HANDLE;
    if (f.CreateShaderModule(dev, &smi, nullptr, &sm) != VK_SUCCESS) return false;
    VkComputePipelineCreateInfo cpi{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    cpi.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0, VK_SHADER_STAGE_COMPUTE_BIT, sm, "main", nullptr};
    cpi.layout = s.pl;
    VkResult r = f.CreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpi, nullptr, &s.pipe);
    f.DestroyShaderModule(dev, sm, nullptr);
    if (r != VK_SUCCESS) return false;
    VkDescriptorSetAllocateInfo dai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, nullptr, pool, 1, &s.dsl};
    return f.AllocateDescriptorSets(dev, &dai, &s.set) == VK_SUCCESS;
}

void write_img(VkDevice dev, Fn& f, VkDescriptorSet set, uint32_t binding, VkDescriptorType t, VkSampler smp,
               VkImageView v, VkImageLayout layout) {
    VkDescriptorImageInfo ii{smp, v, layout};
    VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, set, binding, 0, 1, t, &ii, nullptr, nullptr};
    f.UpdateDescriptorSets(dev, 1, &w, 0, nullptr);
}
void write_buf(VkDevice dev, Fn& f, VkDescriptorSet set, uint32_t binding, VkDescriptorType t, VkBuffer b) {
    VkDescriptorBufferInfo bi{b, 0, VK_WHOLE_SIZE};
    VkWriteDescriptorSet w{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, nullptr, set, binding, 0, 1, t, nullptr, &bi, nullptr};
    f.UpdateDescriptorSets(dev, 1, &w, 0, nullptr);
}

bool build(VkDevice dev, Fn& f, Res& R, uint32_t W, uint32_t H) {
#if !BM_HAVE_SPV
    (void)dev; (void)f; (void)R; (void)W; (void)H;
    return false;
#else
    R.W = W; R.H = H;
    R.qw = (W + 3) / 4; R.qh = (H + 3) / 4;
    R.gw = (R.qw + 7) / 8; R.gh = (R.qh + 7) / 8;
    const VkBufferUsageFlags SB = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    if (!make_image(dev, f, W, H, VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT, &R.C, &R.C_view)) return false;
    if (!make_image(dev, f, W, H, VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT, &R.O, &R.O_view)) return false;
    if (!make_buffer(dev, f, (VkDeviceSize)R.qw * R.qh * 4, SB, &R.l0) || !make_buffer(dev, f, (VkDeviceSize)R.qw * R.qh * 4, SB, &R.l1) ||
        !make_buffer(dev, f, (VkDeviceSize)R.gw * R.gh * 8, SB, &R.mv) || !make_buffer(dev, f, (VkDeviceSize)R.gw * R.gh * 8, SB, &R.flow) ||
        !make_buffer(dev, f, 16, SB, &R.res) ||
        !make_buffer(dev, f, 16 * 16 + 16, VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT, &R.hud))
        return false;
    VkSamplerCreateInfo sci{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    sci.magFilter = sci.minFilter = VK_FILTER_NEAREST;
    sci.addressModeU = sci.addressModeV = sci.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    if (f.CreateSampler(dev, &sci, nullptr, &R.sampler) != VK_SUCCESS) return false;
    VkDescriptorPoolSize ps[4] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 8}, {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 16},
                                  {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 4}, {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 4}};
    VkDescriptorPoolCreateInfo dpi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, nullptr, 0, 8, 4, ps};
    if (f.CreateDescriptorPool(dev, &dpi, nullptr, &R.pool) != VK_SUCCESS) return false;
    const VkDescriptorType CIS = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, SBUF = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                           SIMG = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, UBO = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
    const VkDescriptorType t_luma[] = {CIS, CIS, SBUF, SBUF};
    const VkDescriptorType t_bm[] = {SBUF, SBUF, SBUF};
    const VkDescriptorType t_smooth[] = {SBUF, SBUF};
    const VkDescriptorType t_comp[] = {CIS, CIS, SBUF, SBUF, SIMG, UBO};
    if (!make_stage(dev, f, R.pool, t_luma, 4, 16, kInterpBLumaSpv, kInterpBLumaSpvLen, R.luma) ||
        !make_stage(dev, f, R.pool, t_bm, 3, 16, kInterpBBmSpv, kInterpBBmSpvLen, R.bm) ||
        !make_stage(dev, f, R.pool, t_smooth, 2, 12, kInterpBSmoothSpv, kInterpBSmoothSpvLen, R.smooth) ||
        !make_stage(dev, f, R.pool, t_comp, 6, 44, kInterpBComposeSpv, kInterpBComposeSpvLen, R.compose))
        return false;
    // 不随帧变的绑定 (H 的视图每帧可能不同, 在 synth 里写 binding 0)
    write_img(dev, f, R.luma.set, 1, CIS, R.sampler, R.C_view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    write_buf(dev, f, R.luma.set, 2, SBUF, R.l0);
    write_buf(dev, f, R.luma.set, 3, SBUF, R.l1);
    write_buf(dev, f, R.bm.set, 0, SBUF, R.l0);
    write_buf(dev, f, R.bm.set, 1, SBUF, R.l1);
    write_buf(dev, f, R.bm.set, 2, SBUF, R.mv);
    write_buf(dev, f, R.smooth.set, 0, SBUF, R.mv);
    write_buf(dev, f, R.smooth.set, 1, SBUF, R.flow);
    write_img(dev, f, R.compose.set, 1, CIS, R.sampler, R.C_view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    write_buf(dev, f, R.compose.set, 2, SBUF, R.flow);
    write_buf(dev, f, R.compose.set, 3, SBUF, R.res);
    write_img(dev, f, R.compose.set, 4, SIMG, VK_NULL_HANDLE, R.O_view, VK_IMAGE_LAYOUT_GENERAL);
    write_buf(dev, f, R.compose.set, 5, UBO, R.hud);
    BM_LOG("资源就绪 %ux%u q=%ux%u blocks=%ux%u", W, H, R.qw, R.qh, R.gw, R.gh);
    return true;
#endif
}

void img_barrier(Fn& f, VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to, VkAccessFlags sa,
                 VkAccessFlags da, VkPipelineStageFlags ss, VkPipelineStageFlags ds) {
    VkImageMemoryBarrier ib{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    ib.srcAccessMask = sa; ib.dstAccessMask = da; ib.oldLayout = from; ib.newLayout = to;
    ib.srcQueueFamilyIndex = ib.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    ib.image = img; ib.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    f.CmdPipelineBarrier(cb, ss, ds, 0, 0, nullptr, 0, nullptr, 1, &ib);
}
void mem_barrier(Fn& f, VkCommandBuffer cb, VkAccessFlags sa, VkAccessFlags da, VkPipelineStageFlags ss, VkPipelineStageFlags ds) {
    VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER, nullptr, sa, da};
    f.CmdPipelineBarrier(cb, ss, ds, 0, 1, &mb, 0, nullptr, 0, nullptr);
}

bool synth_bm(const IpSynthCtx& c, void*) {
    if (!c.prev || (c.fmt != VK_FORMAT_R8G8B8A8_UNORM && c.fmt != VK_FORMAT_R8G8B8A8_SRGB)) { g_declined++; return false; }
    std::lock_guard<std::mutex> lk(g_m);
    Fn& f = g_fn[c.dev];
    Res& R = g_res[c.dev];
    if (!R.tried || R.W != c.w || R.H != c.h) {
        if (R.tried && (R.W != c.w || R.H != c.h)) R = Res{};   // 尺寸变了: 旧对象不收 (极少见), 重建
        R.tried = true;
        R.ok = load(c.dev, c.gdpa, f) && build(c.dev, f, R, c.w, c.h);
        if (!R.ok) BM_LOG("建资源失败, 退回 dup");
    }
    if (!R.ok) { g_declined++; return false; }
    if (R.H_for_view != c.prev) {   // 历史图换了 (swapchain 重建): 新视图
        VkImageViewCreateInfo vci{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
        vci.image = c.prev; vci.viewType = VK_IMAGE_VIEW_TYPE_2D; vci.format = VK_FORMAT_R8G8B8A8_UNORM;
        vci.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        if (f.CreateImageView(c.dev, &vci, nullptr, &R.H_view) != VK_SUCCESS) { g_declined++; return false; }
        R.H_for_view = c.prev;
        write_img(c.dev, f, R.luma.set, 0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, R.sampler, R.H_view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
        write_img(c.dev, f, R.compose.set, 0, VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, R.sampler, R.H_view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL);
    }
    VkCommandBuffer cb = c.cb;
    const VkPipelineStageFlags TR = VK_PIPELINE_STAGE_TRANSFER_BIT, CS = VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT;
    const VkAccessFlags ALL = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    mem_barrier(f, cb, ALL, ALL, TR | CS, TR | CS);   // 上一帧对 C/O/缓冲的读写全部完成
    if (!R.zeroed) {   // 残差与 HUD 框: 全零 (不加残差、不排除 HUD)
        f.CmdFillBuffer(cb, R.res, 0, VK_WHOLE_SIZE, 0);
        f.CmdFillBuffer(cb, R.hud, 0, VK_WHOLE_SIZE, 0);
        R.zeroed = true;
    }
    // N → C
    img_barrier(f, cb, R.C, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 0, VK_ACCESS_TRANSFER_WRITE_BIT, TR, TR);
    VkImageCopy rg{};
    rg.srcSubresource = rg.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    rg.extent = {c.w, c.h, 1};
    f.CmdCopyImage(cb, c.cur, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, R.C, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &rg);
    img_barrier(f, cb, R.C, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, TR, CS);
    img_barrier(f, cb, c.prev, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, TR, CS);
    // 1. 亮度
    int32_t p_luma[4] = {(int32_t)c.w, (int32_t)c.h, (int32_t)R.qw, (int32_t)R.qh};
    f.CmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.luma.pipe);
    f.CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.luma.pl, 0, 1, &R.luma.set, 0, nullptr);
    f.CmdPushConstants(cb, R.luma.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof p_luma, p_luma);
    f.CmdDispatch(cb, (R.qw + 7) / 8, (R.qh + 7) / 8, 2);
    mem_barrier(f, cb, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, CS, CS);
    // 2. 块匹配
    int32_t p_bm[4] = {(int32_t)R.qw, (int32_t)R.qh, (int32_t)R.gw, (int32_t)R.gh};
    f.CmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.bm.pipe);
    f.CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.bm.pl, 0, 1, &R.bm.set, 0, nullptr);
    f.CmdPushConstants(cb, R.bm.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof p_bm, p_bm);
    f.CmdDispatch(cb, R.gw, R.gh, 1);
    mem_barrier(f, cb, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT, CS, CS);
    // 3. 平滑
    int32_t p_sm[3] = {(int32_t)R.gw, (int32_t)R.gh, 4};
    f.CmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.smooth.pipe);
    f.CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.smooth.pl, 0, 1, &R.smooth.set, 0, nullptr);
    f.CmdPushConstants(cb, R.smooth.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof p_sm, p_sm);
    f.CmdDispatch(cb, (R.gw * R.gh + 63) / 64, 1, 1);
    mem_barrier(f, cb, VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_UNIFORM_READ_BIT, CS | TR, CS);
    // 4. 合成 t=0.5
    img_barrier(f, cb, R.O, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_GENERAL, 0, VK_ACCESS_SHADER_WRITE_BIT, CS, CS);
    struct { int32_t W, H, gw, gh; float cell; int32_t rw, rh; float t, res_scale, res_zp; int32_t hud_on; } p_c{
        (int32_t)c.w, (int32_t)c.h, (int32_t)R.gw, (int32_t)R.gh, 32.f, 1, 1, 0.5f, 0.f, 0.f, 0};
    f.CmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.compose.pipe);
    f.CmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, R.compose.pl, 0, 1, &R.compose.set, 0, nullptr);
    f.CmdPushConstants(cb, R.compose.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof p_c, &p_c);
    f.CmdDispatch(cb, (c.w + 7) / 8, (c.h + 7) / 8, 1);
    // O → L; H 回到 TRANSFER_SRC
    img_barrier(f, cb, R.O, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT, CS, TR);
    f.CmdCopyImage(cb, R.O, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, c.dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &rg);
    img_barrier(f, cb, c.prev, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_ACCESS_SHADER_READ_BIT, VK_ACCESS_TRANSFER_WRITE_BIT | VK_ACCESS_TRANSFER_READ_BIT, CS, TR);
    g_ok++;
    return true;
}

struct Reg { Reg() { ip_register_synth("bm", synth_bm, nullptr); } } g_reg;

}  // namespace
