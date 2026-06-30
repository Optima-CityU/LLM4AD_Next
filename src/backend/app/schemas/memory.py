"""Memory backend request/response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class MemoryTestRequest(BaseModel):
    """Connectivity test request for the configured memory backend."""

    type: str = "local_yaml"
    mindmemos_base_url: str = ""
    mindmemos_api_key: str = ""
    mindmemos_user_id: str = ""
    mindmemos_app_id: str = "llm4ad"
    mindmemos_agent_id: str = "planner"
    mindmemos_session_id: str = ""
    mindmemos_project_id: str = ""
    run_search_probe: bool = False


class MemoryTestResponse(BaseModel):
    """Connectivity test response for memory backend checks."""

    ok: bool
    message: str
    backend_type: str
    base_url: str | None = None
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
