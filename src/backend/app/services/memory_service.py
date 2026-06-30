"""Memory backend service helpers."""

from collections.abc import Callable
from typing import Any

import httpx

from app.schemas.memory import MemoryTestRequest, MemoryTestResponse


async def test_memory_connectivity(
    request: MemoryTestRequest,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> MemoryTestResponse:
    """Validate memory backend connectivity without mutating remote memory."""
    if request.type != "mindmemos_cloud":
        return MemoryTestResponse(
            ok=True,
            message="Local memory backend does not require remote connectivity.",
            backend_type=request.type,
        )

    missing = [
        field
        for field in ("mindmemos_base_url", "mindmemos_api_key", "mindmemos_user_id")
        if not getattr(request, field).strip()
    ]
    if missing:
        return MemoryTestResponse(
            ok=False,
            message=f"Missing required MindMemOS config: {', '.join(missing)}",
            backend_type=request.type,
            base_url=request.mindmemos_base_url or None,
            details={"missing": missing},
        )

    base_url = request.mindmemos_base_url.rstrip("/")
    try:
        async with http_client_factory(timeout=10.0) as client:
            response = await client.get(f"{base_url}/healthz")
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return MemoryTestResponse(
            ok=False,
            message=f"MindMemOS health check failed: {exc}",
            backend_type=request.type,
            base_url=base_url,
        )

    return MemoryTestResponse(
        ok=True,
        message="MindMemOS service is reachable.",
        backend_type=request.type,
        base_url=base_url,
    )
