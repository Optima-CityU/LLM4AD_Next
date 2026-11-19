"""Fetch the pinned official Linux Android Platform Tools executable."""

import argparse
import os
import shutil
import zipfile
from pathlib import Path

if __package__:
    from .toolchain import download
else:
    from toolchain import download

ARCHIVE = "platform-tools_r36.0.2-linux.zip"
SHA256 = "3afdea91441815ab41254193df0343d92c1b1c0d0237165c3a345c8af8891c31"


def main() -> None:
    """Verify the official archive before installing its Linux ADB binary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("/var/cache/hgamey-android"))
    args = parser.parse_args()
    base = os.environ.get("ANDROID_REPOSITORY_BASE", "https://dl.google.com/android/repository")
    archive = download(base, ARCHIVE, SHA256, "sha256", args.cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle, bundle.open("platform-tools/adb") as source, args.output.open("wb") as target:
        shutil.copyfileobj(source, target)
    args.output.chmod(0o755)


if __name__ == "__main__":
    main()
