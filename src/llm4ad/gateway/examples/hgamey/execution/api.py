"""Private executor HTTP API and operator client."""

import hashlib
import json
import queue
import re
import shutil
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

from .contracts import SubmissionConflictError
from .devices import load_devices
from .storage import read_json


def make_handler(engine):
    """Bind the private executor API to one serialized device queue."""
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            try:
                if self.path in ("/health", "/v1/health"):
                    return self.reply(200, engine.health())
                if self.path == "/v1/devices":
                    return self.reply(
                        200,
                        {
                            "devices": load_devices(engine.config),
                            "selection": "exactly one per job; endpoint and physical serial are verified",
                        },
                    )
                if self.path == "/v1/jobs":
                    jobs = [read_json(p) for p in engine.jobs.glob("*/job.json")]
                    return self.reply(200, {"jobs": sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:100]})
                lookup = re.fullmatch(r"/v1/jobs/by-key/([0-9a-f]{64})", self.path)
                if lookup:
                    return self.reply(200, engine.get_by_key(lookup.group(1)))
                artifact = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})/artifacts/([0-9a-f]{64})", self.path)
                if artifact:
                    key, digest = artifact.groups()
                    engine.get(key)
                    folder = engine.jobs / key / "evidence"
                    result = read_json(engine.jobs / key / "result.json")
                    item = next((item for item in result.get("artifacts", []) if item.get("sha256") == digest), None)
                    if item is None:
                        return self.reply(404, {"error": "unknown evidence"})
                    path = (folder / item["name"]).resolve()
                    if not path.is_relative_to(folder.resolve()) or not path.is_file():
                        return self.reply(404, {"error": "unknown evidence"})
                    if path.stat().st_size != item["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        return self.reply(409, {"error": "evidence changed"})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(item["size"]))
                    self.end_headers()
                    with path.open("rb") as stream:
                        shutil.copyfileobj(stream, self.wfile)
                    return
                match = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})(?:/(result|logs))?", self.path)
                if not match:
                    return self.reply(404, {"error": "unknown endpoint"})
                key, part = match.groups()
                job = engine.get(key)
                if part == "result":
                    path = engine.jobs / key / "result.json"
                    return self.reply(
                        200 if path.exists() else 409,
                        read_json(path) if path.exists() else {"error": "result not available", "job": job},
                    )
                if part == "logs":
                    logs = {}
                    for p in (engine.jobs / key).iterdir():
                        if p.suffix in (".stdout", ".stderr", ".txt"):
                            with p.open("rb") as f:
                                f.seek(max(0, p.stat().st_size - 65536))
                                logs[p.name] = f.read(65536).decode(errors="replace")
                    return self.reply(200, logs)
                return self.reply(200, job)
            except KeyError as error:
                self.reply(404, {"error": str(error)})
            except (ValueError, OSError) as error:
                self.reply(503, {"error": str(error)})

        def do_POST(self):
            resolve = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})/resolve", self.path)
            if resolve:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        return self.reply(413, {"error": "confirmation body must be 1..4096 bytes"})
                    confirmation = json.loads(self.rfile.read(size))
                    return self.reply(200, engine.resolve(resolve.group(1), confirmation))
                except KeyError as error:
                    return self.reply(404, {"error": str(error)})
                except SubmissionConflictError as error:
                    return self.reply(409, {"error": str(error)})
                except (ValueError, TypeError) as error:
                    return self.reply(400, {"error": str(error)})
                except OSError as error:
                    return self.reply(503, {"error": str(error)})
            cancel = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})/cancel", self.path)
            if cancel:
                try:
                    return self.reply(200, engine.cancel(cancel.group(1)))
                except KeyError as error:
                    return self.reply(404, {"error": str(error)})
                except (ValueError, OSError) as error:
                    return self.reply(503, {"error": str(error)})
            if self.path != "/v1/jobs":
                return self.reply(404, {"error": "unknown endpoint"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1048576:
                    return self.reply(413, {"error": "body must be 1..1048576 bytes"})
                return self.reply(202, engine.submit(json.loads(self.rfile.read(size))))
            except SubmissionConflictError as error:
                self.reply(409, {"error": str(error)})
            except queue.Full:
                self.reply(429, {"error": "executor queue is full"})
            except (ValueError, TypeError, OSError) as error:
                self.reply(400, {"error": str(error)})
            except RuntimeError as error:
                self.reply(503, {"error": str(error)})

    return Handler


def http_call(url, path, body=None):
    # Do not inherit system HTTP proxies for local executor requests.
    """Call the local executor without inheriting outbound proxy settings."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode()) from None
