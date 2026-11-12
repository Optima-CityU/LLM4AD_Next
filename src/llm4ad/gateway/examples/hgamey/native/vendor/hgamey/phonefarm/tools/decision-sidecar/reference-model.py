#!/usr/bin/env python3
"""生成参考决策模型(纯计算图, 不含权重)。

    python3 reference-model.py <输出路径.mlmodel>

模型只有一个矩阵乘:

    scores[i] = dot(state, options[i])      state: [64]   options: [32, 64]   scores: [32]

**这为什么是「参考模型」而不是「模型」**: 权重是运行时由输入给的 —— 换句话说
模型本身不说话, 说话的是喂它的那两个编码器。侧车(sidecar.swift)喂的是
字符三元组哈希编码器, 于是整条链路的语义 = 词面余弦相似度。

要把这条链路变成 Laya 那样的东西, 换的是**编码器**(把它换成真的 transformer,
或者在模型里加 encoder 层), 决策头的形状不变。所以这个文件的价值是:
让协议、侧车、Core ML 加载、三种题型的解码这四段能端到端跑起来并被测试覆盖,
而不是提供一个有质量的模型。

需要 coremltools(见 README.md 的安装说明)。用 NeuralNetworkBuilder 而不是
MIL builder: 这台机器上的 coremltools 9 + Python 3.14 缺 libmilstoragepython,
MIL 后端在导出那一步会报 "BlobWriter not loaded"。
"""

import sys

import coremltools as ct
from coremltools.models import datatypes
from coremltools.models.neural_network import NeuralNetworkBuilder

DIM = 256
MAX_OPTIONS = 32


def build():
    inputs = [
        ("state", datatypes.Array(DIM)),
        ("options", datatypes.Array(MAX_OPTIONS, DIM)),
    ]
    outputs = [("scores", datatypes.Array(MAX_OPTIONS))]
    b = NeuralNetworkBuilder(inputs, outputs, disable_rank5_shape_mapping=True)
    # state[64] 广播到 options[32,64], 逐元素乘, 再沿最后一维求和 → [32]
    b.add_multiply_broadcastable(name="prod", input_names=["options", "state"], output_name="prod")
    b.add_reduce_sum(name="sum", input_name="prod", output_name="scores", axes=[1], keepdims=False)
    return ct.models.MLModel(b.spec)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = sys.argv[1]
    model = build()
    model.save(out)
    spec = model.get_spec()
    print(f"写出 {out}")
    print(f"  输入: {[ (i.name, list(i.type.multiArrayType.shape)) for i in spec.description.input ]}")
    print(f"  输出: {[ (o.name, list(o.type.multiArrayType.shape)) for o in spec.description.output ]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
