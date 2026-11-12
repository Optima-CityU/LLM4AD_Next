// vkop_runner — Compute Shader 算子的设备侧执行工装。
//
// 职责边界 (与 phonefarm gpu-op 的分工):
//   本程序只做一件事: 把一份 SPIR-V 算子放到真机 GPU 上跑, 量它自己的 GPU 耗时,
//   算它的重建画质, 然后把数字原样打到 stdout。
//   **不做任何判定** —— 快慢好坏、显著不显著, 全部由 phonefarm 侧裁决。
//
// 零拷贝: 输入与输出 image 都是 DEVICE_LOCAL 的 storage image。测量循环里画面
// 全程不出显存, 一次上传、一次回读都在计时窗口之外。这是上游 game_opt_loop
// 「画面绝对不能离开显存」红线在设备侧的落地点 —— 前车之鉴是算力 2.9ms 而
// 内存与显存之间拷来拷去花了 7ms。
//
// 计时口径: VkQueryPool 时间戳夹住 vkCmdDispatch, 乘 timestampPeriod 换成纳秒。
// 量的是 GPU 执行这一个 dispatch 的时间, 不含 CPU 提交与同步开销。
//
// 画质口径: 参考图 (高分辨率真值) 下采样成输入, 算子把它重建回高分辨率,
// PSNR = 重建结果与参考图之间的峰值信噪比。RGB 三通道联合 MSE, 像素域 [0,255]。
// 与 game_opt_loop / sr_loop 全环口径一致。
//
// 用法:
//   vkop_runner --shader <spv> --track sr|frame_gen [--seconds N] [--iterations N]
//               [--in-width W --in-height H] [--out-width W --out-height H]
//               [--reference <rgba8 raw>] [--output-rgba <new file>] [--json]

#include <vulkan/vulkan.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#include <string>
#include <vector>

namespace {

// ── 失败即止: 任何 Vulkan 错误都直接以 JSON 形式报出, 不继续跑出一个假数字 ──

std::string g_error;

const char *vk_err(VkResult r) {
  switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
    default: return "VK_ERROR";
  }
}

#define VKCHECK(expr, what)                                                    \
  do {                                                                         \
    VkResult _r = (expr);                                                      \
    if (_r != VK_SUCCESS) {                                                    \
      g_error = std::string(what) + ": " + vk_err(_r);                         \
      return false;                                                            \
    }                                                                          \
  } while (0)

void emit_failure(const std::string &msg) {
  printf("{\"v\":1,\"ok\":false,\"timing_us\":{\"samples\":[]},\"error\":\"%s\"}\n",
         msg.c_str());
  fflush(stdout);
}

// ── 参数 ──

struct Args {
  std::string shader;
  std::string track = "sr";
  std::string reference;
  std::string output_rgba;  // Optional raw readback evidence, outside GPU timing.
  int seconds = 0;        // >0 时按时长跑, 否则按 iterations
  int iterations = 200;
  int in_w = 1280, in_h = 720;
  int out_w = 1920, out_h = 1080;
  int warmup = 20;
};

bool parse_args(int argc, char **argv, Args &a) {
  for (int i = 1; i < argc; i++) {
    std::string k = argv[i];
    auto next = [&](const char *name) -> const char * {
      if (i + 1 >= argc) {
        g_error = std::string(name) + " 缺少取值";
        return nullptr;
      }
      return argv[++i];
    };
    if (k == "--shader") { const char *v = next("--shader"); if (!v) return false; a.shader = v; }
    else if (k == "--track") { const char *v = next("--track"); if (!v) return false; a.track = v; }
    else if (k == "--reference") { const char *v = next("--reference"); if (!v) return false; a.reference = v; }
    else if (k == "--output-rgba") { const char *v = next("--output-rgba"); if (!v) return false; a.output_rgba = v; }
    else if (k == "--seconds") { const char *v = next("--seconds"); if (!v) return false; a.seconds = atoi(v); }
    else if (k == "--iterations") { const char *v = next("--iterations"); if (!v) return false; a.iterations = atoi(v); }
    else if (k == "--in-width") { const char *v = next("--in-width"); if (!v) return false; a.in_w = atoi(v); }
    else if (k == "--in-height") { const char *v = next("--in-height"); if (!v) return false; a.in_h = atoi(v); }
    else if (k == "--out-width") { const char *v = next("--out-width"); if (!v) return false; a.out_w = atoi(v); }
    else if (k == "--out-height") { const char *v = next("--out-height"); if (!v) return false; a.out_h = atoi(v); }
    else if (k == "--warmup") { const char *v = next("--warmup"); if (!v) return false; a.warmup = atoi(v); }
    else if (k == "--json") { /* 输出恒为 JSON, 保留该开关以对齐 bench 的用法 */ }
    else { g_error = "不认识的参数 " + k; return false; }
  }
  if (a.shader.empty()) { g_error = "缺少 --shader"; return false; }
  if (a.track != "sr" && a.track != "frame_gen") { g_error = "--track 只能是 sr 或 frame_gen"; return false; }
  if (a.in_w <= 0 || a.in_h <= 0 || a.out_w <= 0 || a.out_h <= 0) { g_error = "分辨率必须为正"; return false; }
  return true;
}

// ── 参考图: 高分辨率真值 ──
//
// 缺省用程序化生成的确定性图案。它刻意包含 SR 算子最吃力的三类结构:
// 锐利斜边 (振铃与锯齿)、同心高频环 (摩尔纹)、平滑渐变 (带状伪影)。
// 生成是纯函数, 同一分辨率每次逐字节一致, 因此 PSNR 可跨轮次比较。
//
// 也接受 --reference 指定一张 RGBA8 裸图 (phonefarm capture 抓的真实游戏帧),
// 那才是最终该用的真值; 程序化图案只是没有真值时的确定性替代。
std::vector<uint8_t> make_reference(int w, int h) {
  std::vector<uint8_t> px(static_cast<size_t>(w) * h * 4);
  for (int y = 0; y < h; y++) {
    for (int x = 0; x < w; x++) {
      float fx = static_cast<float>(x) / w;
      float fy = static_cast<float>(y) / h;

      // 斜边: 半平面硬边界, 边缘方向与像素网格不对齐
      float edge = (fx * 0.7f + fy * 0.3f) > 0.5f ? 1.0f : 0.0f;

      // 同心高频环: 频率随半径升高, 直逼奈奎斯特
      float dx = fx - 0.5f, dy = fy - 0.5f;
      float r = sqrtf(dx * dx + dy * dy);
      float rings = 0.5f + 0.5f * sinf(r * 220.0f);

      // 平滑渐变
      float grad = fy;

      float rr = 0.45f * edge + 0.35f * rings + 0.20f * grad;
      float gg = 0.30f * edge + 0.45f * rings + 0.25f * (1.0f - grad);
      float bb = 0.25f * edge + 0.30f * rings + 0.45f * grad;

      auto q = [](float v) -> uint8_t {
        int i = static_cast<int>(v * 255.0f + 0.5f);
        return static_cast<uint8_t>(i < 0 ? 0 : (i > 255 ? 255 : i));
      };
      size_t o = (static_cast<size_t>(y) * w + x) * 4;
      px[o + 0] = q(rr);
      px[o + 1] = q(gg);
      px[o + 2] = q(bb);
      px[o + 3] = 255;
    }
  }
  return px;
}

/// 盒式下采样: 参考图 → 算子的输入图。
/// 用面积平均而不是抽点, 与「游戏以更低分辨率渲染」的物理含义一致。
std::vector<uint8_t> downsample(const std::vector<uint8_t> &src, int sw, int sh,
                                int dw, int dh) {
  std::vector<uint8_t> dst(static_cast<size_t>(dw) * dh * 4);
  for (int y = 0; y < dh; y++) {
    int y0 = y * sh / dh, y1 = (y + 1) * sh / dh;
    if (y1 <= y0) y1 = y0 + 1;
    for (int x = 0; x < dw; x++) {
      int x0 = x * sw / dw, x1 = (x + 1) * sw / dw;
      if (x1 <= x0) x1 = x0 + 1;
      int acc[4] = {0, 0, 0, 0}, n = 0;
      for (int sy = y0; sy < y1 && sy < sh; sy++) {
        for (int sx = x0; sx < x1 && sx < sw; sx++) {
          size_t o = (static_cast<size_t>(sy) * sw + sx) * 4;
          for (int c = 0; c < 4; c++) acc[c] += src[o + c];
          n++;
        }
      }
      size_t o = (static_cast<size_t>(y) * dw + x) * 4;
      for (int c = 0; c < 4; c++)
        dst[o + c] = static_cast<uint8_t>(n ? (acc[c] + n / 2) / n : 0);
    }
  }
  return dst;
}

/// 水平平移 dx 像素 (边缘钳位)。用于合成插帧赛道的前后帧与真值中间帧。
std::vector<uint8_t> shift_image(const std::vector<uint8_t> &src, int w, int h, int dx) {
  std::vector<uint8_t> dst(src.size());
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) {
      int sx = x - dx;
      if (sx < 0) sx = 0;
      if (sx >= w) sx = w - 1;
      memcpy(&dst[(static_cast<size_t>(y) * w + x) * 4],
             &src[(static_cast<size_t>(y) * w + sx) * 4], 4);
    }
  return dst;
}

/// HUD 区域 (相对坐标)。屏幕空间静止, 不随镜头移动 —— 插帧算子最容易在这里出重影。
struct HudBand { int x0, y0, x1, y1; };
HudBand hud_band(int w, int h) {
  return HudBand{w * 6 / 100, h * 72 / 100, w * 34 / 100, h * 92 / 100};
}

/// 往帧里盖一块静止的高对比细竖条 —— 模拟 HUD 文字。
/// 文字类高频是重影最先暴露的地方: 世界在动而 HUD 不动, 块匹配一旦把 HUD
/// 也当成运动内容, 字就会被拉成双份。
void stamp_hud(std::vector<uint8_t> &px, int w, int h) {
  HudBand b = hud_band(w, h);
  for (int y = b.y0; y < b.y1; y++)
    for (int x = b.x0; x < b.x1; x++) {
      bool ink = ((x / 3) % 2 == 0) && ((y / 7) % 3 != 2);
      uint8_t v = ink ? 250 : 12;
      size_t o = (static_cast<size_t>(y) * w + x) * 4;
      px[o + 0] = v;
      px[o + 1] = v;
      px[o + 2] = v;
      px[o + 3] = 255;
    }
}

/// 可分离的邻域 min/max 包络 (半径 r)。先横后纵, O(w*h*r) 而不是 O(w*h*r^2)。
void envelope(const std::vector<uint8_t> &a, const std::vector<uint8_t> &b, int w,
              int h, int r, std::vector<uint8_t> &lo, std::vector<uint8_t> &hi) {
  size_t n = static_cast<size_t>(w) * h * 4;
  std::vector<uint8_t> tlo(n), thi(n);
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++)
      for (int c = 0; c < 4; c++) {
        uint8_t mn = 255, mx = 0;
        for (int d = -r; d <= r; d++) {
          int sx = x + d;
          if (sx < 0) sx = 0;
          if (sx >= w) sx = w - 1;
          size_t o = (static_cast<size_t>(y) * w + sx) * 4 + c;
          mn = std::min({mn, a[o], b[o]});
          mx = std::max({mx, a[o], b[o]});
        }
        size_t o = (static_cast<size_t>(y) * w + x) * 4 + c;
        tlo[o] = mn;
        thi[o] = mx;
      }
  lo.assign(n, 255);
  hi.assign(n, 0);
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++)
      for (int c = 0; c < 4; c++) {
        uint8_t mn = 255, mx = 0;
        for (int d = -r; d <= r; d++) {
          int sy = y + d;
          if (sy < 0) sy = 0;
          if (sy >= h) sy = h - 1;
          size_t o = (static_cast<size_t>(sy) * w + x) * 4 + c;
          mn = std::min(mn, tlo[o]);
          mx = std::max(mx, thi[o]);
        }
        size_t o = (static_cast<size_t>(y) * w + x) * 4 + c;
        lo[o] = mn;
        hi[o] = mx;
      }
}

/// RGB 三通道联合 MSE 的 PSNR, 像素域 [0,255]。与全环口径一致 (alpha 不参与)。
double psnr_rgb(const std::vector<uint8_t> &a, const std::vector<uint8_t> &b,
                int w, int h) {
  double se = 0.0;
  size_t n = static_cast<size_t>(w) * h;
  for (size_t i = 0; i < n; i++) {
    for (int c = 0; c < 3; c++) {
      double d = static_cast<double>(a[i * 4 + c]) - static_cast<double>(b[i * 4 + c]);
      se += d * d;
    }
  }
  double mse = se / (static_cast<double>(n) * 3.0);
  if (mse <= 0.0) return 99.0;  // 逐位相同; 不报 inf, 下游要做数值比较
  return 10.0 * log10(255.0 * 255.0 / mse);
}

// ── Vulkan ──

struct Img {
  VkImage image = VK_NULL_HANDLE;
  VkDeviceMemory mem = VK_NULL_HANDLE;
  VkImageView view = VK_NULL_HANDLE;
  int w = 0, h = 0;
};

struct Ctx {
  Ctx() = default;
  Ctx(const Ctx &) = delete;
  Ctx &operator=(const Ctx &) = delete;
  ~Ctx() { close(); }

  VkInstance inst = VK_NULL_HANDLE;
  VkPhysicalDevice phys = VK_NULL_HANDLE;
  VkDevice dev = VK_NULL_HANDLE;
  VkQueue queue = VK_NULL_HANDLE;
  uint32_t qfam = 0;
  VkCommandPool pool = VK_NULL_HANDLE;
  VkPhysicalDeviceProperties props{};
  std::vector<Img> images;
  VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
  VkDescriptorPool dpool = VK_NULL_HANDLE;
  VkDescriptorSet dset = VK_NULL_HANDLE;
  VkPipelineLayout playout = VK_NULL_HANDLE;
  VkPipeline pipe = VK_NULL_HANDLE;
  VkShaderModule module = VK_NULL_HANDLE;
  VkQueryPool qpool = VK_NULL_HANDLE;

  VkResult close() {
    VkResult waited = VK_SUCCESS;
    if (dev != VK_NULL_HANDLE) {
      // Drain GPU work before releasing children and the driver's worker threads.
      waited = vkDeviceWaitIdle(dev);
      if (pool) vkDestroyCommandPool(dev, pool, nullptr);
      if (pipe) vkDestroyPipeline(dev, pipe, nullptr);
      if (playout) vkDestroyPipelineLayout(dev, playout, nullptr);
      if (dpool) vkDestroyDescriptorPool(dev, dpool, nullptr);
      if (dsl) vkDestroyDescriptorSetLayout(dev, dsl, nullptr);
      if (module) vkDestroyShaderModule(dev, module, nullptr);
      if (qpool) vkDestroyQueryPool(dev, qpool, nullptr);
      for (auto &image : images) {
        if (image.view) vkDestroyImageView(dev, image.view, nullptr);
        if (image.image) vkDestroyImage(dev, image.image, nullptr);
        if (image.mem) vkFreeMemory(dev, image.mem, nullptr);
      }
      vkDestroyDevice(dev, nullptr);
      dev = VK_NULL_HANDLE;
    }
    if (inst != VK_NULL_HANDLE) {
      vkDestroyInstance(inst, nullptr);
      inst = VK_NULL_HANDLE;
    }
    return waited;
  }
};

uint32_t find_mem(VkPhysicalDevice p, uint32_t bits, VkMemoryPropertyFlags want) {
  VkPhysicalDeviceMemoryProperties mp{};
  vkGetPhysicalDeviceMemoryProperties(p, &mp);
  for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
    if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want)
      return i;
  return UINT32_MAX;
}

bool make_image(Ctx &c, int w, int h, Img &out) {
  out.w = w;
  out.h = h;
  VkImageCreateInfo ci{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
  ci.imageType = VK_IMAGE_TYPE_2D;
  ci.format = VK_FORMAT_R8G8B8A8_UNORM;
  ci.extent = {static_cast<uint32_t>(w), static_cast<uint32_t>(h), 1};
  ci.mipLevels = 1;
  ci.arrayLayers = 1;
  ci.samples = VK_SAMPLE_COUNT_1_BIT;
  ci.tiling = VK_IMAGE_TILING_OPTIMAL;
  ci.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
             VK_IMAGE_USAGE_TRANSFER_DST_BIT;
  ci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
  ci.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
  VKCHECK(vkCreateImage(c.dev, &ci, nullptr, &out.image), "vkCreateImage");

  VkMemoryRequirements mr{};
  vkGetImageMemoryRequirements(c.dev, out.image, &mr);
  // DEVICE_LOCAL: 画面常驻显存, 测量循环里不往主存搬
  uint32_t mt = find_mem(c.phys, mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  if (mt == UINT32_MAX) { g_error = "找不到 DEVICE_LOCAL 显存类型"; return false; }
  VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
  ai.allocationSize = mr.size;
  ai.memoryTypeIndex = mt;
  VKCHECK(vkAllocateMemory(c.dev, &ai, nullptr, &out.mem), "vkAllocateMemory(image)");
  VKCHECK(vkBindImageMemory(c.dev, out.image, out.mem, 0), "vkBindImageMemory");

  VkImageViewCreateInfo vi{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
  vi.image = out.image;
  vi.viewType = VK_IMAGE_VIEW_TYPE_2D;
  vi.format = ci.format;
  vi.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  VKCHECK(vkCreateImageView(c.dev, &vi, nullptr, &out.view), "vkCreateImageView");
  return true;
}

VkCommandBuffer begin_once(Ctx &c) {
  VkCommandBufferAllocateInfo ai{VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
  ai.commandPool = c.pool;
  ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
  ai.commandBufferCount = 1;
  VkCommandBuffer cb = VK_NULL_HANDLE;
  if (vkAllocateCommandBuffers(c.dev, &ai, &cb) != VK_SUCCESS) return VK_NULL_HANDLE;
  VkCommandBufferBeginInfo bi{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
  vkBeginCommandBuffer(cb, &bi);
  return cb;
}

bool end_submit(Ctx &c, VkCommandBuffer cb) {
  vkEndCommandBuffer(cb);
  VkSubmitInfo si{VK_STRUCTURE_TYPE_SUBMIT_INFO};
  si.commandBufferCount = 1;
  si.pCommandBuffers = &cb;
  VKCHECK(vkQueueSubmit(c.queue, 1, &si, VK_NULL_HANDLE), "vkQueueSubmit");
  VKCHECK(vkQueueWaitIdle(c.queue), "vkQueueWaitIdle");
  vkFreeCommandBuffers(c.dev, c.pool, 1, &cb);
  return true;
}

void barrier(VkCommandBuffer cb, VkImage img, VkImageLayout from, VkImageLayout to,
             VkAccessFlags src, VkAccessFlags dst) {
  VkImageMemoryBarrier b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
  b.oldLayout = from;
  b.newLayout = to;
  b.srcAccessMask = src;
  b.dstAccessMask = dst;
  b.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
  b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
  b.image = img;
  b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
  vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                       VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, nullptr, 0,
                       nullptr, 1, &b);
}

bool init_vulkan(Ctx &c) {
  VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
  app.pApplicationName = "vkop_runner";
  app.apiVersion = VK_API_VERSION_1_1;
  VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
  ici.pApplicationInfo = &app;
  VKCHECK(vkCreateInstance(&ici, nullptr, &c.inst), "vkCreateInstance");

  uint32_t n = 0;
  vkEnumeratePhysicalDevices(c.inst, &n, nullptr);
  if (n == 0) { g_error = "没有 Vulkan 物理设备"; return false; }
  std::vector<VkPhysicalDevice> devs(n);
  vkEnumeratePhysicalDevices(c.inst, &n, devs.data());
  c.phys = devs[0];
  vkGetPhysicalDeviceProperties(c.phys, &c.props);

  uint32_t qn = 0;
  vkGetPhysicalDeviceQueueFamilyProperties(c.phys, &qn, nullptr);
  std::vector<VkQueueFamilyProperties> qs(qn);
  vkGetPhysicalDeviceQueueFamilyProperties(c.phys, &qn, qs.data());
  bool found = false;
  for (uint32_t i = 0; i < qn; i++) {
    if (qs[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { c.qfam = i; found = true; break; }
  }
  if (!found) { g_error = "没有 compute 队列族"; return false; }
  // 时间戳位数为 0 的队列族量不了 GPU 耗时, 那就没有标尺可言
  if (qs[c.qfam].timestampValidBits == 0) {
    g_error = "compute 队列族不支持时间戳查询, 无法量 GPU 耗时";
    return false;
  }

  float prio = 1.0f;
  VkDeviceQueueCreateInfo qci{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
  qci.queueFamilyIndex = c.qfam;
  qci.queueCount = 1;
  qci.pQueuePriorities = &prio;
  VkDeviceCreateInfo dci{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
  dci.queueCreateInfoCount = 1;
  dci.pQueueCreateInfos = &qci;
  VKCHECK(vkCreateDevice(c.phys, &dci, nullptr, &c.dev), "vkCreateDevice");
  vkGetDeviceQueue(c.dev, c.qfam, 0, &c.queue);

  VkCommandPoolCreateInfo pci{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
  pci.queueFamilyIndex = c.qfam;
  pci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
  VKCHECK(vkCreateCommandPool(c.dev, &pci, nullptr, &c.pool), "vkCreateCommandPool");

  VkQueryPoolCreateInfo qpi{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
  qpi.queryType = VK_QUERY_TYPE_TIMESTAMP;
  qpi.queryCount = 2;
  VKCHECK(vkCreateQueryPool(c.dev, &qpi, nullptr, &c.qpool), "vkCreateQueryPool");
  return true;
}

bool load_shader(Ctx &c, const std::string &path) {
  FILE *f = fopen(path.c_str(), "rb");
  if (!f) { g_error = "打不开 " + path; return false; }
  fseek(f, 0, SEEK_END);
  long sz = ftell(f);
  fseek(f, 0, SEEK_SET);
  if (sz <= 0 || sz % 4 != 0) { fclose(f); g_error = "SPIR-V 长度非法"; return false; }
  std::vector<uint32_t> words(static_cast<size_t>(sz) / 4);
  size_t got = fread(words.data(), 1, static_cast<size_t>(sz), f);
  fclose(f);
  if (got != static_cast<size_t>(sz)) { g_error = "SPIR-V 读取不全"; return false; }
  if (words[0] != 0x07230203u) { g_error = "SPIR-V 魔数不对"; return false; }

  VkShaderModuleCreateInfo si{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
  si.codeSize = static_cast<size_t>(sz);
  si.pCode = words.data();
  VKCHECK(vkCreateShaderModule(c.dev, &si, nullptr, &c.module), "vkCreateShaderModule");
  return true;
}

bool build_pipeline(Ctx &c, int nbind) {
  std::vector<VkDescriptorSetLayoutBinding> b(nbind);
  for (int i = 0; i < nbind; i++) {
    b[i].binding = static_cast<uint32_t>(i);
    b[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    b[i].descriptorCount = 1;
    b[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
  }
  VkDescriptorSetLayoutCreateInfo li{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
  li.bindingCount = static_cast<uint32_t>(nbind);
  li.pBindings = b.data();
  VKCHECK(vkCreateDescriptorSetLayout(c.dev, &li, nullptr, &c.dsl),
          "vkCreateDescriptorSetLayout");

  VkDescriptorPoolSize ps{VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, static_cast<uint32_t>(nbind)};
  VkDescriptorPoolCreateInfo pi{VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
  pi.maxSets = 1;
  pi.poolSizeCount = 1;
  pi.pPoolSizes = &ps;
  VKCHECK(vkCreateDescriptorPool(c.dev, &pi, nullptr, &c.dpool), "vkCreateDescriptorPool");

  VkDescriptorSetAllocateInfo ai{VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
  ai.descriptorPool = c.dpool;
  ai.descriptorSetCount = 1;
  ai.pSetLayouts = &c.dsl;
  VKCHECK(vkAllocateDescriptorSets(c.dev, &ai, &c.dset), "vkAllocateDescriptorSets");

  std::vector<VkDescriptorImageInfo> ii(nbind);
  std::vector<VkWriteDescriptorSet> w(nbind);
  for (int i = 0; i < nbind; i++) {
    ii[i].imageView = c.images[i].view;
    ii[i].imageLayout = VK_IMAGE_LAYOUT_GENERAL;
    w[i] = VkWriteDescriptorSet{VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    w[i].dstSet = c.dset;
    w[i].dstBinding = static_cast<uint32_t>(i);
    w[i].descriptorCount = 1;
    w[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    w[i].pImageInfo = &ii[i];
  }
  vkUpdateDescriptorSets(c.dev, static_cast<uint32_t>(nbind), w.data(), 0, nullptr);

  VkPipelineLayoutCreateInfo pl{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
  pl.setLayoutCount = 1;
  pl.pSetLayouts = &c.dsl;
  VKCHECK(vkCreatePipelineLayout(c.dev, &pl, nullptr, &c.playout), "vkCreatePipelineLayout");

  VkComputePipelineCreateInfo cp{VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
  cp.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
  cp.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
  cp.stage.module = c.module;
  cp.stage.pName = "main";
  cp.layout = c.playout;
  VKCHECK(vkCreateComputePipelines(c.dev, VK_NULL_HANDLE, 1, &cp, nullptr, &c.pipe),
          "vkCreateComputePipelines");
  return true;
}

/// 一次性把像素上传到 DEVICE_LOCAL image (在计时窗口之外)。
bool upload(Ctx &c, Img &img, const std::vector<uint8_t> &px) {
  VkDeviceSize bytes = px.size();
  VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
  bi.size = bytes;
  bi.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
  VkBuffer buf = VK_NULL_HANDLE;
  VKCHECK(vkCreateBuffer(c.dev, &bi, nullptr, &buf), "vkCreateBuffer(upload)");
  VkMemoryRequirements mr{};
  vkGetBufferMemoryRequirements(c.dev, buf, &mr);
  uint32_t mt = find_mem(c.phys, mr.memoryTypeBits,
                         VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  if (mt == UINT32_MAX) { g_error = "找不到 HOST_VISIBLE 内存类型"; return false; }
  VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
  ai.allocationSize = mr.size;
  ai.memoryTypeIndex = mt;
  VkDeviceMemory mem = VK_NULL_HANDLE;
  VKCHECK(vkAllocateMemory(c.dev, &ai, nullptr, &mem), "vkAllocateMemory(upload)");
  VKCHECK(vkBindBufferMemory(c.dev, buf, mem, 0), "vkBindBufferMemory");
  void *p = nullptr;
  VKCHECK(vkMapMemory(c.dev, mem, 0, bytes, 0, &p), "vkMapMemory");
  memcpy(p, px.data(), bytes);
  vkUnmapMemory(c.dev, mem);

  VkCommandBuffer cb = begin_once(c);
  if (!cb) { g_error = "分配命令缓冲失败"; return false; }
  barrier(cb, img.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
          0, VK_ACCESS_TRANSFER_WRITE_BIT);
  VkBufferImageCopy r{};
  r.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
  r.imageExtent = {static_cast<uint32_t>(img.w), static_cast<uint32_t>(img.h), 1};
  vkCmdCopyBufferToImage(cb, buf, img.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &r);
  barrier(cb, img.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL,
          VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT);
  if (!end_submit(c, cb)) return false;

  vkDestroyBuffer(c.dev, buf, nullptr);
  vkFreeMemory(c.dev, mem, nullptr);
  return true;
}

/// 回读一次输出 (在计时窗口之外), 用于算 PSNR。
bool readback(Ctx &c, Img &img, std::vector<uint8_t> &out) {
  VkDeviceSize bytes = static_cast<VkDeviceSize>(img.w) * img.h * 4;
  out.resize(bytes);
  VkBufferCreateInfo bi{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
  bi.size = bytes;
  bi.usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;
  VkBuffer buf = VK_NULL_HANDLE;
  VKCHECK(vkCreateBuffer(c.dev, &bi, nullptr, &buf), "vkCreateBuffer(readback)");
  VkMemoryRequirements mr{};
  vkGetBufferMemoryRequirements(c.dev, buf, &mr);
  uint32_t mt = find_mem(c.phys, mr.memoryTypeBits,
                         VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  if (mt == UINT32_MAX) { g_error = "找不到 HOST_VISIBLE 内存类型"; return false; }
  VkMemoryAllocateInfo ai{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
  ai.allocationSize = mr.size;
  ai.memoryTypeIndex = mt;
  VkDeviceMemory mem = VK_NULL_HANDLE;
  VKCHECK(vkAllocateMemory(c.dev, &ai, nullptr, &mem), "vkAllocateMemory(readback)");
  VKCHECK(vkBindBufferMemory(c.dev, buf, mem, 0), "vkBindBufferMemory(readback)");

  VkCommandBuffer cb = begin_once(c);
  if (!cb) { g_error = "分配命令缓冲失败"; return false; }
  barrier(cb, img.image, VK_IMAGE_LAYOUT_GENERAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
          VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_TRANSFER_READ_BIT);
  VkBufferImageCopy r{};
  r.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
  r.imageExtent = {static_cast<uint32_t>(img.w), static_cast<uint32_t>(img.h), 1};
  vkCmdCopyImageToBuffer(cb, img.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buf, 1, &r);
  barrier(cb, img.image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, VK_IMAGE_LAYOUT_GENERAL,
          VK_ACCESS_TRANSFER_READ_BIT, VK_ACCESS_SHADER_WRITE_BIT);
  if (!end_submit(c, cb)) return false;

  void *p = nullptr;
  VKCHECK(vkMapMemory(c.dev, mem, 0, bytes, 0, &p), "vkMapMemory(readback)");
  memcpy(out.data(), p, bytes);
  vkUnmapMemory(c.dev, mem);
  vkDestroyBuffer(c.dev, buf, nullptr);
  vkFreeMemory(c.dev, mem, nullptr);
  return true;
}

// Evidence is create-only: a repeated run cannot replace a previous readback.
bool save_readback(const std::string &path, const std::vector<uint8_t> &bytes) {
  const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (fd < 0) { g_error = "cannot create output RGBA file"; return false; }
  size_t offset = 0;
  while (offset < bytes.size()) {
    const ssize_t n = write(fd, bytes.data() + offset, bytes.size() - offset);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) { close(fd); g_error = "cannot write output RGBA file"; return false; }
    offset += static_cast<size_t>(n);
  }
  if (close(fd) != 0) { g_error = "cannot close output RGBA file"; return false; }
  return true;
}

/// 跑一次 dispatch 并返回 GPU 侧耗时 (微秒)。
bool timed_dispatch(Ctx &c, int out_w, int out_h, double &us) {
  VkCommandBuffer cb = begin_once(c);
  if (!cb) { g_error = "分配命令缓冲失败"; return false; }
  vkCmdResetQueryPool(cb, c.qpool, 0, 2);
  vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, c.pipe);
  vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, c.playout, 0, 1,
                          &c.dset, 0, nullptr);
  vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, c.qpool, 0);
  // 工作组固定 16x16 (与上游赛道契约一致), 向上取整覆盖整幅输出
  vkCmdDispatch(cb, static_cast<uint32_t>((out_w + 15) / 16),
                static_cast<uint32_t>((out_h + 15) / 16), 1);
  vkCmdWriteTimestamp(cb, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, c.qpool, 1);
  if (!end_submit(c, cb)) return false;

  uint64_t ts[2] = {0, 0};
  VKCHECK(vkGetQueryPoolResults(c.dev, c.qpool, 0, 2, sizeof(ts), ts, sizeof(uint64_t),
                                VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT),
          "vkGetQueryPoolResults");
  double ns = static_cast<double>(ts[1] - ts[0]) * c.props.limits.timestampPeriod;
  us = ns / 1000.0;
  return true;
}

}  // namespace

int main(int argc, char **argv) {
  Args a;
  if (!parse_args(argc, argv, a)) { emit_failure(g_error); return 2; }

  Ctx c;
  if (!init_vulkan(c)) { emit_failure(g_error); return 2; }

  // 赛道决定插槽数: sr 两张 (进/出), frame_gen 三张 (前帧/当前帧/中间帧)
  const int nbind = (a.track == "sr") ? 2 : 3;
  c.images.resize(nbind);
  if (a.track == "sr") {
    if (!make_image(c, a.in_w, a.in_h, c.images[0])) { emit_failure(g_error); return 2; }
    if (!make_image(c, a.out_w, a.out_h, c.images[1])) { emit_failure(g_error); return 2; }
  } else {
    for (int i = 0; i < 3; i++)
      if (!make_image(c, a.out_w, a.out_h, c.images[i])) { emit_failure(g_error); return 2; }
  }

  // 参考图与输入图
  int ref_w = a.out_w, ref_h = a.out_h;
  std::vector<uint8_t> reference;
  if (!a.reference.empty()) {
    FILE *f = fopen(a.reference.c_str(), "rb");
    if (!f) { emit_failure("打不开参考图 " + a.reference); return 2; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    size_t want = static_cast<size_t>(ref_w) * ref_h * 4;
    if (static_cast<size_t>(sz) != want) {
      fclose(f);
      emit_failure("参考图尺寸不符: 需要 RGBA8 " + std::to_string(want) + " 字节");
      return 2;
    }
    reference.resize(want);
    size_t got = fread(reference.data(), 1, want, f);
    fclose(f);
    if (got != want) { emit_failure("参考图读取不全"); return 2; }
  } else {
    reference = make_reference(ref_w, ref_h);
  }

  std::vector<uint8_t> prev_frame, curr_frame, truth_frame;

  if (a.track == "sr") {
    std::vector<uint8_t> lowres = downsample(reference, ref_w, ref_h, a.in_w, a.in_h);
    if (!upload(c, c.images[0], lowres)) { emit_failure(g_error); return 2; }
    // 输出图也要先过一次 layout 转换, 否则首次 dispatch 的写入 layout 非法
    std::vector<uint8_t> zero(static_cast<size_t>(a.out_w) * a.out_h * 4, 0);
    if (!upload(c, c.images[1], zero)) { emit_failure(g_error); return 2; }
  } else {
    // 插帧赛道的合成场景: 世界横向平移 SHIFT 像素 (模拟镜头转动),
    // HUD 在屏幕空间静止。
    //
    // 真值是**中间帧** (平移 SHIFT/2), 不是前帧。
    // 这一条以前是错的 —— 拿 interp 去和前帧比, 一个什么都不做、直接把前帧
    // 抄出来的算子会拿满分, 指标反过来奖励"不插帧"。
    const int kShift = 8;
    prev_frame = reference;
    curr_frame = shift_image(reference, ref_w, ref_h, kShift);
    truth_frame = shift_image(reference, ref_w, ref_h, kShift / 2);
    // HUD 盖在三帧的同一位置: 它不随世界移动
    stamp_hud(prev_frame, ref_w, ref_h);
    stamp_hud(curr_frame, ref_w, ref_h);
    stamp_hud(truth_frame, ref_w, ref_h);

    std::vector<uint8_t> zero(reference.size(), 0);
    if (!upload(c, c.images[0], prev_frame)) { emit_failure(g_error); return 2; }
    if (!upload(c, c.images[1], curr_frame)) { emit_failure(g_error); return 2; }
    if (!upload(c, c.images[2], zero)) { emit_failure(g_error); return 2; }
  }

  if (!load_shader(c, a.shader)) { emit_failure(g_error); return 2; }
  if (!build_pipeline(c, nbind)) { emit_failure(g_error); return 2; }

  const int out_idx = (a.track == "sr") ? 1 : 2;

  // 预热: 首次 dispatch 含着色器编译与缓存冷启动, 不计入样本
  for (int i = 0; i < a.warmup; i++) {
    double us = 0;
    if (!timed_dispatch(c, c.images[out_idx].w, c.images[out_idx].h, us)) {
      emit_failure(g_error);
      return 2;
    }
  }

  // 正式采样。
  // --seconds > 0 时按**墙上时间**持续跑: 功耗要在稳定负载下才量得准,
  // 跑两百次 dispatch 就收工的话, 采样器读到的基本是空闲功耗。
  std::vector<double> samples;
  int iters = 0;
  if (a.seconds > 0) {
    auto t0 = std::chrono::steady_clock::now();
    const auto limit = std::chrono::seconds(a.seconds);
    samples.reserve(4096);
    while (std::chrono::steady_clock::now() - t0 < limit) {
      double us = 0;
      if (!timed_dispatch(c, c.images[out_idx].w, c.images[out_idx].h, us)) {
        emit_failure(g_error);
        return 2;
      }
      samples.push_back(us);
      iters++;
    }
  } else {
    iters = a.iterations;
    samples.reserve(static_cast<size_t>(iters));
    for (int i = 0; i < iters; i++) {
      double us = 0;
      if (!timed_dispatch(c, c.images[out_idx].w, c.images[out_idx].h, us)) {
        emit_failure(g_error);
        return 2;
      }
      samples.push_back(us);
    }
  }
  if (samples.empty()) { emit_failure("一个样本都没采到"); return 2; }

  std::vector<uint8_t> produced;
  if (!readback(c, c.images[out_idx], produced)) { emit_failure(g_error); return 2; }
  if (!a.output_rgba.empty() && !save_readback(a.output_rgba, produced)) {
    emit_failure(g_error);
    return 2;
  }

  // 画质: SR 比参考图, 插帧比**真值中间帧**
  const std::vector<uint8_t> &truth =
      (a.track == "sr") ? reference : truth_frame;
  double q = psnr_rgb(produced, truth, a.out_w, a.out_h);

  // ── 插帧专属伪影门禁 ──
  //
  // PSNR 是全图平均, 对这两类伪影极不敏感: HUD 只占几个百分点的面积,
  // 拉扯只发生在运动边界。全图 PSNR 看着还行, 人眼已经无法忍受。
  // 所以这两项单独量, 交给上层单独裁决。
  double hud_ghost_db = -1.0;
  double stretch_pct = -1.0;
  if (a.track == "frame_gen") {
    // 1) HUD 重影: 只在 HUD 区域内算 PSNR。HUD 在屏幕空间静止,
    //    正确的插帧结果在这块应当与真值逐像素吻合; 块匹配若把 HUD 当成
    //    运动内容, 字会被拉成双份, 这里的 PSNR 会断崖下跌。
    HudBand b = hud_band(a.out_w, a.out_h);
    double se = 0.0;
    size_t n = 0;
    for (int y = b.y0; y < b.y1; y++)
      for (int x = b.x0; x < b.x1; x++) {
        size_t o = (static_cast<size_t>(y) * a.out_w + x) * 4;
        for (int ch = 0; ch < 3; ch++) {
          double d = static_cast<double>(produced[o + ch]) -
                     static_cast<double>(truth_frame[o + ch]);
          se += d * d;
        }
        n++;
      }
    double mse = n ? se / (static_cast<double>(n) * 3.0) : 0.0;
    hud_ghost_db = (mse <= 0.0) ? 99.0 : 10.0 * log10(255.0 * 255.0 / mse);

    // 2) 拉扯果冻: 插出来的像素必须能在前后两帧的**邻域包络**里找到出处。
    //    落在包络之外 = 算子凭空造了前后帧都没有的内容, 那就是拉扯/撕裂。
    //    用邻域而不是同位, 是因为运动补偿本来就会从位移处取样, 同位包络
    //    会把正常的运动补偿全判成伪影。
    std::vector<uint8_t> lo, hi;
    envelope(prev_frame, curr_frame, a.out_w, a.out_h, 12, lo, hi);
    const int tol = 6;  // 允许滤波带来的小幅溢出
    size_t bad = 0, tot = 0;
    for (size_t i = 0; i < static_cast<size_t>(a.out_w) * a.out_h; i++) {
      bool out = false;
      for (int ch = 0; ch < 3; ch++) {
        int v = produced[i * 4 + ch];
        if (v < static_cast<int>(lo[i * 4 + ch]) - tol ||
            v > static_cast<int>(hi[i * 4 + ch]) + tol) {
          out = true;
        }
      }
      if (out) bad++;
      tot++;
    }
    stretch_pct = tot ? 100.0 * static_cast<double>(bad) / static_cast<double>(tot) : 0.0;
  }

  std::vector<double> sorted = samples;
  for (size_t i = 1; i < sorted.size(); i++) {
    double v = sorted[i];
    size_t j = i;
    while (j > 0 && sorted[j - 1] > v) { sorted[j] = sorted[j - 1]; j--; }
    sorted[j] = v;
  }
  double median = sorted.empty() ? 0.0 : sorted[sorted.size() / 2];

  // 60s 跑下来样本数可达几万; 全量打出去是几百 KB 的 JSON, 白占 adb 通道。
  // 均匀抽稀到上限以内 —— 统计量 (median/p95) 已在全量上算好, 抽稀只影响
  // 下游想自己复算的精度, 不影响本轮结论。
  const size_t kMaxEmit = 2000;
  size_t stride = (samples.size() + kMaxEmit - 1) / kMaxEmit;
  if (stride == 0) stride = 1;

  // Successful metrics are published only after explicit Vulkan teardown.
  if (c.close() != VK_SUCCESS) {
    emit_failure("Vulkan device did not become idle during cleanup");
    return 2;
  }

  printf("{\"v\":1,\"ok\":true,\"timing_us\":{\"samples\":[");
  bool first = true;
  for (size_t i = 0; i < samples.size(); i += stride) {
    printf("%s%.3f", first ? "" : ",", samples[i]);
    first = false;
  }
  printf("],\"count\":%zu,\"emitted_stride\":%zu,\"median\":%.3f},\"psnr_db\":%.4f,",
         samples.size(), stride, median, q);
  if (a.track == "frame_gen") {
    printf("\"artifacts\":{\"hud_ghost_db\":%.4f,\"stretch_pct\":%.4f},",
           hud_ghost_db, stretch_pct);
  }
  printf("\"device\":{\"name\":\"%s\",\"timestamp_period_ns\":%.4f},",
         c.props.deviceName, c.props.limits.timestampPeriod);
  printf("\"params\":{\"track\":\"%s\",\"in\":[%d,%d],\"out\":[%d,%d],"
         "\"iterations\":%d,\"warmup\":%d,\"reference\":\"%s\"},",
         a.track.c_str(), a.in_w, a.in_h, a.out_w, a.out_h, iters, a.warmup,
         a.reference.empty() ? "procedural" : a.reference.c_str());
  printf("\"error\":null}\n");
  fflush(stdout);
  return 0;
}
