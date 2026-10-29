"""Read picker models from the installed Codex app-server protocol."""

import asyncio
import json
import os
import signal
from tempfile import TemporaryDirectory

from llm4ad.infra.codex_cli import _login_env


async def read_models(binary_path: str = "codex", timeout: float = 20) -> dict:
    """Read model metadata without starting a thread or an inference turn.

    Args:
        binary_path: Installed Codex executable.
        timeout: Maximum time to initialize and read the catalog.

    Returns:
        Picker-visible models and the effective configured default model.
    """
    with TemporaryDirectory(prefix="llm4ad-codex-models-") as cwd:
        process = await asyncio.create_subprocess_exec(
            binary_path,
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            'model_provider="openai"',
            cwd=cwd,
            env=_login_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name == "posix",
            limit=4 * 1024 * 1024,
        )
        writer, reader = process.stdin, process.stdout
        assert writer is not None and reader is not None
        request_id = 0

        async def request(method: str, params: dict) -> dict:
            nonlocal request_id
            request_id += 1
            writer.write(
                (json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode()
            )
            await writer.drain()
            while line := await reader.readline():
                response = json.loads(line)
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise RuntimeError(f"Codex {method} failed")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"Codex {method} returned an invalid result")
                return result
            raise RuntimeError("Codex model discovery ended before replying")

        try:
            async with asyncio.timeout(timeout):
                await request(
                    "initialize",
                    {"clientInfo": {"name": "llm4ad", "title": "LLM4AD", "version": "1.0"}},
                )
                writer.write(b'{"method":"initialized"}\n')
                await writer.drain()
                config = await request("config/read", {"includeLayers": False})
                default_model = config.get("config", {}).get("model") or ""
                models = {}
                cursor = None
                while True:
                    page = await request(
                        "model/list", {"limit": 100, "includeHidden": False, "cursor": cursor}
                    )
                    for item in page.get("data", []):
                        model = item.get("model")
                        if not isinstance(model, str) or not model or item.get("hidden"):
                            continue
                        models[model] = {"id": model, "name": item.get("displayName") or model}
                        if not default_model and item.get("isDefault"):
                            default_model = model
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                return {"models": list(models.values()), "default_model": default_model}
        finally:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.returncode is None:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
