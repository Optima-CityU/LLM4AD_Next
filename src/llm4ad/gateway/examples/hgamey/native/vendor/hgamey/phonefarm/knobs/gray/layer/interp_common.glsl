// interp_common.glsl — 插帧两条路线 (A: RIFE 低分辨率光流 + GPU 采样混合; B: 块匹配预对齐 + ANVIL 残差) 共用的 GLSL 片段。
// 由 glslc 的 #include 拉进各个 .comp (glslc 默认开 GL_GOOGLE_include_directive)。
//
// 约定:
//  * 全分辨率帧通过 sampler2D + texelFetch 读, 不用硬件双线性滤波: Adreno 采样器的亚纹素精度只有 8 位,
//    与 numpy 参考对不到 1e-3; 手写 4 次 texelFetch 的双线性才是可核对的。
//  * 像素值统一换算到 0..255 (rgba8 unorm 的 texelFetch 返回 v/255)。
//  * 所有「放大」都是半像素对齐的双线性 (tf.image.resize / torch interpolate align_corners=False),
//    所有「反向采样」坐标夹到 [0, size-1] (RIFE warplayer padding_mode=border, I1 的 warp_np)。
//  * NPU 侧缓冲都是行优先、通道紧密排列的 8 位 (uint8 / int8), 用 std430 uint 数组按 4 字节打包/解包,
//    不依赖 VK_KHR_8bit_storage。

// 反向采样: 在连续坐标 xy (像素中心为整数) 处双线性取值, 返回 0..255
vec3 warp255(sampler2D s, vec2 xy, ivec2 size) {
    vec2 c = clamp(xy, vec2(0.0), vec2(size) - 1.0);
    vec2 f = floor(c);
    ivec2 p0 = ivec2(f);
    ivec2 p1 = min(p0 + 1, size - 1);
    vec2 a = c - f;
    vec3 c00 = texelFetch(s, p0, 0).rgb;
    vec3 c10 = texelFetch(s, ivec2(p1.x, p0.y), 0).rgb;
    vec3 c01 = texelFetch(s, ivec2(p0.x, p1.y), 0).rgb;
    vec3 c11 = texelFetch(s, p1, 0).rgb;
    vec3 top = c00 * (1.0 - a.x) + c10 * a.x;
    vec3 bot = c01 * (1.0 - a.x) + c11 * a.x;
    return (top * (1.0 - a.y) + bot * a.y) * 255.0;
}

// 半像素对齐的放大坐标: 目标下标 x (共 dst 个) 对应源下标 i0/i1 与权重 a (源共 src 个)
void up_coord(int x, int dst, int src, out int i0, out int i1, out float a) {
    float s = max((float(x) + 0.5) * float(src) / float(dst) - 0.5, 0.0);
    float f = floor(s);
    i0 = min(int(f), src - 1);
    i1 = min(i0 + 1, src - 1);
    a = s - f;
}

// 连续坐标版: 源网格 (src 个格子, 每格在目标里占 cell 个像素), 目标连续坐标 p
void up_coord_f(float p, float cell, int src, out int i0, out int i1, out float a) {
    float s = max((p + 0.5) / cell - 0.5, 0.0);
    float f = floor(s);
    i0 = min(int(f), src - 1);
    i1 = min(i0 + 1, src - 1);
    a = s - f;
}

// 取 uint 数组里第 i 个字节 (0..255)
uint byte_at(uint word, uint i) { return (word >> ((i & 3u) * 8u)) & 0xFFu; }
// 同上, 按 int8 解释
float int8_at(uint word, uint i) { return float(int(word << ((3u - (i & 3u)) * 8u)) >> 24); }
// 4 个字节 (各 0..255) 打成一个 uint, b0 在最低位
uint pack4(uint b0, uint b1, uint b2, uint b3) { return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24); }
// 量化到 uint8: q = roundEven(v * inv_scale + zp) 夹到 0..255 (与 numpy np.round 的「四舍六入五成双」一致)
uint quant8(float v, float inv_scale, float zp) { return uint(clamp(roundEven(v * inv_scale + zp), 0.0, 255.0)); }

#ifdef FLOW_BINDING
// 块网格上的运动场 (每块一个 vec2, 全分辨率像素单位), 双线性放大到全分辨率连续坐标 p
layout(std430, binding = FLOW_BINDING) readonly buffer FlowGrid { float v[]; } flowg;
vec2 flow_at(vec2 p, int gw, int gh, float cell) {
    int x0, x1, y0, y1; float ax, ay;
    up_coord_f(p.x, cell, gw, x0, x1, ax);
    up_coord_f(p.y, cell, gh, y0, y1, ay);
    vec2 v00 = vec2(flowg.v[(y0 * gw + x0) * 2], flowg.v[(y0 * gw + x0) * 2 + 1]);
    vec2 v10 = vec2(flowg.v[(y0 * gw + x1) * 2], flowg.v[(y0 * gw + x1) * 2 + 1]);
    vec2 v01 = vec2(flowg.v[(y1 * gw + x0) * 2], flowg.v[(y1 * gw + x0) * 2 + 1]);
    vec2 v11 = vec2(flowg.v[(y1 * gw + x1) * 2], flowg.v[(y1 * gw + x1) * 2 + 1]);
    vec2 top = v00 * (1.0 - ax) + v10 * ax;
    vec2 bot = v01 * (1.0 - ax) + v11 * ax;
    return top * (1.0 - ay) + bot * ay;
}
#endif

#ifdef HUD_BINDING
// HUD 排除框: 框内像素不插帧, 直接取离目标时刻更近的那一帧 (半开区间 [x0,x1) × [y0,y1))
layout(std140, binding = HUD_BINDING) uniform HudBoxes { ivec4 box[16]; ivec4 count; } hud;
bool in_hud(ivec2 p) {
    for (int i = 0; i < hud.count.x && i < 16; i++) {
        ivec4 b = hud.box[i];
        if (p.x >= b.x && p.y >= b.y && p.x < b.z && p.y < b.w) return true;
    }
    return false;
}
#endif
