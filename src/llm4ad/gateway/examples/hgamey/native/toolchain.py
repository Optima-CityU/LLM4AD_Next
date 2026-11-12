"""Fetch checksum-verified Android build tools into a reusable local directory."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

NDK_VERSION = "27.3.13750724"
NDK_ARCHIVE = "android-ndk-r27d-linux.zip"
NDK_SHA1 = "22105e410cf29afcf163760cc95522b9fb981121"


def child(element, name):
    """Find a named direct child in the official Android repository metadata."""
    return next((c for c in element if c.tag.rsplit("}", 1)[-1] == name), None)


def archive_for(xml, package):
    """Resolve the Linux archive and published checksum for an Android package."""
    root = ET.fromstring(xml)
    for p in root.iter():
        if p.tag.rsplit("}", 1)[-1] != "remotePackage" or p.get("path") != package:
            continue
        for a in child(p, "archives"):
            host = child(a, "host-os")
            if host is not None and host.text != "linux":
                continue
            complete = child(a, "complete")
            if complete is not None:
                checksum = child(complete, "checksum")
                return (child(complete, "url").text.strip(), checksum.text.strip(), checksum.get("type", "sha1"))
    raise ValueError("Android repository has no Linux archive for " + package)


def download(base, name, checksum, algorithm, cache):
    """Reuse a verified archive or download and verify its exact content."""
    path = cache / name
    path.parent.mkdir(parents=True, exist_ok=True)

    def valid():
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, algorithm).hexdigest() == checksum

    if valid():
        return path
    opener = urllib.request.build_opener()
    partial = path.with_suffix(path.suffix + ".part")
    with opener.open(base.rstrip("/") + "/" + name, timeout=120) as source, partial.open("wb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    partial.replace(path)
    if not valid():
        raise ValueError("Android archive checksum mismatch: " + name)
    return path


def unpack(archive, destination):
    """Extract an official single-directory archive into the toolchain cache."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="unpack-") as folder:
        subprocess.run(["unzip", "-q", str(archive), "-d", folder], check=True)
        files = list(Path(folder).iterdir())
        if len(files) != 1 or not files[0].is_dir():
            raise ValueError("unexpected Android archive layout: " + str(archive))
        shutil.move(str(files[0]), destination)


def ensure(root):
    """Ensure the pinned Android NDK, SDK platform and build tools are available."""
    root = Path(root)
    sdk, ndk = root / "sdk", root / "ndk"
    cache = Path(os.environ.get("ANDROID_DOWNLOAD_CACHE", root / "downloads"))
    base = os.environ.get("ANDROID_REPOSITORY_BASE", "https://dl.google.com/android/repository")
    if not (ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang++").is_file():
        unpack(download(base, NDK_ARCHIVE, NDK_SHA1, "sha1", cache), ndk)
    missing = [
        ("build-tools;35.0.0", sdk / "build-tools/35.0.0", "aapt2"),
        ("platforms;android-35", sdk / "platforms/android-35", "android.jar"),
    ]
    missing = [(package, target) for package, target, marker in missing if not (target / marker).is_file()]
    if missing:
        opener = urllib.request.build_opener()
        with opener.open(base.rstrip("/") + "/repository2-3.xml", timeout=60) as stream:
            metadata = stream.read()
        for package, target in missing:
            name, checksum, algorithm = archive_for(metadata, package)
            unpack(download(base, name, checksum, algorithm, cache), target)
    return sdk, ndk
