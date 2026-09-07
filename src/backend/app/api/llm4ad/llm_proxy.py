"""LLM 凭据代理（透明反向代理）端点。

演化任务容器内的 llm4ad / 用户评测脚本不再持有真实大模型凭据，只持有一次性
代理 token，并把请求发往本端点。本端点：

1. 从请求头取出代理 token（OpenAI 风格 ``Authorization: Bearer`` 或 Anthropic
   风格 ``x-api-key``）。
2. 经 :mod:`app.services.credential_broker` 校验 token 并解密还原真实凭据
   （type/base_url/api_key/auth_token）——一次 Redis 读取即可，无需查数据库。
3. 用真实凭据重写鉴权头，将原始请求（方法、路径、查询、请求体）透明转发到真实
   上游，并把响应（含 SSE 流）原样回传。

由于只换鉴权头、不解析请求体，天然兼容 openai / openai_compatible / anthropic
等协议。真实 key 始终不进入容器，从根本上消除评测脚本窃取 provider key 的路径。

鉴权方式即代理 token 本身（不可预测的随机串 + 短 TTL + 任务结束吊销）；本端点
不应暴露到公网，应仅在 Docker 用户网络内可达（部署侧加固）。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse

from app.services import credential_broker

router = APIRouter(prefix="/llmproxy", tags=["llm4ad.llmproxy"])

# 转发请求时需丢弃的请求头（逐跳头、由 httpx 重算的头、以及原鉴权头）。
_DROP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "authorization",
    "x-api-key",
}
# 回传响应时需丢弃的响应头（逐跳 / 由 Starlette 重算的头）。
_DROP_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
}

# 各 provider 类型在 base_url 缺省时的默认上游地址（兜底）。
_DEFAULT_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "openai_compatible": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


def _extract_proxy_token(request: Request) -> str | None:
    """从请求头取出代理 token，兼容 OpenAI / Anthropic 两种鉴权位置。"""
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    return None


def _build_auth_headers(provider_type: str, api_key: str, auth_token: str) -> dict[str, str]:
    """按 provider 类型构造发往真实上游的鉴权头。"""
    headers: dict[str, str] = {}
    if provider_type == "anthropic":
        # Anthropic 用 x-api-key；部分网关改用 Bearer auth_token。两种鉴权机制可能
        # 只配其一：仅有 auth_token 时也回填 x-api-key（官方 API 只认 x-api-key，
        # 而只认 Bearer 的网关会忽略多余的 x-api-key），避免单配 auth_token 时漏发
        # x-api-key 导致官方 API 401。
        key = api_key or auth_token
        if key:
            headers["x-api-key"] = key
        if auth_token:
            headers["authorization"] = f"Bearer {auth_token}"
    else:
        # openai / openai_compatible：Bearer api_key。
        key = api_key or auth_token
        if key:
            headers["authorization"] = f"Bearer {key}"
    return headers


def _join_upstream_url(base_url: str, path: str) -> str:
    """Join a provider base URL with a proxy path without duplicating ``/v1``.

    OpenAI clients normally receive a base URL ending in ``/v1``.  Protocol
    adapters such as cc-switch send the full ``/v1/chat/completions`` path, so
    a plain string join would otherwise produce ``/v1/v1/chat/completions``.
    """
    normalized_base = base_url.rstrip("/")
    normalized_path = path.lstrip("/")
    if normalized_base.lower().endswith("/v1") and normalized_path.lower().startswith("v1/"):
        normalized_path = normalized_path[3:]
    return f"{normalized_base}/{normalized_path}" if normalized_path else normalized_base


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_llm(path: str, request: Request) -> Response:
    """透明反向代理：校验代理 token 后用真实凭据转发到上游大模型。

    Args:
        path: 上游 API 子路径（如 ``chat/completions`` 或 ``v1/messages``）。
        request: 原始入站请求。

    Returns:
        透传上游响应的 ``StreamingResponse``，或鉴权失败时的错误响应。
    """
    token = _extract_proxy_token(request)
    creds = credential_broker.resolve_token(token) if token else None
    if creds is None:
        return JSONResponse(
            {"error": "invalid or expired proxy token"},
            status_code=401,
        )

    provider_type = creds.get("type", "openai_compatible")
    base_url = (creds.get("base_url") or "").strip() or _DEFAULT_BASE_URL.get(provider_type, "")
    base_url = base_url.rstrip("/")
    if not base_url:
        return JSONResponse(
            {"error": "upstream base_url unavailable"},
            status_code=502,
        )

    upstream_url = _join_upstream_url(base_url, path)

    # 组装转发请求头：保留原头，剔除逐跳/鉴权头，再注入真实凭据。
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS
    }
    fwd_headers.update(
        _build_auth_headers(provider_type, creds.get("api_key", ""), creds.get("auth_token", ""))
    )

    body = await request.body()

    timeout = httpx.Timeout(creds.get("timeout") or 60.0, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream_req = client.build_request(
            request.method,
            upstream_url,
            headers=fwd_headers,
            # multi_items() 保留重复 query key（如 Azure 的 api-version 等）。
            params=list(request.query_params.multi_items()),
            content=body,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        return JSONResponse(
            {"error": f"upstream request failed: {exc}"},
            status_code=502,
        )

    resp_headers = {
        k: v
        for k, v in upstream_resp.headers.items()
        if k.lower() not in _DROP_RESPONSE_HEADERS
    }

    async def _stream():
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        _stream(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type"),
        background=BackgroundTask(client.aclose),
    )
