#version 450
// idle_cap: 一个绕心旋转的小三角形, 负载可忽略, 已知被 vsync 钉住
layout(push_constant) uniform PC { float phase; int iters; } pc;
void main() {
    const vec2 verts[3] = vec2[](vec2(-0.05, -0.05), vec2(0.05, -0.05), vec2(0.0, 0.08));
    float c = cos(pc.phase), s = sin(pc.phase);
    vec2 p = verts[gl_VertexIndex];
    gl_Position = vec4(mat2(c, -s, s, c) * p, 0.0, 1.0);
}
