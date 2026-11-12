#version 450
// frag_alu: 计算重、流量轻。循环上界来自 push constant (运行期值),
// 编译器无法展开或消除; 迭代是依赖链, 无法并行化掉。
layout(push_constant) uniform PC { float phase; int iters; } pc;
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 oCol;
void main() {
    vec2 p = vUV * 2.0 - 1.0;
    vec2 z = p + vec2(cos(pc.phase), sin(pc.phase)) * 0.1;
    float v = pc.phase;
    for (int i = 0; i < pc.iters; i++) {
        z = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) * 0.99 + p * 0.01;
        z = clamp(z, -2.0, 2.0);
        v = fma(v, 0.9993, dot(z, p));
        v = v - floor(v);
    }
    oCol = vec4(fract(v), fract(v * 0.7), fract(v * 1.3), 1.0);
}
