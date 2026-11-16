"""Run one explicit offline check through the real isolated gateway worker."""

import asyncio
import json
import uuid
from pathlib import Path

from llm4ad.gateway.sdk.bundles import extract_candidate, pack_candidate
from llm4ad.gateway.sdk.contracts import (
    EvaluationContract,
    GatewayEvaluator,
    RemoteJob,
    load_evaluator,
    load_evaluator_class,
    normalize_spec,
)
from llm4ad.gateway.sdk.gateway import Gateway
from llm4ad.gateway.sdk.journal import Reporter, gateway_lease
from llm4ad.gateway.sdk.lifecycle import invoke


class _OfflineAdapter(GatewayEvaluator):
    """Load user constructors in the check worker and only reattach external cleanup."""

    def __init__(self, spec, contract):
        """Keep source and contract inspection free of evaluator construction."""
        self.spec, self.contract, self.adapter = spec, contract, None
        self.cleanup_timeout = getattr(load_evaluator_class(spec), "cleanup_timeout", 30.0)

    async def before_task(self, job, reporter, operation):
        """Load cleanup hooks only when evaluation published external state."""
        if reporter.saved_state:
            self.adapter = load_evaluator(self.spec)
            await invoke(self.adapter.before_task(job, reporter, operation))

    async def cancel(self, job, saved_state):
        """Confirm SDK-local cleanup or delegate a declared external handle."""
        if not saved_state:
            return True
        self.adapter = self.adapter or load_evaluator(self.spec)
        return await invoke(self.adapter.cancel(job, saved_state))

    async def after_task(self, job, reporter, operation, *, result=None, error=None):
        """Finalize optional externally attached cleanup hooks."""
        if self.adapter:
            await invoke(self.adapter.after_task(job, reporter, operation, result=result, error=error))


def check_evaluator(spec: str, candidate: Path, state_dir: Path, timeout: float) -> dict:
    """Evaluate an isolated copy and retain local progress, diagnostics and files.

    Args:
        spec: Evaluator file followed by its class name.
        candidate: Candidate source directory; it is never used as the workspace.
        state_dir: Owned check state, separate from a running gateway identity.
        timeout: Actual execution deadline in seconds.

    Returns:
        JSON-compatible check outcome and retained evidence directory.
    """
    spec = normalize_spec(spec)
    contract = EvaluationContract.model_validate(load_evaluator_class(spec).contract)
    bundle = pack_candidate(str(candidate.expanduser().resolve()))
    root = state_dir.expanduser().resolve()
    with gateway_lease(root):
        identity = str(uuid.uuid4())
        work = root / "work" / identity
        extract_candidate(bundle, work)
        job = RemoteJob(
            id=identity, attempt_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()), project_root=str(work), timeout=timeout
        )
        adapter = _OfflineAdapter(spec, contract)
        gateway = Gateway(
            "http://offline.invalid", "Offline check", [str(uuid.uuid4())], spec, root, adapter=adapter, persist_identity=False
        )
        journal = gateway.journal
        payload = job.model_dump(mode="json")
        journal.accept(payload)

        async def run_check():
            try:
                while True:
                    gateway.enforce_deadlines()
                    await gateway.probe_cleanup()
                    await gateway.reconcile(None, payload)
                    if journal.get(identity)["state"] in {"completed", "failed", "cancelled"}:
                        return
                    await asyncio.sleep(0.05)
            finally:
                await asyncio.to_thread(gateway.stop_workers)

        asyncio.run(run_check())
        row = journal.get(identity)
        result = Reporter(journal, identity, job.attempt_id).enrich(json.loads(row["report"]))
        descriptions = result.get("metadata", {}).get("artifact_descriptions", {})
        return {
            "ok": row["state"] == "completed" and result.get("success", True),
            "state": row["state"],
            "job_id": identity,
            "state_dir": str(root),
            "contract": contract.model_dump(mode="json"),
            "result": result,
            "artifacts": [
                {key: item[key] for key in ("name", "path", "size", "sha256", "media_type")}
                | {"description": descriptions.get(item["name"], "")}
                for item in journal.artifacts(identity, job.attempt_id)
            ],
        }
