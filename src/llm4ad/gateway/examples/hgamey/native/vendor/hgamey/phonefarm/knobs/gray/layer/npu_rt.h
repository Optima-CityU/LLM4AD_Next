// npu_rt.h — npuprobe.cpp (TFLite + QNN 运行时) 与 vk_layer_refknobs.cpp (npuop) 共用的接口。
// 两边都 include 这一份, 结构体只有一个定义。
#pragma once
#include <stddef.h>
#include <stdint.h>

// 模型输入输出 0 号张量的形状、类型与量化参数, 全部从模型里读出 (不写死)。
struct NpuIo {
    size_t in_bytes = 0, out_bytes = 0;
    float in_scale = 0, out_scale = 0;
    int in_zp = 0, out_zp = 0;
    int in_ndims = 0, out_ndims = 0;
    int in_dims[4] = {0, 0, 0, 0};    // NHWC
    int out_dims[4] = {0, 0, 0, 0};
    int in_type = 0, out_type = 0;    // TfLiteType: 1=float32 3=uint8 9=int8
    bool can_bind = false;            // TFLite 有 SetCustomAllocationForTensor (零拷贝可用)
};

void npu_log(const char* fmt, ...) __attribute__((format(printf, 1, 2)));

// 建好运行时 (第一次调用会卡几百毫秒), 报出 0 号输入输出张量的信息。
bool npu_rt_init(NpuIo* io);
// 拷贝模式: in -> 输入张量, 推理, 输出张量 -> out。
bool npu_rt_run(const void* in, size_t in_bytes, void* out, size_t out_bytes);
// 零拷贝模式: 让输入/输出张量直接用调用方的内存 (这里是 Vulkan 主机可见缓冲的映射地址)。
// 指针不变时不用重复调; 换槽位时再调一次。地址须 64 字节对齐。
bool npu_rt_bind(void* in, size_t in_cap, void* out, size_t out_cap);
// 零拷贝模式下的一次推理 (数据已经在绑定的内存里)。
bool npu_rt_invoke();
