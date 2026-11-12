"""Compile and exercise the real selfsr translation unit with a fake Vulkan driver."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class SelfsrTests(unittest.TestCase):
    """Exercise real hooks against a host-only simulated Vulkan driver."""

    @classmethod
    def setUpClass(cls):
        """Compile the real translation unit with isolated Vulkan headers."""
        cls.root = Path(__file__).resolve().parent
        headers = Path(os.environ.get(
            "VULKAN_HEADERS_DIR",
            str(Path.home() / ".cache/llm4ad/android-tools/ndk-r27d/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include"),
        ))
        if not (headers / "vulkan/vulkan.h").is_file():
            raise RuntimeError("Set VULKAN_HEADERS_DIR to existing Vulkan headers")
        cls.temp = tempfile.TemporaryDirectory(prefix="selfsr-host-")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        for name in ("vulkan", "vk_video"):
            if (headers / name).is_dir():
                (folder / name).symlink_to(headers / name, target_is_directory=True)
        (folder / "sys").mkdir()
        (folder / "sys/system_properties.h").write_text(
            '#pragma once\n#define PROP_VALUE_MAX 92\nint __system_property_get(const char*, char*);\n'
        )
        (folder / "selfsr_spv.h").write_text(
            'alignas(4) static const unsigned char kSelfsrUpSpv[20]={3,2,35,7};\n'
            'static const unsigned int kSelfsrUpSpvLen=20;\n'
            'alignas(4) static const unsigned char kSelfsrRcasSpv[20]={3,2,35,7};\n'
            'static const unsigned int kSelfsrRcasSpvLen=20;\n'
        )
        (folder / "selfsr_candidate_metadata.h").write_text(
            'static const char kSelfsrCandidateSourceSha256[]="source-fixture";\n'
            'static const char kSelfsrCandidateSpirvSha256[]="adapted-fixture";\n'
            'static const char kSelfsrCandidateKind[]="diagnostic";\n'
        )
        cls.exe = folder / "selfsr-test"
        compiled = subprocess.run([
            os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-Wno-missing-field-initializers", "-Wno-unused-parameter", "-pthread",
            "-I", str(folder), "-I", str(cls.root / "stubs"),
            "-I", str(cls.root.parent / "layer"), str(cls.root / "selfsr_test.cpp"),
            "-o", str(cls.exe),
        ], capture_output=True, text=True)
        if compiled.returncode:
            raise RuntimeError(compiled.stderr)

    def test_real_hooks(self):
        """Verify queue, submission, completion and cleanup failure boundaries."""
        for case in (
            "native", "live", "queue", "family", "format", "usage", "caps", "protected",
            "shared", "multi", "reset", "begin", "end", "reset_fence", "submit",
            "submit_oom", "fence_timeout", "present", "cleanup", "legacy",
        ):
            with self.subTest(case=case):
                result = subprocess.run([str(self.exe), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
