// harness.cpp — 插帧 GPU 侧 (knobs/gray/layer/interp_*.comp) 的宿主侧核对与计时工具。
// 读裸文件、按路线 A / B 的顺序逐个 dispatch 编好的 SPIR-V、把每一级中间缓冲原样写回裸文件, 由 validate.py
// 拿 numpy 参考逐级比对; 同时用时间戳查询量每个 dispatch 的 GPU 耗时 (JSON 打到 stdout)。
// 只依赖 Vulkan 1.1 核心: Mac 上走 MoltenVK (VK_ICD_FILENAMES 指到 MoltenVK_icd.json), 手机上用 NDK 直编。
// 用法: harness spv=<目录> route=a|b f0=<rgba8 裸图> f1=<...> W= H= [reps=5] 其余键见 run_a / run_b。
#include <vulkan/vulkan.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

namespace {

struct Ctx {
    VkInstance inst = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    VkDevice dev = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t qfam = 0;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkQueryPool qpool = VK_NULL_HANDLE;
    VkSampler sampler = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties props{};
    VkPhysicalDeviceMemoryProperties mem{};
};

[[noreturn]] void die(const char* what, VkResult r = VK_SUCCESS) {
    fprintf(stderr, "harness: %s (VkResult %d)\n", what, (int)r);
    exit(2);
}
#define VK(x) do { VkResult _r = (x); if (_r != VK_SUCCESS) die(#x, _r); } while (0)

struct Buf { VkBuffer b = VK_NULL_HANDLE; VkDeviceMemory m = VK_NULL_HANDLE; void* map = nullptr; VkDeviceSize size = 0; };
struct Img { VkImage i = VK_NULL_HANDLE; VkDeviceMemory m = VK_NULL_HANDLE; VkImageView v = VK_NULL_HANDLE; int w = 0, h = 0; };

uint32_t find_mem(const Ctx& c, uint32_t bits, VkMemoryPropertyFlags req) {
    for (uint32_t i = 0; i < c.mem.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (c.mem.memoryTypes[i].propertyFlags & req) == req) return i;
    die("没有合适的内存类型");
}

Buf make_buf(const Ctx& c, VkDeviceSize size, VkBufferUsageFlags usage) {
    Buf b; b.size = size;
    VkBufferCreateInfo bc{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bc.size = size; bc.usage = usage; bc.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    VK(vkCreateBuffer(c.dev, &bc, nullptr, &b.b));
    VkMemoryRequirements mr; vkGetBufferMemoryRequirements(c.dev, b.b, &mr);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = mr.size;
    ai.memoryTypeIndex = find_mem(c, mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    VK(vkAllocateMemory(c.dev, &ai, nullptr, &b.m));
    VK(vkBindBufferMemory(c.dev, b.b, b.m, 0));
    VK(vkMapMemory(c.dev, b.m, 0, size, 0, &b.map));
    memset(b.map, 0, size);
    return b;
}

template <class F> void once(const Ctx& c, F rec) {
    VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = c.pool; ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ai.commandBufferCount = 1;
    VkCommandBuffer cb; VK(vkAllocateCommandBuffers(c.dev, &ai, &cb));
    VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VK(vkBeginCommandBuffer(cb, &bi));
    rec(cb);
    VK(vkEndCommandBuffer(cb));
    VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO}; si.commandBufferCount = 1; si.pCommandBuffers = &cb;
    VK(vkQueueSubmit(c.queue, 1, &si, VK_NULL_HANDLE));
    VK(vkQueueWaitIdle(c.queue));
    vkFreeCommandBuffers(c.dev, c.pool, 1, &cb);
}

void barrier_img(VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to) {
    VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    b.oldLayout = from; b.newLayout = to;
    b.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT | VK_ACCESS_MEMORY_READ_BIT;
    b.dstAccessMask = VK_ACCESS_MEMORY_WRITE_BIT | VK_ACCESS_MEMORY_READ_BIT;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = img; b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr, 0, nullptr, 1, &b);
}

// rgba8 图, 布局固定为 GENERAL (采样、存储、拷贝都允许)
Img make_img(const Ctx& c, int w, int h, const void* rgba /* 可空 */) {
    Img im; im.w = w; im.h = h;
    VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    ic.imageType = VK_IMAGE_TYPE_2D; ic.format = VK_FORMAT_R8G8B8A8_UNORM;
    ic.extent = {(uint32_t)w, (uint32_t)h, 1}; ic.mipLevels = 1; ic.arrayLayers = 1;
    ic.samples = VK_SAMPLE_COUNT_1_BIT; ic.tiling = VK_IMAGE_TILING_OPTIMAL;
    ic.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    ic.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VK(vkCreateImage(c.dev, &ic, nullptr, &im.i));
    VkMemoryRequirements mr; vkGetImageMemoryRequirements(c.dev, im.i, &mr);
    VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    ai.allocationSize = mr.size; ai.memoryTypeIndex = find_mem(c, mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    VK(vkAllocateMemory(c.dev, &ai, nullptr, &im.m));
    VK(vkBindImageMemory(c.dev, im.i, im.m, 0));
    VkImageViewCreateInfo vc{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    vc.image = im.i; vc.viewType = VK_IMAGE_VIEW_TYPE_2D; vc.format = ic.format;
    vc.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VK(vkCreateImageView(c.dev, &vc, nullptr, &im.v));
    Buf st = make_buf(c, (VkDeviceSize)w * h * 4, VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
    if (rgba) memcpy(st.map, rgba, (size_t)w * h * 4);
    once(c, [&](VkCommandBuffer cb) {
        barrier_img(cb, im.i, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        VkBufferImageCopy r{}; r.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}; r.imageExtent = {(uint32_t)w, (uint32_t)h, 1};
        vkCmdCopyBufferToImage(cb, st.b, im.i, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &r);
        barrier_img(cb, im.i, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL);
    });
    vkDestroyBuffer(c.dev, st.b, nullptr); vkFreeMemory(c.dev, st.m, nullptr);
    return im;
}

std::vector<uint8_t> read_img(const Ctx& c, const Img& im) {
    Buf st = make_buf(c, (VkDeviceSize)im.w * im.h * 4, VK_BUFFER_USAGE_TRANSFER_DST_BIT);
    once(c, [&](VkCommandBuffer cb) {
        VkBufferImageCopy r{}; r.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}; r.imageExtent = {(uint32_t)im.w, (uint32_t)im.h, 1};
        vkCmdCopyImageToBuffer(cb, im.i, VK_IMAGE_LAYOUT_GENERAL, st.b, 1, &r);
    });
    std::vector<uint8_t> out((size_t)im.w * im.h * 4);
    memcpy(out.data(), st.map, out.size());
    vkDestroyBuffer(c.dev, st.b, nullptr); vkFreeMemory(c.dev, st.m, nullptr);
    return out;
}

struct Bind { VkDescriptorType type; VkImageView view = VK_NULL_HANDLE; VkBuffer buf = VK_NULL_HANDLE; VkDeviceSize size = 0; };
Bind bsamp(const Img& i) { return {VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, i.v}; }
Bind bstore(const Img& i) { return {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, i.v}; }
Bind bssbo(const Buf& b) { return {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, VK_NULL_HANDLE, b.b, b.size}; }
Bind bubo(const Buf& b) { return {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, VK_NULL_HANDLE, b.b, b.size}; }

struct Pipe {
    VkDescriptorSetLayout dsl = VK_NULL_HANDLE; VkPipelineLayout pl = VK_NULL_HANDLE; VkPipeline p = VK_NULL_HANDLE;
    VkDescriptorPool dp = VK_NULL_HANDLE; VkDescriptorSet ds = VK_NULL_HANDLE; VkShaderModule sm = VK_NULL_HANDLE;
    std::string name;
};

std::vector<uint8_t> read_file(const std::string& path) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) { fprintf(stderr, "harness: 打不开 %s\n", path.c_str()); exit(2); }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> d((size_t)std::max(0L, n));
    if (n > 0 && fread(d.data(), 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "harness: 读不全 %s\n", path.c_str()); exit(2); }
    fclose(f);
    return d;
}

void write_file(const std::string& path, const void* d, size_t n) {
    if (path.empty()) return;
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { fprintf(stderr, "harness: 写不了 %s\n", path.c_str()); exit(2); }
    fwrite(d, 1, n, f); fclose(f);
}

Pipe make_pipe(const Ctx& c, const std::string& spv_dir, const std::string& name, const std::vector<Bind>& binds, uint32_t push_size) {
    Pipe p; p.name = name;
    std::vector<uint8_t> code = read_file(spv_dir + "/" + name + ".spv");
    VkShaderModuleCreateInfo sc{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    sc.codeSize = code.size(); sc.pCode = (const uint32_t*)code.data();
    VK(vkCreateShaderModule(c.dev, &sc, nullptr, &p.sm));
    std::vector<VkDescriptorSetLayoutBinding> lb(binds.size());
    std::map<VkDescriptorType, uint32_t> cnt;
    for (size_t i = 0; i < binds.size(); i++) {
        lb[i].binding = (uint32_t)i; lb[i].descriptorType = binds[i].type; lb[i].descriptorCount = 1;
        lb[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT; cnt[binds[i].type]++;
    }
    VkDescriptorSetLayoutCreateInfo lc{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    lc.bindingCount = (uint32_t)lb.size(); lc.pBindings = lb.data();
    VK(vkCreateDescriptorSetLayout(c.dev, &lc, nullptr, &p.dsl));
    VkPushConstantRange pr{VK_SHADER_STAGE_COMPUTE_BIT, 0, push_size};
    VkPipelineLayoutCreateInfo pc{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    pc.setLayoutCount = 1; pc.pSetLayouts = &p.dsl; pc.pushConstantRangeCount = push_size ? 1 : 0; pc.pPushConstantRanges = &pr;
    VK(vkCreatePipelineLayout(c.dev, &pc, nullptr, &p.pl));
    VkComputePipelineCreateInfo ci{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
    ci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT; ci.stage.module = p.sm; ci.stage.pName = "main"; ci.layout = p.pl;
    VK(vkCreateComputePipelines(c.dev, VK_NULL_HANDLE, 1, &ci, nullptr, &p.p));
    std::vector<VkDescriptorPoolSize> ps;
    for (auto& kv : cnt) ps.push_back({kv.first, kv.second});
    VkDescriptorPoolCreateInfo dpc{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpc.maxSets = 1; dpc.poolSizeCount = (uint32_t)ps.size(); dpc.pPoolSizes = ps.data();
    VK(vkCreateDescriptorPool(c.dev, &dpc, nullptr, &p.dp));
    VkDescriptorSetAllocateInfo da{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    da.descriptorPool = p.dp; da.descriptorSetCount = 1; da.pSetLayouts = &p.dsl;
    VK(vkAllocateDescriptorSets(c.dev, &da, &p.ds));
    std::vector<VkWriteDescriptorSet> ws(binds.size());
    std::vector<VkDescriptorImageInfo> ii(binds.size());
    std::vector<VkDescriptorBufferInfo> bi(binds.size());
    for (size_t i = 0; i < binds.size(); i++) {
        ws[i] = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        ws[i].dstSet = p.ds; ws[i].dstBinding = (uint32_t)i; ws[i].descriptorCount = 1; ws[i].descriptorType = binds[i].type;
        if (binds[i].view) {
            ii[i] = {c.sampler, binds[i].view, VK_IMAGE_LAYOUT_GENERAL}; ws[i].pImageInfo = &ii[i];
        } else {
            bi[i] = {binds[i].buf, 0, binds[i].size}; ws[i].pBufferInfo = &bi[i];
        }
    }
    vkUpdateDescriptorSets(c.dev, (uint32_t)ws.size(), ws.data(), 0, nullptr);
    return p;
}

// 一次 dispatch, 返回 GPU 时间 (ms, 时间戳查询)
double dispatch(const Ctx& c, const Pipe& p, const void* push, uint32_t push_size, uint32_t gx, uint32_t gy, uint32_t gz) {
    once(c, [&](VkCommandBuffer cb) {
        vkCmdResetQueryPool(cb, c.qpool, 0, 2);
        vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, c.qpool, 0);
        vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.p);
        vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, p.pl, 0, 1, &p.ds, 0, nullptr);
        if (push_size) vkCmdPushConstants(cb, p.pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, push_size, push);
        vkCmdDispatch(cb, gx, gy, gz);
        VkMemoryBarrier mb{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
        mb.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT; mb.dstAccessMask = VK_ACCESS_HOST_READ_BIT | VK_ACCESS_SHADER_READ_BIT;
        vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &mb, 0, nullptr, 0, nullptr);
        vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, c.qpool, 1);
    });
    uint64_t ts[2] = {0, 0};
    VK(vkGetQueryPoolResults(c.dev, c.qpool, 0, 2, sizeof(ts), ts, sizeof(uint64_t), VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT));
    return (double)(ts[1] - ts[0]) * c.props.limits.timestampPeriod / 1e6;
}

struct Timing { std::string name; std::vector<double> ms; };
std::vector<Timing> g_times;

// 跑 reps 次取样 (第一次往往带编译/预热, 一并记下, 由统计端取中位数)
void run(const Ctx& c, const Pipe& p, const void* push, uint32_t push_size, uint32_t gx, uint32_t gy, uint32_t gz, int reps) {
    Timing t; t.name = p.name;
    for (int i = 0; i < std::max(1, reps); i++) t.ms.push_back(dispatch(c, p, push, push_size, gx, gy, gz));
    g_times.push_back(t);
}

uint32_t cdiv(uint32_t a, uint32_t b) { return (a + b - 1) / b; }

struct Args {
    std::map<std::string, std::string> kv;
    std::string s(const char* k, const char* def = "") const { auto it = kv.find(k); return it == kv.end() ? std::string(def) : it->second; }
    int i(const char* k, int def = 0) const { auto it = kv.find(k); return it == kv.end() ? def : atoi(it->second.c_str()); }
    float f(const char* k, float def = 0) const { auto it = kv.find(k); return it == kv.end() ? def : (float)atof(it->second.c_str()); }
    bool has(const char* k) const { return kv.count(k) != 0; }
};

// HUD 框文件: 每行 "x0 y0 x1 y1" (半开区间), 最多 16 个; 写成 std140 的 ivec4 box[16] + ivec4 count
Buf make_hud(const Ctx& c, const std::string& path, int* n_out) {
    Buf b = make_buf(c, 16 * 16 + 16, VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT);
    int32_t* d = (int32_t*)b.map; int n = 0;
    if (!path.empty()) {
        FILE* f = fopen(path.c_str(), "r");
        if (!f) { fprintf(stderr, "harness: 打不开 HUD 框文件 %s\n", path.c_str()); exit(2); }
        int x0, y0, x1, y1;
        while (n < 16 && fscanf(f, "%d %d %d %d", &x0, &y0, &x1, &y1) == 4) { d[n * 4] = x0; d[n * 4 + 1] = y0; d[n * 4 + 2] = x1; d[n * 4 + 3] = y1; n++; }
        fclose(f);
    }
    d[64] = n; *n_out = n;
    return b;
}

// 路线 A: f0,f1 -> interp_a_down -> (NPU: flow/mask 由文件给) -> interp_a_blend
void run_a(const Ctx& c, const Args& a, const std::string& spv) {
    int W = a.i("W"), H = a.i("H"), w = a.i("w", 576), h = a.i("h", 256), reps = a.i("reps", 5);
    std::vector<uint8_t> f0 = read_file(a.s("f0")), f1 = read_file(a.s("f1"));
    if (f0.size() != (size_t)W * H * 4 || f1.size() != (size_t)W * H * 4) die("f0/f1 大小与 W*H*4 不符");
    Img i0 = make_img(c, W, H, f0.data()), i1 = make_img(c, W, H, f1.data()), io = make_img(c, W, H, nullptr);
    Buf d0 = make_buf(c, (VkDeviceSize)w * h * 3, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf d1 = make_buf(c, (VkDeviceSize)w * h * 3, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf fl = make_buf(c, (VkDeviceSize)w * h * 4 * 4, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf mk = make_buf(c, (VkDeviceSize)w * h * 4, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    if (a.has("flow")) { auto v = read_file(a.s("flow")); if (v.size() != fl.size) die("flow 大小与 w*h*4*4 不符"); memcpy(fl.map, v.data(), v.size()); }
    if (a.has("mask")) { auto v = read_file(a.s("mask")); if (v.size() != mk.size) die("mask 大小与 w*h*4 不符"); memcpy(mk.map, v.data(), v.size()); }
    int nhud = 0; Buf hud = make_hud(c, a.s("hud"), &nhud);

    Pipe down = make_pipe(c, spv, "interp_a_down", {bsamp(i0), bsamp(i1), bssbo(d0), bssbo(d1)}, 24);
    struct { int32_t W, H, w, h; float inv_scale, zp; } pd{W, H, w, h, 1.0f / a.f("in_scale", 1.0f), a.f("in_zp", 0.0f)};
    run(c, down, &pd, sizeof(pd), cdiv((uint32_t)(w * h), 4 * 64), 2, 1, reps);
    write_file(a.s("out_down0"), d0.map, (size_t)w * h * 3);
    write_file(a.s("out_down1"), d1.map, (size_t)w * h * 3);

    Pipe blend = make_pipe(c, spv, "interp_a_blend", {bsamp(i0), bsamp(i1), bssbo(fl), bssbo(mk), bstore(io), bubo(hud)}, 24);
    struct { int32_t W, H, w, h; float t; int32_t hud_on; } pb{W, H, w, h, a.f("t", 0.5f), a.i("hud_on", nhud > 0 ? 1 : 0)};
    run(c, blend, &pb, sizeof(pb), cdiv((uint32_t)W, 8), cdiv((uint32_t)H, 8), 1, reps);
    std::vector<uint8_t> out = read_img(c, io);
    write_file(a.s("out"), out.data(), out.size());
}

// 路线 B: f0,f1 -> luma -> bm -> smooth -> prealign -> (NPU: 残差由文件给, 可缺) -> compose
void run_b(const Ctx& c, const Args& a, const std::string& spv) {
    int W = a.i("W"), H = a.i("H"), ow = a.i("ow", W), oh = a.i("oh", H), reps = a.i("reps", 5);
    int qw = (W + 3) / 4, qh = (H + 3) / 4, gw = (qw + 7) / 8, gh = (qh + 7) / 8;
    float cell = a.f("cell", 32.0f), t = a.f("t", 0.5f);
    std::vector<uint8_t> f0 = read_file(a.s("f0")), f1 = read_file(a.s("f1"));
    if (f0.size() != (size_t)W * H * 4 || f1.size() != (size_t)W * H * 4) die("f0/f1 大小与 W*H*4 不符");
    if ((ow * oh) % 2) die("ow*oh 必须是偶数");
    Img i0 = make_img(c, W, H, f0.data()), i1 = make_img(c, W, H, f1.data()), io = make_img(c, W, H, nullptr);
    Buf l0 = make_buf(c, (VkDeviceSize)qw * qh * 4, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf l1 = make_buf(c, (VkDeviceSize)qw * qh * 4, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf mv = make_buf(c, (VkDeviceSize)gw * gh * 8, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf fl = make_buf(c, (VkDeviceSize)gw * gh * 8, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf npuin = make_buf(c, (VkDeviceSize)ow * oh * 6, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buf res = make_buf(c, (VkDeviceSize)ow * oh * 3, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    bool have_res = a.has("res");
    if (have_res) { auto v = read_file(a.s("res")); if (v.size() != res.size) die("res 大小与 ow*oh*3 不符"); memcpy(res.map, v.data(), v.size()); }
    int nhud = 0; Buf hud = make_hud(c, a.s("hud"), &nhud);

    Pipe luma = make_pipe(c, spv, "interp_b_luma", {bsamp(i0), bsamp(i1), bssbo(l0), bssbo(l1)}, 16);
    struct { int32_t W, H, qw, qh; } pl{W, H, qw, qh};
    run(c, luma, &pl, sizeof(pl), cdiv((uint32_t)qw, 8), cdiv((uint32_t)qh, 8), 2, reps);
    write_file(a.s("out_luma0"), l0.map, l0.size);
    write_file(a.s("out_luma1"), l1.map, l1.size);

    Pipe bm = make_pipe(c, spv, "interp_b_bm", {bssbo(l0), bssbo(l1), bssbo(mv)}, 16);
    struct { int32_t qw, qh, gw, gh; } pbm{qw, qh, gw, gh};
    run(c, bm, &pbm, sizeof(pbm), (uint32_t)gw, (uint32_t)gh, 1, reps);
    write_file(a.s("out_mv"), mv.map, mv.size);

    Pipe sm = make_pipe(c, spv, "interp_b_smooth", {bssbo(mv), bssbo(fl)}, 12);
    struct { int32_t gw, gh, up; } psm{gw, gh, 4};
    run(c, sm, &psm, sizeof(psm), cdiv((uint32_t)(gw * gh), 64), 1, 1, reps);
    write_file(a.s("out_flow"), fl.map, fl.size);

    Pipe pa = make_pipe(c, spv, "interp_b_prealign", {bsamp(i0), bsamp(i1), bssbo(fl), bssbo(npuin)}, 40);
    struct { int32_t W, H, ow, oh, gw, gh; float cell, t, inv_scale, zp; } ppa{W, H, ow, oh, gw, gh, cell, t, 1.0f / a.f("in_scale", 1.0f), a.f("in_zp", 0.0f)};
    run(c, pa, &ppa, sizeof(ppa), cdiv((uint32_t)(ow * oh), 2 * 64), 1, 1, reps);
    write_file(a.s("out_npuin"), npuin.map, npuin.size);

    if (have_res || a.has("out")) {
        Pipe co = make_pipe(c, spv, "interp_b_compose", {bsamp(i0), bsamp(i1), bssbo(fl), bssbo(res), bstore(io), bubo(hud)}, 44);
        struct { int32_t W, H, gw, gh; float cell; int32_t rw, rh; float t, res_scale, res_zp; int32_t hud_on; }
            pco{W, H, gw, gh, cell, ow, oh, t, a.f("res_scale", 0.0f), a.f("res_zp", 0.0f), a.i("hud_on", nhud > 0 ? 1 : 0)};
        run(c, co, &pco, sizeof(pco), cdiv((uint32_t)W, 8), cdiv((uint32_t)H, 8), 1, reps);
        std::vector<uint8_t> out = read_img(c, io);
        write_file(a.s("out"), out.data(), out.size());
    }
}

void init(Ctx& c) {
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "interp_harness"; app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    std::vector<const char*> iext;
#ifdef __APPLE__
    iext.push_back("VK_KHR_portability_enumeration");
    ici.flags |= 0x00000001; // VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR
#endif
    ici.enabledExtensionCount = (uint32_t)iext.size(); ici.ppEnabledExtensionNames = iext.data();
    VK(vkCreateInstance(&ici, nullptr, &c.inst));
    uint32_t n = 0; vkEnumeratePhysicalDevices(c.inst, &n, nullptr);
    if (!n) die("没有 Vulkan 物理设备 (Mac 上要设 VK_ICD_FILENAMES 指向 MoltenVK_icd.json)");
    std::vector<VkPhysicalDevice> devs(n); vkEnumeratePhysicalDevices(c.inst, &n, devs.data());
    c.phys = devs[0];
    vkGetPhysicalDeviceProperties(c.phys, &c.props);
    vkGetPhysicalDeviceMemoryProperties(c.phys, &c.mem);
    uint32_t qn = 0; vkGetPhysicalDeviceQueueFamilyProperties(c.phys, &qn, nullptr);
    std::vector<VkQueueFamilyProperties> qs(qn); vkGetPhysicalDeviceQueueFamilyProperties(c.phys, &qn, qs.data());
    bool found = false;
    for (uint32_t i = 0; i < qn; i++) if ((qs[i].queueFlags & VK_QUEUE_COMPUTE_BIT) && qs[i].timestampValidBits) { c.qfam = i; found = true; break; }
    if (!found) die("没有带时间戳的 compute 队列族");
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = c.qfam; qci.queueCount = 1; qci.pQueuePriorities = &prio;
    std::vector<const char*> dext;
    uint32_t en = 0; vkEnumerateDeviceExtensionProperties(c.phys, nullptr, &en, nullptr);
    std::vector<VkExtensionProperties> eps(en); vkEnumerateDeviceExtensionProperties(c.phys, nullptr, &en, eps.data());
    for (auto& e : eps) if (!strcmp(e.extensionName, "VK_KHR_portability_subset")) dext.push_back("VK_KHR_portability_subset");
    VkDeviceCreateInfo dci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1; dci.pQueueCreateInfos = &qci;
    dci.enabledExtensionCount = (uint32_t)dext.size(); dci.ppEnabledExtensionNames = dext.data();
    VK(vkCreateDevice(c.phys, &dci, nullptr, &c.dev));
    vkGetDeviceQueue(c.dev, c.qfam, 0, &c.queue);
    VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    pci.queueFamilyIndex = c.qfam; pci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    VK(vkCreateCommandPool(c.dev, &pci, nullptr, &c.pool));
    VkQueryPoolCreateInfo qpi{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
    qpi.queryType = VK_QUERY_TYPE_TIMESTAMP; qpi.queryCount = 2;
    VK(vkCreateQueryPool(c.dev, &qpi, nullptr, &c.qpool));
    VkSamplerCreateInfo sci{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    sci.magFilter = sci.minFilter = VK_FILTER_NEAREST; sci.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
    sci.addressModeU = sci.addressModeV = sci.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    VK(vkCreateSampler(c.dev, &sci, nullptr, &c.sampler));
}

}  // namespace

int main(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; i++) {
        std::string s = argv[i]; size_t eq = s.find('=');
        if (eq == std::string::npos) { fprintf(stderr, "harness: 参数要写成 key=value: %s\n", argv[i]); return 2; }
        a.kv[s.substr(0, eq)] = s.substr(eq + 1);
    }
    std::string route = a.s("route"), spv = a.s("spv");
    if (spv.empty() || (route != "a" && route != "b")) { fprintf(stderr, "用法: harness spv=<目录> route=a|b f0= f1= W= H= ...\n"); return 2; }
    Ctx c; init(c);
    if (route == "a") run_a(c, a, spv); else run_b(c, a, spv);
    printf("{\"device\":\"%s\",\"timestamp_period_ns\":%g,\"dispatch_ms\":{", c.props.deviceName, c.props.limits.timestampPeriod);
    for (size_t i = 0; i < g_times.size(); i++) {
        std::vector<double> v = g_times[i].ms; std::sort(v.begin(), v.end());
        printf("%s\"%s\":{\"min\":%.4f,\"median\":%.4f,\"n\":%zu}", i ? "," : "", g_times[i].name.c_str(), v.front(), v[v.size() / 2], v.size());
    }
    printf("}}\n");
    return 0;
}
