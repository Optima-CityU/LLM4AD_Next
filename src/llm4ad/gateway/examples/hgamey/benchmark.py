"""Local, versioned inputs for the Genshin Shader gateway example."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from llm4ad.evaluator.base import Metric, MetricType
from llm4ad.gateway.examples.hgamey.compilation.native import build_game_layer
from llm4ad.gateway.examples.hgamey.compilation.shader import compile_shader, validate_interface
from llm4ad.gateway.sdk.contracts import EvaluationContract
from llm4ad.gateway.sdk.retention import owned_path

COMPILER_LOG_NAMES = (
    "candidate.tool-0.log",
    "candidate.tool-1.log",
    "baseline.tool-0.log",
    "baseline.tool-1.log",
    "baseline-layer.log",
    "candidate-layer.log",
)


def digest(value: bytes) -> str:
    """Return a content identity suitable for paths and idempotency keys."""
    return hashlib.sha256(value).hexdigest()


def canonical(value: dict) -> bytes:
    """Encode persisted requests without platform-dependent formatting."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path: Path, value: dict) -> None:
    """Atomically replace a local evidence record."""
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def compiler_logs(folder: Path, job, data_version: str) -> dict[str, Path]:
    """List existing compiler logs only after checking attempt ownership.

    Args:
        folder: Expected profile/jobs/attempt staging directory.
        job: Authoritative SDK job and attempt.
        data_version: Frozen benchmark identity for this profile.

    Returns:
        Known relative log names and their regular, non-symlink local files.
    """
    root = folder.parent.parent.parent
    folder = owned_path(folder, root)
    owner = owned_path(folder / "owner.json", root)
    if not owner.is_file():
        return {}
    expected = {"job_id": job.id, "attempt_id": job.attempt_id, "task_id": job.task_id, "data_version": data_version}
    if json.loads(owner.read_text()) != expected:
        raise ValueError("Compiler logs belong to another accepted attempt")
    result = {}
    for name in COMPILER_LOG_NAMES:
        path = owned_path(folder / name, root)
        if path.is_file():
            result["compiler/" + name] = path
    return result


class Benchmark:
    """Freeze the operator benchmark outside the candidate worktree.

    Args:
        filename: Operator-owned JSON settings file.
    """

    def __init__(self, filename: str | Path):
        """Load and pin the local device, data and measurement contract."""
        self.filename = Path(filename).expanduser().resolve()
        self.settings = json.loads(self.filename.read_text(encoding="utf-8"))
        settings = self.settings
        if settings.get("game") != "genshin":
            raise ValueError("This example supports game=genshin only")
        for field in ("device_id", "expected_serial", "device_profile"):
            if not isinstance(settings.get(field), str) or not settings[field].strip():
                raise ValueError(f"Configure {field} for the authorized local phone")
        self.url = settings.get("executor_url", "http://127.0.0.1:8767").rstrip("/")
        from urllib.parse import urlsplit

        url = urlsplit(self.url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("executor_url must be an HTTP(S) service URL without credentials")
        self.inputs = self.local_path(settings["inputs_root"])
        reference = self.local_path(settings["reference_path"])
        frames = (
            [reference]
            if reference.is_file()
            else sorted(
                path for path in reference.iterdir() if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            )
        )
        if len(frames) > 32:
            frames = [frames[(index * (len(frames) - 1) + 15) // 31] for index in range(32)]
        if not frames or any(path.suffix.lower() not in {".png", ".jpg", ".jpeg"} for path in frames):
            raise ValueError("Provide Genshin reference PNG/JPEG frames (up to 32 evenly sampled sorted frames)")
        self.baseline = Path(__file__).with_name("assets") / "baseline.comp"
        self.compile_timeout = self.positive("compile_timeout", 60)
        self.poll_interval = self.positive("poll_interval", 2)
        self.compiler = settings.get("compiler", "glslangValidator")
        self.validator = settings.get("spirv_validator", "spirv-val")
        self.tools = {"compiler": self.tool_version(self.compiler), "validator": self.tool_version(self.validator)}
        self.request = settings["request"]
        if self.request.get("version") != 2 or self.request.get("kind") != "shader":
            raise ValueError("Configure a version 2 kind=shader request")
        if self.request.get("workload", {}).get("carrier") != "headless" or self.request.get("payload", {}).get("track") != "sr":
            raise ValueError("This first example uses the headless super-resolution operator benchmark")
        if self.request.get("protocol", {}).get("stage") != "full":
            raise ValueError("Use full A/B measurement; screen results do not establish improvement")
        if any(key in self.request.get("payload", {}) for key in ("shader_path", "spirv_path", "quality_reference")):
            raise ValueError("Candidate and reference paths are owned by the adapter")
        self.evaluation_profiles = self.load_profiles()
        self.score_mode = settings.get("score_mode", "latency")
        if self.score_mode not in {"latency", "relative_latency"}:
            raise ValueError("score_mode must be latency or relative_latency")
        self.min_operator_improvement_pct = self.load_improvement_threshold()
        self.prefix = PurePosixPath(settings.get("executor_inputs_prefix", "/inputs"))
        if not self.prefix.is_absolute() or ".." in self.prefix.parts:
            raise ValueError("executor_inputs_prefix must be an absolute container path")
        self.manifest = {
            "adapter_version": "1",
            "game": "genshin",
            "device_id": settings["device_id"],
            "expected_serial": settings["expected_serial"],
            "device_profile": settings["device_profile"],
            "baseline_sha256": digest(self.baseline.read_bytes()),
            "frames": [{"name": path.name, "sha256": digest(path.read_bytes())} for path in frames],
            "request": self.request,
            "tools": self.tools,
            "compile_flags": ["-V", "--target-env", "vulkan1.0", "-S", "comp"],
        }
        self.game_check = self.load_game_check() if "game_check" in settings else None
        if self.game_check is not None:
            self.manifest["game_check"] = self.game_identity()
        if self.evaluation_profiles is not None or self.score_mode != "latency":
            self.manifest.update(
                adapter_version="2",
                evaluation_profiles=self.evaluation_profiles,
                score_mode=self.score_mode,
                score_definition="100*(baseline-candidate)/baseline" if self.score_mode == "relative_latency" else "-latency",
            )
        if self.min_operator_improvement_pct > 0:
            self.manifest.update(
                adapter_version="3",
                min_operator_improvement_pct=self.min_operator_improvement_pct,
            )
        self.version = digest(canonical(self.manifest))
        self.contract = EvaluationContract(
            evaluator_id="hgamey-genshin-sr-headless",
            version="1",
            data_version=self.version,
            metrics=[
                Metric(name="operator_improvement_pct", type=MetricType.MAXIMIZE)
                if self.score_mode == "relative_latency"
                else Metric(name="operator_latency_ms", type=MetricType.MINIMIZE)
            ],
        )
        profile_name = digest(str(self.filename).encode())
        self.profile = self.inputs / "llm4ad-hgamey" / profile_name
        self.profile.mkdir(parents=True, exist_ok=True)
        pin = self.profile / "benchmark.json"
        # A worker must never silently switch the contract registered by its parent.
        try:
            with pin.open("x", encoding="utf-8") as stream:
                json.dump({"data_version": self.version, "manifest": self.manifest}, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if json.loads(pin.read_text(encoding="utf-8"))["data_version"] != self.version:
                raise ValueError("Benchmark changed: use a new settings filename and restart/register its gateway") from None
        self.references = self.profile / "reference"
        self.references.mkdir(exist_ok=True)
        for index, (frame, facts) in enumerate(zip(frames, self.manifest["frames"], strict=True)):
            target = self.references / f"{index:02d}{frame.suffix.lower()}"
            if not target.exists():
                shutil.copyfile(frame, target)
            if digest(target.read_bytes()) != facts["sha256"]:
                raise ValueError("Frozen reference frame changed; restore the original benchmark files")

    def local_path(self, value: str) -> Path:
        """Resolve a local operator path relative to the settings file."""
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.filename.parent / path).resolve()

    def load_profiles(self) -> dict | None:
        """Validate the two operator-owned fidelity budgets without changing data."""
        profiles = self.settings.get("evaluation_profiles")
        if profiles is None:
            return None
        if not isinstance(profiles, dict) or set(profiles) != {"standard", "elite"}:
            raise ValueError("evaluation_profiles must define standard and elite")
        for name, options in profiles.items():
            if not isinstance(options, dict) or set(options) != {"seconds", "rounds"}:
                raise ValueError(f"Profile {name} must contain only seconds and rounds")
            if any(type(options[key]) is not int or options[key] < minimum for key, minimum in (("seconds", 1), ("rounds", 2))):
                raise ValueError(f"Profile {name} requires positive seconds and at least two rounds")
        if any(profiles["elite"][key] < profiles["standard"][key] for key in ("seconds", "rounds")):
            raise ValueError("Elite budgets must be at least the standard budgets")
        return copy.deepcopy(profiles)

    def load_improvement_threshold(self) -> float:
        """Freeze the operator's minimum confirmed gain, preserving legacy profiles."""
        if "min_operator_improvement_pct" not in self.settings:
            return 1.0 if self.evaluation_profiles is not None or self.score_mode == "relative_latency" else 0.0
        threshold = self.settings["min_operator_improvement_pct"]
        if type(threshold) not in {int, float} or not math.isfinite(threshold) or not 1 <= threshold < 100:
            raise ValueError("min_operator_improvement_pct must be a finite number in [1, 100)")
        return float(threshold)

    def request_for(self, profile: str) -> dict:
        """Copy one frozen request with only its declared fidelity budget changed."""
        if profile not in {"standard", "elite"}:
            raise ValueError("Unknown evaluation profile")
        request = copy.deepcopy(self.request)
        if self.evaluation_profiles is not None:
            options = self.evaluation_profiles[profile]
            request["workload"]["seconds"] = options["seconds"]
            request["protocol"]["rounds"] = options["rounds"]
        return request

    def uses_game(self, profile: str) -> bool:
        """Run configured game validation only for elite in fidelity-aware settings."""
        return self.game_check is not None and (self.evaluation_profiles is None or profile == "elite")

    def attempt_key(self, job) -> str:
        """Keep legacy identities recoverable and bind new attempts to their profile."""
        identity = {"job": job.id, "attempt": job.attempt_id, "benchmark": self.version}
        if self.evaluation_profiles is not None:
            identity["evaluation_profile"] = job.evaluation_profile
        return digest(canonical(identity))

    def load_game_check(self) -> dict:
        """Resolve the optional operator-owned resize validation profile."""
        config = self.settings["game_check"]
        fields = {"ndk_path", "phonefarm_root", "builder_path"}
        if not isinstance(config, dict) or config.keys() - (fields | {"options"}):
            raise ValueError("game_check must contain builder_path, ndk_path, phonefarm_root and options")
        if any(not isinstance(config.get(key), str) or not config[key].strip() for key in fields):
            raise ValueError("Configure all game_check build paths")
        config = {**config, **{key: str(self.local_path(config[key])) for key in fields}}
        phonefarm = shutil.which("phonefarm")
        if not phonefarm:
            raise ValueError("game_check requires phonefarm on PATH")
        config["phonefarm"] = str(Path(phonefarm).resolve())
        defaults = {
            "package": "com.miHoYo.Yuanshen",
            "a": "sr:1:2800x1272:0",
            "b": "sr:1:2800x1272:0",
            "rounds": 1,
            "window": 8,
            "settle": 5,
            "enter_script": "s:35;t:0.5,0.93;s:20",
            "pan": True,
            "timeout_seconds": 900,
            "cool_max": 0,
            "power_profile": "disabled",
            "perfetto": True,
        }
        options = config.get("options", {})
        if not isinstance(options, dict) or options.keys() - defaults.keys():
            raise ValueError("Unsupported game_check options; candidate identities and layer paths are adapter-owned")
        options = defaults | options
        if options["package"] != defaults["package"]:
            raise ValueError("game_check supports Genshin only")
        arm = options["a"]
        if (
            not isinstance(arm, str)
            or arm != options["b"]
            or not re.fullmatch(r"sr:(?:1|0\.[1-9][0-9]*):[1-9][0-9]{1,3}x[1-9][0-9]{1,3}:(?:0|1|0\.[0-9]+)", arm)
            or any(not 64 <= int(value) <= 4096 for value in arm.split(":")[2].split("x"))
        ):
            raise ValueError("game_check requires matching SR arms with output extents within 64..4096")
        for key, low, high in (("rounds", 1, 12), ("window", 1, 120), ("timeout_seconds", 30, 14400)):
            if type(options[key]) is not int or not low <= options[key] <= high:
                raise ValueError(f"game_check {key} must be an integer within {low}..{high}")
        for key, high in (("settle", 300), ("cool_max", 60)):
            value = options[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= high
            ):
                raise ValueError(f"Invalid game_check {key}")
        if options["power_profile"] not in ("disabled", "linux_micro_negative", "oplus_milli_positive"):
            raise ValueError("Unsupported battery power profile")
        if any(type(options[key]) is not bool for key in ("pan", "perfetto")):
            raise ValueError("game_check pan and perfetto must be boolean")
        entry = options["enter_script"]
        if entry is not None:
            if not isinstance(entry, str) or not entry or len(entry) > 4096:
                raise ValueError("game_check enter_script must be a nonempty action script")
            for step in entry.split(";"):
                if re.fullmatch(r"s:[0-9]+(?:\.[0-9]+)?", step) and 0 <= float(step[2:]) <= 120:
                    continue
                if not re.fullmatch(r"t:(?:0|1|0\.[0-9]+),(?:0|1|0\.[0-9]+)", step):
                    raise ValueError("game_check entry actions support waits up to 120 seconds and normalized taps")
        config["options"] = options
        return config

    def game_identity(self) -> dict:
        """Hash the builder, native inputs and NDK tools used by both arms."""
        config = self.game_check
        ndk = Path(config["ndk_path"])
        native = Path(config["phonefarm_root"]) / "knobs/gray"
        paths = {
            "builder": Path(config["builder_path"]),
            "phonefarm": Path(config["phonefarm"]),
            "ndk_properties": ndk / "source.properties",
            "glslc": ndk / "shader-tools/linux-x86_64/glslc",
            "cxx": ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang++",
        }
        adapter = Path(__file__).parent
        for name in (
            "game_ab.py",
            "upstream/selfsr/selfsr_ab.py",
            "upstream/selfsr/evidence_transfer.py",
            "upstream/selfsr/mali_capture.py",
            "upstream/selfsr/mali_telemetry.py",
            "upstream/selfsr/frame_telemetry.py",
            "upstream/selfsr/power_telemetry.py",
            "upstream/selfsr/perfetto_capture.py",
            "upstream/selfsr/perfetto_analysis.py",
            "upstream/gfrc/gfrc_ab.py",
        ):
            paths[f"protocol/{name}"] = adapter / "protocol" / name
        # Android target drivers can be shell wrappers around the actual compiler.
        for name in ("clang++", "ld.lld"):
            path = paths["cxx"].parent / name
            if path.is_file():
                paths[name] = path
        native_files = {
            f"native/{path.relative_to(native).as_posix()}": path
            for path in sorted(native.rglob("*"))
            if path.is_file() and not {"out", "__pycache__", ".git"}.intersection(path.relative_to(native).parts)
        }
        if not native_files:
            raise ValueError("game_check phonefarm_root must contain knobs/gray native sources")
        hashes = {}
        for name, path in (paths | native_files).items():
            with path.open("rb") as stream:
                hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        return json.loads(canonical({**config, "mode": "resize", "files": hashes}))

    def check_game_identity(self) -> None:
        """Refuse to build under an identity that changed after registration."""
        try:
            current = self.game_identity()
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Game build inputs are unavailable: {error}") from error
        if current != self.manifest["game_check"]:
            raise RuntimeError("Game build inputs changed after benchmark registration")

    def build_game_layer(self, source: Path, output: Path, kind: str) -> Path:
        """Build the exact shader with the case-native layer compiler."""
        return build_game_layer(self.game_check, source, output, kind)

    def positive(self, key: str, default: float) -> float:
        """Read a finite positive duration from local settings."""
        value = float(self.settings.get(key, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive duration")
        return value

    def tool_version(self, executable: str) -> str:
        """Identify the exact local tool used to build device binaries."""
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=self.compile_timeout, check=True
        )
        return (result.stdout + result.stderr).strip()

    def container_path(self, path: Path) -> str:
        """Translate a staged host file through the executor's read-only mount."""
        return str(self.prefix / path.resolve().relative_to(self.inputs).as_posix())

    def compile(self, source: Path, output: Path) -> None:
        """Compile and validate a candidate before any device dispatch."""
        compile_shader(source, output, compiler=self.compiler, validator=self.validator, timeout=self.compile_timeout)

    validate_interface = staticmethod(validate_interface)
