"""Shared local health endpoint, server lifecycle and container probe."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .storage import persist


class HealthServer:
    """Serve SDK health alongside an optional local backend API."""

    def __init__(self, root: Path, callback, *, port: int = 0, handler=None):
        """Bind a loopback listener and publish its address under the owned state root."""
        base = handler or BaseHTTPRequestHandler

        class Handler(base):
            """Preserve optional backend endpoints while sharing the health contract."""

            def do_GET(self):  # noqa: N802
                """Report readiness without running another backend experiment."""
                if self.path != "/v1/service/health":
                    inherited = getattr(super(), "do_GET", None)
                    return inherited() if inherited else self.send_error(404)
                value = callback()
                content = json.dumps(value, allow_nan=False).encode()
                self.send_response(200 if value.get("ready") else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format, *args):  # noqa: A002
                """Keep routine container probes out of evaluator execution logs."""

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/v1/service/health"
        try:
            persist(root / "health-endpoint.json", {"url": self.endpoint})
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Stop accepting local requests and join the owned listener thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def probe(root: Path) -> dict:
    """Query only the live loopback endpoint registered by this gateway service."""
    try:
        endpoint = json.loads((root / "health-endpoint.json").read_text(encoding="utf-8"))["url"]
        url = urlsplit(endpoint)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.path != "/v1/service/health":
            raise ValueError("Invalid local gateway health endpoint")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(endpoint, timeout=4) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            return json.load(error)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"ready": False, "error": str(error)}
