#version 450
// 离屏结果搬上交换链, 两臂/各场景完全相同的固定成本
layout(set = 0, binding = 0) uniform sampler2D uTex;
layout(location = 0) in vec2 vUV;
layout(location = 0) out vec4 oCol;
void main() { oCol = texture(uTex, vUV); }
