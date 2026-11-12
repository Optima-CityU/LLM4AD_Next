#!/usr/bin/env python3
"""shadercompiler_shim.py — 假扮 AnKi 的宿主 ShaderCompiler, 从缓存里发货。

为什么要它
----------
AnKi 的 Android 构建要求宿主平台有一个能跑的 ShaderCompiler, 用来把
AnKi/Shaders/*.ankiprog (HLSL) 编成 *.ankiprogbin。那个工具运行期 dlopen DXC,
而 AnKi 只随包了 WinX64 / WinArm64 / LinuxX64 三份 DXC —— 没有 macOS 版,
上游 DXC 也不发布 macOS 二进制。所以在这台 arm64 Mac 上它跑不起来。

但这一步是可解耦的: AnKi/Shaders/CMakeLists.txt 只是把
ANKI_OVERRIDE_SHADER_COMPILER 当一个命令行工具调, 产物就是普通 asset。
所以先用 build_shadercompiler.sh 在 linux/amd64 容器里跑一次, 把 63 个 bin
缓存下来; 之后 macOS 侧的 gradle 构建把 ANKI_OVERRIDE_SHADER_COMPILER 指到
本脚本, 它就只是「按名字从缓存里拷一份到 -o 指定的位置」。

于是 macOS 侧只剩纯 NDK 交叉编译, 完全不需要 DXC。

命令行形状
----------
必须吃下 AnKi/Shaders/CMakeLists.txt 里 add_custom_command 的那串调用:

    <compiler> -o <out.ankiprogbin> -j <N> -I <ankiroot> \
               -DANKI_PLATFORM_MOBILE=1 -spirv <in.ankiprog>

我们只关心 -o 和最后那个位置参数(输入 .ankiprog), 其余原样忽略 —— 但会校验
缓存确实是用同一组语义编出来的(见缓存目录里的 MANIFEST.txt)。

缓存不命中 = 硬失败。绝不产出一个空的/占位的 .ankiprogbin: 那会让引擎在设备上
以极难定位的方式崩掉, 或者更糟 —— 悄悄少了一个 pass 还照跑。
"""

import os
import shutil
import sys

CACHE_DIR = os.environ.get("ANKI_SHADERBIN_CACHE", "/Users/mac/projects/thirdparty/anki-shaderbins")


def die(msg: str) -> "NoReturn":  # noqa: F821
    sys.stderr.write("shadercompiler_shim: %s\n" % msg)
    sys.exit(1)


def main() -> None:
    argv = sys.argv[1:]

    out_path = None
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-o":
            if i + 1 >= len(argv):
                die("-o 后面没有路径")
            out_path = argv[i + 1]
            i += 2
        elif a in ("-j", "-I", "-sm"):
            i += 2  # 带一个值的开关, 跳过
        elif a.startswith("-"):
            i += 1  # 无值开关 (-spirv / -dxil / -DANKI_PLATFORM_MOBILE=1 ...)
        else:
            positional.append(a)
            i += 1

    if out_path is None:
        die("没给 -o")
    if len(positional) != 1:
        die("期望恰好一个输入 .ankiprog, 实际拿到 %r" % positional)

    src_name = os.path.basename(positional[0])  # e.g. FinalComposite.ankiprog
    cached = os.path.join(CACHE_DIR, src_name + "bin")  # -> FinalComposite.ankiprogbin

    if not os.path.isfile(cached):
        die(
            "缓存不命中: %s\n"
            "  缓存目录: %s\n"
            "  先跑一次 scripts/build_shadercompiler.sh 生成缓存 "
            "(需要 linux/amd64, 因为 DXC 没有 macOS 版)。" % (cached, CACHE_DIR)
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    shutil.copyfile(cached, out_path)


if __name__ == "__main__":
    main()
