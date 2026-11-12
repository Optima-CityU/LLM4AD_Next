#!/usr/bin/env python3
"""Syntax only using already installed headers + declaration stubs; no GPU/APK."""
import argparse
from pathlib import Path
import subprocess
import tempfile
import sys
p=argparse.ArgumentParser(description=__doc__)
p.add_argument("--vulkan-include",required=True,help="existing directory containing vulkan/vk_video")
a=p.parse_args()
root=Path(__file__).resolve().parents[1]
headers=Path(a.vulkan_include)
with tempfile.TemporaryDirectory(prefix="refbench-host-syntax-") as folder:
    tmp=Path(folder)
    (tmp/"vulkan").mkdir()
    for file in (headers/"vulkan").iterdir():
        if file.is_file() and file.name != "vulkan_android.h":
            (tmp/"vulkan"/file.name).symlink_to(file)
    (tmp/"vulkan"/"vulkan_android.h").symlink_to(root/"tests/stubs/vulkan_android.h")
    if (headers/"vk_video").is_dir(): (tmp/"vk_video").symlink_to(headers/"vk_video")
    (tmp/"gen").mkdir()
    for name in ("fullscreen_vert","pingpong_frag","alu_frag","blit_frag","tri_vert","tri_frag"):
        (tmp/"gen"/(name+".h")).write_text(f"// NOT GPU-valid: syntax fixture only\nalignas(4) static const unsigned char g_{name}[]={{3,2,35,7}};\nstatic const unsigned int g_{name}_len=4;\n")
    command=["c++","-std=c++17","-fno-exceptions","-fno-rtti","-Wall","-Wextra","-Wno-missing-field-initializers","-fsyntax-only","-include",str(root/"tests/stubs/host_platform.h"),"-I",str(tmp),"-I",str(root/"tests/stubs"),str(root/"app/src/main.cpp")]
    print("HOST SYNTAX ONLY: Android/JNI declarations and non-executable SPIR-V fixture; no APK, ABI, GPU, or native frame validation",flush=True)
    sys.exit(subprocess.run(command).returncode)
