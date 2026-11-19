"""Generate private SearXNG settings before running the official entrypoint."""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path


def build_settings() -> dict:
    """Enable JSON and only operator-selected engines from upstream defaults."""
    engines = list(
        dict.fromkeys(
            name.strip() for name in os.environ.get("SEARXNG_ENGINES", "bing,baidu,sogou,360search").split(",") if name.strip()
        )
    )
    if not engines or len(engines) > 8 or any(not re.fullmatch(r"[a-zA-Z0-9 _.-]{1,64}", name) for name in engines):
        raise ValueError("SEARXNG_ENGINES requires one to eight upstream engine names")
    return {
        "use_default_settings": {"engines": {"keep_only": engines}},
        "general": {"instance_name": "LLM4AD research search", "enable_metrics": False},
        "server": {"secret_key": secrets.token_hex(32), "limiter": False, "public_instance": False},
        "search": {"formats": ["html", "json"], "autocomplete": "", "max_page": 10},
        "outgoing": {"request_timeout": 8.0, "max_request_timeout": 12.0},
        "engines": [{"name": name, "disabled": False} for name in engines],
    }


def main() -> None:
    """Write JSON-compatible YAML and preserve the official image startup."""
    path = Path("/etc/searxng/settings.yml")
    path.write_text(json.dumps(build_settings()), encoding="utf-8")
    path.chmod(0o600)
    # The official entrypoint fixes ownership and starts its bundled WSGI server.
    os.execv("/usr/local/searxng/entrypoint.sh", ["/usr/local/searxng/entrypoint.sh"])


if __name__ == "__main__":
    main()
