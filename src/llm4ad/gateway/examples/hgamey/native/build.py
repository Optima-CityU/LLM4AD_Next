"""Build the included Android runner, Vulkan layer and refbench APK from source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__:
    from .toolchain import ensure
else:
    from toolchain import ensure


def build(source: Path, toolchain: Path, output: Path, signing_key: Path | None = None) -> dict:
    """Compile real native artifacts in a scratch copy without touching a phone.

    Args:
        source: Included HGamey source root with phonefarm and refbench directories.
        toolchain: Reusable Android SDK/NDK and default signing-key directory.
        output: Destination for compiled Android artifacts and their manifest.
        signing_key: Persistent refbench signing-key path for compatible upgrades.

    Returns:
        Artifact identities and the exact toolchain/source versions used.
    """
    javac = shutil.which("javac")
    if javac is None:
        raise FileNotFoundError("Install OpenJDK 17+ to build the refbench APK")
    source = source.expanduser().resolve(strict=True)
    toolchain = toolchain.expanduser().resolve()
    output = output.expanduser().resolve()
    sdk, ndk = ensure(toolchain)
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(
        os.environ,
        ANDROID_SDK_ROOT=str(sdk),
        VKOP_NDK=str(ndk),
        REFBENCH_NDK=str(ndk),
        REFBENCH_BUILD_TOOLS=str(sdk / "build-tools/35.0.0"),
        JAVA_HOME=str(Path(javac).resolve().parents[1]),
    )
    key = signing_key.expanduser().resolve() if signing_key else toolchain / "refbench-debug.keystore"
    with tempfile.TemporaryDirectory(prefix="hgamey-native-") as scratch:
        work = Path(scratch)
        for name, relative in (
            ("vkop", "phonefarm/tools/vkop"),
            ("gray", "phonefarm/knobs/gray"),
            ("refbench", "refbench"),
        ):
            shutil.copytree(source / relative, work / name, ignore=shutil.ignore_patterns("out", "__pycache__"))
        apk_build = work / "refbench/build/out"
        if key.is_file():
            apk_build.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(key, apk_build / "debug.keystore")
        for script in ("vkop/build.sh", "gray/build/build_layer.sh", "refbench/build/build.sh"):
            subprocess.run(["bash", str(work / script)], env=environment, check=True, timeout=900)
        artifacts = {
            "android_aarch64_vkop_runner": work / "vkop/android_aarch64_vkop_runner",
            "libVkLayer_refknobs.so": work / "gray/build/out/libVkLayer_refknobs.so",
            "refbench-mali.apk": apk_build / "refbench.apk",
        }
        for name, path in artifacts.items():
            shutil.copyfile(path, output / name)
            (output / name).chmod(0o755 if name == "android_aarch64_vkop_runner" else 0o644)
        # Preserve the generated signing identity for subsequent APK upgrades.
        if not key.exists():
            key.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((apk_build / "debug.keystore").read_bytes())
        manifest = ET.parse(work / "refbench/app/AndroidManifest.xml").getroot()
        source_files = {
            path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.rglob("*"))
            if path.is_file() and not {"out", "target", "__pycache__", ".git"}.intersection(path.relative_to(source).parts)
        }
        report = {
            "schema": "hgamey/native-artifacts@1",
            "source": "included native/vendor/hgamey",
            "ndk_properties": (ndk / "source.properties").read_text(encoding="utf-8"),
            "sdk_build_tools": "35.0.0",
            "android_api": 35,
            "refbench_version": manifest.get("{http://schemas.android.com/apk/res/android}versionName"),
            "source_files": source_files,
            "files": {
                name: {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for name, path in artifacts.items()
            },
        }
        (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report


def main() -> None:
    """Build Android artifacts using the bundled source or an explicit source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).parent / "vendor/hgamey")
    parser.add_argument("--toolchain", type=Path, default=Path("./toolchain"))
    parser.add_argument("--output", type=Path, default=Path("./artifacts"))
    parser.add_argument("--signing-key", type=Path)
    args = parser.parse_args()
    report = build(args.source, args.toolchain, args.output, args.signing_key)
    print(json.dumps({"output": str(args.output.resolve()), "files": report["files"]}, indent=2))


if __name__ == "__main__":
    main()
