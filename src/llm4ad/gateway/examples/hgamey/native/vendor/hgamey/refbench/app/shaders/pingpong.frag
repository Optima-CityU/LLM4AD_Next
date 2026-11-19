#version 450
// bw_pingpong: 流量重、计算轻。4 tap 采样 + 极少 ALU。
// 内容只依赖 push constant 里的 phase (= 帧号的函数), 不读时钟。
layout(set = 0, binding = 0) uniform sampler2D uTex;
layout(push_constant) uniform PC { float phase; int iters; } pc;
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 oCol;
// 逐像素 hash 噪声 (确定性, 只依赖像素坐标与 phase)。
// 用途: 保持 RT 内容高熵, 让 UBWC 无损压缩失效 —— 否则模糊收敛成平滑渐变后
// 压缩比飙升, "带宽负载"会被硬件偷偷免单 (实测 230fps / bus vote 仅 1.3GB/s)。
float hash1(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}
void main() {
    // 单 tap 流式整面读: 每 pass 流量 = 读 33.5MB + 写 33.5MB (+ LOAD 33.5MB 当旋钮 off)。
    // 多 tap 会被纹理 cache 吃掉, 稀释 LOAD 的占比 —— 单 tap 让旋钮理论差恰为 1/3。
    vec4 a = texture(uTex, vUV);
    vec2 px = vUV * 2048.0 + vec2(pc.phase * 37.0, pc.phase * 23.0);
    vec4 noise = vec4(hash1(px), hash1(px + 19.7), hash1(px + 41.3), hash1(px + 77.1));
    oCol = a * 0.5 + noise * 0.5;
}
