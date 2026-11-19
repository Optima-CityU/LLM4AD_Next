"""HGamey device backend hooks for the shared SDK service."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from llm4ad.gateway.sdk.contracts import GatewayEvaluator, RemoteJob, load_evaluator
from llm4ad.gateway.sdk.executions import Executions
from llm4ad.gateway.sdk.journal import Journal, Reporter
from llm4ad.gateway.sdk.lifecycle import run_operation
from llm4ad.gateway.sdk.retention import owned_path
from llm4ad.gateway.sdk.runtime import GatewayBinding, GatewayRuntime
from llm4ad.gateway.sdk.storage import persist

from .benchmark import canonical, compiler_logs, digest, profile_key
from .configuration import configure
from .evidence import HGameyEvidencePolicy
from .pool import HGameyPoolEvaluator

if TYPE_CHECKING:
    from .execution import Engine


def run_startup_operation(filename, mode, payload, journal_path, operation, seconds, rounds):
    """Run readiness work in the same SDK slot used by platform evaluations.

    Args:
        filename: Frozen evaluator settings file.
        mode: Static, game, or legacy evaluator mode.
        payload: Private readiness job, unrelated to evolutionary fitness.
        journal_path: Durable readiness observations.
        operation: Evaluate or recover the original readiness identity.
        seconds: Shortened readiness sampling window.
        rounds: Fixed-image readiness rounds.

    Returns:
        The actual measurement result as a JSON-compatible dictionary.
    """
    from .evaluator import HGameyShaderEvaluator
    from .evaluators import HGameyGameEvaluator, HGameyStaticEvaluator

    cls = {"static": HGameyStaticEvaluator, "game": HGameyGameEvaluator}.get(mode, HGameyShaderEvaluator)
    adapter = cls(filename)
    job = RemoteJob.model_validate(payload)
    reporter = Reporter(Journal(journal_path), job.id, job.attempt_id)

    async def run():
        async def action():
            if operation == "recover":
                return await adapter.recover(job, reporter)
            state = reporter.saved_state
            if state["request"]["operation"] == "game-ab":
                state["request"]["options"].update(window=seconds, rounds=1)
            else:
                request = state["request"]["request"]
                request["workload"]["seconds"], request["protocol"]["rounds"] = seconds, rounds
            state["request_sha256"] = digest(canonical(state["request"]))
            state["purpose"] = "startup_readiness_only"
            reporter.save_state(state)
            persist(Path(state["evidence_directory"]) / "submission.json", state)
            return await adapter.evaluate(job, reporter)

        result = await run_operation(adapter, operation, job, reporter, action)
        if not await adapter.cancel(job, reporter.saved_state):
            raise RuntimeError("Readiness native cleanup was not confirmed")
        return result.model_dump(mode="json")

    return asyncio.run(run())


class HGameyRuntime(GatewayRuntime):
    """Own only the local device executor, immutable profiles and device restoration."""

    max_concurrency = 1

    def __init__(self, environment: dict, evaluator: str, executor_module=None):
        """Configure the hardware backend without starting a device experiment."""
        if os.name == "nt":
            raise ValueError("The Android phonefarm adapter requires Linux; use its container on Windows")
        if int(environment.get("LLM4AD_GATEWAY_CONCURRENCY", "1")) != 1:
            raise ValueError("Each HGamey evaluator binding has one slot; different phones run concurrently")
        environment = dict(environment)
        root = Path(environment.get("LLM4AD_GATEWAY_STATE_DIR", environment.get("HGAMEY_RUNTIME_DIR", ".llm4ad-gateway")))
        environment["HGAMEY_RUNTIME_DIR"] = str(root)
        environment.setdefault("EXECUTOR_DATA", str(root / "executor"))
        if executor_module is None:
            from . import execution as executor_module
        self.executor_module = executor_module
        self.adapter_directory = Path(environment.get("HGAMEY_ADAPTER_DIR", str(Path(__file__).parent))).resolve()
        self.options = configure(environment, executor_module, self.adapter_directory)
        self.evaluator = evaluator
        self.engine: Engine | None = None
        self.platform_directory = self.options["root"] / "platforms" / uuid.uuid5(uuid.NAMESPACE_URL, self.options["server"]).hex
        self.legacy: str | None = None
        self.pool_log_profiles: list[dict] = []
        self.device_checks: dict[str, dict[str, tuple[GatewayEvaluator, Path]]] = {}
        self.pool_checks: dict[str, asyncio.Task[None]] = {}
        self.device_check_locks: dict[str, asyncio.Lock] = {}

    def lease_directories(self) -> list[Path]:
        """Preserve backend/profile ownership and existing persisted gateway identities."""
        return [self.options["root"] / "service-lease", self.platform_directory]

    async def start(self) -> None:
        """Start one idle worker per phone before binding SDK evaluators."""
        self.legacy = self.legacy_device(self.platform_directory)
        if self.options["generated_devices"] is not None:
            persist(self.options["devices_file"], self.options["generated_devices"])
        # The shared daemon belongs to this runtime, never to an SDK attempt.
        if start := getattr(self.executor_module, "start_runtime", None):
            await asyncio.to_thread(start, self.options["executor_data"], self.options["devices_file"])
        self.engine = self.executor_module.Engine(
            self.options["executor_data"], self.options["devices_file"], defer_dispatch=True
        )

    def bindings(self) -> list[GatewayBinding]:
        """Advertise one evaluator per comparison contract."""
        groups: dict[str, list[tuple[dict, GatewayBinding]]] = {}
        for profile in self.options["profiles"]:
            device_id = profile["device"]["id"]
            directory = (
                self.platform_directory if device_id == self.legacy
                else self.platform_directory / "devices" / device_id
            )
            mode = profile["mode"]
            key = device_id
            if mode != "legacy":
                directory = directory / "evaluators" / mode
                key += f"--{mode}"
            persist(profile["settings_path"], profile["settings"])
            spec = self.configured_adapter(profile)
            contract = load_evaluator(spec).contract.model_dump(mode="json")
            contract["metrics"].sort(key=lambda metric: metric["name"])
            binding = GatewayBinding(
                key, profile["name"], spec, directory,
                metadata={"endpoint": profile["device"]["endpoint"], "device_id": device_id, "evaluation_mode": mode},
                resource_key=device_id,
            )
            groups.setdefault(digest(canonical(contract)), []).append((profile, binding))

        registry = self.platform_directory / "evaluators"
        anchors = {
            Path(json.loads(path.read_text())["directory"]) for path in registry.glob("*/pool-anchor.json")
        }
        bindings = []
        for key, profiles in groups.items():
            scope = registry / key
            if len(profiles) == 1 and not (scope / "pool-anchor.json").is_file() and profiles[0][1].directory not in anchors:
                bindings.append(profiles[0][1])
            else:
                bindings.append(self.pool_binding(key, scope, profiles, anchors))
        return bindings

    def pool_binding(self, key, scope, profiles, anchors):
        """Build the local pool while preserving one drained gateway identity."""
        anchor_path = scope / "pool-anchor.json"
        if anchor_path.is_file():
            directory = Path(json.loads(anchor_path.read_text())["directory"])
        else:
            previous = [binding.directory for _, binding in profiles if binding.directory not in anchors]
            for base in previous:
                for name in ("jobs.sqlite3", "startup.sqlite3"):
                    journal_path = base / name
                    if journal_path.is_file():
                        journal = Journal(journal_path)
                        Executions(journal)
                        if any(row["state"] not in {"completed", "failed", "cancelled"} for row in journal.rows()):
                            raise RuntimeError("Drain HGamey evaluator and readiness work before pooling devices")
                        with journal.connect() as db:
                            if db.execute("SELECT 1 FROM cleanup_debts LIMIT 1").fetchone():
                                raise RuntimeError("Resolve HGamey restoration obligations before pooling devices")
            # Keep one exact legacy registration when combining previously independent bindings.
            directory = next((path for path in previous if (path / "identity.json").is_file()), scope)
            persist(anchor_path, {"directory": str(directory)})
            anchors.add(directory)

        members = {}
        readiness = {}
        for profile, original in profiles:
            device_id = profile["device"]["id"]
            settings_path = profile["settings_path"]
            pooled = {
                **profile,
                "settings": {**profile["settings"], "execution_backend": "http"},
                "settings_path": settings_path.with_name(settings_path.stem + "-pool.json"),
            }
            persist(pooled["settings_path"], pooled["settings"])
            members[device_id] = self.configured_adapter(pooled)
            readiness[device_id] = (load_evaluator(original.evaluator), original.directory)
            self.pool_log_profiles.append(pooled)

        ready_path = scope / "ready.json"
        config_path = scope / "members.json"
        persist(ready_path, {"devices": []})
        persist(config_path, {
            "members": members,
            "startup_members": {profile["device"]["id"]: original.evaluator for profile, original in profiles},
            "key": key,
            "database": str(self.options["executor_data"].absolute() / "assignments.sqlite3"),
            "ready": str(ready_path),
        })
        spec = scope / "evaluator.py"
        source = (
            '"""Locally generated HGamey pool binding."""\n'
            "from llm4ad.gateway.examples.hgamey.pool import HGameyPoolEvaluator\n"
            "class PooledEvaluator(HGameyPoolEvaluator):\n"
            '    """Use the local registry for this comparison contract."""\n'
            "    def __init__(self):\n"
            f"        super().__init__({str(config_path)!r})\n"
        )
        spec.write_text(source, encoding="utf-8")
        self.device_checks[key] = readiness
        first_profile, first_binding = profiles[0]
        return GatewayBinding(
            key, first_binding.name, f"{spec}:PooledEvaluator", directory,
            capacity=len(members),
            metadata={"evaluation_mode": first_profile["mode"], "pool_status": str(ready_path)},
            fixed_capacity=True,
        )

    def api_handler(self):
        """Provide the private device-job API to the SDK-owned local server."""
        return self.executor_module.make_handler(self.engine)

    def _require_engine(self) -> Engine:
        """Get the running backend for operations that require an executor."""
        if self.engine is None:
            raise RuntimeError("HGamey device executor has not started")
        return self.engine

    def executor_for(self, binding: GatewayBinding):
        """Share one SDK delivery slot across native API and evaluator bindings.

        Args:
            binding: Evaluator binding with its canonical physical device ID.

        Returns:
            The already constructed SDK executor for this physical resource.
        """
        device_id = binding.metadata.get("device_id")
        return self.engine.executors[device_id] if self.engine and device_id else None

    async def check(self, gateway) -> None:
        """Verify local phones independently for each declared benchmark."""
        if not isinstance(gateway.adapter, HGameyPoolEvaluator):
            await self.check_device(gateway.adapter, gateway.root)
            return
        key = gateway.adapter.config["key"]
        if key not in self.pool_checks:
            started = asyncio.get_running_loop().create_future()
            self.pool_checks[key] = asyncio.create_task(self.verify_pool(key, gateway.adapter, started))
            done, _ = await asyncio.wait((started, self.pool_checks[key]), return_when=asyncio.FIRST_COMPLETED)
            if self.pool_checks[key] in done:
                self.pool_checks[key].result()
        if not gateway.adapter.verified():
            raise RuntimeError("No HGamey phone has passed readiness checks")

    async def verify_pool(self, key, adapter, started) -> None:
        """Retry unready members locally so healthy peers can still run."""
        pending = self.device_checks[key].copy()
        verified = set()

        async def verify(device_id, member, directory):
            try:
                await asyncio.wait_for(self.check_device(member, directory), self.options["startup_timeout"])
            except Exception as error:
                print(f"HGamey phone {device_id} failed readiness: {error}", flush=True)
                return
            verified.add(device_id)
            pending.pop(device_id)
            persist(adapter.ready_path, {"devices": sorted(verified)})
            if not started.done():
                started.set_result(None)

        while pending:
            await asyncio.gather(*(verify(device, *entry) for device, entry in list(pending.items())))
            if not started.done():
                started.set_result(None)
            if pending:
                await asyncio.sleep(15)

    async def check_device(self, adapter, directory):
        """Serialize startup restoration and readiness per physical phone."""
        device_id = adapter.benchmark.settings["device_id"]
        lock = self.device_check_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            await self.restore_startup_packets(adapter, device_id)
            engine = self._require_engine()
            engine.executors[device_id].release("sdk-restoration")
            engine.activate()
            if engine.health()["devices"][device_id]["status"] != "ok":
                raise RuntimeError(f"Device {device_id} has an unconfirmed restoration obligation")
            directory.mkdir(parents=True, exist_ok=True)
            await self.startup_check(adapter, directory)

    async def restore_startup_packets(self, adapter, device_id):
        """Retire interrupted readiness work before any recovered slot dispatch.

        Args:
            adapter: Current adapter used only for persisted native cleanup.
            device_id: Exact physical SDK resource whose queue is reconciled.
        """
        executor = self._require_engine().executors[device_id]
        target = __name__ + ":run_startup_operation"
        for key, packet in executor.entries().items():
            if packet.get("target") != target:
                continue
            executor.hold("hgamey-startup-restoration")
            await asyncio.to_thread(executor.cancel, key)
            args = packet["args"]
            job = RemoteJob.model_validate(args[2])
            path = owned_path(Path(args[3]), self.options["root"])
            journal = Journal(path)
            reporter = Reporter(journal, job.id, job.attempt_id)
            if not await adapter.cancel(job, reporter.saved_state):
                raise RuntimeError("Interrupted readiness restoration remains unconfirmed")
            journal.update(job.id, "cancelled", {"message": "Interrupted readiness cleanup verified",
                                               "external_cleanup_confirmed": True})
            executor.ack(key)
        executor.release("hgamey-startup-restoration")

    def details(self) -> dict:
        """Contribute observed device queue state to the shared health report."""
        return {"executor": self.engine.health() if self.engine else {"status": "starting"}}

    def alive(self) -> bool:
        """Report the actual executor worker status for every configured phone."""
        return bool(self.engine and not self.engine.closed)

    def ready(self) -> bool:
        """Keep healthy phones usable when another phone requires restoration."""
        return self.alive()

    def binding_ready(self, binding: GatewayBinding) -> bool:
        """Pause only the evaluator bindings whose phone requires restoration."""
        if not self.engine:
            return False
        health = self.engine.health()["devices"]
        if status := binding.metadata.get("pool_status"):
            device_ids = json.loads(Path(status).read_text())["devices"]
        else:
            device_ids = [binding.metadata["device_id"]]
        return any(
            health.get(device, {}).get("status") == "ok"
            and health[device].get("worker_alive", False)
            for device in device_ids
        )

    def evidence_policy(self, gateway):
        """Declare HGamey restoration and evidence ownership checks to the SDK collector."""
        return HGameyEvidencePolicy(self.engine, gateway)

    def local_logs(self, job: RemoteJob, saved_state: dict) -> dict[str, Path]:
        """Discover owned compiler and executor logs using local receipts only.

        Args:
            job: Authoritative SDK job and attempt being archived.
            saved_state: Accepted external intent, including an optional game arm.

        Returns:
            Existing regular files under configured staging and executor roots.

        Raises:
            ValueError: A saved intent, ownership receipt or path belongs elsewhere.
        """
        if saved_state and (saved_state.get("remote_job_id") != job.id or saved_state.get("remote_attempt_id") != job.attempt_id):
            raise ValueError("Log state belongs to another accepted attempt")
        logs, accepted_key = {}, None
        for profile in [*self.options["profiles"], *getattr(self, "pool_log_profiles", ())]:
            settings = profile["settings"]
            if saved_state and saved_state.get("evaluation_mode") != settings.get("evaluation_mode"):
                continue
            if saved_state and saved_state.get("request", {}).get("device_id") != settings["device_id"]:
                continue
            filename = profile["settings_path"].resolve()
            inputs = Path(settings["inputs_root"])
            if not inputs.is_absolute():
                inputs = filename.parent / inputs
            root = inputs.absolute() / "llm4ad-hgamey"
            directory = owned_path(root / profile_key(filename, settings.get("evaluation_mode")), root)
            manifest = owned_path(directory / "benchmark.json", root)
            if not manifest.is_file():
                continue
            version = json.loads(manifest.read_text())["data_version"]
            identity = {"job": job.id, "attempt": job.attempt_id, "benchmark": version}
            if getattr(job, "execution_id", None):
                identity["execution_id"] = job.execution_id
            if settings.get("evaluation_profiles") is not None:
                identity["evaluation_profile"] = job.evaluation_profile
            key = digest(canonical(identity))
            stage = owned_path(directory / "jobs" / key, root)
            if saved_state:
                if Path(saved_state["evidence_directory"]) != stage:
                    continue
                if saved_state.get("idempotency_key") != key or saved_state.get("data_version") != version:
                    raise ValueError("Log staging identity differs from the accepted attempt")
            if not (stage / "owner.json").is_file():
                continue
            logs.update(compiler_logs(stage, job, version))
            accepted_key = key
        if not saved_state:
            return logs
        if accepted_key is None:
            raise ValueError("No owned staging receipt exists for the accepted log state")
        root = self.options["executor_data"].absolute() / "jobs"
        operation = saved_state.get("operation", "eval")
        for arm, intent in [(operation, saved_state), *([("game-ab", saved_state["game"])] if "game" in saved_state else [])]:
            expected = accepted_key if intent is saved_state else digest(
                canonical({"candidate_id": accepted_key, "operation": arm})
            )
            if intent.get("idempotency_key") != expected or digest(canonical(intent["request"])) != intent["request_sha256"]:
                raise ValueError("Executor log intent changed after acceptance")
            key = intent.get("external_job_id")
            if not key:
                continue
            if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
                raise ValueError("Invalid executor log identity")
            folder = owned_path(root / key, root)
            receipt = owned_path(folder / "job.json", root)
            if not receipt.is_file():
                continue
            record = json.loads(receipt.read_text())
            if (
                record.get("id") != key
                or record.get("idempotency_key") != expected
                or record.get("device_id") != intent["request"]["device_id"]
                or record.get("operation") != arm
            ):
                raise ValueError("Executor logs belong to another accepted attempt")
            for name in ("worker.stdout", "worker.stderr", "backend.stdout", "backend.stderr", "game.stdout", "game.stderr"):
                path = owned_path(folder / name, root)
                if path.is_file():
                    logs[f"executor/{arm}/{name}"] = path
            for path in (folder / "evidence/quality/replay").glob("frame-*/native.*"):
                if path.name in {"native.stdout", "native.stderr"}:
                    path = owned_path(path, root)
                    if path.is_file():
                        logs[f"executor/{arm}/quality/{path.parent.name}/{path.name}"] = path
        return logs

    def stop_accepting(self) -> None:
        """Stop queue admission before SDK workers stop observing device jobs."""
        if self.engine:
            self.engine.stopping.set()

    async def close(self) -> None:
        """Wait for the device worker's existing cleanup before releasing ownership."""
        for task in self.pool_checks.values():
            task.cancel()
        await asyncio.gather(*self.pool_checks.values(), return_exceptions=True)
        if self.engine:
            await asyncio.to_thread(self.engine.close)

    async def startup_check(self, adapter, state_directory: Path, retries: int = 0) -> None:
        """Run or reconcile one readiness-only baseline through the normal queue."""
        if not self.options["startup_check"]:
            return
        journal = Journal(state_directory / "startup.sqlite3")
        Executions(journal)
        pending = [row for row in journal.rows() if row["state"] not in {"completed", "failed", "cancelled"}]
        if len(pending) > 1:
            raise RuntimeError("Multiple incomplete startup checks require operator reconciliation")
        if pending:
            payload = json.loads(pending[0]["job"])
            job = RemoteJob.model_validate(payload)
            reporter = Reporter(journal, job.id, job.attempt_id)
            submission = adapter.benchmark.profile / "jobs" / adapter.benchmark.attempt_key(job) / "submission.json"
            if not reporter.saved_state and not submission.is_file():
                # No dispatch can happen before the durable submission intent exists.
                journal.update(job.id, "cancelled", {"message": "Prior startup stopped before external submission"})
                return await self.startup_check(adapter, state_directory)
            operation = "recover"
        else:
            work = state_directory / "startup-work"
            work.mkdir(exist_ok=True)
            shutil.copyfile(adapter.benchmark.baseline, work / "shader.comp")
            job = RemoteJob(
                id=str(uuid.uuid4()),
                attempt_id=str(uuid.uuid4()),
                task_id=str(uuid.uuid4()),
                execution_id=str(uuid.uuid4()),
                project_root=str(work),
                timeout=self.options["startup_timeout"],
                evaluation_profile=(
                    "elite"
                    if adapter.benchmark.game_check is not None and adapter.benchmark.evaluation_mode is None
                    else "standard"
                ),
            )
            journal.accept(job.model_dump(mode="json"))
            reporter = Reporter(journal, job.id, job.attempt_id)
            reporter.progress("Readiness-only baseline compilation and queued device test")
            operation = "evaluate"

        reporter.metadata({"purpose": "startup_readiness_only"})
        executor = self._require_engine().executors[adapter.benchmark.settings["device_id"]]
        key = f"startup:{job.id}:{job.execution_id}"

        try:
            future = executor.submit(key, run_startup_operation, args=(
                str(adapter.benchmark.filename), adapter.benchmark.evaluation_mode,
                job.model_dump(mode="json"), str(journal.path), operation,
                self.options["startup_seconds"], self.options["startup_rounds"],
            ))
            executor.poll()
            try:
                while not future.done():
                    await asyncio.sleep(0.02)
                    executor.poll()
                summary = future.result()
            except BaseException:
                await asyncio.to_thread(executor.cancel, key)
                raise
        except Exception as error:
            confirmed = False
            # An unsuccessful cleanup must retain the original external obligation.
            with suppress(Exception):
                confirmed = await run_operation(
                    adapter, "cancel", job, reporter, lambda: adapter.cancel(job, reporter.saved_state)
                )
            journal.update(
                job.id, "failed" if confirmed else "recovery_required",
                {"message": f"Startup check failed: {type(error).__name__}: {error}",
                 "external_cleanup_confirmed": confirmed, "metadata": {"purpose": "startup_readiness_only"}},
            )
            if confirmed:
                executor.ack(key)
                if retries < 2:
                    return await self.startup_check(adapter, state_directory, retries + 1)
            raise
        summary["metadata"]["purpose"] = "startup_readiness_only"
        journal.update(job.id, "completed" if summary.get("success") else "failed", summary)
        executor.ack(key)
        if not summary.get("success"):
            raise RuntimeError(f"Startup baseline check failed: {summary.get('error_message')}")

    def configured_adapter(self, profile: dict) -> str:
        """Bind each spawned SDK worker to an immutable settings path.

        Args:
            profile: One validated device and its benchmark settings.

        Returns:
            SDK evaluator specification pointing at a local configured subclass.
        """
        path = profile["settings_path"].with_suffix(".py")
        mode = profile.get("mode", "legacy")
        evaluator = self.evaluator if mode == "legacy" else (
            "llm4ad.gateway.examples.hgamey.evaluators:"
            + ("HGameyStaticEvaluator" if mode == "static" else "HGameyGameEvaluator")
        )
        source = (
            '"""Locally generated device binding for spawned SDK workers."""\n'
            "from llm4ad.gateway.sdk.contracts import load_evaluator_class\n\n"
            f"_Evaluator = load_evaluator_class({evaluator!r})\n"
            "class DeviceEvaluator(_Evaluator):\n"
            '    """Use this device profile without process-global environment switches."""\n'
            "    def __init__(self):\n"
            f"        super().__init__({str(profile['settings_path'])!r})\n"
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(source, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        return f"{path}:DeviceEvaluator"

    def legacy_device(self, directory: Path) -> str | None:
        """Preserve the prior single-device identity and incomplete work in place.

        Args:
            directory: Platform state directory used by the previous container.

        Returns:
            Device ID bound to legacy state, or None for a new installation.
        """
        binding = directory / "legacy-device.json"
        if binding.exists():
            return cast(str, json.loads(binding.read_text())["device_id"])
        if not any((directory / name).exists() for name in ("identity.json", "jobs.sqlite3", "startup.sqlite3")):
            return None
        devices: set[str] = set()
        for name in ("startup.sqlite3", "jobs.sqlite3"):
            path = directory / name
            if path.exists():
                for row in Journal(path).rows():
                    saved = json.loads(row["saved"])
                    device_id = saved.get("request", {}).get("device_id")
                    if device_id:
                        devices.add(device_id)
        configured_devices = {profile["device"]["id"] for profile in self.options["profiles"]}
        if not devices and len(configured_devices) == 1:
            devices.update(configured_devices)
        if len(devices) != 1:
            raise RuntimeError(
                "Cannot identify the legacy device. Restore its original enabled-device configuration and restart."
            )
        device_id = devices.pop()
        persist(binding, {"device_id": device_id})
        return device_id
