"""Optional local backend hooks; ordinary evaluators need only their evaluation class."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from llm4ad.gateway.configuration import flag, number, user_ids, validate_server


def platform_directory(root: Path, server: str) -> Path:
    """Retain legacy identities while separating newly configured platforms."""
    identity = root / "identity.json"
    if identity.is_file():
        value = json.loads(identity.read_text(encoding="utf-8"))
        if value.get("server") == server.rstrip("/") + "/api/v1/llm4ad/evaluation-gateways":
            return root
    return root / "platforms" / uuid.uuid5(uuid.NAMESPACE_URL, server.rstrip("/")).hex


def cleanup_options(environment: dict) -> dict:
    """Resolve the shared collector schedule and optional debugging retention."""
    return {
        "cleanup_enabled": flag(environment, "LLM4AD_GATEWAY_CLEANUP_ENABLED", True),
        "cleanup_interval": number(
            environment, "LLM4AD_GATEWAY_CLEANUP_INTERVAL_SECONDS", 30, minimum=1
        ),
        "cleanup_keep_seconds": number(environment, "LLM4AD_GATEWAY_EVIDENCE_KEEP_SECONDS", 0),
        "local_log_keep_seconds": number(environment, "LLM4AD_GATEWAY_LOG_KEEP_SECONDS", 604800),
    }


@dataclass
class GatewayBinding:
    """Bind one advertised evaluator to its own durable state directory."""

    key: str
    name: str
    evaluator: str
    directory: Path
    capacity: int = 1
    metadata: dict = field(default_factory=dict)


class GatewayRuntime:
    """Describe optional backend resources managed by the common service.

    Ordinary evaluators use this default implementation. A device or external
    scheduler integration overrides only the hooks requiring local resources.
    """

    max_concurrency: int | None = None

    def __init__(self, environment: dict, evaluator: str):
        """Resolve common settings independently of any hardware case."""
        self.options = {
            "server": validate_server(environment["LLM4AD_SERVER_URL"]),
            "name": environment["LLM4AD_GATEWAY_NAME"],
            "allowed_users": user_ids([environment["LLM4AD_ALLOWED_USER_IDS"]]),
            "root": Path(environment.get("LLM4AD_GATEWAY_STATE_DIR", ".llm4ad-gateway"))
            .expanduser()
            .resolve(),
            "port": int(environment.get("LLM4AD_GATEWAY_HEALTH_PORT", "0")),
            **cleanup_options(environment),
        }
        self.evaluator = evaluator
        self.capacity = int(environment.get("LLM4AD_GATEWAY_CONCURRENCY", "1"))

    def lease_directories(self) -> list[Path]:
        """Hold ownership before any runtime resource or configuration is changed."""
        return [self.options["root"]]

    async def start(self) -> None:
        """Prepare optional backend resources before constructing evaluators."""

    def bindings(self) -> list[GatewayBinding]:
        """Expose a single evaluator unless the backend owns multiple resources."""
        directory = platform_directory(self.options["root"], self.options["server"])
        return [
            GatewayBinding(
                "gateway", self.options["name"], self.evaluator, directory, self.capacity
            )
        ]

    def api_handler(self):
        """Optionally extend the common local health server with a backend API."""
        return None

    async def check(self, gateway) -> None:
        """Perform optional backend readiness without registering platform work."""

    def details(self) -> dict:
        """Expose backend-specific observations within the common health report."""
        return {}

    def local_logs(self, job, saved_state: dict) -> dict[str, Path]:
        """Declare existing backend logs for this job without probing or starting resources."""
        return {}

    def alive(self) -> bool:
        """Report whether optional backend resources are still running."""
        return True

    def ready(self) -> bool:
        """Report backend readiness separately from platform connectivity."""
        return True

    def evidence_policy(self, gateway):
        """Optionally guard external evidence cleanup after SDK upload acknowledgement."""
        return None

    def stop_accepting(self) -> None:
        """Stop accepting new backend work before cancelling SDK workers."""

    async def close(self) -> None:
        """Drain optional backend work after SDK workers have stopped polling it."""
