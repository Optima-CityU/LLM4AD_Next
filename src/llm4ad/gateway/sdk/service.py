"""Common gateway lifecycle, readiness, health and acknowledged evidence collection."""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from .contracts import load_evaluator_class
from .gateway import Gateway
from .health import HealthServer
from .journal import gateway_lease
from .retention import collect_finished, collect_local_logs
from .runtime import GatewayBinding, GatewayRuntime

if TYPE_CHECKING:
    from .scheduling import DurableExecutor

type _GatewayFactory = Callable[[str, str, list[str], str, Path, int], Gateway]
type _RetentionCounter = Literal["cleaned", "removed_bytes", "errors"]
_RETENTION_COUNTERS: tuple[_RetentionCounter, ...] = ("cleaned", "removed_bytes", "errors")


class _LocalLogRetention(TypedDict):
    """Track local log collection counts and the latest error type."""

    cleaned: int
    removed_bytes: int
    errors: int
    error_type: NotRequired[str]


class _EvidenceRetention(_LocalLogRetention):
    """Track the evidence collection schedule alongside its outcomes."""

    enabled: bool
    last_run: NotRequired[float]


class GatewayService:
    """Run ordinary evaluators and optional local backends with the same lifecycle."""

    def __init__(self, runtime: GatewayRuntime, gateway_factory: _GatewayFactory | None = None):
        """Keep backend-specific resources behind runtime hooks."""
        self.runtime = runtime
        self.options = runtime.options
        self.gateway_factory = gateway_factory or Gateway
        self.stop = asyncio.Event()
        self.gateways: dict[str, Gateway] = {}
        self.gateway_tasks: dict[str, asyncio.Task[None]] = {}
        self.binding_status: dict[str, str] = {}
        self.binding_errors: dict[str, str] = {}
        self.binding_available: dict[str, bool] = {}
        self.executors: dict[str, DurableExecutor] = {}
        self.cleanup_probe_tasks: dict[str, asyncio.Task[None]] = {}
        self.bound: list[GatewayBinding] = []
        self.gateway: Gateway | None = None
        self.gateway_task: asyncio.Task[None] | None = None
        self.startup_task: asyncio.Task[None] | None = None
        self.retention_task: asyncio.Task[None] | None = None
        self.health_server: HealthServer | None = None
        self.readiness = "starting"
        self.error: str | None = None
        self.admission_paused = False
        self.local_log_retention_task: asyncio.Task[None] | None = None
        self.local_log_retention_status: _LocalLogRetention = {"cleaned": 0, "errors": 0, "removed_bytes": 0}
        self.retention_status: _EvidenceRetention = {
            "enabled": self.options["cleanup_enabled"],
            "cleaned": 0,
            "removed_bytes": 0,
            "errors": 0,
        }

    def set_admission(self, paused: bool) -> None:
        """Pause only new platform claims without stopping the backend or reconciliation."""
        self.admission_paused = paused
        self.sync_admission()

    def _checking_resources(self) -> set[str]:
        """Identify resources fenced by an unfinished readiness check."""
        return {
            binding.resource_key or binding.key
            for binding in self.bound
            if self.binding_status.get(binding.key) == "checking"
        }

    def sync_admission(self) -> None:
        """Keep unavailable resources paused while reconciling their accepted work."""
        checking = self._checking_resources()
        for binding in self.bound:
            gateway = self.gateways[binding.key]
            paused = (
                self.admission_paused
                or self.binding_status.get(binding.key) != "ready"
                or (binding.resource_key or binding.key) in checking
                or not self.runtime.binding_ready(binding)
                or bool(getattr(getattr(gateway, "executor", None), "cleanup_debts", set()))
            )
            method = getattr(gateway, "pause_accepting" if paused else "resume_accepting", None)
            if method:
                method()

    async def apply_live_settings(
        self,
        *,
        name,
        allowed_users,
        concurrency,
        cleanup_enabled,
        cleanup_interval,
        cleanup_keep_seconds,
        local_log_keep_seconds=None,
    ) -> None:
        """Change universal settings without replacing accepted-task ownership."""
        maximum = self.runtime.max_concurrency
        if concurrency < 1 or (maximum is not None and concurrency > maximum):
            raise ValueError("Concurrency exceeds the adapter's supported capacity")
        previous_name = self.options["name"]
        self.options.update(
            name=name,
            allowed_users=allowed_users,
            cleanup_enabled=cleanup_enabled,
            cleanup_interval=cleanup_interval,
            cleanup_keep_seconds=cleanup_keep_seconds,
        )
        self.runtime.capacity = concurrency
        if local_log_keep_seconds is not None:
            self.options["local_log_keep_seconds"] = local_log_keep_seconds
        for binding in self.bound:
            binding.name = name + binding.name[len(previous_name) :] if binding.name.startswith(previous_name) else name
            if not binding.fixed_capacity:
                binding.capacity = concurrency
            gateway = self.gateways[binding.key]
            gateway.name, gateway.allowed_users, gateway.capacity = (
                binding.name,
                list(allowed_users),
                binding.capacity,
            )
            executor = getattr(gateway, "executor", None)
            if executor is not None:
                executor.concurrency = binding.capacity
            if wakeup := getattr(gateway, "wakeup", None):
                wakeup.set()
        self.retention_status["enabled"] = cleanup_enabled
        if cleanup_enabled and self.readiness == "ready" and (self.retention_task is None or self.retention_task.done()):
            self.retention_task = asyncio.create_task(self.cleanup_evidence())
        elif not cleanup_enabled and self.retention_task and not self.retention_task.done():
            self.retention_task.cancel()
            await asyncio.gather(self.retention_task, return_exceptions=True)

    def health(self) -> dict:
        """Combine generic transport observations with optional backend details."""
        gateways = {}
        checking = self._checking_resources()
        for binding in self.bound:
            gateway = self.gateways.get(binding.key)
            task = self.gateway_tasks.get(binding.key)
            contact = getattr(gateway, "last_contact", None)
            error = getattr(gateway, "last_error", None)
            status = self.binding_status.get(binding.key, "starting")
            if (binding.resource_key or binding.key) in checking:
                status = "checking"
            elif status == "ready" and not self.runtime.binding_ready(binding):
                status = "unready"
            gateways[binding.key] = {
                **binding.metadata,
                "name": binding.name,
                "status": status,
                "running": bool(task and not task.done()),
                "last_contact": contact,
                "last_error": error,
                "connected": bool(contact and not error and time.time() - contact < 60),
                "state_directory": str(gateway.root) if gateway else None,
                "startup_error": self.binding_errors.get(binding.key),
            }
        return {
            **self.runtime.details(),
            "status": self.readiness,
            "ready": self.readiness == "ready"
            and self.runtime.ready()
            and any(item["status"] == "ready" and item["running"] for item in gateways.values()),
            "startup_error": self.error,
            "retention": dict(self.retention_status),
            "local_log_retention": {
                **self.local_log_retention_status,
                "keep_seconds": self.options.get("local_log_keep_seconds", 604800),
            },
            "gateways": gateways,
            "gateway": next(iter(gateways.values()), None),
        }

    async def check_bindings(self, bindings: Sequence[GatewayBinding] | None = None) -> None:
        """Verify selected resources without discarding interrupted startup receipts.

        Args:
            bindings: Bindings to verify, or all bindings during initial startup.
        """
        errors = []
        groups: dict[str, list[GatewayBinding]] = {}
        for binding in self.bound if bindings is None else bindings:
            groups.setdefault(binding.resource_key or binding.key, []).append(binding)

        async def check_group(bindings: list[GatewayBinding]) -> None:
            verified = []
            for binding in bindings:
                self.binding_status[binding.key] = "checking"
            self.sync_admission()
            for binding in bindings:
                try:
                    check = self.runtime.check(self.gateways[binding.key])
                    timeout = self.options.get("startup_timeout")
                    await asyncio.wait_for(check, timeout=timeout)
                except asyncio.CancelledError:
                    for pending in bindings:
                        if self.binding_status.get(pending.key) == "checking":
                            self.binding_status[pending.key] = "unready"
                            self.binding_errors[pending.key] = (
                                "Startup check cancelled; readiness remains unverified"
                            )
                    self.sync_admission()
                    raise
                except Exception as error:
                    self.binding_status[binding.key] = "unready"
                    self.binding_errors[binding.key] = str(error) or type(error).__name__
                    self.binding_available[binding.key] = self.runtime.binding_ready(binding)
                    errors.append(
                        f"Evaluator {binding.key} startup check failed: {self.binding_errors[binding.key]}"
                    )
                    continue
                self.binding_errors.pop(binding.key, None)
                verified.append(binding.key)
            for key in verified:
                self.binding_status[key] = "ready"
            self.sync_admission()

        await asyncio.gather(*(check_group(bindings) for bindings in groups.values()))
        if all(status != "ready" for status in self.binding_status.values()):
            raise RuntimeError("; ".join(errors))
        self.sync_admission()

    async def cleanup_evidence(self) -> None:
        """Collect on the SDK schedule and finish in-flight deletion before shutdown."""
        while not self.stop.is_set():
            for gateway in self.gateways.values():
                work = asyncio.create_task(
                    asyncio.to_thread(
                        collect_finished,
                        gateway,
                        policy=self.runtime.evidence_policy(gateway),
                        keep_seconds=self.options["cleanup_keep_seconds"],
                    )
                )
                try:
                    values = await asyncio.shield(work)
                    self.retention_status["last_run"] = time.time()
                    for key in _RETENTION_COUNTERS:
                        self.retention_status[key] += values[key]
                except asyncio.CancelledError:
                    await work
                    raise
                except Exception as error:
                    self.retention_status["last_run"] = time.time()
                    self.retention_status["error_type"] = type(error).__name__
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=self.options["cleanup_interval"])

    async def cleanup_local_logs(self) -> None:
        """Expire local execution logs independently of evidence upload retention."""
        from .journal import Journal

        while not self.stop.is_set():
            for gateway in self.gateways.values():
                root = getattr(gateway, "root", None)
                if root is None:
                    continue
                for filename in ("jobs.sqlite3", "startup.sqlite3"):
                    path = root / filename
                    if not path.is_file():
                        continue

                    def collect(path=path, gateway=gateway):
                        hook = getattr(self.runtime, "evidence_policy", None)
                        return collect_local_logs(
                            gateway,
                            Journal(path),
                            policy=hook(gateway) if hook else None,
                            keep_seconds=self.options.get("local_log_keep_seconds", 604800),
                        )

                    work = asyncio.create_task(asyncio.to_thread(collect))
                    try:
                        values = await asyncio.shield(work)
                        for key in _RETENTION_COUNTERS:
                            self.local_log_retention_status[key] += values[key]
                    except asyncio.CancelledError:
                        await work
                        raise
                    except Exception as error:
                        self.local_log_retention_status["error_type"] = type(error).__name__
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=self.options["cleanup_interval"])

    async def _probe_startup_cleanup(self) -> None:
        """Schedule one cleanup probe per resource until its transports take ownership.

        Outstanding probes fence transport startup, preventing overlap with the
        probes inside Gateway.run. The service loop bounds scheduling frequency;
        gateway cleanup deadlines and durable retry times bound restoration work.
        """
        for resource, task in tuple(self.cleanup_probe_tasks.items()):
            if task.done():
                self.cleanup_probe_tasks.pop(resource)
                await task
        groups: dict[str, list[GatewayBinding]] = {}
        for binding in self.bound:
            groups.setdefault(binding.resource_key or binding.key, []).append(binding)
        checking = self._checking_resources()
        for resource, bindings in groups.items():
            if resource in checking or resource in self.cleanup_probe_tasks:
                continue
            if any(binding.key not in self.binding_status for binding in bindings):
                continue
            if any(binding.key in self.gateway_tasks for binding in bindings):
                continue
            gateways = [self.gateways[binding.key] for binding in bindings]
            gateways = [gateway for gateway in gateways if isinstance(gateway, Gateway)]
            if not any(getattr(gateway.executor, "cleanup_debts", set()) for gateway in gateways):
                continue

            async def probe_group(gateways: list[Gateway] = gateways) -> None:
                """Serialize restoration for evaluator bindings sharing a resource."""
                for gateway in gateways:
                    await gateway.probe_cleanup()

            self.cleanup_probe_tasks[resource] = asyncio.create_task(probe_group())

    async def _shutdown(self) -> list[BaseException]:
        """Attempt every cleanup and retain failures without asserting clean ownership.

        Returns:
            Failures from task finalization, pool termination, backend restoration
            and health shutdown, in the order they were observed.
        """
        self.readiness = "draining"
        failures: list[BaseException] = []
        try:
            self.runtime.stop_accepting()
        except BaseException as error:
            failures.append(error)
        tasks = [
            *self.gateway_tasks.values(),
            *self.cleanup_probe_tasks.values(),
            *([self.startup_task] if self.startup_task else []),
            *([self.retention_task] if self.retention_task else []),
            *([self.local_log_retention_task] if self.local_log_retention_task else []),
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        failures.extend(
            outcome for task, outcome in zip(tasks, outcomes, strict=True)
            if task is not self.startup_task
            and isinstance(outcome, BaseException)
            and not isinstance(outcome, asyncio.CancelledError)
        )
        self.cleanup_probe_tasks.clear()
        # Cancelling a probe leaves its shielded restoration owned by the gateway.
        for gateway in self.gateways.values():
            cleanups = set(getattr(gateway, "_cleanup_operations", {}).values())
            if not cleanups:
                continue
            try:
                _, pending = await asyncio.wait(cleanups, timeout=gateway._cleanup_timeout() + 5)
                if pending:
                    failures.append(TimeoutError("Gateway resource restoration did not finish before shutdown"))
                for task in pending:
                    task.cancel()
                outcomes = await asyncio.gather(*cleanups, return_exceptions=True)
                failures.extend(
                    outcome for outcome in outcomes
                    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError)
                )
            except BaseException as error:
                failures.append(error)
        for executor in self.executors.values():
            try:
                await asyncio.to_thread(executor.close)
            except BaseException as error:
                failures.append(error)
        try:
            await self.runtime.close()
        except BaseException as error:
            failures.append(error)
        if self.health_server:
            try:
                await asyncio.to_thread(self.health_server.close)
            except BaseException as error:
                failures.append(error)
        return failures

    async def run(self) -> int:
        """Own leases, readiness, transports, backend draining and local health."""
        options = self.options
        result_code = 0
        run_error: BaseException | None = None
        handled_error: Exception | None = None
        with ExitStack() as leases:
            held = set()
            for directory in self.runtime.lease_directories():
                leases.enter_context(gateway_lease(directory))
                held.add(directory)
            try:
                await self.runtime.start()
                self.health_server = HealthServer(
                    options["root"],
                    self.health,
                    port=options["port"],
                    handler=self.runtime.api_handler(),
                )
                self.bound = self.runtime.bindings()
                if not self.bound:
                    raise ValueError("Runtime must advertise at least one evaluator")
                for binding in self.bound:
                    if binding.key in self.gateways:
                        raise ValueError("Runtime evaluator keys must be unique")
                    if binding.directory not in held:
                        leases.enter_context(gateway_lease(binding.directory))
                        held.add(binding.directory)
                    gateway = self.gateway_factory(
                        options["server"],
                        binding.name,
                        options["allowed_users"],
                        binding.evaluator,
                        binding.directory,
                        binding.capacity,
                    )
                    gateway.cleanup_managed = True
                    if isinstance(gateway, Gateway):
                        from .scheduling import DurableExecutor

                        resource = binding.resource_key or binding.key
                        if resource not in self.executors:
                            existing = self.runtime.executor_for(binding)
                            if existing is None:
                                directory = options["root"] / "queues" / hashlib.sha256(resource.encode()).hexdigest()
                                existing = DurableExecutor(directory, concurrency=binding.capacity)
                            self.executors[resource] = existing
                        gateway.executor = self.executors[resource]
                        gateway.executor.resource_exclusive = binding.resource_key is not None
                        gateway.executor_owned = False

                    def local_sources(job: dict, saved: dict, runtime: GatewayRuntime = self.runtime) -> dict[str, Path]:
                        from .contracts import RemoteJob

                        hook = getattr(runtime, "local_logs", None)
                        sources: dict[str, Path] = hook(RemoteJob.model_validate(job), saved) if hook else {}
                        return sources

                    gateway.local_log_sources = local_sources
                    if self.admission_paused and (pause := getattr(gateway, "pause_accepting", None)):
                        pause()
                    self.gateways[binding.key] = gateway
                self.gateway = next(iter(self.gateways.values()))
                for gateway in self.gateways.values():
                    if isinstance(gateway, Gateway):
                        gateway.restore_pending()
                groups: dict[str, list[Gateway]] = {}
                for binding in self.bound:
                    groups.setdefault(binding.resource_key or binding.key, []).append(self.gateways[binding.key])

                async def restore_group(gateways: list[Gateway]) -> None:
                    for gateway in gateways:
                        if isinstance(gateway, Gateway):
                            await gateway.probe_cleanup()

                await asyncio.gather(*(restore_group(group) for group in groups.values()))
                for executor in self.executors.values():
                    executor.release("sdk-restoration")
                self.local_log_retention_task = asyncio.create_task(self.cleanup_local_logs())
                self.startup_task = asyncio.create_task(self.check_bindings())
                checked_task = None
                try:
                    while not self.stop.is_set():
                        await self._probe_startup_cleanup()
                        if self.startup_task.done() and self.startup_task is not checked_task:
                            checked_task = self.startup_task
                            try:
                                await self.startup_task
                            except Exception as error:
                                self.readiness, self.error = "unready", str(error)
                                result_code = 1
                                print(f"Gateway unready: {error}", flush=True)
                        checking = self._checking_resources()
                        self.sync_admission()
                        if any(status == "ready" for status in self.binding_status.values()):
                            self.readiness, self.error, result_code = "ready", None, 0
                            if not self.gateway_tasks:
                                print(
                                    "Verified evaluator gateways ready; waiting for platform jobs.",
                                    flush=True,
                                )
                            for binding in self.bound:
                                if (
                                    binding.key not in self.gateway_tasks
                                    and (binding.resource_key or binding.key) not in checking
                                    and (binding.resource_key or binding.key) not in self.cleanup_probe_tasks
                                ):
                                    self.gateway_tasks[binding.key] = asyncio.create_task(
                                        self.gateways[binding.key].run()
                                    )
                            self.gateway_task = next(iter(self.gateway_tasks.values()), None)
                            if options["cleanup_enabled"] and self.retention_task is None:
                                self.retention_task = asyncio.create_task(self.cleanup_evidence())
                        if self.startup_task.done():
                            recovered = []
                            for binding in self.bound:
                                if self.binding_status.get(binding.key) != "unready":
                                    continue
                                if (binding.resource_key or binding.key) in self.cleanup_probe_tasks:
                                    continue
                                available = self.runtime.binding_ready(binding)
                                if available and self.binding_available.get(binding.key) is False:
                                    recovered.append(binding)
                                self.binding_available[binding.key] = available
                            if recovered:
                                self.startup_task = asyncio.create_task(self.check_bindings(recovered))
                        for task in self.gateway_tasks.values():
                            if task.done():
                                await task
                                raise RuntimeError("Gateway transport stopped unexpectedly")
                        if not self.runtime.alive():
                            raise RuntimeError("Local evaluation backend stopped unexpectedly")
                        with suppress(TimeoutError):
                            await asyncio.wait_for(self.stop.wait(), timeout=0.5)
                except Exception as error:
                    handled_error = error
                    self.readiness, self.error = "unready", str(error)
                    print(f"Gateway unready: {error}", flush=True)
                    result_code = 1
            except BaseException as error:
                run_error = error
                raise
            finally:
                shutdown = asyncio.create_task(self._shutdown())
                interruptions: list[BaseException] = []
                while True:
                    try:
                        failures = await asyncio.shield(shutdown)
                        break
                    except asyncio.CancelledError as error:
                        if shutdown.cancelled():
                            raise
                        if run_error is None:
                            run_error = error
                        elif not isinstance(run_error, asyncio.CancelledError):
                            interruptions.append(error)
                failures = [failure for failure in failures if failure is not handled_error] + interruptions
                if failures:
                    if (original := run_error or handled_error) is not None:
                        failures.insert(0, original)
                    self.readiness = "unready"
                    self.error = "; ".join(str(failure) or type(failure).__name__ for failure in failures)
                    if len(failures) == 1:
                        raise failures[0]
                    raise BaseExceptionGroup("Gateway service shutdown failed", failures)
                self.readiness = "stopped"
                if run_error is not None:
                    raise run_error
        return result_code


def create_service(environment: dict, evaluator: str, gateway_factory: _GatewayFactory | None = None) -> GatewayService:
    """Select optional backend hooks through the evaluator interface for every entrypoint."""
    evaluator_class = load_evaluator_class(evaluator)
    factory = getattr(evaluator_class, "create_runtime", None)
    runtime = factory(environment, evaluator) if factory else None
    return GatewayService(GatewayRuntime(environment, evaluator) if runtime is None else runtime, gateway_factory)


async def run_service(service: GatewayService) -> int:
    """Translate process shutdown signals into the shared graceful stop sequence."""
    loop = asyncio.get_running_loop()
    installed = []
    try:
        if os.name != "nt":
            for name in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(name, service.stop.set)
                installed.append(name)
        return await service.run()
    finally:
        for name in installed:
            loop.remove_signal_handler(name)


async def serve(gateway):
    """Preserve the legacy direct coroutine runner for existing SDK integrations."""
    loop = asyncio.get_running_loop()
    installed = False
    try:
        if os.name != "nt":
            task = asyncio.current_task()
            assert task is not None
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            installed = True
        await gateway.run()
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)
