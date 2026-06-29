import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "docker"
START_SCRIPT = DOCKER_DIR / "start.sh"


@pytest.fixture()
def fake_docker_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker-env.log"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
  exit 0
fi

if [[ "${1:-}" == "compose" ]]; then
  {
    printf 'SWR_REGISTRY=%s\\n' "${SWR_REGISTRY:-}"
    printf 'TAG=%s\\n' "${TAG:-}"
  } >> "${DOCKER_STUB_LOG}"
  exit 0
fi

printf 'unexpected docker args:'
printf ' %q' "$@"
printf '\\n' >&2
exit 1
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["DOCKER_STUB_LOG"] = str(log_file)
    env.pop("TAG", None)
    env.pop("SWR_REGISTRY", None)
    return env


def run_start(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(START_SCRIPT), *args],
        cwd=DOCKER_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def read_docker_env(env: dict[str, str]) -> str:
    return Path(env["DOCKER_STUB_LOG"]).read_text(encoding="utf-8")


def test_start_uses_default_docker_registry_and_version(
    fake_docker_env: dict[str, str],
) -> None:
    result = run_start(["start", "--dry-run"], fake_docker_env)

    assert result.returncode == 0, result.stderr
    docker_env = read_docker_env(fake_docker_env)
    assert "SWR_REGISTRY=docker.io/noah2012\n" in docker_env
    assert "TAG=latest\n" in docker_env


def test_mirrors_overrides_registry_and_preserves_tag(
    fake_docker_env: dict[str, str],
) -> None:
    fake_docker_env["TAG"] = "v1.2.3"

    result = run_start(
        ["upgrade", "--mirrors", "docker.1ms.run/noah2012", "--dry-run"],
        fake_docker_env,
    )

    assert result.returncode == 0, result.stderr
    docker_env = read_docker_env(fake_docker_env)
    assert "SWR_REGISTRY=docker.1ms.run/noah2012\n" in docker_env
    assert "TAG=v1.2.3\n" in docker_env


def test_mirrors_requires_value(fake_docker_env: dict[str, str]) -> None:
    result = run_start(["start", "--mirrors"], fake_docker_env)

    assert result.returncode == 2
    assert "Missing value for --mirrors" in result.stderr
