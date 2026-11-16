"""Compare synthetic HGamey pool members without compilers or device access."""

import copy
import json
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_hgamey_game_adapter import EXAMPLE, Reporter
from test_hgamey_game_adapter import local as hgamey_fixture

from llm4ad.gateway.examples.hgamey.benchmark import Benchmark, canonical, compiler_logs, digest
from llm4ad.gateway.examples.hgamey.evaluators import HGameyGameEvaluator, HGameyStaticEvaluator

local = hgamey_fixture
pytestmark = pytest.mark.unit


@pytest.fixture
def pool(local, tmp_path, monkeypatch):
    """Copy identical inputs to two independently bound synthetic installations."""
    adapter = tmp_path / "adapter"
    for name in ("assets", "protocol"):
        shutil.copytree(
            EXAMPLE / name, adapter / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    shutil.copyfile(EXAMPLE / "scoring.py", adapter / "scoring.py")
    monkeypatch.setattr(local.module, "__file__", str(adapter / "benchmark.py"))
    original_path = os.environ["PATH"]

    def new(name):
        """Create one phone's settings and relocated copies of the benchmark files."""
        root = tmp_path / name
        root.mkdir()
        tools = root / "build tools"
        shutil.copytree(local.builder.parent, tools)
        references = root / "references"
        shutil.copytree(local.filename.parent / "inputs/genshin", references)
        settings = copy.deepcopy(local.settings)
        settings.update(
            device_id=f"phone-{name}",
            expected_serial=f"physical-{name}",
            executor_url=f"http://executor-{name}.invalid:8767",
            execution_backend="direct",
            executor_data=str(root / "executor"),
            executor_config=str(root / "devices.json"),
            inputs_root=str(root / "inputs"),
            executor_inputs_prefix=f"/inputs/{name}",
            reference_path=str(references),
        )
        settings["game_check"].update(
            ndk_path=str(tools / "ndk"),
            phonefarm_root=str(tools / "phonefarm"),
            builder_path=str(tools / local.builder.name),
        )
        (root / "devices.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "devices": [
                        {
                            "id": settings["device_id"],
                            "label": name,
                            "host": f"adb-{name}.invalid",
                            "port": 5555,
                            "expected_serial": settings["expected_serial"],
                            "enabled": True,
                        }
                    ],
                }
            )
        )
        cxx = (
            tools / "ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang++"
        )
        clang, linker = cxx.with_name("clang++"), cxx.with_name("ld.lld")
        clang.write_bytes(b"synthetic native compiler")
        linker.write_bytes(b"synthetic native linker")
        return SimpleNamespace(
            filename=root / "settings.json",
            settings=settings,
            references=references,
            native=tools / "phonefarm/knobs/gray/layer/selfsr.cpp",
            builder=tools / local.builder.name,
            executable=tools / "phonefarm/phonefarm",
            cxx=cxx,
            clang=clang,
            linker=linker,
            glslc=tools / "ndk/shader-tools/linux-x86_64/glslc",
            ndk_properties=tools / "ndk/source.properties",
        )

    def load(profile, mode="static"):
        """Load a profile using only its own local tools and operator settings."""
        profile.filename.write_text(json.dumps(profile.settings))
        monkeypatch.setenv(
            "PATH", profile.settings["game_check"]["phonefarm_root"] + os.pathsep + original_path
        )
        return Benchmark(profile.filename, mode)

    return SimpleNamespace(new=new, load=load, adapter=adapter)


@pytest.mark.parametrize("mode", ["static", "game", None])
def test_two_phones_share_comparison_contract_but_keep_local_bindings(pool, mode):
    """Device IDs, physical serials, ADB endpoints and installation paths are local."""
    a, b = pool.new("a"), pool.new("b")
    b.settings["inputs_root"] = a.settings["inputs_root"]
    first, second = pool.load(a, mode), pool.load(b, mode)

    assert first.contract == second.contract
    assert first.manifest == second.manifest
    assert first.version == digest(canonical(first.manifest))
    assert first.settings["expected_serial"] != second.settings["expected_serial"]
    assert first.local_binding != second.local_binding
    assert first.profile != second.profile
    for benchmark in (first, second):
        pin = json.loads((benchmark.profile / "benchmark.json").read_text())
        assert pin["local_binding"] == benchmark.local_binding
        assert "device_id" not in pin["manifest"] and "expected_serial" not in pin["manifest"]
        if benchmark.game_check:
            assert set(benchmark.game_identity()) == {"mode", "options", "files"}
            benchmark.check_game_identity()


@pytest.mark.parametrize(
    "change",
    ["frames", "baseline", "protocol", "payload", "capability", "score", "threshold", "tools"],
)
def test_static_semantic_changes_separate_comparison_contracts(pool, local, monkeypatch, change):
    """Data, measurement rules, scoring and comparable capabilities remain versioned."""
    a, b = pool.new("a"), pool.new("b")
    first = pool.load(a)
    assert pool.load(b).contract == first.contract
    b.filename = b.filename.with_name("changed-settings.json")
    if change == "frames":
        (b.references / "frame.png").write_bytes(b"different reference pixels")
    elif change == "baseline":
        (pool.adapter / "assets/baseline.comp").write_bytes(b"different fixed baseline")
    elif change == "protocol":
        b.settings["request"]["protocol"]["rounds"] += 2
    elif change == "payload":
        b.settings["request"]["payload"]["quality_baseline_db"] += 1
    elif change == "capability":
        b.settings["device_profile"] = "different-gpu-driver-build"
    elif change == "score":
        b.settings["score_mode"] = "relative_latency"
    elif change == "threshold":
        b.settings["min_operator_improvement_pct"] = 2
    else:
        monkeypatch.setattr(
            local.module.Benchmark, "tool_version", lambda *args: "different compiler version"
        )
    assert pool.load(b).contract.data_version != first.contract.data_version


@pytest.mark.parametrize(
    "name", ["native", "builder", "executable", "cxx", "clang", "linker", "glslc", "ndk_properties"]
)
def test_game_build_content_changes_separate_contracts_and_block_local_builds(pool, name):
    """Relocation is comparable, but changing any actual build input remains unsafe."""
    a, b = pool.new("a"), pool.new("b")
    first, second = pool.load(a, "game"), pool.load(b, "game")
    assert first.contract == second.contract
    path = getattr(b, name)
    path.write_bytes(path.read_bytes() + b"\nchanged build input")

    first.check_game_identity()
    with pytest.raises(RuntimeError, match="build inputs changed"):
        second.check_game_identity()
    with pytest.raises(ValueError, match="Benchmark changed"):
        pool.load(b, "game")
    b.filename = b.filename.with_name("new-settings.json")
    assert pool.load(b, "game").contract.data_version != first.contract.data_version


@pytest.mark.parametrize(
    "change",
    [
        "options",
        "request_protocol",
        "capability",
        "scoring",
        "protocol_file",
        "scoring_file",
        "quality_protocol",
    ],
)
def test_game_semantic_changes_separate_comparison_contracts(pool, change):
    """Game options, score policy and executed protocol contents remain comparable only when equal."""
    a, b = pool.new("a"), pool.new("b")
    first = pool.load(a, "game")
    assert pool.load(b, "game").contract == first.contract
    b.filename = b.filename.with_name("changed-settings.json")
    if change == "options":
        b.settings["game_check"]["options"]["window"] = 9
    elif change == "request_protocol":
        b.settings["request"]["protocol"]["p_threshold"] = 0.05
    elif change == "capability":
        b.settings["device_profile"] = "different-gpu-driver-build"
    elif change == "scoring":
        b.settings["game_scoring"] = {
            "weights": {"fps": 1, "frame_p95": 0, "gpu_per_frame": 0, "quality": 0}
        }
    else:
        relative = {
            "protocol_file": "protocol/game_ab.py",
            "scoring_file": "scoring.py",
            "quality_protocol": "protocol/same_frame_quality.py",
        }[change]
        path = pool.adapter / relative
        path.write_bytes(path.read_bytes() + b"\n# Changed measurement implementation.\n")
        with pytest.raises(RuntimeError, match="build inputs changed"):
            first.check_game_identity()
    assert pool.load(b, "game").contract.data_version != first.contract.data_version


def test_evaluation_modes_and_legacy_fidelity_budgets_remain_distinct(pool):
    """Static, game and legacy fidelity semantics cannot be mixed in a pool."""
    a, b = pool.new("a"), pool.new("b")
    static, game = pool.load(a), pool.load(a, "game")
    assert static.contract.evaluator_id != game.contract.evaluator_id
    assert static.contract.data_version != game.contract.data_version
    for profile in (a, b):
        profile.settings["evaluation_profiles"] = {
            "standard": {"seconds": 5, "rounds": 8},
            "elite": {"seconds": 5, "rounds": 16},
        }
    legacy = pool.load(a, None)
    b.settings["evaluation_profiles"]["elite"]["rounds"] = 18
    assert pool.load(b, None).contract.data_version != legacy.contract.data_version


@pytest.mark.parametrize(
    "field",
    ["device_id", "expected_serial", "executor_url", "executor_inputs_prefix", "executor_config"],
)
def test_existing_settings_cannot_silently_rebind_local_execution(pool, field):
    """A comparison-compatible replacement phone still needs a fresh local profile."""
    a = pool.new("a")
    benchmark = pool.load(a)
    pin = benchmark.profile / "benchmark.json"
    original = pin.read_bytes()
    a.settings[field] = (
        "http://replacement.invalid:8767"
        if field == "executor_url"
        else a.settings[field] + "-changed"
    )
    with pytest.raises(ValueError, match="local binding differs"):
        pool.load(a)
    assert pin.read_bytes() == original
    a.filename = a.filename.with_name("replacement-settings.json")
    assert pool.load(a).contract == benchmark.contract


@pytest.mark.parametrize("field", ["builder_path", "ndk_path", "phonefarm_root", "phonefarm"])
def test_loaded_game_cannot_switch_to_identical_inputs_at_other_paths(pool, field):
    """A portable comparison hash does not authorize replacing the registered build configuration."""
    a, b = pool.new("a"), pool.new("b")
    first, second = pool.load(a, "game"), pool.load(b, "game")
    first.game_check[field] = second.game_check[field]
    assert first.game_identity() == first.manifest["game_check"]
    with pytest.raises(RuntimeError, match="build inputs changed"):
        first.check_game_identity()
    a.settings["game_check"].update(
        {key: second.game_check[key] for key in ("builder_path", "ndk_path", "phonefarm_root")}
    )
    with pytest.raises(ValueError, match="local binding differs"):
        pool.load(a, "game")


@pytest.mark.parametrize("mode", ["static", "game", None])
def test_legacy_pins_are_rejected_without_rewriting_running_attempts(pool, mode):
    """Old device-specific hashes and their accepted submissions require explicit migration."""
    a = pool.new("a")
    benchmark = pool.load(a, mode)
    pin = benchmark.profile / "benchmark.json"
    manifest = copy.deepcopy(benchmark.manifest)
    manifest.pop("comparison_identity_version")
    manifest.update(
        device_id=a.settings["device_id"], expected_serial=a.settings["expected_serial"]
    )
    if benchmark.game_check:
        manifest["game_check"].update(benchmark.game_check)
    legacy_version = digest(canonical(manifest))
    pin.write_text(json.dumps({"data_version": legacy_version, "manifest": manifest}))
    submission = benchmark.profile / "jobs/accepted/submission.json"
    submission.parent.mkdir(parents=True)
    submission.write_text(
        json.dumps({"data_version": legacy_version, "external_job_id": "running-job"})
    )
    original_pin, original_submission = pin.read_bytes(), submission.read_bytes()

    with pytest.raises(ValueError, match="Benchmark changed"):
        pool.load(a, mode)
    assert pin.read_bytes() == original_pin
    assert submission.read_bytes() == original_submission


@pytest.mark.parametrize(
    "field", ["job_id", "attempt_id", "task_id", "execution_id", "data_version"]
)
def test_comparison_identity_preserves_compiler_log_attempt_ownership(pool, local, field):
    """A shared contract cannot expose another job, task, attempt or local execution's logs."""
    benchmark = pool.load(pool.new("a"))
    job = local.job.model_copy(update={"execution_id": "local-execution"})
    folder = benchmark.profile / "jobs" / benchmark.attempt_key(job)
    folder.mkdir(parents=True)
    owner = {
        "job_id": job.id,
        "attempt_id": job.attempt_id,
        "task_id": job.task_id,
        "execution_id": job.execution_id,
        "data_version": benchmark.version,
    }
    (folder / "owner.json").write_text(json.dumps(owner))
    log = folder / "candidate.tool-0.log"
    log.write_text("synthetic diagnostic")
    assert compiler_logs(folder, job, benchmark.version) == {"compiler/candidate.tool-0.log": log}
    owner[field] = "another-owner"
    (folder / "owner.json").write_text(json.dumps(owner))
    with pytest.raises(ValueError, match="another accepted attempt"):
        compiler_logs(folder, job, benchmark.version)


@pytest.mark.parametrize("evaluator", [HGameyStaticEvaluator, HGameyGameEvaluator])
async def test_same_comparison_contract_still_rejects_wrong_executor_serial(
    pool, monkeypatch, evaluator
):
    """Phone eligibility remains strict before any measurement can be submitted."""
    a = pool.new("a")
    pool.load(a, evaluator.evaluation_mode)
    adapter = evaluator(a.filename)
    rpc = AsyncMock(
        side_effect=[
            {"features": ["idempotent_submit", "job_lookup", "cancel", "game_ab"]},
            {
                "devices": [
                    {
                        "id": a.settings["device_id"],
                        "enabled": True,
                        "expected_serial": "wrong-physical-phone",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(adapter, "rpc", rpc)
    with pytest.raises(RuntimeError, match="device identity differs"):
        await adapter.check_executor(None, Reporter())
    assert [call.args[1:3] for call in rpc.await_args_list] == [
        ("GET", "/v1/health"),
        ("GET", "/v1/devices"),
    ]
