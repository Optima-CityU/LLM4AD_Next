"""Verify that the CLI reveals one skill topic at a time."""

import pytest
from rich.text import Text
from typer.testing import CliRunner

from llm4ad.frontend import cli

pytestmark = pytest.mark.unit
runner = CliRunner()


def test_skill_listing_and_index_do_not_load_runtime_settings(monkeypatch, tmp_path):
    """Discover the built-in skills from any working directory."""
    monkeypatch.chdir(tmp_path)

    def fail_if_loaded():
        pytest.fail("Skill lookup loaded dotenv")

    monkeypatch.setattr(cli, "load_dotenv", fail_if_loaded)
    listing = runner.invoke(cli.app, ["--skill", "list"])
    assert listing.exit_code == 0
    assert "`cloud-package`" in listing.stdout
    assert "`gateway`" in listing.stdout
    assert "--topic validation" not in listing.stdout
    assert "docker compose build gateway" not in listing.stdout

    index = runner.invoke(cli.app, ["--skill", "cloud-package"])
    assert index.exit_code == 0
    assert "llm4ad --skill cloud-package --topic validation" in index.stdout
    assert "llm4ad --skill cloud-package --topic remote-evaluation" in index.stdout
    assert "validate_and_pack.py" not in index.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("name", "topic", "expected", "other_topic"),
    [
        ("cloud-package", "validation", "validate_and_pack.py", "### Windows PowerShell"),
        ("llm4ad-evaluator-gateway", "docker", "docker compose build gateway", "# Native gateway startup"),
    ],
)
def test_skill_topic_returns_only_its_guidance(name, topic, expected, other_topic):
    """Resolve only the selected document and show its resource directory."""
    response = runner.invoke(cli.app, ["--skill", name, "--topic", topic])
    assert response.exit_code == 0
    assert expected in response.stdout
    assert "Skill directory:" in response.stdout
    assert other_topic not in response.stdout


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--skill", "missing"], "llm4ad --skill list"),
        (["--skill", "gateway", "--topic", "../SKILL.md"], "Unknown topic"),
        (["--topic", "native"], "requires --skill"),
        (["--skill", "list", "--topic", "native"], "requires a named skill"),
        (["--skill", "gateway", "version"], "cannot be combined with a command"),
    ],
)
def test_invalid_skill_queries_explain_available_options(arguments, message):
    """Reject invalid lookups before performing normal CLI actions."""
    response = runner.invoke(cli.app, arguments, env={"COLUMNS": "100"})
    assert response.exit_code == 2
    assert response.stdout == ""
    assert message in Text.from_ansi(response.stderr).plain
