"""Common gateway lifecycle, readiness, health and acknowledged evidence collection."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import ExitStack, suppress

from .contracts import load_evaluator_class
from .gateway import Gateway
from .health import HealthServer
from .journal import gateway_lease
from .retention import collect_finished, collect_local_logs
from .runtime import GatewayRuntime


class GatewayService:
    """Run ordinary evaluators and optional local backends with the same lifecycle."""

    def __init__(self, runtime: GatewayRuntime, gateway_factory=None):
        """Keep backend-specific resources behind runtime hooks."""
        self.runtime = runtime
        self.options = runtime.options
        self.gateway_factory = gateway_factory or Gateway
        self.stop = asyncio.Event()
        self.gateways, self.gateway_tasks, self.binding_status = {}, {}, {}
        self.bound = []
        self.gateway = self.gateway_task = self.startup_task = self.retention_task = (
            self.health_server
        ) = None
        self.readiness, self.error = "starting", None
        self.admission_paused = False
        self.local_log_retention_task = None
        self.local_log_retention_status = {"cleaned": 0, "errors": 0, "removed_bytes": 0}
        self.retention_status = {
            "enabled": self.options["cleanup_enabled"],
            "cleaned": 0,
            "removed_bytes": 0,
            "errors": 0,
        }

    def set_admission(self, paused: bool) -> None:
        """Pause only new platform claims without stopping the backend or reconciliation."""
        self.admission_paused = paused
        for gateway in self.gateways.values():
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
            binding.name = (
                name + binding.name[len(previous_name) :]
                if binding.name.startswith(previous_name)
                else name
            )
            binding.capacity = concurrency
            gateway = self.gateways[binding.key]
            gateway.name, gateway.allowed_users, gateway.capacity = (
                binding.name,
                list(allowed_users),
                concurrency,
            )
            if wakeup := getattr(gateway, "wakeup", None):
                wakeup.set()
        self.retention_status["enabled"] = cleanup_enabled
        if (
            cleanup_enabled
            and self.readiness == "ready"
            and (self.retention_task is None or self.retention_task.done())
        ):
            self.retention_task = asyncio.create_task(self.cleanup_evidence())
        elif not cleanup_enabled and self.retention_task and not self.retention_task.done():
            self.retention_task.cancel()
            await asyncio.gather(self.retention_task, return_exceptions=True)

    def health(self) -> dict:
        """Combine generic transport observations with optional backend details."""
        gateways = {}
        for binding in self.bound:
            gateway = self.gateways.get(binding.key)
            task = self.gateway_tasks.get(binding.key)
            contact = getattr(gateway, "last_contact", None)
            error = getattr(gateway, "last_error", None)
            gateways[binding.key] = {
                **binding.metadata,
                "name": binding.name,
                "status": self.binding_status.get(binding.key, "starting"),
                "running": bool(task and not task.done()),
                "last_contact": contact,
                "last_error": error,
                "connected": bool(contact and not error and time.time() - contact < 60),
                "state_directory": str(gateway.root) if gateway else None,
            }
        return {
            **self.runtime.details(),
            "status": self.readiness,
            "ready": self.readiness == "ready"
            and self.runtime.ready()
            and bool(gateways)
            and all(item["running"] for item in gateways.values()),
            "startup_error": self.error,
            "retention": dict(self.retention_status),
            "local_log_retention": {
                **self.local_log_retention_status,
                "keep_seconds": self.options.get("local_log_keep_seconds", 604800),
            },
            "gateways": gateways,
            "gateway": next(iter(gateways.values()), None),
        }

    async def check_bindings(self) -> None:
        """Prepare each advertised resource once before accepting platform work."""
        for binding in self.bound:
            self.binding_status[binding.key] = "checking"
            try:
                check = self.runtime.check(self.gateways[binding.key])
                timeout = self.options.get("startup_timeout")
                await asyncio.wait_for(check, timeout=timeout)
            except Exception as error:
                self.binding_status[binding.key] = "unready"
                raise RuntimeError(
                    f"Evaluator {binding.key} startup check failed: {error}"
                ) from error
            self.binding_status[binding.key] = "ready"

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
                    self.retention_status.update(last_run=time.time())
                    for key in ("cleaned", "removed_bytes", "errors"):
                        self.retention_status[key] += values[key]
                except asyncio.CancelledError:
                    await work
                    raise
                except Exception as error:
                    self.retention_status.update(
                        last_run=time.time(), error_type=type(error).__name__
                    )
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
                        for key, value in values.items():
                            self.local_log_retention_status[key] += value
                    except asyncio.CancelledError:
                        await work
                        raise
                    except Exception as error:
                        self.local_log_retention_status["error_type"] = type(error).__name__
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=self.options["cleanup_interval"])

    async def run(self) -> int:
        """Own leases, readiness, transports, backend draining and local health."""
        options = self.options
        result_code = 0
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

                    def local_sources(job, saved, runtime=self.runtime):
                        from .contracts import RemoteJob

                        hook = getattr(runtime, "local_logs", None)
                        return hook(RemoteJob.model_validate(job), saved) if hook else {}

                    gateway.local_log_sources = local_sources
                    if self.admission_paused and (
                        pause := getattr(gateway, "pause_accepting", None)
                    ):
                        pause()
                    self.gateways[binding.key] = gateway
                self.gateway = next(iter(self.gateways.values()))
                self.local_log_retention_task = asyncio.create_task(self.cleanup_local_logs())
                self.startup_task = asyncio.create_task(self.check_bindings())
                stop_wait = asyncio.create_task(self.stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        [self.startup_task, stop_wait], return_when=asyncio.FIRST_COMPLETED
                    )
                    if self.stop.is_set():
                        return 0
                    if self.startup_task in done:
                        await self.startup_task
                    self.readiness = "ready"
                    print(
                        f"{len(self.gateways)} evaluator gateway(s) ready; waiting for platform jobs.",
                        flush=True,
                    )
                    self.gateway_tasks = {
                        key: asyncio.create_task(gateway.run())
                        for key, gateway in self.gateways.items()
                    }
                    self.gateway_task = next(iter(self.gateway_tasks.values()))
                    if options["cleanup_enabled"]:
                        self.retention_task = asyncio.create_task(self.cleanup_evidence())
                    while not self.stop.is_set():
                        for task in self.gateway_tasks.values():
                            if task.done():
                                await task
                                raise RuntimeError("Gateway transport stopped unexpectedly")
                        if not self.runtime.alive():
                            raise RuntimeError("Local evaluation backend stopped unexpectedly")
                        with suppress(TimeoutError):
                            await asyncio.wait_for(self.stop.wait(), timeout=0.5)
                except Exception as error:
                    self.readiness, self.error = "unready", str(error)
                    for task in self.gateway_tasks.values():
                        task.cancel()
                    await asyncio.gather(*self.gateway_tasks.values(), return_exceptions=True)
                    print(f"Gateway unready: {error}", flush=True)
                    await self.stop.wait()
                    result_code = 1
                finally:
                    stop_wait.cancel()
                    await asyncio.gather(stop_wait, return_exceptions=True)
            finally:
                self.readiness = "draining"
                self.runtime.stop_accepting()
                tasks = [
                    *self.gateway_tasks.values(),
                    *([self.startup_task] if self.startup_task else []),
                    *([self.retention_task] if self.retention_task else []),
                    *([self.local_log_retention_task] if self.local_log_retention_task else []),
                ]
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await self.runtime.close()
                finally:
                    if self.health_server:
                        await asyncio.to_thread(self.health_server.close)
                    self.readiness = "stopped"
        return result_code


def create_service(environment: dict, evaluator: str, gateway_factory=None) -> GatewayService:
    """Select optional backend hooks through the evaluator interface for every entrypoint."""
    evaluator_class = load_evaluator_class(evaluator)
    factory = getattr(evaluator_class, "create_runtime", None)
    runtime = factory(environment, evaluator) if factory else None
    return GatewayService(
        GatewayRuntime(environment, evaluator) if runtime is None else runtime, gateway_factory
    )


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
            loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
            installed = True
        await gateway.run()
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)
