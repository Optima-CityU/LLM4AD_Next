"""Run one explicit offline check through the real isolated gateway worker."""

import json
import multiprocessing
import uuid
from pathlib import Path

from llm4ad.gateway.sdk.bundles import extract_candidate, pack_candidate
from llm4ad.gateway.sdk.contracts import EvaluationContract, RemoteJob, load_evaluator_class, normalize_spec
from llm4ad.gateway.sdk.gateway import execute, stop_process
from llm4ad.gateway.sdk.journal import Journal, Reporter, gateway_lease


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
        journal = Journal(root / "jobs.sqlite3")
        journal.accept(job.model_dump(mode="json"))
        process = multiprocessing.get_context("spawn").Process(
            target=execute, args=(journal.path, spec, job.model_dump(mode="json"))
        )
        process.start()
        try:
            process.join(timeout)
            if process.is_alive():
                stop_process(process)
                journal.update(
                    identity,
                    "recovery_required",
                    {"message": "Local check timed out; reconcile any external work before retrying"},
                )
            else:
                row = journal.get(identity)
                if row["state"] not in {"completed", "failed", "recovery_required"}:
                    journal.update(identity, "recovery_required", {"message": "Worker exited without a final result"})
        finally:
            stop_process(process)
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
