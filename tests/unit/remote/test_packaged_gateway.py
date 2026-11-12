"""Exercise generic SDK workspaces, environment startup and real local checks."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from llm4ad.gateway.cli import app
from llm4ad.gateway.sdk.service import serve as _serve
from llm4ad.remote import EvaluationArtifact, EvaluationContract, EvaluationResult, GatewayEvaluator, Reporter
from llm4ad.remote.check import check_evaluator

USER = str(uuid.uuid4())


@pytest.mark.parametrize("relative", ["templates/custom", "examples/basic/deployment", "examples/hgamey/deployment"])
@pytest.mark.parametrize("override", [False, True])
def test_compose_dashboard_defaults_and_headless_optout(tmp_path, relative, override):
    """Render real Compose merges without loading credentials or starting containers."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is not installed")
    configuration = tmp_path / "docker-config"
    configuration.mkdir()
    command = [docker, "--config", str(configuration), "compose"]
    environment = {"PATH": os.defpath}
    version = subprocess.run(
        [*command, "version"], env=environment, text=True, capture_output=True, timeout=10
    )
    if version.returncode:
        pytest.skip("Docker Compose plugin is not installed")
    root = Path(__file__).resolve().parents[3] / "src/llm4ad/gateway" / relative
    command += ["--env-file", os.devnull, "-f", str(root / "compose.yaml")]
    if override:
        command += ["-f", str(root / "compose.ui.yaml")]
    command += ["config", "--no-env-resolution", "--format", "json"]
    environment.update(
        LLM4AD_GATEWAY_ENV_FILE=str(tmp_path / "unused-env"),
        LLM4AD_GATEWAY_DATA_DIR=str(tmp_path / "state"),
        HGAMEY_DATA_DIR=str(tmp_path / "data"),
        HGAMEY_INPUTS_DIR=str(tmp_path / "inputs"),
        HGAMEY_CONFIG_DIR=str(tmp_path / "configuration"),
        HGAMEY_PHONEFARM_DIR=str(tmp_path / "phonefarm"),
        ADB_IDENTITY_FILE=str(tmp_path / "unused-adb-identity"),
        ADB_STATE_DIR=str(tmp_path / "adb-state"),
    )
    for scenario in ("defaults", "custom-endpoint", "headless"):
        values = dict(environment)
        host, port = "127.0.0.1", "8768"
        if scenario != "defaults":
            host, port = "127.0.0.2", "18768"
            values.update(
                LLM4AD_GATEWAY_UI_HOST=host,
                LLM4AD_GATEWAY_UI_PORT=port,
                LLM4AD_GATEWAY_UI_PASSWORD="fixture-only-password",
            )
            if relative == "examples/hgamey/deployment":
                values["HGAMEY_NATIVE_IMAGE"] = "llm4ad-native-fixture:verified"
        if scenario == "headless":
            values["LLM4AD_GATEWAY_UI"] = "false"
        result = subprocess.run(command, env=values, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        gateway = json.loads(result.stdout)["services"]["gateway"]
        arguments = gateway["command"]
        assert "--ui" not in arguments and "--no-ui" not in arguments
        assert "--no-open-browser" in arguments
        assert gateway["environment"]["LLM4AD_GATEWAY_UI"] == ("false" if scenario == "headless" else "true")
        assert gateway["build"]["args"]["LLM4AD_EXTRAS"] == "gateway-ui"
        assert "LLM4AD_GATEWAY_UI_PASSWORD" not in gateway["build"]["args"]
        if relative == "templates/custom":
            assert gateway["environment"].get("LLM4AD_GATEWAY_UI_PASSWORD") == values.get("LLM4AD_GATEWAY_UI_PASSWORD")
        else:
            assert "LLM4AD_GATEWAY_UI_PASSWORD" not in gateway["environment"]
            assert gateway["env_file"]
        if relative == "examples/hgamey/deployment":
            assert gateway["build"]["args"]["HGAMEY_NATIVE_IMAGE"] == values.get("HGAMEY_NATIVE_IMAGE", "native-runtime")
            assert gateway["network_mode"] == "host" and not gateway.get("ports")
            assert gateway["environment"]["LLM4AD_GATEWAY_CONCURRENCY"] == "1"
            assert arguments[arguments.index("--ui-host") + 1] == host
            assert arguments[arguments.index("--ui-port") + 1] == port
        else:
            assert len(gateway["ports"]) == 1
            mapping = gateway["ports"][0]
            assert mapping["host_ip"] == host and str(mapping["published"]) == port
            assert mapping["target"] == 8768
            assert arguments[arguments.index("--ui-host") + 1] == "0.0.0.0"
            assert arguments[arguments.index("--ui-port") + 1] == "8768"


@pytest.fixture
def workspace(tmp_path):
    """Prepare user-owned evaluator code without a CLI project generator."""
    import shutil

    from llm4ad.gateway.examples.basic import evaluator as example

    root = tmp_path / "gateway"
    root.mkdir()
    source = Path(example.__file__).parent
    shutil.copy2(source / "evaluator.py", root / "evaluator.py")
    shutil.copy2(source / "runner.py", root / "runner.py")
    shutil.copytree(source / "candidate", root / "candidate")
    return root


def test_direct_cli_exposes_source_and_optional_templates(tmp_path):
    """The public workflow is direct startup and inspectable installed source."""
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0 and "start" in result.output and "source" in result.output
    result = CliRunner().invoke(app, ["source", "--adapter", "hgamey"])
    data = json.loads(result.stdout)
    assert Path(data["sdk"]).is_dir()
    assert Path(data["adapter"]).is_dir()
    assert Path(data["service"]).is_file()
    assert (Path(data["deployment"]) / "compose.yaml").is_file()


def test_builtin_example_check_needs_no_project(tmp_path):
    """Run the installed example without init, files or a platform connection."""
    result = CliRunner().invoke(app, ["check", "--adapter", "example", "--state-dir", str(tmp_path / "check")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["ok"]


def test_real_check_keeps_candidate_and_retains_explicit_files(workspace, tmp_path):
    """Run syntax compilation and an actual child process, without changing the source."""
    source = workspace / "candidate/solution.py"
    original = source.read_bytes()
    outcome = check_evaluator(f"{workspace / 'evaluator.py'}:Evaluator", source.parent, tmp_path / "check", 30)
    assert outcome["ok"] and outcome["state"] == "completed"
    assert outcome["result"]["metrics"]["mse"] == 8.8
    assert outcome["result"]["score"] == -8.8
    assert outcome["result"]["metadata"]["cases"] == 5
    assert len(outcome["artifacts"]) == 2
    for item in outcome["artifacts"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
    assert source.read_bytes() == original
    assert not (source.parent / "__pycache__").exists()
    assert not (tmp_path / "check/identity.json").exists()


@pytest.mark.parametrize("code", ["syntax error !", "def solve(x):\n    raise RuntimeError('candidate failure')\n"])
def test_check_reports_candidate_failure(workspace, tmp_path, code):
    """Compilation and execution failures remain inspectable and unsuccessful."""
    (workspace / "candidate/solution.py").write_text(code)
    result = check_evaluator(f"{workspace / 'evaluator.py'}:Evaluator", workspace / "candidate", tmp_path / "check", 30)
    assert not result["ok"] and result["state"] == "failed"
    assert result["result"]["message"]


def test_check_deadline_preserves_uncertainty(workspace, tmp_path):
    """A killed candidate cannot be reported as successful or silently retried."""
    (workspace / "candidate/solution.py").write_text("import time\ndef solve(x):\n    time.sleep(60)\n    return x\n")
    result = check_evaluator(f"{workspace / 'evaluator.py'}:Evaluator", workspace / "candidate", tmp_path / "check", 3)
    assert not result["ok"] and result["state"] == "recovery_required"
    assert "timed out" in result["result"]["message"]


def test_start_environment_and_cli_override(tmp_path, monkeypatch):
    """Container settings and normal flags share the same gateway implementation."""
    calls = []

    def construct(environment, spec):
        args = (environment["LLM4AD_SERVER_URL"], environment["LLM4AD_GATEWAY_NAME"],
                environment["LLM4AD_ALLOWED_USER_IDS"].split(","), spec,
                Path(environment["LLM4AD_GATEWAY_STATE_DIR"]), int(environment["LLM4AD_GATEWAY_CONCURRENCY"]))
        calls.append(args)
        return SimpleNamespace(options={"root": args[4]})

    monkeypatch.setattr("llm4ad.gateway.cli.create_service", construct)
    monkeypatch.setattr("llm4ad.gateway.cli.run_service", AsyncMock(return_value=0))
    env = {
        "LLM4AD_SERVER_URL": "https://platform.example.org/",
        "LLM4AD_GATEWAY_NAME": "lab",
        "LLM4AD_ALLOWED_USER_IDS": USER + "," + USER,
        "LLM4AD_EVALUATOR": "./custom.py:Evaluator",
        "LLM4AD_GATEWAY_STATE_DIR": str(tmp_path / "state"),
        "LLM4AD_GATEWAY_CONCURRENCY": "2",
    }
    result = CliRunner().invoke(app, ["start", "--no-ui"], env=env)
    assert result.exit_code == 0, result.output
    assert calls[0][:4] == ("https://platform.example.org", "lab", [USER], "./custom.py:Evaluator")
    assert calls[0][-1] == 2
    other = str(uuid.uuid4())
    result = CliRunner().invoke(app, ["start", "--no-ui", "--allow-user", other, "--concurrency", "3"], env=env)
    assert result.exit_code == 0, result.output
    assert calls[-1][2] == [other] and calls[-1][-1] == 3


@pytest.mark.parametrize(
    "server,user", [("ftp://invalid", USER), ("https://platform.example.org", "invalid"), ("https://platform.example.org", "")]
)
def test_invalid_startup_never_creates_identity(tmp_path, server, user):
    """Bad connection or sharing settings fail before acquiring state."""
    state = tmp_path / "state"
    result = CliRunner().invoke(
        app, ["start", "--no-ui", "--server", server, "--name", "lab", "--state-dir", str(state)],
        env={"LLM4AD_ALLOWED_USER_IDS": user}
    )
    assert result.exit_code != 0 and not state.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX container SIGTERM shutdown")
@pytest.mark.asyncio
async def test_container_sigterm_cancels_gateway_and_runs_cleanup():
    """Container stop reaches the gateway's async cleanup instead of abandoning it."""
    events = []

    async def run():
        try:
            asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.SIGTERM)
            await asyncio.sleep(10)
        finally:
            events.append("cleaned")

    with pytest.raises(asyncio.CancelledError):
        await _serve(SimpleNamespace(run=run))
    assert events == ["cleaned"]


def test_public_sdk_types_are_canonical():
    """Adapters import the platform's result and artifact contracts directly."""
    assert GatewayEvaluator and EvaluationContract and Reporter
    assert (
        EvaluationResult(metrics={}, artifacts=[EvaluationArtifact(name="a.json", path="a.json")]).artifacts[0].name == "a.json"
    )


def test_local_check_cli_returns_json_and_nonzero_on_failure(workspace, tmp_path):
    """The installed module CLI is scriptable and exposes real failure status."""
    (workspace / "candidate/solution.py").write_text("invalid syntax !")
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "llm4ad.remote",
            "check",
            "--evaluator",
            f"{workspace / 'evaluator.py'}:Evaluator",
            "--candidate",
            str(workspace / "candidate"),
            "--state-dir",
            str(tmp_path / "check"),
        ],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 1, process.stderr
    assert json.loads(process.stdout)["state"] == "failed"


def test_hgamey_start_uses_packaged_lifecycle(tmp_path, monkeypatch):
    """Built-in startup selects the in-package executor without exporting files."""
    from unittest.mock import Mock

    from llm4ad.gateway.examples.hgamey.evaluator import HGameyShaderEvaluator
    from llm4ad.gateway.sdk.runtime import GatewayRuntime

    factory = Mock(side_effect=GatewayRuntime)
    monkeypatch.setattr(HGameyShaderEvaluator, "create_runtime", factory)
    run = AsyncMock(return_value=0)
    monkeypatch.setattr("llm4ad.gateway.cli.run_service", run)
    result = CliRunner().invoke(
        app,
        [
            "start",
            "--no-ui",
            "--adapter",
            "hgamey",
            "--server",
            "https://platform.example.org",
            "--name",
            "phone",
            "--allow-user",
            USER,
            "--state-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    environment = factory.call_args.args[0]
    assert environment["LLM4AD_ALLOWED_USER_IDS"] == USER
    assert environment["LLM4AD_GATEWAY_STATE_DIR"] == str(tmp_path)
    assert run.call_args.args[0].runtime.__class__ is GatewayRuntime
    assert not list(tmp_path.iterdir())


def test_old_imports_reference_the_same_sdk():
    """Compatibility imports cannot fork journals, contracts or worker state."""
    from llm4ad.gateway.sdk.gateway import Gateway as NewGateway
    from llm4ad.remote.gateway import Gateway as OldGateway

    assert OldGateway is NewGateway


def test_optional_scaffold_is_available_and_protects_existing_files(tmp_path):
    """Templates remain an optional workflow over the same installed SDK."""
    root = tmp_path / "optional"
    result = CliRunner().invoke(app, ["init", str(root), "--server", "https://platform.example.org", "--allow-user", USER])
    assert result.exit_code == 0, result.output
    assert (root / "Dockerfile").is_file() and (root / "evaluator.py").is_file()
    assert (root / "compose.ui.yaml").is_file()
    original = (root / "evaluator.py").read_bytes()
    result = CliRunner().invoke(app, ["init", str(root)])
    assert result.exit_code != 0 and (root / "evaluator.py").read_bytes() == original


def test_scaffold_without_optional_readme_still_creates_runnable_sources(tmp_path, monkeypatch):
    """Initialize SDK resources when documentation is absent from a distribution."""
    from llm4ad.gateway import scaffold

    packaged = tmp_path / "resources"
    for source in scaffold.RESOURCES:
        target = packaged / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(scaffold.files("llm4ad.gateway").joinpath(source).read_bytes())
    monkeypatch.setattr(scaffold, "files", lambda _package: packaged)
    destination = tmp_path / "workspace"
    scaffold.initialize(destination)
    assert not (destination / "README.md").exists()
    assert (destination / "evaluator.py").is_file()
    assert (destination / "runner.py").is_file()
    assert (destination / "Dockerfile").is_file()
    assert (destination / ".env").is_file()


def test_explicit_custom_evaluator_overrides_builtin_environment(tmp_path, monkeypatch):
    """An inherited built-in selection must not override an explicit evaluator."""
    calls = []

    def construct(environment, spec):
        args = (environment["LLM4AD_SERVER_URL"], environment["LLM4AD_GATEWAY_NAME"],
                environment["LLM4AD_ALLOWED_USER_IDS"].split(","), spec,
                Path(environment["LLM4AD_GATEWAY_STATE_DIR"]), int(environment["LLM4AD_GATEWAY_CONCURRENCY"]))
        calls.append((args, os.environ.get("CUSTOM_DATA_ROOT")))
        return SimpleNamespace(options={"root": args[4]})

    monkeypatch.setattr("llm4ad.gateway.cli.create_service", construct)
    monkeypatch.setattr("llm4ad.gateway.cli.run_service", AsyncMock(return_value=0))
    config = tmp_path / "gateway.json"
    config.write_text(
        json.dumps(
            {
                "LLM4AD_SERVER_URL": "https://platform.example.org",
                "LLM4AD_GATEWAY_NAME": "lab",
                "LLM4AD_ALLOWED_USER_IDS": USER,
                "CUSTOM_DATA_ROOT": "local-data",
            }
        )
    )
    result = CliRunner().invoke(
        app,
        ["start", "--no-ui", "--config", str(config), "--evaluator", "./custom.py:Evaluator", "--state-dir", str(tmp_path / "state")],
        env={"LLM4AD_GATEWAY_ADAPTER": "hgamey"},
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0][3] == "./custom.py:Evaluator" and calls[0][1] == "local-data"
    assert os.environ.get("CUSTOM_DATA_ROOT") is None
