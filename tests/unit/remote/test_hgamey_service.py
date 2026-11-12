"""Container lifecycle fixtures: no ADB, phone connection or real GPU measurements."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import web

from llm4ad.gateway.examples import hgamey
from llm4ad.gateway.examples.hgamey import execution as executor
from llm4ad.gateway.examples.hgamey.benchmark import Benchmark
from llm4ad.gateway.examples.hgamey.configuration import configure
from llm4ad.gateway.examples.hgamey.runtime import HGameyRuntime
from llm4ad.gateway.sdk.service import GatewayService as Service


class GatewayFixture:
    """Represent SDK registration without making any external request."""

    registrations = 0

    def __init__(self, server, name, users, evaluator, directory, concurrency):
        """Use the actual SDK loader and local adapter in the fixture."""
        from llm4ad.remote.contracts import load_evaluator

        self.adapter = load_evaluator(evaluator)
        self.root = directory
        self.root.mkdir(parents=True, exist_ok=True)

    async def run(self):
        """Remain connected until the service deliberately cancels transport."""
        type(self).registrations += 1
        await asyncio.Event().wait()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real executor queues and SDK lifecycle with dummy device backends."""

    def setUp(self):
        """Prepare isolated runtime state and a fake, non-routable phone identity."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.references = self.root / "reference"
        self.references.mkdir()
        (self.references / "frame.png").write_bytes(b"readiness-fixture-not-a-phone-frame")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.environment = {
            "LLM4AD_SERVER_URL": "http://fixture-platform.invalid",
            "LLM4AD_GATEWAY_NAME": "fixture-gateway",
            "LLM4AD_ALLOWED_USER_IDS": str(uuid.uuid4()),
            "HGAMEY_DEVICE_HOST": "192.0.2.10",
            "HGAMEY_DEVICE_PORT": "5555",
            "HGAMEY_DEVICE_SERIAL": "fixture-physical-serial",
            "HGAMEY_DEVICE_PROFILE": "fixture-gpu-v1",
            "HGAMEY_RUNTIME_DIR": str(self.root / "gateway"),
            "EXECUTOR_DATA": str(self.root / "executor"),
            "HGAMEY_REFERENCE_PATH": str(self.references),
            "EXECUTOR_PORT": str(port),
            "HGAMEY_ADAPTER_DIR": str(Path(os.environ.get("HGAMEY_TEST_ADAPTER_DIR", str(Path(hgamey.__file__).parent)))),
            "HGAMEY_STARTUP_CHECK": "true",
            "HGAMEY_STARTUP_CHECK_TIMEOUT": "10",
            "HGAMEY_POLL_INTERVAL": "0.001",
        }
        self.patches = [
            patch.object(Benchmark, "tool_version", return_value="fixture-compiler"),
            patch.object(
                Benchmark, "compile", lambda self, source, output: output.write_bytes(b"\x03\x02\x23\x07" + source.read_bytes())
            ),
        ]
        for fixture in self.patches:
            fixture.start()
        self.calls = []
        self.status = "PASS"
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()
        self.services, self.tasks = [], []
        GatewayFixture.registrations = 0

    async def asyncTearDown(self):
        """Drain and remove every local fixture service and temporary state store."""
        self.release.set()
        for service in self.services:
            service.stop.set()
        for task in self.tasks:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        for fixture in reversed(self.patches):
            fixture.stop()
        self.temporary.cleanup()

    def backend(self, folder):
        """Return explicitly synthetic measurement receipts under the real queue."""
        body = executor.read_json(folder / "input.json")["submission"]
        self.calls.append(body)
        self.entered.set()
        self.release.wait(5)
        request = body.get("request", {})
        if getattr(self, "bulk_fixture", False):
            (folder / "evidence").mkdir(exist_ok=True)
            (folder / "evidence/synthetic-recording.mp4").write_bytes(b"synthetic recording, not device evidence")
        executor.write_json(
            folder / "result.json",
            {
                "version": 2,
                "kind": "shader",
                "candidate_id": request.get("candidate_id"),
                "source": "phonefarm",
                "status": self.status,
                "metrics": {"operator_latency_ms": 2.5, "psnr_db": 40, "power_watt": None},
                "baseline_metrics": {"operator_latency_ms": 2.5, "psnr_db": 40},
                "verdict": {"is_pareto_improvement": False, "p_value": 1},
                "conditions_observed": {"restored": True},
                "fixture_only": True,
            },
        )
        executor.write_json(
            folder / "foreground.json",
            {
                "restore_required": False,
                "restored": None,
                "paused": False,
                "headless": True,
            },
        )
        return 0

    def launch(self, gateway_factory=GatewayFixture):
        """Run the real lifecycle around a deterministic executor backend."""
        module = SimpleNamespace(
            load_devices=executor.load_devices,
            make_handler=executor.make_handler,
            Engine=lambda data, config: executor.Engine(data, config, self.backend),
        )
        service = Service(HGameyRuntime(self.environment, "llm4ad.gateway.examples.hgamey.evaluator:HGameyShaderEvaluator", module), gateway_factory)
        task = asyncio.create_task(service.run())
        self.services.append(service)
        self.tasks.append(task)
        return service, task

    async def until(self, predicate):
        """Wait for a concrete fixture transition with a finite diagnostic bound."""
        for _ in range(2000):
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("fixture transition did not complete")

    async def test_idle_startup_does_not_evaluate_without_explicit_readiness_check(self):
        """Disabling the one-shot check leaves the executor idle and unlocked."""
        self.environment["HGAMEY_STARTUP_CHECK"] = "false"
        service, _ = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 1)
        self.assertEqual(self.calls, [])
        self.assertIsNone(service.runtime.engine.health()["active_job"])
        self.assertTrue(service.health()["ready"])

    async def test_startup_check_uses_queue_once_before_registration(self):
        """The baseline check has isolated provenance and never enters SDK outbox."""
        service, _ = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["request"]["workload"]["seconds"], 1)
        self.assertEqual(self.calls[0]["request"]["protocol"]["rounds"], 2)
        self.assertEqual(service.gateway.adapter.benchmark.request["protocol"]["rounds"], 8)
        from llm4ad.remote.journal import Journal

        checks = Journal(service.gateway.root / "startup.sqlite3").rows()
        self.assertEqual(len(checks), 1)
        self.assertEqual(json.loads(checks[0]["report"])["metadata"]["purpose"], "startup_readiness_only")
        from llm4ad.gateway.sdk.journal import Reporter

        phases = Reporter(Journal(service.gateway.root / "startup.sqlite3"), checks[0]["id"]).enrich({})["metadata"]
        self.assertEqual(
            [(phase["operation"], phase["stage"], phase["status"]) for phase in phases["lifecycle_stages"]],
            [("evaluate", stage, "completed") for stage in ("preparing", "evaluating", "finalizing")],
        )
        self.assertEqual(Journal(service.gateway.root / "jobs.sqlite3").rows(), [])
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.calls), 1)

    async def test_completed_readiness_bulk_evidence_is_collected(self):
        """Retain the readiness receipt while removing its completed bulk files."""
        self.bulk_fixture = True
        self.environment["HGAMEY_CLEANUP_INTERVAL_SECONDS"] = "1"
        service, _ = self.launch()
        await self.until(lambda: service.retention_status["cleaned"] == 1)
        jobs = list(service.runtime.engine.jobs.glob("*/job.json"))
        self.assertEqual(len(jobs), 1)
        folder = jobs[0].parent
        self.assertFalse((folder / "evidence").exists())
        self.assertFalse((folder / "inputs").exists())
        self.assertTrue((folder / "result.json").exists())
        self.assertEqual(service.runtime.engine.get(folder.name)["evidence_cleanup"]["state"], "cleaned")
        frozen = executor.read_json(folder / "input.json")
        token = service.runtime.engine.get(folder.name)["idempotency_key"]
        repeated = service.runtime.engine.submit({**frozen["submitted_body"], "idempotency_key": token})
        self.assertEqual(repeated["id"], folder.name)
        self.assertIsNone(service.runtime.engine.health()["active_job"])
        self.assertTrue(service.health()["ready"])
        self.assertEqual(len(self.calls), 1)

    async def test_shutdown_waits_for_inflight_evidence_cleanup(self):
        """Keep ownership leases until an already-started filesystem cleanup exits."""
        self.environment["HGAMEY_STARTUP_CHECK"] = "false"
        entered, finish = threading.Event(), threading.Event()

        def cleanup(*args, **kwargs):
            entered.set()
            finish.wait(5)
            return {"cleaned": 0, "removed_bytes": 0, "errors": 0}

        with patch("llm4ad.gateway.sdk.service.collect_finished", side_effect=cleanup):
            service, task = self.launch()
            await self.until(entered.is_set)
            service.stop.set()
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            finish.set()
            await asyncio.wait_for(asyncio.shield(task), timeout=5)

    def test_evidence_retention_configuration(self):
        """Configure delayed debugging retention without weakening cleanup guards."""
        adapter = Path(self.environment["HGAMEY_ADAPTER_DIR"])
        defaults = configure(self.environment, executor, adapter)
        self.assertTrue(defaults["cleanup_enabled"])
        self.assertEqual(defaults["cleanup_keep_seconds"], 0)
        delayed = configure({**self.environment, "HGAMEY_LOCAL_EVIDENCE_KEEP_HOURS": "2",
                             "HGAMEY_CLEANUP_ENABLED": "false"}, executor, adapter)
        self.assertFalse(delayed["cleanup_enabled"])
        self.assertEqual(delayed["cleanup_keep_seconds"], 7200)
        for key, value in (("HGAMEY_CLEANUP_INTERVAL_SECONDS", "0"), ("HGAMEY_LOCAL_EVIDENCE_KEEP_HOURS", "-1")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                configure({**self.environment, key: value}, executor, adapter)

    async def test_failed_startup_never_registers_or_repeats_device_work(self):
        """A quality failure retains one diagnostic check and no usable gateway."""
        self.status = "POOR_QUALITY"
        service, _ = self.launch()
        await self.until(lambda: service.readiness == "unready")
        self.assertEqual(GatewayFixture.registrations, 0)
        self.assertFalse(service.health()["ready"])
        self.assertIn("POOR_QUALITY", service.error)
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.calls), 1)

    async def test_stop_preserves_queue_and_waits_for_active_cleanup(self):
        """Stopping transport cannot kill the backend or discard pending jobs."""
        self.environment["HGAMEY_STARTUP_CHECK"] = "false"
        service, task = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 1)
        self.release.clear()
        active = service.runtime.engine.submit({"operation": "probe"})
        await self.until(self.entered.is_set)
        pending = service.runtime.engine.submit({"operation": "probe"})
        service.stop.set()
        await self.until(service.runtime.engine.stopping.is_set)
        self.assertFalse(task.done())
        self.assertEqual(service.runtime.engine.get(pending["id"])["state"], "queued")
        self.release.set()
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
        self.assertEqual(service.runtime.engine.get(active["id"])["cleanup_status"], "completed")
        self.assertEqual(service.runtime.engine.get(pending["id"])["state"], "queued")
        self.assertEqual(len(self.calls), 1)

    async def test_restart_reconciles_incomplete_check_without_second_measurement(self):
        """An interrupted polling task reuses the completed executor experiment."""
        self.release.clear()
        first, first_task = self.launch()
        await self.until(self.entered.is_set)
        first.stop.set()
        await self.until(first.runtime.engine.stopping.is_set)
        self.release.set()
        await asyncio.wait_for(asyncio.shield(first_task), timeout=5)
        second, _ = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 1)
        self.assertTrue(second.health()["ready"])
        self.assertEqual(len(self.calls), 1)

    async def test_service_lease_rejects_second_instance_before_starting_an_executor(self):
        """Two containers cannot share SDK/queue state through a second service."""
        self.environment["HGAMEY_STARTUP_CHECK"] = "false"
        self.launch()
        await self.until(lambda: GatewayFixture.registrations == 1)
        configured = self.services[0].options["devices_file"].read_bytes()
        first_settings = self.services[0].options["settings_path"].read_bytes()
        self.environment["HGAMEY_DEVICE_HOST"] = "192.0.2.11"
        second, second_task = self.launch()
        import sqlite3

        with self.assertRaises(sqlite3.OperationalError):
            await second_task
        self.tasks.remove(second_task)
        self.assertIsNone(second.runtime.engine)
        self.assertEqual(self.services[0].options["devices_file"].read_bytes(), configured)
        self.assertEqual(self.services[0].options["settings_path"].read_bytes(), first_settings)

    def test_missing_platform_configuration_fails_before_a_phone_backend_exists(self):
        """Required connection and authorization values are validated locally."""
        for key in ("LLM4AD_SERVER_URL", "LLM4AD_GATEWAY_NAME", "LLM4AD_ALLOWED_USER_IDS"):
            values = {**self.environment, key: ""}
            with self.assertRaises(ValueError):
                configure(values, executor, Path(self.environment["HGAMEY_ADAPTER_DIR"]))
        self.assertEqual(self.calls, [])

    def multiple_devices(self):
        """Configure two distinct wireless devices without a global profile."""
        self.environment.pop("HGAMEY_DEVICE_HOST")
        self.environment.pop("HGAMEY_DEVICE_PROFILE")
        devices = [
            {"id": "phone-a", "host": "192.0.2.10", "port": 37123, "expected_serial": "physical-a", "device_profile": "gpu-a"},
            {"id": "phone-b", "host": "2001:db8::2", "port": 41235, "expected_serial": "physical-b", "device_profile": "gpu-b"},
        ]
        self.environment["HGAMEY_DEVICES_JSON"] = json.dumps(devices)
        return devices

    def test_game_check_file_is_optional_and_propagated_to_each_device(self):
        """Read real configuration JSON without compiling shaders or contacting a GPU."""
        self.multiple_devices()
        adapter = Path(self.environment["HGAMEY_ADAPTER_DIR"])
        original = configure(self.environment, executor, adapter)
        self.assertTrue(all("game_check" not in profile["settings"] for profile in original["profiles"]))
        game_check = {
            "ndk_path": "/fixture/ndk",
            "phonefarm_root": "/fixture/phonefarm",
            "builder_path": "/fixture/build_present_candidate.py",
            "options": {"a": "sr:1:2800x1272:0", "b": "sr:1:2800x1272:0", "window": 8},
        }
        filename = self.root / "game-check.json"
        filename.write_text(json.dumps(game_check), encoding="utf-8")
        self.environment["HGAMEY_GAME_CHECK_CONFIG"] = str(filename)
        configured = configure(self.environment, executor, adapter)
        self.assertEqual(configured["settings"]["game_check"], game_check)
        self.assertEqual(len(configured["profiles"]), 2)
        for before, after in zip(original["profiles"], configured["profiles"], strict=True):
            self.assertEqual(after["settings"]["game_check"], game_check)
            self.assertNotEqual(before["settings_path"], after["settings_path"])
        self.assertEqual(self.calls, [])

    async def test_all_wireless_devices_checked_and_registered_with_separate_contracts(self):
        """Use both IPv4/IPv6 endpoints and retain independent startup journals."""
        self.multiple_devices()
        service, _ = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 2)
        self.assertEqual([call["device_id"] for call in self.calls], ["phone-a", "phone-b"])
        self.assertEqual([call["request"]["serial"] for call in self.calls], ["192.0.2.10:37123", "[2001:db8::2]:41235"])
        gateways = list(service.gateways.values())
        self.assertNotEqual(gateways[0].root, gateways[1].root)
        self.assertNotEqual(gateways[0].adapter.contract, gateways[1].adapter.contract)
        self.assertTrue(service.health()["ready"])
        self.assertEqual(set(service.health()["gateways"]), {"phone-a", "phone-b"})

    async def test_device_selection_and_disabled_devices(self):
        """An optional ID selects one device; disabled entries are never advertised."""
        devices = self.multiple_devices()
        devices[1]["enabled"] = False
        self.environment["HGAMEY_DEVICES_JSON"] = json.dumps(devices)
        service, _ = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 1)
        self.assertEqual(list(service.gateways), ["phone-a"])
        self.assertEqual([call["device_id"] for call in self.calls], ["phone-a"])
        self.environment["HGAMEY_DEVICE_ID"] = "phone-b"
        with self.assertRaisesRegex(ValueError, "enabled"):
            configure(self.environment, executor, Path(self.environment["HGAMEY_ADAPTER_DIR"]))

    def test_improvement_threshold_configuration(self):
        """Freeze the default or stricter gain threshold before any phone work."""
        adapter = Path(self.environment["HGAMEY_ADAPTER_DIR"])
        default = configure(self.environment, executor, adapter)
        self.assertEqual(default["settings"]["min_operator_improvement_pct"], 1)
        strict = configure({**self.environment, "HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT": "2"}, executor, adapter)
        self.assertEqual(strict["settings"]["min_operator_improvement_pct"], 2)
        self.assertNotEqual(strict["settings_path"], default["settings_path"])
        for value in ("0.5", "100", "nan", "inf", "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configure({**self.environment, "HGAMEY_MIN_OPERATOR_IMPROVEMENT_PCT": value}, executor, adapter)

    def test_bad_wireless_configuration_rejected_without_device_work(self):
        """Reject ambiguity, duplicate identities, malformed endpoints and ports."""
        devices = self.multiple_devices()
        variants = [
            [],
            {},
            ["invalid"],
            [devices[0], devices[0]],
            [{**devices[0], "port": 70000}],
            [{**devices[0], "host": "not-an-ip"}],
            [{**devices[0], "device_profile": ""}],
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                configure(
                    {**self.environment, "HGAMEY_DEVICES_JSON": json.dumps(variant)},
                    executor,
                    Path(self.environment["HGAMEY_ADAPTER_DIR"]),
                )
        with self.assertRaisesRegex(ValueError, "not both"):
            configure(
                {**self.environment, "HGAMEY_DEVICE_HOST": "192.0.2.1"}, executor, Path(self.environment["HGAMEY_ADAPTER_DIR"])
            )
        self.assertEqual(self.calls, [])

    def test_existing_device_file_supports_multiple_devices_and_selection(self):
        """Keep the original executor JSON format usable without JSON env input."""
        devices = self.multiple_devices()
        self.environment.pop("HGAMEY_DEVICES_JSON")
        self.environment["HGAMEY_DEVICE_PROFILE"] = "shared-gpu-profile"
        filename = self.root / "devices.json"
        filename.write_text(
            json.dumps(
                {
                    "version": 1,
                    "devices": [
                        {
                            "id": item["id"],
                            "label": item["id"],
                            "host": item["host"],
                            "port": item["port"],
                            "expected_serial": item["expected_serial"],
                            "enabled": True,
                        }
                        for item in devices
                    ],
                }
            )
        )
        original = filename.read_bytes()
        self.environment["EXECUTOR_CONFIG"] = str(filename)
        options = configure(self.environment, executor, Path(self.environment["HGAMEY_ADAPTER_DIR"]))
        self.assertEqual(len(options["profiles"]), 2)
        self.environment["HGAMEY_DEVICE_ID"] = "phone-b"
        selected = configure(self.environment, executor, Path(self.environment["HGAMEY_ADAPTER_DIR"]))
        self.assertEqual([profile["device"]["id"] for profile in selected["profiles"]], ["phone-b"])
        self.assertEqual(filename.read_bytes(), original)

    async def test_expanding_legacy_single_device_preserves_its_journal(self):
        """Keep legacy recovery in place when a second wireless device is added."""
        from llm4ad.remote.contracts import RemoteJob
        from llm4ad.remote.journal import Journal, Reporter

        self.multiple_devices()
        self.environment["HGAMEY_STARTUP_CHECK"] = "false"
        platform = (
            Path(self.environment["HGAMEY_RUNTIME_DIR"])
            / "platforms"
            / uuid.uuid5(uuid.NAMESPACE_URL, self.environment["LLM4AD_SERVER_URL"]).hex
        )
        platform.mkdir(parents=True)
        journal = Journal(platform / "startup.sqlite3")
        job = RemoteJob(
            id=str(uuid.uuid4()), attempt_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()), project_root=str(self.root), timeout=10
        )
        journal.accept(job.model_dump(mode="json"))
        Reporter(journal, job.id, job.attempt_id).save_state({"request": {"device_id": "phone-a"}})
        journal.update(job.id, "completed", {"fixture": True})
        service, _ = self.launch()
        await self.until(lambda: GatewayFixture.registrations == 2)
        self.assertEqual(service.gateways["phone-a"].root, platform)
        self.assertEqual(service.gateways["phone-b"].root, platform / "devices" / "phone-b")
        self.assertEqual(journal.get(job.id)["state"], "completed")
        self.assertEqual(json.loads((platform / "legacy-device.json").read_text()), {"device_id": "phone-a"})

    async def test_one_failed_device_prevents_claims_without_repeating_checks(self):
        """Never register usable gateways while a selected device failed readiness."""
        self.multiple_devices()
        original = self.backend

        def backend(folder):
            body = executor.read_json(folder / "input.json")["submission"]
            self.status = "POOR_QUALITY" if body["device_id"] == "phone-b" else "PASS"
            return original(folder)

        self.backend = backend
        service, _ = self.launch()
        await self.until(lambda: service.readiness == "unready")
        self.assertEqual(GatewayFixture.registrations, 0)
        self.assertIn("phone-b", service.error)
        self.assertEqual(service.binding_status, {"phone-a": "ready", "phone-b": "unready"})
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.calls), 2)

    @unittest.skipUnless(shutil.which("glslangValidator") and shutil.which("spirv-val"), "Native shader compiler tools required")
    async def test_spawned_sdk_workers_keep_device_bindings_without_environment_switches(self):
        """Run two real SDK child processes into the shared serialized executor."""
        from llm4ad.remote.contracts import RemoteJob
        from llm4ad.remote.gateway import Gateway

        class WorkerGateway(Gateway):
            async def run(self):
                await asyncio.Event().wait()

        self.multiple_devices()
        self.environment["HGAMEY_STARTUP_CHECK"] = "false"
        for fixture in self.patches:
            fixture.stop()
        service, _ = self.launch(WorkerGateway)
        await self.until(lambda: service.readiness == "ready")
        jobs = []
        try:
            for gateway in service.gateways.values():
                work = gateway.root / "candidate"
                work.mkdir()
                (work / "shader.comp").write_bytes(gateway.adapter.benchmark.baseline.read_bytes())
                job = RemoteJob(
                    id=str(uuid.uuid4()),
                    attempt_id=str(uuid.uuid4()),
                    task_id=str(uuid.uuid4()),
                    project_root=str(work),
                    timeout=30,
                )
                payload = job.model_dump(mode="json")
                gateway.journal.accept(payload)
                # Actual child processes must load the profile from the evaluator spec.
                with patch.dict(os.environ, {"HGAMEY_GATEWAY_CONFIG": "/does/not/exist.json"}):
                    gateway.start_process(payload)
                jobs.append((gateway, job))
            await self.until(
                lambda: all(gateway.journal.get(job.id)["state"] in {"completed", "failed"} for gateway, job in jobs)
            )
            for gateway, job in jobs:
                row = gateway.journal.get(job.id)
                self.assertEqual(row["state"], "completed", row["report"])
                self.assertTrue(json.loads(row["report"])["success"])
            self.assertEqual(sorted(call["device_id"] for call in self.calls), ["phone-a", "phone-b"])
            self.assertEqual(service.runtime.engine.health()["max_active_jobs"], 1)
        finally:
            for gateway, _ in jobs:
                for process in gateway.processes.values():
                    await asyncio.to_thread(process.join, 5)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)

    async def test_actual_sdk_registers_only_after_readiness_and_keeps_platform_outages_local(self):
        """Exercise the real SDK against loopback registration/heartbeat endpoints."""
        registrations = []

        async def register(request):
            registrations.append(await request.json())
            return web.json_response({"ok": True})

        async def heartbeat(request):
            return web.json_response({"jobs": []})

        async def claim(request):
            return web.json_response(None)

        app = web.Application()
        prefix = "/api/v1/llm4ad/evaluation-gateways"
        app.add_routes(
            [
                web.post(prefix + "/register", register),
                web.post(prefix + "/{id}/heartbeat", heartbeat),
                web.post(prefix + "/{id}/claim", claim),
            ]
        )
        runner = web.AppRunner(app)
        await runner.setup()
        listener = web.TCPSite(runner, "127.0.0.1", 0)
        await listener.start()
        port = listener._server.sockets[0].getsockname()[1]
        self.environment["LLM4AD_SERVER_URL"] = f"http://127.0.0.1:{port}"
        try:
            from llm4ad.gateway.sdk.gateway import Gateway as ObservedGateway

            service, _ = self.launch(ObservedGateway)
            await self.until(lambda: bool(registrations))
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(registrations[0]["name"], "fixture-gateway")
            self.assertEqual(registrations[0]["allowed_user_ids"], [self.environment["LLM4AD_ALLOWED_USER_IDS"]])
            await runner.cleanup()
            service.gateway.wakeup.set()
            await self.until(lambda: bool(getattr(service.gateway, "last_error", None)))
            self.assertTrue(service.health()["ready"])
            self.assertTrue(service.runtime.engine.thread.is_alive())
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
