"""Memory backend service helpers."""

import asyncio
import ast
import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.schemas.memory import (
    MemoryCardExtractionCommitRequest,
    MemoryCardExtractionRequest,
    MemoryCardExtractionResponse,
    MemoryCardResponse,
    MemoryCardReadonlyInfo,
    MemoryCardUpsertRequest,
    MemoryCardPageResponse,
    MemoryConfigUpdate,
    MemoryHealthResponse,
    MemoryProviderBindingResponse,
    MemoryProviderBindingUpdate,
    MemoryScope,
    MemoryTestRequest,
    MemoryTestResponse,
    ProjectMemoryConfigResponse,
    UserMemoryConfigResponse,
)
from app.services.project_service import get_project_with_auth
from app.services.task_service.auth import get_task_with_auth

LLM4AD_MEMORY_TYPES = {
    "good_algorithm",
    "error_reflection",
    "domain_knowledge",
    "general_insight",
}
LLM4AD_MEMORY_ENTITY_TYPE = "llm4ad_memory_card"
LLM4AD_MEMORY_TAG_PROPERTY = "tags"
LLM4AD_MEMORY_ENTITY_FILTER = {"entity_type": LLM4AD_MEMORY_ENTITY_TYPE}
LLM4AD_MEMORY_CARD_PROPERTY_FILTER = {
    "in": ["good_algorithm", "error_reflection", "domain_knowledge", "general_insight"]
}
MINDMEMOS_DEFAULT_SCORE_THRESHOLD = 0.65
MEMORY_STREAM_HEARTBEAT_SECONDS = 10.0


def _memory_stream_progress_event(
    stage: str,
    *,
    percent: int,
    zh: str,
    en: str,
) -> dict[str, Any]:
    return {
        "event": "progress",
        "stage": stage,
        "message": zh,
        "message_i18n": {"zh": zh, "en": en},
        "percent": percent,
    }


def _memory_stream_completed_event(
    *,
    preview_id: str,
    items: list[MemoryCardResponse],
) -> dict[str, Any]:
    has_items = bool(items)
    if has_items:
        message = "记忆提取完成"
        message_i18n = {
            "zh": "记忆提取完成",
            "en": "Memory extraction completed",
        }
    else:
        message = "MindMemOS 已完成处理，但没有提取到符合 LLM4AD 记忆卡片结构的内容。"
        message_i18n = {
            "zh": "没有提取到可保存的记忆",
            "en": "No saveable memory was extracted",
        }
    return {
        "event": "completed",
        "stage": "completed",
        "message": message,
        "message_i18n": message_i18n,
        "percent": 100,
        "preview_id": preview_id,
        "items": [item.model_dump(mode="json") for item in items],
    }


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


def _require_mindmemos_memory_enabled() -> None:
    if not settings.LLM4AD_MINDMEMOS_ENABLED:
        raise HTTPException(status_code=404, detail="MindMemOS memory management is disabled")
    if not settings.mindmemos_runtime_available:
        raise HTTPException(status_code=503, detail="MindMemOS runtime is not fully configured")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mindmemos_project_id(current_user: models.User) -> str:
    return f"llm4ad_user_{current_user.id.hex}"


def _mindmemos_gateway_token(
    current_user: models.User,
    *,
    scopes: list[str],
    ttl_seconds: int = 600,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": settings.LLM4AD_MINDMEMOS_JWT_ISSUER,
        "aud": settings.LLM4AD_MINDMEMOS_JWT_AUDIENCE,
        "sub": str(current_user.id),
        "account_id": str(current_user.id),
        "project_id": _mindmemos_project_id(current_user),
        "api_key_uuid": f"llm4ad-{current_user.id}",
        "memory_algorithm": "schema",
        "scopes": scopes,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")),
            _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        settings.LLM4AD_MINDMEMOS_JWT_SECRET.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _mindmemos_headers(current_user: models.User, *, scopes: list[str]) -> dict[str, str]:
    if not settings.LLM4AD_MINDMEMOS_JWT_SECRET.strip():
        raise HTTPException(status_code=503, detail="MindMemOS JWT secret is not configured")
    return {"Authorization": f"Bearer {_mindmemos_gateway_token(current_user, scopes=scopes)}"}


def _mindmemos_base_url() -> str:
    base_url = settings.LLM4AD_MINDMEMOS_BASE_URL.rstrip("/")
    if not base_url:
        raise HTTPException(status_code=503, detail="MindMemOS base URL is not configured")
    return base_url


def _missing_mindmemos_runtime_fields() -> list[str]:
    missing: list[str] = []
    if not settings.LLM4AD_MINDMEMOS_ENABLED:
        missing.append("LLM4AD_MINDMEMOS_ENABLED")
    if not settings.LLM4AD_MINDMEMOS_BASE_URL.strip():
        missing.append("LLM4AD_MINDMEMOS_BASE_URL")
    if not settings.LLM4AD_MINDMEMOS_JWT_SECRET.strip():
        missing.append("LLM4AD_MINDMEMOS_JWT_SECRET")
    return missing


def _parse_mindmemos_error_response(response: httpx.Response) -> tuple[str | None, str]:
    text = response.text or ""
    try:
        payload = response.json()
    except ValueError:
        return None, text or "MindMemOS request failed"

    if isinstance(payload, dict):
        code = payload.get("code")
        message = payload.get("message") or payload.get("detail")
        if isinstance(message, dict):
            message = message.get("message") or message.get("detail")
        if code and message:
            return str(code), f"{code}: {message}"
        if message:
            return str(code) if code else None, str(message)
    return None, text or "MindMemOS request failed"


def _base_mindmemos_scope(current_user: models.User, session_id: str, agent_id: str) -> dict[str, str]:
    return {
        "user_id": str(current_user.id),
        "app_id": settings.LLM4AD_MINDMEMOS_APP_ID,
        "agent_id": agent_id,
        "session_id": session_id,
    }


def _raise_for_mindmemos_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _code, detail = _parse_mindmemos_error_response(exc.response)
        raise HTTPException(status_code=502, detail=detail) from exc


def _mindmemos_http_timeout(timeout: float | int | None) -> float | None:
    if timeout is None:
        return None
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return None
    return None if value == 0 else value


def _mindmemos_timeout_for_path(path: str) -> float | None:
    if path in {"/v1/memory/add", "/v1/memory/add/stream"}:
        return _mindmemos_http_timeout(settings.LLM4AD_MINDMEMOS_ADD_TIMEOUT)
    return _mindmemos_http_timeout(settings.LLM4AD_MINDMEMOS_REQUEST_TIMEOUT)


def _mindmemos_post(
    current_user: models.User,
    path: str,
    payload: dict[str, Any],
    *,
    scopes: list[str],
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=_mindmemos_timeout_for_path(path)) as client:
            response = client.post(
                f"{_mindmemos_base_url()}{path}",
                headers=_mindmemos_headers(current_user, scopes=scopes),
                json=payload,
            )
            _raise_for_mindmemos_error(response)
            data = response.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MindMemOS request failed: {exc}") from exc
    if data.get("code") not in (None, "ok", "queued"):
        raise HTTPException(status_code=502, detail=data.get("message") or "MindMemOS request failed")
    return data


async def _mindmemos_stream_post(
    current_user: models.User,
    path: str,
    payload: dict[str, Any],
    *,
    scopes: list[str],
) -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any] | Exception | None] = asyncio.Queue()

    async def produce() -> None:
        event_name = "message"
        data_lines: list[str] = []

        async def emit_event() -> dict[str, Any] | None:
            nonlocal event_name, data_lines
            if not data_lines:
                event_name = "message"
                return None
            raw_data = "\n".join(data_lines)
            data_lines = []
            try:
                payload_data = json.loads(raw_data)
            except ValueError:
                payload_data = {"message": raw_data}
            if not isinstance(payload_data, dict):
                payload_data = {"data": payload_data}
            payload_data["event"] = event_name
            event_name = "message"
            return payload_data

        try:
            async with httpx.AsyncClient(timeout=_mindmemos_timeout_for_path(path)) as client:
                async with client.stream(
                    "POST",
                    f"{_mindmemos_base_url()}{path}",
                    headers=_mindmemos_headers(current_user, scopes=scopes),
                    json=payload,
                ) as response:
                    _raise_for_mindmemos_error(response)
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line.split(":", 1)[1].strip() or "message"
                        elif line.startswith("data:"):
                            data_lines.append(line.split(":", 1)[1].strip())
                        elif line == "":
                            event = await emit_event()
                            if event is not None:
                                await queue.put(event)
                    event = await emit_event()
                    if event is not None:
                        await queue.put(event)
        except HTTPException as exc:
            await queue.put(exc)
        except Exception as exc:  # noqa: BLE001
            await queue.put(HTTPException(status_code=502, detail=f"MindMemOS request failed: {exc}"))
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=MEMORY_STREAM_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                yield {
                    "event": "heartbeat",
                    "stage": "waiting",
                    "message": "MindMemOS is still processing.",
                    "message_i18n": {
                        "zh": "MindMemOS 仍在处理，请稍候",
                        "en": "MindMemOS is still processing.",
                    },
                }
                continue
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        if not task.done():
            task.cancel()

            def _consume_task_result(done_task: asyncio.Task) -> None:
                try:
                    done_task.exception()
                except asyncio.CancelledError:
                    pass

            task.add_done_callback(_consume_task_result)


def _mindmemos_get(
    current_user: models.User,
    path: str,
    *,
    scopes: list[str],
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=_mindmemos_http_timeout(settings.LLM4AD_MINDMEMOS_REQUEST_TIMEOUT)) as client:
            response = client.get(
                f"{_mindmemos_base_url()}{path}",
                headers=_mindmemos_headers(current_user, scopes=scopes),
            )
            _raise_for_mindmemos_error(response)
            data = response.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MindMemOS request failed: {exc}") from exc
    if data.get("code") not in (None, "ok", "queued"):
        raise HTTPException(status_code=502, detail=data.get("message") or "MindMemOS request failed")
    return data


def _mindmemos_patch(
    current_user: models.User,
    path: str,
    payload: dict[str, Any],
    *,
    scopes: list[str],
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=_mindmemos_http_timeout(settings.LLM4AD_MINDMEMOS_REQUEST_TIMEOUT)) as client:
            response = client.patch(
                f"{_mindmemos_base_url()}{path}",
                headers=_mindmemos_headers(current_user, scopes=scopes),
                json=payload,
            )
            _raise_for_mindmemos_error(response)
            data = response.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MindMemOS request failed: {exc}") from exc
    if data.get("code") not in (None, "ok", "queued"):
        raise HTTPException(status_code=502, detail=data.get("message") or "MindMemOS request failed")
    return data


def get_mindmemos_health(
    current_user: models.User,
    http_client_factory: Callable[..., Any] = httpx.Client,
) -> MemoryHealthResponse:
    """Check whether the configured MindMemOS runtime is ready for card management."""
    system_fields = _system_config_fields()
    missing = _missing_mindmemos_runtime_fields()
    if missing:
        return MemoryHealthResponse(
            ok=False,
            message=f"MindMemOS system config is incomplete: {', '.join(missing)}",
            service_reachable=False,
            auth_ok=False,
            error_code="config.missing",
            details={"missing": missing},
            **system_fields,
        )

    base_url = settings.LLM4AD_MINDMEMOS_BASE_URL.rstrip("/")
    probe_payload = {
        "filters": {
            "AND": [
                {"user_id": str(current_user.id)},
                {"app_id": settings.LLM4AD_MINDMEMOS_APP_ID},
                {"agent_id": settings.LLM4AD_MINDMEMOS_AGENT_ID},
                {"session_id": "global"},
            ]
        },
        "top_k": 1,
    }
    try:
        with http_client_factory(timeout=10.0) as client:
            health_response = client.get(f"{base_url}/healthz")
            if health_response.status_code >= 400:
                code, message = _parse_mindmemos_error_response(health_response)
                return MemoryHealthResponse(
                    ok=False,
                    message=f"MindMemOS health check failed: {message}",
                    service_reachable=False,
                    auth_ok=False,
                    error_code=code or "health.unreachable",
                    details={"status_code": health_response.status_code},
                    **system_fields,
                )

            auth_response = client.post(
                f"{base_url}/v1/memory/get",
                headers=_mindmemos_headers(current_user, scopes=["memory:read"]),
                json=probe_payload,
            )
            if auth_response.status_code >= 400:
                code, message = _parse_mindmemos_error_response(auth_response)
                return MemoryHealthResponse(
                    ok=False,
                    message=message,
                    service_reachable=True,
                    auth_ok=False,
                    error_code=code or "auth.failed",
                    details={"status_code": auth_response.status_code},
                    **system_fields,
                )

            data = auth_response.json()
    except Exception as exc:  # noqa: BLE001
        return MemoryHealthResponse(
            ok=False,
            message=f"MindMemOS health check failed: {exc}",
            service_reachable=False,
            auth_ok=False,
            error_code="health.unreachable",
            details={},
            **system_fields,
        )

    if data.get("code") not in (None, "ok", "queued"):
        message = str(data.get("message") or "MindMemOS request failed")
        return MemoryHealthResponse(
            ok=False,
            message=message,
            service_reachable=True,
            auth_ok=True,
            error_code=str(data.get("code") or "mindmemos.error"),
            details={"response": data},
            **system_fields,
        )

    return MemoryHealthResponse(
        ok=True,
        message="MindMemOS service is ready.",
        service_reachable=True,
        auth_ok=True,
        details={},
        **system_fields,
    )


def _llm4ad_request_record_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    request_metadata = metadata.get("request_metadata")
    if not isinstance(request_metadata, dict):
        return {}
    record_metadata = request_metadata.get("record_metadata")
    if not isinstance(record_metadata, list):
        return {}
    records = [record for record in record_metadata if isinstance(record, dict)]
    for record in records:
        if record.get("source") == "llm4ad":
            return record
    return records[0] if records else {}


def _memory_item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(_llm4ad_request_record_metadata(item))
    item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata.update(item_metadata)
    for field in ("entity_id", "entity_type", "entity_name", "property_name", "property_time"):
        if item.get(field) is not None and metadata.get(field) is None:
            metadata[field] = item.get(field)
    return metadata


def _memory_property_name(item: dict[str, Any]) -> str | None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return _optional_str(item.get("property_name") or metadata.get("property_name"))


def _memory_entity_type(item: dict[str, Any]) -> str | None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return _optional_str(item.get("entity_type") or metadata.get("entity_type"))


def _memory_entity_key(item: dict[str, Any]) -> str | None:
    metadata = _memory_item_metadata(item)
    for value in (
        item.get("entity_id"),
        metadata.get("entity_id"),
        item.get("entity_name"),
        metadata.get("entity_name"),
    ):
        key = _optional_str(value)
        if key:
            return key
    return None


def _normalize_tag_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        raw_values = list(value)
    else:
        text = str(value).strip()
        if not text:
            return []
        raw_values: list[Any]
        try:
            parsed = json.loads(text)
        except ValueError:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
        if isinstance(parsed, list | tuple | set):
            raw_values = list(parsed)
        else:
            raw_values = re.split(r"[,，;；\n]+", text)

    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_values:
        tag = str(raw_tag).strip().strip("\"'")
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag[:64])
    return tags


def _tag_values_from_memory_item(item: dict[str, Any]) -> list[str]:
    metadata = _memory_item_metadata(item)
    for value in (
        item.get("memory"),
        item.get("content"),
        metadata.get("tags"),
    ):
        tags = _normalize_tag_values(value)
        if tags:
            return tags
    return []


def _remote_memory_to_card(item: dict[str, Any]) -> MemoryCardResponse:
    memory_id = str(item.get("id") or item.get("memory_id") or "")
    content = str(item.get("memory") or item.get("content") or "")
    metadata = _memory_item_metadata(item)
    title = _memory_card_title(item, metadata)
    property_name = _memory_property_name(item)
    raw_type = _normalize_llm4ad_memory_type(
        metadata.get("memory_type"),
        property_name,
        item.get("memory_type"),
        item.get("mem_type"),
    )
    tags = _normalize_tag_values(metadata.get("tags"))
    status = str(item.get("status") or metadata.get("status") or "active")
    enabled = status == "active" and metadata.get("enabled", True) is not False
    score = _optional_float(item.get("score"))
    if score is None:
        score = _optional_float(metadata.get("score"))
    return MemoryCardResponse(
        id=memory_id,
        type=raw_type,
        title=title,
        content=content,
        enabled=enabled,
        source="mindmemos",
        tags=[str(tag) for tag in tags],
        score=score,
        generation=_optional_int(metadata.get("generation")),
        algorithm_id=_optional_str(metadata.get("algorithm_id")),
        metadata=metadata,
        readonly=MemoryCardReadonlyInfo(
            source="mindmemos",
            status=status,
            entity_name=_optional_str(metadata.get("entity_name") or item.get("entity_name")),
            property_name=property_name,
            property_time=_optional_str(metadata.get("property_time") or item.get("property_time")),
            last_update_at=_optional_str(item.get("last_update_at")),
            event_time=_optional_str(item.get("event_time")),
            source_timestamp=_optional_str(item.get("source_timestamp")),
        ),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _memory_card_title(item: dict[str, Any], metadata: dict[str, Any]) -> str:
    for value in (
        metadata.get("title"),
        item.get("title"),
        metadata.get("name"),
        item.get("name"),
        metadata.get("entity_name"),
        item.get("entity_name"),
    ):
        title = _optional_str(value)
        if title:
            return title[:120]
    return "未命名记忆"


def _normalize_llm4ad_memory_type(*candidates: Any) -> str:
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value in LLM4AD_MEMORY_TYPES:
            return value
    return "general_insight"


def _is_llm4ad_schema_item(item: dict[str, Any]) -> bool:
    return _memory_entity_type(item) == LLM4AD_MEMORY_ENTITY_TYPE


def _llm4ad_card_property_name(item: dict[str, Any]) -> str | None:
    metadata = _memory_item_metadata(item)
    for value in (
        _memory_property_name(item),
        metadata.get("memory_type"),
        item.get("memory_type"),
        item.get("mem_type"),
    ):
        property_name = _optional_str(value)
        if property_name in LLM4AD_MEMORY_TYPES:
            return property_name
    return None


def _remote_items_to_cards(
    items: list[Any],
) -> list[MemoryCardResponse]:
    content_items: list[tuple[str, dict[str, Any]]] = []
    tags_by_entity: dict[str, list[str]] = {}
    name_by_entity: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        card_property_name = _llm4ad_card_property_name(item)
        if not _is_llm4ad_schema_item(item) and not card_property_name:
            continue
        entity_key = _memory_entity_key(item) or _optional_str(item.get("id") or item.get("memory_id"))
        if not entity_key:
            continue
        property_name = _memory_property_name(item)
        if property_name == LLM4AD_MEMORY_TAG_PROPERTY:
            tags_by_entity[entity_key] = _tag_values_from_memory_item(item)
            continue
        if property_name == "name":
            name = _optional_str(item.get("memory") or item.get("content"))
            if name:
                name_by_entity[entity_key] = name
            continue
        if property_name in {"input_messages", None}:
            if not card_property_name:
                continue
        if card_property_name:
            content_items.append((entity_key, item))

    cards: list[MemoryCardResponse] = []
    for entity_key, item in content_items:
        card = _remote_memory_to_card(item)
        if not card.id:
            continue
        if name_by_entity.get(entity_key) and card.title == "未命名记忆":
            card = card.model_copy(update={"title": name_by_entity[entity_key][:120]})
        if entity_key and tags_by_entity.get(entity_key):
            card = card.model_copy(update={"tags": tags_by_entity[entity_key]})
        cards.append(card)
    return cards


def _remote_card_filters() -> dict[str, Any]:
    return {
        "entity_type": LLM4AD_MEMORY_ENTITY_TYPE,
        "property_name": LLM4AD_MEMORY_CARD_PROPERTY_FILTER,
    }


def _remote_tag_filters(entity_ids: list[str]) -> dict[str, Any]:
    return {
        "entity_id": {"in": entity_ids},
        "property_name": LLM4AD_MEMORY_TAG_PROPERTY,
    }


def _remote_scoped_filters(scope_data: dict[str, str], filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": scope_data["user_id"],
        "app_id": scope_data["app_id"],
        "session_id": scope_data["session_id"],
        "agent_id": scope_data["agent_id"],
        **filters,
    }


def _memory_page_data(data: dict[str, Any]) -> dict[str, Any]:
    page_data = data.get("data") or {}
    return page_data if isinstance(page_data, dict) else {}


def _remote_list_cards(
    current_user: models.User,
    scope_data: dict[str, str],
    *,
    page: int,
    page_size: int,
) -> MemoryCardPageResponse:
    payload = {
        **scope_data,
        "filters": _remote_scoped_filters(scope_data, _remote_card_filters()),
        "page": page,
        "page_size": page_size,
        "include_total": True,
        "include_inactive": True,
    }
    data = _mindmemos_post(current_user, "/v1/memory/list", payload, scopes=["memory:read"])
    page_data = _memory_page_data(data)
    memories = [
        item
        for item in (page_data.get("memories") or [])
        if isinstance(item, dict)
    ]
    cards = _remote_items_to_cards(memories)
    entity_ids = _unique_ids(
        [
            entity_id
            for item in memories
            if isinstance(item, dict)
            for entity_id in [_memory_entity_key(item)]
            if entity_id
        ]
    )
    if entity_ids:
        tag_data = _mindmemos_post(
            current_user,
            "/v1/memory/list",
            {
                **scope_data,
                "filters": _remote_scoped_filters(scope_data, _remote_tag_filters(entity_ids)),
                "page": 1,
                "page_size": max(len(entity_ids), 1),
                "include_total": False,
                "include_inactive": True,
            },
            scopes=["memory:read"],
        )
        tag_items = [
            item
            for item in ((_memory_page_data(tag_data).get("memories") or []))
            if isinstance(item, dict)
        ]
        tag_cards_by_entity = {
            entity_key: tags
            for entity_key, tags in (
                (_memory_entity_key(item), _tag_values_from_memory_item(item))
                for item in tag_items
                if _memory_property_name(item) == LLM4AD_MEMORY_TAG_PROPERTY
            )
            if entity_key and tags
        }
        if tag_cards_by_entity:
            cards = [
                card.model_copy(update={"tags": tag_cards_by_entity.get(_memory_entity_key(item), card.tags)})
                for card, item in zip(cards, memories, strict=False)
            ]
    remote_total = page_data.get("total")
    total = len(cards)
    if remote_total is not None:
        try:
            parsed_total = int(remote_total)
        except (TypeError, ValueError):
            parsed_total = len(cards)
        if parsed_total > len(memories):
            total = parsed_total
    return MemoryCardPageResponse(
        items=cards,
        page=int(page_data.get("page") or page),
        page_size=int(page_data.get("page_size") or page_size),
        total=total,
        has_more=bool(page_data.get("has_more", False)) if cards else False,
    )


def _unique_ids(memory_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(memory_id.strip() for memory_id in memory_ids if memory_id.strip()))


def _remote_fetch_cards_by_ids(
    current_user: models.User,
    scope_data: dict[str, str],
    memory_ids: list[str],
) -> dict[str, MemoryCardResponse]:
    ids = _unique_ids(memory_ids)
    if not ids:
        return {}
    data = _mindmemos_post(
        current_user,
        "/v1/memory/list",
        {
            **scope_data,
            "filters": _remote_scoped_filters(scope_data, {"memory_id": {"in": ids}}),
            "page": 1,
            "page_size": max(len(ids), 1),
            "include_total": False,
            "include_inactive": True,
        },
        scopes=["memory:read"],
    )
    memories = ((data.get("data") or {}).get("memories") or [])
    memory_items = [item for item in memories if isinstance(item, dict)]
    return {
        card.id: card
        for card in _remote_items_to_cards(memory_items)
        if card.id in ids
    }


def _generated_card_metadata(
    *,
    generation_id: str,
    scope_name: MemoryScope,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    enabled: bool,
) -> dict[str, Any]:
    del scope_name, project_id, task_id
    return {
        "source": "llm4ad",
        "llm4ad_generation_id": generation_id,
        "enabled": enabled,
    }


def _editable_card_metadata(
    *,
    scope_name: MemoryScope,
    memory_type: str,
    title: str,
    enabled: bool,
    tags: list[str],
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
) -> dict[str, Any]:
    del scope_name, project_id, task_id
    return {
        "source": "llm4ad",
        "memory_type": memory_type,
        "title": title,
        "enabled": enabled,
        "tags": tags,
    }


def _remote_update_card_status(
    current_user: models.User,
    memory_id: str,
    *,
    scope_data: dict[str, str],
    content: str | None = None,
    status: str,
    metadata_patch: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        **scope_data,
        "memory_id": memory_id,
        "metadata_patch": {key: value for key, value in metadata_patch.items() if value is not None},
        "status": status,
    }
    if content is not None:
        payload["content"] = content
    _mindmemos_post(
        current_user,
        "/v1/memory/update",
        payload,
        scopes=["memory:write"],
    )


def _generated_card_update_metadata(
    card: MemoryCardResponse,
    *,
    generation_id: str,
    scope_name: MemoryScope,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    enabled: bool,
) -> dict[str, Any]:
    return {
        **card.metadata,
        **_generated_card_metadata(
            generation_id=generation_id,
            scope_name=scope_name,
            project_id=project_id,
            task_id=task_id,
            enabled=enabled,
        ),
        "memory_type": card.type,
        "title": card.title,
        "tags": card.tags,
    }


def _archive_generated_card(
    current_user: models.User,
    scope_data: dict[str, str],
    card: MemoryCardResponse,
    *,
    generation_id: str,
    scope_name: MemoryScope,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
) -> MemoryCardResponse:
    metadata = _generated_card_update_metadata(
        card,
        generation_id=generation_id,
        scope_name=scope_name,
        project_id=project_id,
        task_id=task_id,
        enabled=False,
    )
    try:
        _remote_update_card_status(
            current_user,
            card.id,
            scope_data=scope_data,
            status="archived",
            metadata_patch=metadata,
        )
    except Exception:
        _remote_delete_card(current_user, card.id)
        raise
    return card.model_copy(
        update={
            "enabled": False,
            "metadata": {key: value for key, value in metadata.items() if value is not None},
        }
    )


def _fallback_card_from_add_event(
    item: dict[str, Any],
    *,
    generation_id: str,
    scope_name: MemoryScope,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
) -> MemoryCardResponse | None:
    memory_id = str(item.get("memory_id") or item.get("id") or "")
    extracted_content = str(item.get("content") or item.get("memory") or "").strip()
    if not memory_id or not extracted_content:
        return None
    metadata = _generated_card_metadata(
        generation_id=generation_id,
        scope_name=scope_name,
        project_id=project_id,
        task_id=task_id,
        enabled=False,
    )
    memory_type = _normalize_llm4ad_memory_type(
        item.get("property_name"),
        item.get("memory_type"),
        item.get("mem_type"),
    )
    metadata["memory_type"] = memory_type
    metadata["title"] = extracted_content.splitlines()[0][:80] or "MindMemOS memory"
    return MemoryCardResponse(
        id=memory_id,
        type=memory_type,
        title=str(metadata["title"]),
        content=extracted_content,
        enabled=False,
        source="mindmemos",
        tags=[],
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def _remote_generated_cards_from_add_memories(
    current_user: models.User,
    scope_data: dict[str, str],
    scope_name: MemoryScope,
    memories: list[Any],
    *,
    generation_id: str,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
) -> list[MemoryCardResponse]:
    cards: list[MemoryCardResponse] = []
    seen_ids: set[str] = set()
    for item in memories:
        if not isinstance(item, dict):
            continue
        operation = str(item.get("operation") or "add")
        if operation != "add":
            continue
        related_ids = _unique_ids([str(memory_id) for memory_id in item.get("related_memory_ids") or []])
        if related_ids:
            related_cards = _remote_fetch_cards_by_ids(
                current_user,
                scope_data,
                related_ids,
            )
            for related_id in related_ids:
                related_card = related_cards.get(related_id)
                if related_card is None or related_id in seen_ids:
                    continue
                seen_ids.add(related_id)
                cards.append(
                    _archive_generated_card(
                        current_user,
                        scope_data,
                        related_card,
                        generation_id=generation_id,
                        scope_name=scope_name,
                        project_id=project_id,
                        task_id=task_id,
                    )
                )
            continue
        fallback_card = _fallback_card_from_add_event(
            item,
            generation_id=generation_id,
            scope_name=scope_name,
            project_id=project_id,
            task_id=task_id,
        )
        if fallback_card is None or fallback_card.id in seen_ids:
            continue
        seen_ids.add(fallback_card.id)
        try:
            _remote_update_card_status(
                current_user,
                fallback_card.id,
                scope_data=scope_data,
                status="archived",
                metadata_patch=fallback_card.metadata,
            )
        except Exception:
            _remote_delete_card(current_user, fallback_card.id)
            raise
        cards.append(fallback_card)
    return cards


def _remote_extract_preview_cards(
    current_user: models.User,
    scope_data: dict[str, str],
    scope_name: MemoryScope,
    *,
    content: str,
    preview_id: str,
    prompt_language: str | None,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
) -> list[MemoryCardResponse]:
    payload = _memory_add_preview_payload(
        scope_data,
        scope_name,
        content=content,
        preview_id=preview_id,
        prompt_language=prompt_language,
        project_id=project_id,
        task_id=task_id,
    )
    data = _mindmemos_post(
        current_user,
        "/v1/memory/add",
        payload,
        scopes=["memory:write"],
    )
    memories = ((data.get("data") or {}).get("memories") or [])
    return _remote_generated_cards_from_add_memories(
        current_user,
        scope_data,
        scope_name,
        memories,
        generation_id=preview_id,
        project_id=project_id,
        task_id=task_id,
    )


def _memory_add_preview_payload(
    scope_data: dict[str, str],
    scope_name: MemoryScope,
    *,
    content: str,
    preview_id: str,
    prompt_language: str | None,
    project_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
) -> dict[str, Any]:
    metadata = _generated_card_metadata(
        generation_id=preview_id,
        scope_name=scope_name,
        project_id=project_id,
        task_id=task_id,
        enabled=False,
    )
    if prompt_language:
        metadata["llm4ad_prompt_language"] = prompt_language
    payload: dict[str, Any] = {
        **scope_data,
        "mode": "sync",
        "messages": [{"role": "user", "content": content}],
        "metadata": metadata,
        "task_id": str(task_id) if task_id else None,
    }
    if prompt_language:
        payload["prompt_language"] = prompt_language
    return payload


def _remote_upsert_card(
    current_user: models.User,
    scope_data: dict[str, str],
    scope_name: MemoryScope,
    request: MemoryCardUpsertRequest,
    *,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> MemoryCardResponse:
    if request.id:
        metadata_patch = _editable_card_metadata(
            scope_name=scope_name,
            memory_type=request.type,
            title=request.title,
            enabled=request.enabled,
            tags=request.tags,
            project_id=project_id,
            task_id=task_id,
        )
        _mindmemos_post(
            current_user,
            "/v1/memory/update",
            {
                **scope_data,
                "memory_id": request.id,
                "content": request.content,
                "metadata_patch": metadata_patch,
                "status": "active" if request.enabled else "archived",
            },
            scopes=["memory:write"],
        )
        fallback = MemoryCardResponse(
            id=request.id,
            type=request.type,
            title=request.title or "未命名记忆",
            content=request.content,
            enabled=request.enabled,
            source="mindmemos",
            tags=request.tags,
            score=request.score,
            generation=request.generation,
            algorithm_id=request.algorithm_id,
            metadata={key: value for key, value in metadata_patch.items() if value is not None},
        )
        return _remote_fetch_cards_by_ids(current_user, scope_data, [request.id]).get(request.id, fallback)

    metadata = _editable_card_metadata(
        scope_name=scope_name,
        memory_type=request.type,
        title=request.title,
        enabled=request.enabled,
        tags=request.tags,
        project_id=project_id,
        task_id=task_id,
    )
    data = _mindmemos_post(
        current_user,
        "/v1/memory/add",
        {
            **scope_data,
            "mode": "sync",
            "messages": [{"role": "user", "content": request.content}],
            "metadata": {key: value for key, value in metadata.items() if value is not None},
            "score": request.score,
            "task_id": str(task_id) if task_id else None,
        },
        scopes=["memory:write"],
    )
    memories = ((data.get("data") or {}).get("memories") or [])
    memory_id = ""
    if memories and isinstance(memories[0], dict):
        memory_id = str(memories[0].get("memory_id") or memories[0].get("id") or "")
    return MemoryCardResponse(
        id=memory_id or uuid.uuid4().hex[:8],
        type=request.type,
        title=request.title or "未命名记忆",
        content=request.content,
        enabled=request.enabled,
        source="mindmemos",
        tags=request.tags,
        score=request.score,
        generation=request.generation,
        algorithm_id=request.algorithm_id,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def _remote_delete_card(current_user: models.User, memory_id: str) -> None:
    _mindmemos_post(
        current_user,
        "/v1/memory/delete",
        {"memory_id": memory_id, "hard": True},
        scopes=["memory:write"],
    )


def _system_config_fields() -> dict[str, Any]:
    return {
        "system_enabled": settings.LLM4AD_MINDMEMOS_ENABLED,
        "system_base_url": settings.LLM4AD_MINDMEMOS_BASE_URL.rstrip("/"),
        "system_api_key_configured": bool(settings.LLM4AD_MINDMEMOS_JWT_SECRET),
        "system_chat_configured": True,
        "system_embedding_configured": True,
        "system_embedding_dimensions": None,
        "system_rerank_enabled": settings.MINDMEMOS_RERANK_ENABLED,
        "system_rerank_configured": settings.mindmemos_rerank_configured,
        "system_runtime_available": settings.mindmemos_runtime_available,
    }


def _with_system_config(config: models.UserMemoryConfig | models.ProjectMemoryConfig) -> dict[str, Any]:
    data = {field: getattr(config, field) for field in config.__class__.model_fields}
    if not data.get("mindmemos_rerank"):
        data["mindmemos_score_threshold"] = None
    data.update(_system_config_fields())
    return data


def _normalize_memory_config_rerank_threshold(config: models.UserMemoryConfig | models.ProjectMemoryConfig) -> None:
    """Keep score threshold meaningful only when MindMemOS rerank is enabled."""
    if not config.mindmemos_rerank:
        config.mindmemos_score_threshold = None


def _copy_user_binding_fields(data: dict[str, Any], user_defaults: UserMemoryConfigResponse) -> dict[str, Any]:
    """Expose the user-level MindMemOS provider binding on project config responses."""

    for field in (
        "mindmemos_binding_id",
        "mindmemos_chat_provider_id",
        "mindmemos_chat_model",
        "mindmemos_embedding_provider_id",
        "mindmemos_embedding_model",
        "mindmemos_embedding_dim",
    ):
        data[field] = getattr(user_defaults, field)
    return data


def get_user_memory_config(db: Session, current_user: models.User) -> UserMemoryConfigResponse:
    """Get or create the current user's memory defaults."""
    stmt = select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == current_user.id)
    config = db.exec(stmt).first()
    if config is None:
        rerank_enabled = settings.mindmemos_rerank_configured
        config = models.UserMemoryConfig(
            user_id=current_user.id,
            mindmemos_rerank=rerank_enabled,
            mindmemos_score_threshold=MINDMEMOS_DEFAULT_SCORE_THRESHOLD if rerank_enabled else None,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    return UserMemoryConfigResponse.model_validate(_with_system_config(config))


def update_user_memory_config(
    db: Session,
    current_user: models.User,
    request: MemoryConfigUpdate,
) -> UserMemoryConfigResponse:
    """Update the current user's memory defaults."""
    get_user_memory_config(db, current_user)
    config = db.exec(
        select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == current_user.id)
    ).one()
    config.sqlmodel_update(request.model_dump(exclude_unset=True))
    _normalize_memory_config_rerank_threshold(config)
    config.updated_time = datetime.now(UTC)
    db.add(config)
    db.commit()
    db.refresh(config)
    return UserMemoryConfigResponse.model_validate(_with_system_config(config))


def get_memory_provider_binding(
    db: Session,
    current_user: models.User,
) -> MemoryProviderBindingResponse:
    """Return the current user's MindMemOS provider binding state."""

    config = get_user_memory_config(db, current_user)
    return MemoryProviderBindingResponse(
        configured=bool(config.mindmemos_binding_id),
        binding_id=config.mindmemos_binding_id,
        project_id=_mindmemos_project_id(current_user),
        user_id=current_user.id,
        chat_provider_id=config.mindmemos_chat_provider_id,
        chat_model=config.mindmemos_chat_model,
        embedding_provider_id=config.mindmemos_embedding_provider_id,
        embedding_model=config.mindmemos_embedding_model,
        embedding_dim=config.mindmemos_embedding_dim,
        embedding_locked=bool(config.mindmemos_embedding_model and config.mindmemos_embedding_dim),
        message="MindMemOS provider binding is configured." if config.mindmemos_binding_id else "Provider binding is not configured.",
    )


def upsert_memory_provider_binding(
    db: Session,
    current_user: models.User,
    request: MemoryProviderBindingUpdate,
) -> MemoryProviderBindingResponse:
    """Create or update the MindMemOS provider binding for the current user."""

    _require_mindmemos_memory_enabled()
    get_user_memory_config(db, current_user)
    config = db.exec(select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == current_user.id)).one()

    chat_provider = db.get(models.LLMProvider, request.chat_provider_id)
    if chat_provider is None:
        raise HTTPException(status_code=404, detail="LLM provider does not exist")
    if not _provider_accessible(chat_provider, current_user):
        raise HTTPException(status_code=403, detail="No access to selected LLM provider")
    available_models = [model.strip() for model in chat_provider.model.split(";") if model.strip()]
    if not request.chat_model.strip():
        raise HTTPException(status_code=400, detail="Chat model is required")
    if available_models and request.chat_model not in available_models:
        raise HTTPException(status_code=400, detail="Selected chat model does not belong to this provider")

    embedding_provider = db.get(models.EmbeddingProvider, request.embedding_provider_id)
    if embedding_provider is None:
        raise HTTPException(status_code=404, detail="Embedding provider does not exist")
    if not (current_user.is_superuser or embedding_provider.user_id == current_user.id):
        raise HTTPException(status_code=403, detail="No access to selected embedding provider")

    embedding_identity = _embedding_identity(embedding_provider)
    if config.mindmemos_embedding_model and config.mindmemos_embedding_model != embedding_identity["model"]:
        raise HTTPException(status_code=409, detail="Embedding model is locked for this memory space")
    if config.mindmemos_embedding_dim and config.mindmemos_embedding_dim != embedding_identity["dimensions"]:
        raise HTTPException(status_code=409, detail="Embedding dimension is locked for this memory space")

    routers = _memory_provider_routers(chat_provider, request.chat_model, embedding_provider)
    payload = _memory_provider_binding_payload(current_user, routers)
    project_id = _mindmemos_project_id(current_user)
    if config.mindmemos_binding_id:
        data = _mindmemos_patch(
            current_user,
            f"/internal/v1/projects/{project_id}/provider-bindings/{config.mindmemos_binding_id}",
            {"routers": routers},
            scopes=["provider:write"],
        )
    else:
        data = _mindmemos_post(
            current_user,
            f"/internal/v1/projects/{project_id}/provider-bindings",
            payload,
            scopes=["provider:write"],
        )

    binding = data.get("data") or {}
    config.mindmemos_binding_id = str(binding.get("binding_id") or config.mindmemos_binding_id or "")
    config.mindmemos_chat_provider_id = chat_provider.id
    config.mindmemos_chat_model = request.chat_model
    config.mindmemos_embedding_provider_id = embedding_provider.id
    config.mindmemos_embedding_model = embedding_identity["model"]
    config.mindmemos_embedding_dim = embedding_identity["dimensions"]
    config.updated_time = datetime.now(UTC)
    db.add(config)
    db.commit()
    db.refresh(config)

    return get_memory_provider_binding(db, current_user)


def _memory_provider_routers(
    chat_provider: models.LLMProvider,
    chat_model: str,
    embedding_provider: models.EmbeddingProvider,
) -> dict[str, Any]:
    return {
        "chat_model_router": {
            "routing_strategy": "simple-shuffle",
            "endpoints": [_chat_endpoint(chat_provider, chat_model)],
        },
        "embed_model_router": {
            "routing_strategy": "simple-shuffle",
            "endpoints": [_embedding_endpoint(embedding_provider)],
        },
    }


def _memory_provider_binding_payload(current_user: models.User, routers: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": {"user_id": str(current_user.id)},
        "routers": routers,
    }


def _ensure_mindmemos_provider_binding(db: Session, current_user: models.User) -> None:
    """Ensure the remote MindMemOS provider binding exists before model-backed calls."""

    config = db.exec(select(models.UserMemoryConfig).where(models.UserMemoryConfig.user_id == current_user.id)).first()
    if config is None or not config.mindmemos_binding_id:
        raise HTTPException(status_code=400, detail="请先在记忆设置中绑定 Chat 与 Embedding 模型")

    project_id = _mindmemos_project_id(current_user)
    data = _mindmemos_get(
        current_user,
        f"/internal/v1/projects/{project_id}/provider-bindings",
        scopes=["provider:read"],
    )
    items = (data.get("data") or {}).get("items") or []
    existing = next(
        (
            item
            for item in items
            if isinstance(item, dict) and str(item.get("binding_id") or "") == config.mindmemos_binding_id
        ),
        None,
    )
    if existing is not None and not (
        config.mindmemos_chat_provider_id and config.mindmemos_chat_model and config.mindmemos_embedding_provider_id
    ):
        return

    if not (config.mindmemos_chat_provider_id and config.mindmemos_chat_model and config.mindmemos_embedding_provider_id):
        config.mindmemos_binding_id = ""
        config.updated_time = datetime.now(UTC)
        db.add(config)
        db.commit()
        raise HTTPException(status_code=400, detail="MindMemOS 模型绑定已失效，请重新绑定 Chat 与 Embedding 模型")

    chat_provider = db.get(models.LLMProvider, config.mindmemos_chat_provider_id)
    embedding_provider = db.get(models.EmbeddingProvider, config.mindmemos_embedding_provider_id)
    if chat_provider is None or embedding_provider is None:
        config.mindmemos_binding_id = ""
        config.updated_time = datetime.now(UTC)
        db.add(config)
        db.commit()
        raise HTTPException(status_code=400, detail="MindMemOS 绑定的模型配置不存在，请重新绑定")

    routers = _memory_provider_routers(chat_provider, config.mindmemos_chat_model, embedding_provider)
    if existing is not None:
        if _provider_routers_need_refresh(existing.get("routers") or {}, routers):
            _mindmemos_patch(
                current_user,
                f"/internal/v1/projects/{project_id}/provider-bindings/{config.mindmemos_binding_id}",
                {"routers": routers},
                scopes=["provider:write"],
            )
        return

    data = _mindmemos_post(
        current_user,
        f"/internal/v1/projects/{project_id}/provider-bindings",
        _memory_provider_binding_payload(current_user, routers),
        scopes=["provider:write"],
    )
    binding = data.get("data") or {}
    config.mindmemos_binding_id = str(binding.get("binding_id") or config.mindmemos_binding_id or "")
    config.updated_time = datetime.now(UTC)
    db.add(config)
    db.commit()
    db.refresh(config)


def _provider_routers_need_refresh(current: dict[str, Any], expected: dict[str, Any]) -> bool:
    for router_name in ("chat_model_router", "embed_model_router"):
        current_endpoints = ((current.get(router_name) or {}).get("endpoints") or [])
        expected_endpoints = ((expected.get(router_name) or {}).get("endpoints") or [])
        if len(current_endpoints) != len(expected_endpoints):
            return True
        for current_endpoint, expected_endpoint in zip(current_endpoints, expected_endpoints, strict=True):
            for key in ("model", "api_base", "api_key", "dimensions", "timeout", "num_retries"):
                if current_endpoint.get(key) != expected_endpoint.get(key):
                    return True
    return False


def _provider_accessible(provider: models.LLMProvider, current_user: models.User) -> bool:
    return bool(
        current_user.is_superuser
        or provider.user_id == current_user.id
        or (provider.is_builtin and provider.visible_to_all)
    )


def _mindmemos_model_name(provider_type: str, model: str) -> str:
    if "/" in model:
        return model
    if provider_type in {"openai", "openai_compatible", "local"}:
        return f"openai/{model}"
    if provider_type == "anthropic":
        return f"anthropic/{model}"
    if provider_type == "jina":
        return f"jina_ai/{model}"
    return model


def _chat_endpoint(provider: models.LLMProvider, model: str) -> dict[str, Any]:
    endpoint: dict[str, Any] = {
        "model": _mindmemos_model_name(str(provider.type.value), model),
        "api_key": provider.api_key or provider.auth_token,
        "timeout": _mindmemos_provider_timeout(provider.timeout),
        "num_retries": 1,
        "temperature": provider.temperature,
    }
    if provider.base_url:
        endpoint["api_base"] = provider.base_url.rstrip("/")
    return endpoint


def _embedding_identity(provider: models.EmbeddingProvider) -> dict[str, Any]:
    if provider.mode == models.EmbeddingMode.SPLIT:
        model = provider.text_model or provider.model
        provider_type = provider.text_type.value
    else:
        model = provider.model
        provider_type = provider.type.value
    return {
        "provider": provider_type,
        "model": _mindmemos_model_name(provider_type, model),
        "dimensions": provider.dim,
    }


def _embedding_endpoint(provider: models.EmbeddingProvider) -> dict[str, Any]:
    identity = _embedding_identity(provider)
    if provider.mode == models.EmbeddingMode.SPLIT:
        api_key = provider.text_api_key or provider.text_auth_token
        base_url = provider.text_base_url
        timeout = provider.timeout
    else:
        api_key = provider.api_key or provider.auth_token
        base_url = provider.base_url
        timeout = provider.timeout
    endpoint: dict[str, Any] = {
        "model": identity["model"],
        "api_key": api_key,
        "dimensions": identity["dimensions"],
        "timeout": _mindmemos_provider_timeout(timeout),
        "num_retries": 1,
    }
    if base_url:
        endpoint["api_base"] = base_url.rstrip("/")
    return endpoint


def _mindmemos_provider_timeout(timeout: float | int | None) -> int:
    """Bound dynamic provider calls so memory extraction errors surface quickly."""

    try:
        value = int(timeout or 30)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(value, 30))


def get_project_memory_config(
    db: Session,
    project_id: uuid.UUID,
    current_user: models.User,
) -> ProjectMemoryConfigResponse:
    """Get or create project-level memory defaults."""
    get_project_with_auth(db, project_id, current_user)
    stmt = select(models.ProjectMemoryConfig).where(models.ProjectMemoryConfig.project_id == project_id)
    config = db.exec(stmt).first()
    if config is None:
        user_defaults = get_user_memory_config(db, current_user)
        config = models.ProjectMemoryConfig(
            project_id=project_id,
            enabled=user_defaults.enabled,
            include_user_memory=user_defaults.include_user_memory,
            include_project_memory=user_defaults.include_project_memory,
            include_task_memory=user_defaults.include_task_memory,
            user_memory_limit=user_defaults.user_memory_limit,
            project_memory_limit=user_defaults.project_memory_limit,
            task_memory_limit=user_defaults.task_memory_limit,
            mindmemos_search_strategy=user_defaults.mindmemos_search_strategy,
            mindmemos_rerank=user_defaults.mindmemos_rerank,
            mindmemos_score_threshold=user_defaults.mindmemos_score_threshold
            if user_defaults.mindmemos_rerank
            else None,
            mindmemos_fail_open=user_defaults.mindmemos_fail_open,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    data = _copy_user_binding_fields(_with_system_config(config), get_user_memory_config(db, current_user))
    return ProjectMemoryConfigResponse.model_validate(data)


def update_project_memory_config(
    db: Session,
    project_id: uuid.UUID,
    current_user: models.User,
    request: MemoryConfigUpdate,
) -> ProjectMemoryConfigResponse:
    """Update project-level memory defaults."""
    get_project_memory_config(db, project_id, current_user)
    config = db.exec(
        select(models.ProjectMemoryConfig).where(models.ProjectMemoryConfig.project_id == project_id)
    ).one()
    config.sqlmodel_update(request.model_dump(exclude_unset=True))
    _normalize_memory_config_rerank_threshold(config)
    config.updated_time = datetime.now(UTC)
    db.add(config)
    db.commit()
    db.refresh(config)
    data = _copy_user_binding_fields(_with_system_config(config), get_user_memory_config(db, current_user))
    return ProjectMemoryConfigResponse.model_validate(data)


def _resolve_card_scope(
    db: Session,
    current_user: models.User,
    scope: MemoryScope,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> tuple[dict[str, str], uuid.UUID | None, uuid.UUID | None]:
    if scope == "user":
        return _base_mindmemos_scope(current_user, "global", "global"), None, None
    if scope == "project":
        if project_id is None:
            raise HTTPException(status_code=400, detail="project_id is required for project memory")
        project = get_project_with_auth(db, project_id, current_user)
        return _base_mindmemos_scope(current_user, str(project.id), "project"), project.id, None
    if scope == "task":
        if task_id is None:
            raise HTTPException(status_code=400, detail="task_id is required for task memory")
        task = get_task_with_auth(db, task_id, current_user)
        root_task_id = task.group_id or task.id
        return _base_mindmemos_scope(current_user, str(root_task_id), "task"), task.project_id, root_task_id
    raise HTTPException(status_code=400, detail=f"Unsupported memory scope: {scope}")


def list_memory_cards(
    db: Session,
    current_user: models.User,
    *,
    scope: MemoryScope,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> MemoryCardPageResponse:
    """List MindMemOS cards for a user/project/task scope."""
    _require_mindmemos_memory_enabled()
    scope_data, _, _ = _resolve_card_scope(db, current_user, scope, project_id, task_id)
    return _remote_list_cards(current_user, scope_data, page=1, page_size=20)


def list_memory_cards_page(
    db: Session,
    current_user: models.User,
    *,
    scope: MemoryScope,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> MemoryCardPageResponse:
    """List MindMemOS cards with remote pagination."""
    _require_mindmemos_memory_enabled()
    scope_data, _, _ = _resolve_card_scope(db, current_user, scope, project_id, task_id)
    return _remote_list_cards(
        current_user,
        scope_data,
        page=page,
        page_size=page_size,
    )


def extract_memory_cards(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    request: MemoryCardExtractionRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> MemoryCardExtractionResponse:
    """Extract memory cards from raw text and archive them until the user enables them."""
    _require_mindmemos_memory_enabled()
    _ensure_mindmemos_provider_binding(db, current_user)
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="记忆描述不能为空")
    scope_data, resolved_project_id, resolved_task_id = _resolve_card_scope(
        db,
        current_user,
        scope,
        project_id,
        task_id,
    )
    preview_id = f"llm4ad-preview-{uuid.uuid4().hex}"
    items = _remote_extract_preview_cards(
        current_user,
        scope_data,
        scope,
        content=content,
        preview_id=preview_id,
        prompt_language=request.prompt_language,
        project_id=resolved_project_id,
        task_id=resolved_task_id,
    )
    message = "" if items else "MindMemOS 没有从这段描述中提取出可保存的记忆"
    return MemoryCardExtractionResponse(preview_id=preview_id, items=items, message=message)


async def stream_extract_memory_cards(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    request: MemoryCardExtractionRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Extract memory cards through MindMemOS SSE and emit progress events."""
    _require_mindmemos_memory_enabled()
    _ensure_mindmemos_provider_binding(db, current_user)
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="记忆描述不能为空")
    scope_data, resolved_project_id, resolved_task_id = _resolve_card_scope(
        db,
        current_user,
        scope,
        project_id,
        task_id,
    )
    preview_id = f"llm4ad-preview-{uuid.uuid4().hex}"
    payload = _memory_add_preview_payload(
        scope_data,
        scope,
        content=content,
        preview_id=preview_id,
        prompt_language=request.prompt_language,
        project_id=resolved_project_id,
        task_id=resolved_task_id,
    )
    async for event in _mindmemos_stream_post(
        current_user,
        "/v1/memory/add/stream",
        payload,
        scopes=["memory:write"],
    ):
        event_name = str(event.get("event") or "progress")
        if event_name != "completed":
            yield event
            if event_name in {"error", "cancelled"}:
                return
            continue

        memories = ((event.get("data") or {}).get("memories") or [])
        yield _memory_stream_progress_event(
            "finalizing",
            percent=96,
            zh="正在整理记忆预览",
            en="Preparing memory preview",
        )
        items = _remote_generated_cards_from_add_memories(
            current_user,
            scope_data,
            scope,
            memories,
            generation_id=preview_id,
            project_id=resolved_project_id,
            task_id=resolved_task_id,
        )
        yield _memory_stream_completed_event(preview_id=preview_id, items=items)
        return


def commit_memory_card_extraction(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    preview_id: str,
    request: MemoryCardExtractionCommitRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> MemoryCardExtractionResponse:
    """Activate selected generated cards and leave unselected cards archived."""
    _require_mindmemos_memory_enabled()
    _ensure_mindmemos_provider_binding(db, current_user)
    scope_data, resolved_project_id, resolved_task_id = _resolve_card_scope(
        db,
        current_user,
        scope,
        project_id,
        task_id,
    )
    selected_ids = _unique_ids(request.selected_ids)
    cards_by_id = _remote_fetch_cards_by_ids(
        current_user,
        scope_data,
        selected_ids,
    )

    activated: list[MemoryCardResponse] = []
    for memory_id in selected_ids:
        card = cards_by_id.get(memory_id)
        if card is None:
            raise HTTPException(status_code=404, detail=f"记忆不存在或不属于当前范围: {memory_id}")
        metadata = {
            **card.metadata,
            **_generated_card_metadata(
                generation_id=str(card.metadata.get("llm4ad_generation_id") or preview_id),
                scope_name=scope,
                project_id=resolved_project_id,
                task_id=resolved_task_id,
                enabled=True,
            ),
            "memory_type": card.type,
            "title": card.title,
            "tags": card.tags,
        }
        _remote_update_card_status(
            current_user,
            memory_id,
            scope_data=scope_data,
            status="active",
            metadata_patch=metadata,
        )
        activated.append(card.model_copy(update={"enabled": True, "metadata": metadata}))

    return MemoryCardExtractionResponse(preview_id=preview_id, items=activated)


def discard_memory_card_extraction(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    preview_id: str,
    memory_ids: list[str],
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> None:
    """Hard-delete generated cards when the user chooses not to keep them."""
    del preview_id
    _require_mindmemos_memory_enabled()
    scope_data, _, _ = _resolve_card_scope(db, current_user, scope, project_id, task_id)
    ids = _unique_ids(memory_ids)
    cards_by_id = _remote_fetch_cards_by_ids(current_user, scope_data, ids)
    missing_ids = [memory_id for memory_id in ids if memory_id not in cards_by_id]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"记忆不存在或不属于当前范围: {missing_ids[0]}")
    for memory_id in ids:
        _remote_delete_card(current_user, memory_id)


def upsert_memory_card(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    request: MemoryCardUpsertRequest,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> MemoryCardResponse:
    """Create or update a MindMemOS card for a user/project/task scope."""
    _require_mindmemos_memory_enabled()
    _ensure_mindmemos_provider_binding(db, current_user)
    scope_data, resolved_project_id, resolved_task_id = _resolve_card_scope(
        db,
        current_user,
        scope,
        project_id,
        task_id,
    )
    return _remote_upsert_card(
        current_user,
        scope_data,
        scope,
        request,
        project_id=resolved_project_id,
        task_id=resolved_task_id,
    )


def update_memory_card_status(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    memory_id: str,
    enabled: bool,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> MemoryCardResponse:
    """Enable or archive a MindMemOS card without rewriting its content."""
    _require_mindmemos_memory_enabled()
    scope_data, _, _ = _resolve_card_scope(db, current_user, scope, project_id, task_id)
    existing = _remote_fetch_cards_by_ids(current_user, scope_data, [memory_id]).get(memory_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记忆不存在或不属于当前范围")

    status = "active" if enabled else "archived"
    metadata_patch = {"enabled": enabled}
    _remote_update_card_status(
        current_user,
        memory_id,
        scope_data=scope_data,
        status=status,
        metadata_patch=metadata_patch,
    )
    updated = _remote_fetch_cards_by_ids(current_user, scope_data, [memory_id]).get(memory_id)
    if updated is not None:
        return updated
    return existing.model_copy(
        update={
            "enabled": enabled,
            "metadata": {**existing.metadata, **metadata_patch},
            "readonly": existing.readonly.model_copy(update={"status": status}),
        }
    )


def delete_memory_card(
    db: Session,
    *,
    current_user: models.User,
    scope: MemoryScope,
    memory_id: str,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> None:
    """Delete a MindMemOS card after checking scope authorization."""
    _require_mindmemos_memory_enabled()
    scope_data, _, _ = _resolve_card_scope(db, current_user, scope, project_id, task_id)
    if memory_id not in _remote_fetch_cards_by_ids(current_user, scope_data, [memory_id]):
        raise HTTPException(status_code=404, detail="记忆不存在或不属于当前范围")
    _remote_delete_card(current_user, memory_id)


def list_task_memory_cards(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> MemoryCardPageResponse:
    """List task memory cards stored in MindMemOS."""
    return list_memory_cards(db, current_user, scope="task", task_id=task_id)


def upsert_task_memory_card(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    request: MemoryCardUpsertRequest,
) -> MemoryCardResponse:
    """Create or update a task memory card in MindMemOS."""
    return upsert_memory_card(
        db,
        current_user=current_user,
        scope="task",
        task_id=task_id,
        request=request,
    )


def update_task_memory_card_status(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    memory_id: str,
    *,
    enabled: bool,
) -> MemoryCardResponse:
    """Enable or archive a task memory card without triggering content embedding."""
    return update_memory_card_status(
        db,
        current_user=current_user,
        scope="task",
        task_id=task_id,
        memory_id=memory_id,
        enabled=enabled,
    )


def delete_task_memory_card(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    memory_id: str,
) -> None:
    """Delete a task memory card from MindMemOS."""
    delete_memory_card(
        db,
        current_user=current_user,
        scope="task",
        task_id=task_id,
        memory_id=memory_id,
    )
