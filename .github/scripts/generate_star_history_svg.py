#!/usr/bin/env python3
"""Download the Star History SVG into the repository."""

from __future__ import annotations

import argparse
import os
import urllib.parse
import urllib.request
from pathlib import Path


def star_history_url(repository: str) -> str:
    query = urllib.parse.urlencode({"repos": repository.lower(), "type": "Date"})
    return f"https://api.star-history.com/svg?{query}"


def download_svg(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "llm4ad-star-history-updater"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read().decode("utf-8")
    if "<svg" not in content[:4096]:
        raise SystemExit(f"Downloaded content is not an SVG from {url}")
    return content


def write_svg(path: Path, svg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(svg, encoding="utf-8")
    tmp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("STAR_HISTORY_REPOSITORY", ""))
    parser.add_argument("--output", default=os.environ.get("STAR_HISTORY_PATH", "docs/assets/star-history.svg"))
    args = parser.parse_args()

    repository = args.repository.strip()
    if not repository:
        raise SystemExit("STAR_HISTORY_REPOSITORY is required")

    url = star_history_url(repository)
    write_svg(Path(args.output), download_svg(url))
    print(f"Updated {args.output} from {url}.")


if __name__ == "__main__":
    main()
