"""Attempt-owned checkpoints for repeatable local preparation steps."""

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .retention import owned_path
from .storage import persist


class TaskCheckpoints:
    """Freeze declared inputs and automatically retain completed local step outputs.

    This store is for local compilation and preprocessing. External execution
    handles must still be persisted with Reporter.save_state before submission.
    The gateway owns the execution lease; callers must not run steps concurrently.
    """

    def __init__(self, job, directory: Path, *, inputs: dict[str, Path], context: dict | None = None):
        """Open this accepted attempt's immutable inputs and completed steps.

        Args:
            job: Authoritative accepted job and attempt identity.
            directory: Private preparation workspace retained with the attempt.
            inputs: Relative frozen names mapped to original local input files.
            context: Extra comparison identity, such as an evaluator/data version.
        """
        self.root = directory.absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.resolve() != self.root:
            raise ValueError("Checkpoint directory must not contain symlinks")
        self.identity = {
            "job_id": job.id, "attempt_id": job.attempt_id,
            "task_id": job.task_id, "context": context or {},
        }
        self.receipt = self.path(".sdk-checkpoints.json")
        if any(self.path(name).parts[len(self.root.parts)].startswith(".sdk-") for name in inputs):
            raise ValueError("Checkpoint inputs overlap SDK bookkeeping")
        if self.receipt.exists():
            self.state = json.loads(self.receipt.read_text())
            if self.state.get("identity") != self.identity:
                raise RuntimeError("Checkpoint belongs to another attempt or evaluator context")
            if set(self.state["inputs"]) != set(inputs):
                raise RuntimeError("Checkpoint input declarations changed")
            self._validate(self.state["inputs"])
            for step in self.state["steps"].values():
                self._validate(step["files"])
        else:
            frozen = {}
            for name, original in inputs.items():
                path, content = self.path(name), original.read_bytes()
                if path.exists() and path.read_bytes() != content:
                    raise RuntimeError("Uncheckpointed preparation differs from the accepted input")
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                try:
                    with temporary.open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                frozen[name] = self._digest(path)
            self.state = {"identity": self.identity, "inputs": frozen, "steps": {}}
            persist(self.receipt, self.state)

    def path(self, name: str) -> Path:
        """Resolve a regular relative path inside this checkpoint workspace."""
        value = PurePosixPath(name)
        if not name or value.is_absolute() or ".." in value.parts or "\\" in name or (len(name) > 1 and name[1] == ":"):
            raise ValueError("Checkpoint paths must be relative to their workspace")
        return owned_path(self.root / name, self.root)

    @staticmethod
    def _digest(path: Path) -> str:
        """Hash a regular file without following symlinks."""
        if path.is_symlink() or not path.is_file():
            raise ValueError("Checkpoint outputs must be regular files")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _validate(self, files: dict) -> None:
        """Refuse changed inputs or previously completed outputs."""
        for name, expected in files.items():
            if self._digest(self.path(name)) != expected:
                raise RuntimeError("Checkpoint input or completed output changed")

    def run(self, name: str, action: Callable, *, outputs: list[str], required: list[str]) -> None:
        """Run a local step once, or reuse its validated completed outputs.

        Args:
            name: Stable name for this repeatable local preparation step.
            action: Synchronous compilation or preprocessing operation.
            outputs: Files/directories owned exclusively by this step, including logs.
            required: Regular output files whose presence and hashes prove completion.
        """
        roots = [self.path(value) for value in outputs]
        if not roots or not required:
            raise ValueError("A checkpoint step must declare its outputs and required files")
        protected = [self.path(value) for value in self.state["inputs"]] + [self.receipt]
        for root in roots:
            if root.name.startswith(".sdk-") or any(value.is_relative_to(root) for value in protected):
                raise ValueError("Step output overlaps checkpoint inputs or bookkeeping")
        for value in required:
            if not any(self.path(value).is_relative_to(root) for root in roots):
                raise ValueError("Required file is outside this step's declared outputs")
        prior = self.state["steps"].get(name)
        if prior is not None:
            if prior["outputs"] != outputs or set(prior["files"]) != set(required):
                raise RuntimeError("Checkpoint step declarations changed")
            self._validate(prior["files"])
            return
        retained = None
        for root in roots:
            if root.exists():
                if retained is None:
                    retained = self.root / ".sdk-interrupted" / uuid.uuid4().hex
                destination = retained / root.relative_to(self.root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                root.rename(destination)
        action()
        self._validate(self.state["inputs"])
        files = {value: self._digest(self.path(value)) for value in required}
        self.state["steps"][name] = {"outputs": outputs, "files": files}
        persist(self.receipt, self.state)
