"""Authenticated local bridge from experiment containers to the host Codex CLI."""

import argparse
import json
import secrets
import shutil
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp import web

from llm4ad.infra.codex_cli import _communicate, _login_env
from llm4ad.infra.codex_models import read_models
from llm4ad.infra.provider import ChatMessage
from llm4ad.infra.provider.codex_cli import CodexCLIProvider


async def login_status(binary_path: str = "codex") -> dict:
    """Inspect CLI availability and login without making a model request."""
    binary = shutil.which(binary_path)
    result = {
        "installed": bool(binary),
        "authenticated": False,
        "version": "",
        "models": [],
        "default_model": "",
        "models_available": False,
    }
    if binary is None:
        return result
    with TemporaryDirectory(prefix="llm4ad-codex-status-") as temp:
        code, out, err = await _communicate(
            [binary, "login", "status"],
            _login_env(),
            Path(temp),
            15,
        )
        result["authenticated"] = code == 0 and "Logged in using ChatGPT" in out + err
        code, out, _ = await _communicate([binary, "--version"], _login_env(), Path(temp), 15)
        if code == 0:
            result["version"] = out.strip()[:100]
    if result["authenticated"]:
        try:
            result.update(await read_models(binary))
            result["models_available"] = bool(result["models"])
        except (OSError, TimeoutError, RuntimeError, ValueError, KeyError):
            pass
    return result


def create_app(token: str, binary_path: str = "codex") -> web.Application:
    """Create a bridge that exposes no account tokens or arbitrary shell endpoint."""
    if len(token) < 32:
        raise ValueError("The bridge token must contain at least 32 characters.")

    @web.middleware
    async def authenticate(request, handler):
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            return web.json_response({"error": {"message": "Unauthorized"}}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=4 * 1024 * 1024)

    async def status(request):
        try:
            return web.json_response(await login_status(binary_path))
        except (OSError, TimeoutError):
            return web.json_response(
                {"installed": True, "authenticated": False, "version": ""}, status=503
            )

    async def completions(request):
        try:
            data = await request.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("messages"), list)
                or not data["messages"]
            ):
                raise ValueError("messages must be a nonempty list")
            if data.get("tools") or data.get("functions"):
                raise ValueError("Local Codex supports text experiments, not API function calling.")
            messages = [ChatMessage.model_validate(message) for message in data["messages"]]
            response_format = data.get("response_format") or {}
            if not isinstance(response_format, dict) or response_format.get("type") not in (
                None,
                "text",
                "json_object",
            ):
                raise ValueError("Supported response formats: text, json_object")
            if response_format.get("type") == "json_object":
                messages.insert(
                    0, ChatMessage(role="system", content="Return only a valid JSON object.")
                )
            model = data.get("model") or "codex-default"
            if not isinstance(model, str) or len(model) > 255:
                raise ValueError("Invalid model name")
            provider = CodexCLIProvider(
                {
                    "model": "" if model == "codex-default" else model,
                    "binary_path": binary_path,
                    "timeout": 600,
                }
            )
            result = await provider.chat(messages)
        except (ValueError, NotImplementedError) as exc:
            return web.json_response(
                {"error": {"message": str(exc), "type": "invalid_request_error"}}, status=400
            )
        except TimeoutError:
            return web.json_response(
                {"error": {"message": "Codex timed out", "type": "timeout"}}, status=504
            )
        except (RuntimeError, OSError) as exc:
            return web.json_response(
                {"error": {"message": str(exc), "type": "codex_error"}}, status=503
            )
        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        common = {"id": response_id, "created": int(time.time()), "model": model}
        usage = {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        }
        if data.get("stream"):
            response = web.StreamResponse(
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                }
            )
            await response.prepare(request)
            for delta, finish in [
                ({"role": "assistant", "content": result.text}, None),
                ({}, "stop"),
            ]:
                chunk = {
                    **common,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                if finish:
                    chunk["usage"] = usage
                await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        return web.json_response(
            {
                **common,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": result.text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            }
        )

    app.router.add_get("/status", status)
    app.router.add_post("/v1/chat/completions", completions)
    return app


def main() -> None:
    """Start the bridge as the host user who is signed into Codex."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18143)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--binary", default="codex")
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    web.run_app(
        create_app(token, args.binary), host=args.host, port=args.port, handler_cancellation=True
    )


if __name__ == "__main__":
    main()
