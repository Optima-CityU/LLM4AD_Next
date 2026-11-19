"""Validate standard and cloud search topology without touching live services."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def compose_model(*files):
    """Resolve public defaults without loading service env files or deploying."""
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required to validate Compose models")
    command = ["docker", "compose", "--profile", "*", "--env-file", "docker/.env.develop.local.example"]
    for file in files:
        command.extend(["-f", file])
    command.extend(["config", "--no-env-resolution", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        env=dict(os.environ, LLM4AD_COMPOSE_BASE="compose-cloud.yml"),
    )
    if "is not a docker command" in result.stderr:
        pytest.skip("Docker Compose plugin is not installed")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cloud_inherits_the_standard_services_and_registry_images():
    """Prevent a cloud deployment from silently missing search or its settings."""
    standard = compose_model("docker/compose.yml", "docker/compose.swr.yml")
    cloud = compose_model("docker/compose-cloud.yml")
    assert standard["services"] == cloud["services"]
    search = cloud["services"]["searxng"]
    assert not search.get("ports")
    assert search["environment"]["SEARXNG_ENGINES"] == cloud["services"]["backend"]["environment"]["PAPER_SEARCH_ENGINES"]


@pytest.mark.parametrize(
    "base,override",
    [
        ("docker/compose.yml", "docker/compose.override.yml"),
        ("docker/compose-cloud.yml", "docker/compose.remote-evaluation.yml"),
    ],
)
def test_search_survives_existing_compose_overrides(base, override):
    """Support development and remote-evaluation layouts without host exposure."""
    model = compose_model(base, override)
    assert not model["services"]["searxng"].get("ports")
    assert "PAPER_SEARCH_SEARXNG_URL" in model["services"]["backend"]["environment"]
