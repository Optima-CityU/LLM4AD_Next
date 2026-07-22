"""MindMemOS client for CLI - Direct API communication with JWT auth."""

from __future__ import annotations

import hmac
import json
import time
from collections.abc import Iterable, Iterator
from threading import Event, Thread
from typing import Any

import httpx

# LLM4AD memory card schema (matches backend memory_service.py).
LLM4AD_MEMORY_ENTITY_TYPE = "llm4ad_memory_card"
LLM4AD_MEMORY_TYPES = [
    "good_algorithm",
    "error_reflection",
    "domain_knowledge",
    "general_insight",
]


class MindMemOSClient:
    """Client for MindMemOS API with JWT authentication."""

    # Fixed system identity for CLI
    CLI_USER_ID = "llm4ad-cli-system"
    CLI_PROJECT_ID = "llm4ad-cli"

    def __init__(
        self,
        *,
        base_url: str,
        jwt_secret: str,
        jwt_issuer: str = "llm4ad-cli",
        jwt_audience: str = "mindmemos",
        timeout: int = 60,
    ):
        """Initialize the client with connection and JWT settings."""
        self.base_url = base_url.rstrip("/")
        self.jwt_secret = jwt_secret
        self.jwt_issuer = jwt_issuer
        self.jwt_audience = jwt_audience
        self.timeout = timeout

    def _generate_jwt(self, scopes: list[str] | None = None) -> str:
        """Generate JWT token for MindMemOS gateway authentication.

        Format matches backend's _mindmemos_gateway_token implementation.
        """
        import base64

        now = int(time.time())

        # Header
        header = {"alg": "HS256", "typ": "JWT"}

        # Payload - must match backend format
        payload = {
            "iss": self.jwt_issuer,
            "aud": self.jwt_audience,
            "sub": self.CLI_USER_ID,
            "account_id": self.CLI_USER_ID,
            "project_id": self.CLI_PROJECT_ID,
            "api_key_uuid": f"llm4ad-cli-{self.CLI_USER_ID}",
            "memory_algorithm": "schema",
            "scopes": scopes or ["memory:read", "memory:write"],
            "iat": now,
            "exp": now + 600,  # 10 minutes
        }

        # Build JWT: base64url(header).base64url(payload).signature
        def b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        header_b64 = b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        payload_b64 = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))

        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self.jwt_secret.encode("utf-8"),
            signing_input.encode("ascii"),
            "sha256",
        ).digest()
        signature_b64 = b64url(signature)

        return f"{signing_input}.{signature_b64}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Make authenticated request to MindMemOS."""
        token = self._generate_jwt(scopes)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}{path}"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(
                method,
                url,
                json=json_data,
                params=params,
                headers=headers,
            )

            if response.status_code >= 400:
                try:
                    error_data = response.json()
                    message = error_data.get("message") or error_data.get("detail") or response.text
                except Exception:
                    message = response.text or f"HTTP {response.status_code}"
                raise MindMemOSError(message, status_code=response.status_code)

            data = response.json()
            if not isinstance(data, dict):
                raise MindMemOSError(
                    "MindMemOS returned an invalid JSON response", status_code=response.status_code
                )
            return data

    def health_check(self) -> dict[str, Any]:
        """Check MindMemOS service health."""
        return self._request("GET", "/healthz", scopes=[])

    def bind_providers(
        self,
        *,
        chat_base_url: str,
        chat_api_key: str,
        chat_model: str,
        embedding_base_url: str,
        embedding_api_key: str,
        embedding_model: str,
        embedding_dim: int,
    ) -> dict[str, Any]:
        """Bind chat and embedding providers for CLI project.

        Format matches backend's _chat_endpoint and _embedding_endpoint.
        Model names need provider prefix for LiteLLM (e.g. openai/gpt-4).
        """
        # Transform model names to include provider prefix for LiteLLM
        # If base_url is OpenAI-compatible but not official OpenAI, use "openai/" prefix
        def transform_model_name(base_url: str, model: str) -> str:
            if "/" in model:
                # Already has provider prefix
                return model
            # OpenAI-compatible API
            return f"openai/{model}"

        chat_model_name = transform_model_name(chat_base_url, chat_model)
        embedding_model_name = transform_model_name(embedding_base_url, embedding_model)

        # Chat endpoint - matches _chat_endpoint (line 1623-1634)
        chat_endpoint = {
            "model": chat_model_name,
            "api_key": chat_api_key,
            "timeout": 30,
            "num_retries": 1,
        }
        if chat_base_url:
            chat_endpoint["api_base"] = chat_base_url.rstrip("/")

        # Embedding endpoint - matches _embedding_endpoint (line 1658-1667)
        embedding_endpoint = {
            "model": embedding_model_name,
            "api_key": embedding_api_key,
            "dimensions": embedding_dim,
            "timeout": 30,
            "num_retries": 1,
        }
        if embedding_base_url:
            embedding_endpoint["api_base"] = embedding_base_url.rstrip("/")

        payload = {
            "scope": {"user_id": self.CLI_USER_ID},
            "routers": {
                "chat_model_router": {
                    "routing_strategy": "simple-shuffle",
                    "endpoints": [chat_endpoint],
                },
                "embed_model_router": {
                    "routing_strategy": "simple-shuffle",
                    "endpoints": [embedding_endpoint],
                },
            },
        }

        return self._request(
            "POST",
            f"/internal/v1/projects/{self.CLI_PROJECT_ID}/provider-bindings",
            json_data=payload,
            scopes=["provider:write"],
        )

    def get_binding(self) -> dict[str, Any]:
        """Get current provider binding."""
        return self._request(
            "GET",
            f"/internal/v1/projects/{self.CLI_PROJECT_ID}/provider-bindings",
            scopes=["provider:read"],
        )

    def check_ready(self) -> dict[str, str]:
        """Verify the service and the CLI project's model binding.

        This intentionally performs no model invocation or memory write.  It
        gives the TUI a fast, actionable readiness result before users start a
        potentially long extraction.
        """
        self.health_check()
        items = ((self.get_binding().get("data") or {}).get("items") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            routers = item.get("routers") or {}
            chat_endpoints = ((routers.get("chat_model_router") or {}).get("endpoints") or [])
            embedding_endpoints = ((routers.get("embed_model_router") or {}).get("endpoints") or [])
            if not chat_endpoints or not embedding_endpoints:
                continue
            chat = chat_endpoints[0] if isinstance(chat_endpoints[0], dict) else {}
            embedding = embedding_endpoints[0] if isinstance(embedding_endpoints[0], dict) else {}
            if chat.get("model") and embedding.get("model"):
                return {
                    "binding_id": str(item.get("binding_id") or ""),
                    "chat_model": str(chat["model"]),
                    "embedding_model": str(embedding["model"]),
                }
        raise MindMemOSError("MindMemOS provider binding is incomplete")

    @staticmethod
    def parse_sse_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
        """Decode standard SSE frames into MindMemOS event dictionaries."""
        event_name: str | None = None
        data_lines: list[str] = []

        def flush() -> dict[str, Any] | None:
            nonlocal event_name, data_lines
            if not data_lines:
                event_name = None
                return None
            raw_data = "\n".join(data_lines)
            data_lines = []
            try:
                decoded = json.loads(raw_data)
            except json.JSONDecodeError:
                event_name = None
                return None
            event = decoded if isinstance(decoded, dict) else {"data": decoded}
            if event_name and "event" not in event:
                event["event"] = event_name
            event_name = None
            return event

        for line in lines:
            if not line:
                event = flush()
                if event is not None:
                    yield event
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        event = flush()
        if event is not None:
            yield event

    def list_memories(
        self,
        *,
        scope: str,
        task_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List memories in scope."""
        session_id = task_id if scope == "task" else "global"

        payload = {
            "user_id": self.CLI_USER_ID,
            "app_id": "llm4ad",
            "agent_id": scope,
            "session_id": session_id,
            "filters": {
                "user_id": self.CLI_USER_ID,
                "app_id": "llm4ad",
                "agent_id": scope,
                "session_id": session_id,
                # Only real LLM4AD memory cards (matches backend card filter),
                # so junk rows like input_messages/tags never reach the list.
                "entity_type": LLM4AD_MEMORY_ENTITY_TYPE,
                "property_name": {"in": list(LLM4AD_MEMORY_TYPES)},
            },
            "page": page,
            "page_size": page_size,
            "include_total": True,
            # Include archived (disabled) memories so users can re-enable them,
            # matching the frontend (memory_service.py:923).
            "include_inactive": True,
        }

        return self._request(
            "POST",
            "/v1/memory/list",
            json_data=payload,
            scopes=["memory:read"],
        )

    def add_memory(
        self,
        *,
        scope: str,
        task_id: str | None,
        content: str,
        generation_id: str,
        prompt_language: str | None = None,
    ) -> dict[str, Any]:
        """Add memory content (non-streaming).

        Uses MindMemOS /v1/memory/add API.
        Payload format matches backend's _memory_add_preview_payload.
        """
        session_id = task_id if scope == "task" else "global"

        # Build metadata
        metadata = {
            "llm4ad_generation_id": generation_id,
            "llm4ad_scope": scope,
            "llm4ad_enabled": False,  # Default to disabled, user can enable after preview
            "source": "llm4ad-cli",
        }
        if task_id:
            metadata["llm4ad_task_id"] = task_id
        if prompt_language:
            metadata["llm4ad_prompt_language"] = prompt_language

        # Build payload (matches backend format from line 1220)
        # Note: project_id is NOT in the payload, it's in JWT claims
        payload = {
            "user_id": self.CLI_USER_ID,
            "app_id": "llm4ad",
            "agent_id": scope,
            "session_id": session_id,
            "mode": "sync",
            "messages": [{"role": "user", "content": content}],
            "metadata": metadata,
            "task_id": task_id,
        }
        if prompt_language:
            payload["prompt_language"] = prompt_language

        return self._request(
            "POST",
            "/v1/memory/add",
            json_data=payload,
            scopes=["memory:write"],
        )

    def add_memory_stream(
        self,
        *,
        scope: str,
        task_id: str | None,
        content: str,
        generation_id: str,
        prompt_language: str | None = None,
        timeout: int | None = None,
        cancel_event: Event | None = None,
    ):
        """Add memory content with streaming progress (SSE).

        Uses MindMemOS /v1/memory/add/stream API.
        """
        session_id = task_id if scope == "task" else "global"

        # Build metadata
        metadata = {
            "llm4ad_generation_id": generation_id,
            "llm4ad_scope": scope,
            "llm4ad_enabled": False,
            "source": "llm4ad-cli",
        }
        if task_id:
            metadata["llm4ad_task_id"] = task_id
        if prompt_language:
            metadata["llm4ad_prompt_language"] = prompt_language

        # Build payload
        payload = {
            "user_id": self.CLI_USER_ID,
            "app_id": "llm4ad",
            "agent_id": scope,
            "session_id": session_id,
            "mode": "sync",
            "messages": [{"role": "user", "content": content}],
            "metadata": metadata,
            "task_id": task_id,
        }
        if prompt_language:
            payload["prompt_language"] = prompt_language

        token = self._generate_jwt(["memory:write"])
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        url = f"{self.base_url}/v1/memory/add/stream"

        timeout_val = timeout or self.timeout

        with httpx.Client(timeout=timeout_val) as client, client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                # In streaming mode the body must be read before .json()/.text.
                response.read()
                try:
                    error_data = response.json()
                    message = error_data.get("message") or error_data.get("detail") or response.text
                except Exception:
                    message = response.text or f"HTTP {response.status_code}"
                raise MindMemOSError(message, status_code=response.status_code)

            stop_watcher = Event()
            if cancel_event is not None:
                watcher = Thread(
                    target=self._close_stream_on_cancel,
                    args=(response, cancel_event, stop_watcher),
                    daemon=True,
                )
                watcher.start()
            try:
                for event in self.parse_sse_events(response.iter_lines()):
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    yield event
            finally:
                stop_watcher.set()

    @staticmethod
    def _close_stream_on_cancel(response: httpx.Response, cancel_event: Event, stop_event: Event) -> None:
        """Close a blocking HTTP stream promptly when a TUI user cancels."""
        while not stop_event.wait(0.1):
            if cancel_event.is_set():
                response.close()
                return

    def search_memories(
        self,
        *,
        scope: str,
        task_id: str | None,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Search memories."""
        session_id = task_id if scope == "task" else "global"

        payload = {
            "user_id": self.CLI_USER_ID,
            "app_id": "llm4ad",
            "agent_id": scope,
            "session_id": session_id,
            "query": query,
            "top_k": top_k,
        }

        return self._request(
            "POST",
            "/v1/memory/search",
            json_data=payload,
            scopes=["memory:read"],
        )

    def fetch_cards_by_ids(
        self,
        *,
        scope: str,
        task_id: str | None,
        memory_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch specific memories by id within a scope.

        Used by the insert preview flow to load freshly-extracted cards.
        Matches backend's _remote_fetch_cards_by_ids filter shape.
        """
        ids = [m for m in dict.fromkeys(memory_ids) if m]
        if not ids:
            return []
        session_id = task_id if scope == "task" else "global"
        payload = {
            "user_id": self.CLI_USER_ID,
            "app_id": "llm4ad",
            "agent_id": scope,
            "session_id": session_id,
            "filters": {
                "user_id": self.CLI_USER_ID,
                "app_id": "llm4ad",
                "agent_id": scope,
                "session_id": session_id,
                "memory_id": {"in": ids},
            },
            "page": 1,
            "page_size": max(len(ids), 1),
            "include_total": False,
            "include_inactive": True,
        }
        data = self._request(
            "POST",
            "/v1/memory/list",
            json_data=payload,
            scopes=["memory:read"],
        )
        memories = (data.get("data") or {}).get("memories") or []
        return [m for m in memories if isinstance(m, dict)]

    def update_memory(
        self,
        *,
        scope: str,
        task_id: str | None = None,
        memory_id: str,
        content: str | None = None,
        status: str | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update memory content, status, and metadata.

        Matches backend's _remote_upsert_card update path (memory_service.py:1251).
        Supports editing content, title/type/tags/enabled (via metadata_patch),
        and enable/disable (via status).
        """
        session_id = task_id if scope == "task" else "global"

        payload: dict[str, Any] = {
            "user_id": self.CLI_USER_ID,
            "app_id": "llm4ad",
            "agent_id": scope,
            "session_id": session_id,
            "memory_id": memory_id,
        }

        if content is not None:
            payload["content"] = content
        if status is not None:
            payload["status"] = status
        if metadata_patch:
            payload["metadata_patch"] = {
                k: v for k, v in metadata_patch.items() if v is not None
            }

        return self._request(
            "POST",
            "/v1/memory/update",
            json_data=payload,
            scopes=["memory:write"],
        )

    def delete_memory(self, *, memory_id: str) -> dict[str, Any]:
        """Delete memory.

        Matches backend's _remote_delete_card (memory_service.py:1319).
        """
        payload = {"memory_id": memory_id}

        return self._request(
            "POST",
            "/v1/memory/delete",
            json_data=payload,
            scopes=["memory:write"],
        )


class MindMemOSError(RuntimeError):
    """MindMemOS API error."""

    def __init__(self, message: str, status_code: int | None = None):
        """Store the error message and optional HTTP status code."""
        super().__init__(message)
        self.status_code = status_code
