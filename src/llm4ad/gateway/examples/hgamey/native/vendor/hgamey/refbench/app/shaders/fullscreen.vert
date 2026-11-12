#version 450
// 全屏三角形: 无顶点缓冲, gl_VertexIndex 生成 3 个顶点覆盖全屏
layout(location = 0) out vec2 vUV;
void main() {
    vUV = vec2((gl_VertexIndex << 1) & 2, gl_VertexIndex & 2);
    gl_Position = vec4(vUV * 2.0 - 1.0, 0.0, 1.0);
}
