"""Shared gateway administration independent of browsers and UI dependencies."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from pathlib import Path

from llm4ad.gateway.settings import COLD_SETTINGS, LISTENER_SETTINGS, GatewaySettings, SettingsStore

from .journal import Journal, gateway_lease
from .runtime import platform_directory
from .service import create_service


@contextmanager
def runtime_environment(environment: dict):
    """Apply effective adapter settings for a service and all of its spawned workers."""
    previous = dict(os.environ)
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


class GatewayController:
    """Own configuration transitions and expose safe, paginated operator observations."""

    def __init__(
        self,
        settings: GatewaySettings,
        bootstrap_root: Path,
        *,
        environment=None,
        service_factory=None,
    ):
        """Bind the stable configuration root separately from editable execution storage."""
        self.settings = settings
        self.bootstrap_root = bootstrap_root.expanduser().resolve()
        self.store = SettingsStore(self.bootstrap_root)
        saved = self.store.read()
        self.pending_settings = (
            GatewaySettings.model_validate(saved["pending"]) if saved.get("pending") else None
        )
        if saved.get("applied"):
            previous = GatewaySettings.model_validate(saved["applied"])
            overrides = {
                key: value
                for key, value in settings.model_dump().items()
                if value != getattr(previous, key)
            }
            if self.pending_settings is not None or any(key in COLD_SETTINGS for key in overrides):
                desired = self.pending_settings or previous
                self.pending_settings = GatewaySettings.model_validate(
                    {**desired.model_dump(), **overrides}
                )
                self.settings = GatewaySettings.model_validate(
                    {
                        **previous.model_dump(),
                        **{
                            key: value
                            for key, value in overrides.items()
                            if key not in COLD_SETTINGS
                        },
                    }
                )
        self.paused = bool(saved.get("paused", False))
        self.environment = dict(os.environ if environment is None else environment)
        self.service_factory = service_factory or create_service
        self.service = self.service_task = self.monitor_task = None
        self.status, self.error = "configuration_required", None
        self.pending_error = saved.get("pending_error")
        self._previous = None
        self._rollback = None
        self._listener = {key: getattr(settings, key) for key in LISTENER_SETTINGS}
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._lease = None

    def _persist(self):
        self.store.write(self.settings, self.pending_settings, self.paused, self.pending_error)

    async def start(self) -> None:
        """Start one SDK lifecycle regardless of browser connections or reloads."""
        if self.monitor_task:
            return
        lease = gateway_lease(self.bootstrap_root / "dashboard-lease")
        lease.__enter__()
        self._lease = lease
        if self.pending_settings and not self.pending_error and await self._idle():
            self.settings, self.pending_settings = self.pending_settings, None
            self._persist()
        if self.settings.ready:
            self._launch()
        self.monitor_task = asyncio.create_task(self._monitor())

    def _launch(self):
        self.status, self.error = "starting", None
        settings = self.settings

        async def run():
            try:
                settings.require_ready()
                with runtime_environment(settings.environment(self.environment)):
                    self.service = self.service_factory(
                        settings.environment(self.environment), settings.evaluator_spec
                    )
                    self.service.set_admission(self.paused or self.pending_settings is not None)
                    await self.service.run()
            except Exception as error:
                self.error = str(error)
                self.status = "unready"

        self.service_task = asyncio.create_task(run())

    async def _stop_service(self):
        service, task = self.service, self.service_task
        if service:
            service.stop.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        self.service = self.service_task = None

    def _sources(self, binding=None):
        if self.service and self.service.bound:
            return [
                (item.key, item.name, item.directory / "jobs.sqlite3")
                for item in self.service.bound
                if binding is None or binding == item.key
            ]
        directory = platform_directory(Path(self.settings.state_dir), self.settings.server)
        paths = [("gateway", self.settings.name, directory / "jobs.sqlite3")]
        paths += [
            (item.parent.name, item.parent.name, item)
            for item in directory.glob("devices/*/jobs.sqlite3")
        ]
        return [item for item in paths if binding is None or item[0] == binding]

    def _journal(self, binding, purpose="task"):
        if purpose not in {"task", "startup"}:
            raise ValueError("purpose must be task or startup")
        for key, _, path in self._sources(binding):
            path = path if purpose == "task" else path.with_name("startup.sqlite3")
            if key == binding and path.is_file() and not path.is_symlink():
                return Journal(path)
        raise ValueError("Evaluation binding or journal not found")

    def _detail(self, binding, job_id, attempt_id, *, purpose="task", **options):
        from . import monitoring

        detail = monitoring.job_detail(self._journal(binding, purpose), job_id, attempt_id, **options)
        name = next(name for key, name, _ in self._sources(binding) if key == binding)
        context = {
            "binding": binding, "gateway_name": name, "purpose": purpose,
            **({"pending_upload": False, "can_cancel": False} if purpose == "startup" else {}),
        }
        return {
            **detail,
            **context,
            "job": {**detail["job"], **context},
        }

    def _startup(self, binding, *, detail=True):
        from . import monitoring

        sources = self._sources(binding)
        if not sources:
            raise ValueError("Evaluation binding not found")
        if not any(path.with_name("startup.sqlite3").is_file() for _, _, path in sources):
            return None
        journal = self._journal(binding, "startup")
        rows = monitoring.list_jobs(journal, page_size=1)["items"]
        if not rows:
            return None
        item = rows[0]
        if detail:
            return self._detail(binding, item["job_id"], item["attempt_id"], purpose="startup")
        return {
            **item,
            "binding": binding,
            "gateway_name": sources[0][1],
            "purpose": "startup",
            "pending_upload": False,
            "can_cancel": False,
        }

    def _local_log_store(self, binding, job_id, attempt_id, purpose, *, source=None):
        from .contracts import RemoteJob

        journal = self._journal(binding, purpose)
        row = journal.get(job_id)
        if row is None:
            raise LookupError("Job is not present locally")
        raw = json.loads(row["job"])
        if raw["attempt_id"] != attempt_id:
            raise ValueError("Execution attempt is no longer current")
        from .local_logs import LocalLogStore, _no_links, _regular

        store = LocalLogStore(journal)
        runtime = self.service.runtime if self.service else None
        discover = getattr(runtime, "local_logs", None)
        if discover:
            try:
                sources = discover(RemoteJob.model_validate(raw), json.loads(row["saved"]))
                if sources is None:
                    sources = {}
                if not isinstance(sources, Mapping):
                    raise ValueError("Local log discovery must return a source mapping")
                prepared, names = [], set()
                for name, value in sources.items():
                    name = store._name(name)
                    if name in names:
                        raise ValueError("Local log discovery returned duplicate source names")
                    names.add(name)
                    path = Path(value["path"] if isinstance(value, Mapping) else value).absolute()
                    description = value.get("description", "") if isinstance(value, Mapping) else ""
                    if not isinstance(description, str):
                        raise ValueError("Local log descriptions must be strings")
                    _no_links(path)
                    if path.exists():
                        _regular(path)
                    prepared.append((name, path, description))
            except Exception:
                if not store.has_managed(job_id, attempt_id):
                    raise
                catalog = store._load(job_id, attempt_id)
                managed = {name for name, item in catalog["sources"].items() if item.get("managed")}
                allowed = {
                    item["name"] for item in store.list(job_id, attempt_id)
                    if item["name"] in managed and item["available"] and not item.get("expired", False)
                }
                if not allowed or source is not None and source not in allowed:
                    raise
                return store, allowed
            for name, path, description in prepared:
                store.register_external(job_id, attempt_id, name, path, description=description)
        return store, None

    async def _idle(self) -> bool:
        sources = self._sources()

        def inspect():
            for _, _, path in sources:
                for filename, startup in ((path, False), (path.with_name("startup.sqlite3"), True)):
                    if not filename.is_file():
                        continue
                    for row in Journal(filename).rows(pending_only=True):
                        if (
                            row["state"] not in {"completed", "failed", "cancelled"}
                            or (not startup and row["seq"] > row["ack"])
                        ):
                            return False
            return True

        if not await asyncio.to_thread(inspect):
            return False
        if self.service:
            startup = getattr(self.service, "startup_task", None)
            if startup and not startup.done():
                return False
            if self.service.readiness == "ready" and not self.service.runtime.ready():
                return False
        return True

    async def _monitor(self):
        while not self._stop.is_set():
            try:
                async with self._lock:
                    if self.service:
                        self.service.set_admission(self.paused or self.pending_settings is not None)
                        if self.service.readiness == "ready":
                            self.status = (
                                "draining_for_settings" if self.pending_settings else "ready"
                            )
                            self._rollback = None
                        elif self.service.error:
                            self.error, self.status = self.service.error, "unready"
                        elif self.service_task.done():
                            self.error, self.status = "Gateway service stopped", "unready"
                    if self._rollback and (self.error or (self.service and self.service.error)):
                        failed = self.settings
                        await self._stop_service()
                        self.settings, self._rollback = self._rollback, None
                        self.pending_settings, self.pending_error = failed, self.error
                        self._persist()
                        self._launch()
                    elif self.pending_settings and not self.pending_error and await self._idle():
                        await self._switch()
            except Exception as error:
                self.error = str(error)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=0.5)

    async def _switch(self):
        previous, desired = self.settings, self.pending_settings
        await self._stop_service()
        self.settings, self.pending_settings = desired, None
        self._rollback = previous
        self._persist()
        if self.settings.ready:
            self._launch()
        else:
            self.status = "configuration_required"

    async def pause(self) -> None:
        """Persist admission pause while keeping all accepted work alive."""
        async with self._lock:
            self.paused = True
            if self.service:
                self.service.set_admission(True)
            self._persist()

    async def resume(self) -> None:
        """Resume claiming unless a deferred configuration is still draining."""
        async with self._lock:
            self.paused = False
            if self.service:
                self.service.set_admission(self.pending_settings is not None)
            self._persist()

    async def apply_settings(self, values: dict) -> dict:
        """Apply hot settings now and defer ownership-changing settings until idle."""
        async with self._lock:
            base = self.pending_settings or self.settings
            desired = GatewaySettings.model_validate({**base.model_dump(), **values})
            desired.require_ready()
            changed_cold = any(
                getattr(desired, key) != getattr(self.settings, key) for key in COLD_SETTINGS
            )
            hot = {
                key: value
                for key, value in desired.model_dump().items()
                if key not in COLD_SETTINGS
            }
            maximum = (
                getattr(self.service.runtime, "max_concurrency", None) if self.service else None
            )
            if (
                maximum is not None
                and hot["concurrency"] > maximum
                and desired.adapter != self.settings.adapter
            ):
                hot["concurrency"] = self.settings.concurrency
            effective = GatewaySettings.model_validate({**self.settings.model_dump(), **hot})
            if self.service:
                await self.service.apply_live_settings(
                    **{
                        key: getattr(effective, key)
                        for key in (
                            "name",
                            "allowed_users",
                            "concurrency",
                            "cleanup_enabled",
                            "cleanup_interval",
                            "cleanup_keep_seconds",
                            "local_log_keep_seconds",
                        )
                        if hasattr(effective, key)
                    }
                )
            if (
                changed_cold
                or self.service is None
                or (self.service_task and self.service_task.done())
            ):
                self.settings = effective
                self.pending_settings, self.pending_error = desired, None
                if self.service:
                    self.service.set_admission(True)
                self.status = "draining_for_settings"
            else:
                self.settings = desired
                if self.pending_settings:
                    self.pending_settings = desired
                self.pending_error = None
            self._persist()
            if self.pending_settings and await self._idle():
                await self._switch()
            return {
                "applied": self.pending_settings is None,
                "pending": self.pending_settings is not None,
                "restart_required": any(
                    getattr(desired, key) != value for key, value in self._listener.items()
                ),
            }

    async def discard_pending(self) -> None:
        """Withdraw deferred changes without discarding any task or evidence."""
        async with self._lock:
            self.pending_settings, self.pending_error = None, None
            if self.service:
                self.service.set_admission(self.paused)
            self._persist()

    async def get_snapshot(self) -> dict:
        """Return public health and counts without exposing gateway credentials."""
        from . import monitoring

        service = self.service
        sources = self._sources()

        def inspect():
            totals = dict.fromkeys(
                (
                    "pending",
                    "running",
                    "recovery_required",
                    "success",
                    "failed",
                    "cancelled",
                    "cancelling",
                    "pending_upload",
                    "total",
                ),
                0,
            )
            bindings = []
            for key, name, path in sources:
                counts = (
                    monitoring.summary(Journal(path))
                    if path.is_file()
                    else dict.fromkeys(totals, 0)
                )
                for field in totals:
                    totals[field] += counts.get(field, 0)
                gateway = service.gateways.get(key) if service else None
                transport = getattr(gateway, "transport", {}) if gateway else {}
                bindings.append(
                    {
                        "key": key,
                        "name": name,
                        "summary": counts,
                        "transport": dict(transport),
                        "startup": self._startup(key, detail=False),
                    }
                )
            return totals, bindings

        totals, bindings = await asyncio.to_thread(inspect)
        health = self.service.health() if self.service else {}
        # State directories belong to the operator but recovery handles never belong in the UI.
        return {
            "status": self.status,
            "error": self.error,
            "settings": self.settings.model_dump(mode="json"),
            "pending_settings": (
                self.pending_settings.model_dump(mode="json") if self.pending_settings else None
            ),
            "pending_error": self.pending_error,
            "paused": self.paused,
            "service": health,
            "summary": totals,
            "bindings": bindings,
            "restart_required": any(
                getattr(self.settings, key) != value for key, value in self._listener.items()
            ),
            "max_concurrency": (
                getattr(self.service.runtime, "max_concurrency", None)
                if self.service
                else (1 if self.settings.adapter == "hgamey" else None)
            ),
        }

    async def list_jobs(self, *, page=1, page_size=20, state=None, search="", binding=None) -> dict:
        """Page task summaries over the active platform's SDK bindings."""
        from . import monitoring

        sources = self._sources(binding)
        offset = monitoring._pagination(page, page_size)

        def inspect():
            jobs, total = [], 0
            for key, name, path in sources:
                if path.is_file():
                    result = monitoring.list_jobs(
                        Journal(path),
                        page=1,
                        page_size=page * page_size,
                        state=state,
                        search=search,
                    )
                    jobs += [
                        {**row, "binding": key, "gateway_name": name} for row in result["items"]
                    ]
                    total += result["total"]
            jobs.sort(key=lambda row: (row["job_id"], row["binding"]))
            jobs.sort(
                key=lambda row: row["created_at"] if row["created_at"] is not None else row.get("started") or 0,
                reverse=True,
            )
            return {"items": jobs[offset : offset + page_size], "total": total}

        return await asyncio.to_thread(inspect)

    async def get_job(
        self, binding, job_id, attempt_id, *, messages_page=1, messages_page_size=100, purpose="task"
    ) -> dict:
        """Load one fenced task, without evaluating or re-reading its candidate sources."""
        return await asyncio.to_thread(
            lambda: self._detail(
                binding, job_id, attempt_id, purpose=purpose,
                messages_page=messages_page,
                messages_page_size=messages_page_size,
            )
        )

    async def get_messages(
        self, binding, job_id, attempt_id, *, after_sequence=None, before_sequence=None,
        limit=100, level=None, search="", purpose="task",
    ) -> dict:
        """Read an attempt-fenced event tail or cursor window without evaluating work.

        Args:
            binding: Exact known SDK binding key.
            job_id: Locally recorded job identifier.
            attempt_id: Current attempt identifier.
            after_sequence: Exclusive forward cursor, or None.
            before_sequence: Exclusive history cursor, or None.
            limit: Maximum number of events, from 1 to 1000.
            level: Optional exact diagnostic level.
            search: Literal message substring.
            purpose: Select only the known task or startup journal.

        Returns:
            Ascending items, filtered total, page flags and first/last sequence IDs.
        """
        from . import monitoring

        return await asyncio.to_thread(
            lambda: monitoring.job_messages(
                self._journal(binding, purpose), job_id, attempt_id,
                after_sequence=after_sequence, before_sequence=before_sequence,
                limit=limit, level=level, search=search,
            )
        )

    async def list_local_logs(self, binding, job_id, attempt_id, *, purpose="task") -> list[dict]:
        """List only registered local sources without exposing their filesystem paths.

        Args:
            binding: Exact known SDK binding key.
            job_id: Locally recorded job identifier.
            attempt_id: Current attempt identifier.
            purpose: Select the task or startup journal.

        Returns:
            Public source names, descriptions, availability, sizes and update times.
        """
        from .monitoring import _safe

        def inspect():
            store, allowed = self._local_log_store(binding, job_id, attempt_id, purpose)
            return [
                _safe({
                    **{key: item.get(key) for key in ("name", "description", "available", "size", "updated_at")},
                    **({"expired": item["expired"]} if "expired" in item else {}),
                })
                for item in store.list(job_id, attempt_id)
                if allowed is None or item["name"] in allowed
            ]

        return await asyncio.to_thread(inspect)

    async def read_local_log(self, binding, job_id, attempt_id, source, *, offset=None, limit=65536, purpose="task") -> dict:
        """Read a bounded byte window from an explicitly registered local source.

        Args:
            binding: Exact known SDK binding key.
            job_id: Locally recorded job identifier.
            attempt_id: Current attempt identifier.
            source: Registered source name rather than a filesystem path.
            offset: Byte offset; None requests the latest bytes.
            limit: Maximum byte count delegated to LocalLogStore.
            purpose: Select the task or startup journal.

        Returns:
            Text, offsets, size, EOF, rotation/reset and availability indicators.
        """
        def inspect():
            store, _ = self._local_log_store(binding, job_id, attempt_id, purpose, source=source)
            result = store.read(job_id, attempt_id, source, offset=offset, limit=limit)
            return {key: result[key] for key in ("text", "offset", "next_offset", "size", "eof", "reset", "available")}

        return await asyncio.to_thread(inspect)

    async def local_log_snapshot(self, binding, job_id, attempt_id, source, *, purpose="task") -> Path:
        """Create a temporary download copy of one registered current-attempt log.

        Args:
            binding: Exact known SDK binding key.
            job_id: Locally recorded job identifier.
            attempt_id: Current attempt identifier.
            source: Registered local source name.
            purpose: Select the task or startup journal.

        Returns:
            Temporary snapshot path for the authenticated download handler to remove.
        """
        def inspect():
            store, _ = self._local_log_store(binding, job_id, attempt_id, purpose, source=source)
            return store.snapshot(job_id, attempt_id, source)

        return await asyncio.to_thread(inspect)

    async def get_startup(self, binding) -> dict | None:
        """Read the latest local startup check without starting or repeating it.

        Args:
            binding: Exact known SDK binding key.

        Returns:
            Safe attempt-fenced job detail, or None when no startup record exists.
        """
        return await asyncio.to_thread(self._startup, binding)

    async def artifact_path(self, binding, job_id, attempt_id, name) -> Path:
        """Resolve only evidence declared by the selected execution attempt."""
        from . import monitoring

        return await asyncio.to_thread(
            lambda: monitoring.artifact_path(self._journal(binding), job_id, attempt_id, name)
        )

    async def request_cancel(self, binding, job_id, attempt_id):
        """Delegate cancellation to the current SDK owner, including while offline."""
        if self.service is None or binding not in self.service.gateways:
            raise ValueError("Gateway is not running")
        return await self.service.gateways[binding].request_cancel(job_id, attempt_id)

    async def close(self) -> None:
        """Stop the controller on application shutdown rather than browser disconnect."""
        self._stop.set()
        if self.monitor_task:
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        await self._stop_service()
        if self._lease:
            self._lease.__exit__(None, None, None)
            self._lease = None


async def run_headless(controller: GatewayController) -> int:
    """Resume saved web configuration and deferred transitions without importing a UI."""
    loop = asyncio.get_running_loop()
    installed = []
    try:
        await controller.start()
        controller.settings.require_ready()
        if os.name != "nt":
            for name in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(name, controller._stop.set)
                installed.append(name)
        await controller._stop.wait()
    finally:
        await controller.close()
        for name in installed:
            loop.remove_signal_handler(name)
    return 0
