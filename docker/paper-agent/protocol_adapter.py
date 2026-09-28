"""Run a workspace-local Anthropic protocol endpoint through cc-switch."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

CONFIG_ROOT = Path("/tmp/llm4ad-paper-protocol-adapter")
HOME = CONFIG_ROOT / "home"
CLAUDE_CONFIG_DIR = HOME / ".claude"
PROVIDER_CONFIG_PATH = CONFIG_ROOT / "provider.json"


def required_env(name: str) -> str:
    """Return a required environment value or fail without exposing secrets."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required protocol adapter setting: {name}")
    return value


def run_cc_switch(arguments: list[str], runtime_env: dict[str, str]) -> None:
    """Run one non-interactive cc-switch configuration command."""
    result = subprocess.run(
        ["cc-switch", *arguments],
        env=runtime_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("cc-switch protocol adapter configuration failed")


def main() -> int:
    """Configure the selected upstream and replace this process with its proxy."""
    upstream_base_url = required_env("LLM4AD_UPSTREAM_BASE_URL")
    upstream_api_key = required_env("LLM4AD_UPSTREAM_API_KEY")
    upstream_model = required_env("LLM4AD_UPSTREAM_MODEL")
    upstream_api_format = required_env("LLM4AD_UPSTREAM_API_FORMAT")
    listen_port = required_env("LLM4AD_PROTOCOL_PROXY_PORT")
    if upstream_api_format not in {"anthropic", "openai_chat"}:
        raise RuntimeError("Unsupported protocol adapter API format")
    try:
        parsed_port = int(listen_port)
    except ValueError as exc:
        raise RuntimeError("Invalid protocol adapter listen port") from exc
    if not 0 < parsed_port < 65536:
        raise RuntimeError("Invalid protocol adapter listen port")

    CLAUDE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    provider_config = {
        "env": {
            "ANTHROPIC_AUTH_TOKEN": upstream_api_key,
            "ANTHROPIC_BASE_URL": upstream_base_url,
            "ANTHROPIC_MODEL": upstream_model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": upstream_model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": upstream_model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": upstream_model,
        }
    }
    PROVIDER_CONFIG_PATH.write_text(
        json.dumps(provider_config, ensure_ascii=False),
        encoding="utf-8",
    )
    PROVIDER_CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    runtime_env = {
        **os.environ,
        "HOME": str(HOME),
        "CLAUDE_CONFIG_DIR": str(CLAUDE_CONFIG_DIR),
        "CC_SWITCH_CONFIG_DIR": str(CONFIG_ROOT),
    }
    run_cc_switch(
        [
            "--app",
            "claude",
            "provider",
            "add",
            "--name",
            "LLM4AD Runtime",
            "--id",
            "llm4ad-runtime",
            "--config-file",
            str(PROVIDER_CONFIG_PATH),
            "--api-format",
            upstream_api_format,
        ],
        runtime_env,
    )
    run_cc_switch(
        ["--app", "claude", "provider", "switch", "llm4ad-runtime"],
        runtime_env,
    )
    os.execvpe(
        "cc-switch",
        [
            "cc-switch",
            "proxy",
            "serve",
            "--listen-address",
            "127.0.0.1",
            "--listen-port",
            str(parsed_port),
        ],
        runtime_env,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(str(error) or "Protocol adapter failed", file=sys.stderr)
        raise SystemExit(1) from error
