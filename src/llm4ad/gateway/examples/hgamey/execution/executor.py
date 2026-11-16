"""Optional executor CLI and leased device subprocess entrypoint."""

import argparse
import json
import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from .api import http_call, make_handler
from .device import execute_on_device, preparation_not_started
from .queue import Engine
from .storage import read_json, write_json


def main():
    """Run the executor CLI or execute one inherited device lease."""
    if len(sys.argv) == 3 and sys.argv[1] == "_restore":
        from .recovery import restore_on_device

        try:
            proof = restore_on_device(Path(sys.argv[2]))
        except Exception as error:
            proof = {"job_id": Path(sys.argv[2]).name, "restored": False, "error": str(error)}
        print(json.dumps(proof), flush=True)
        return 0 if proof["restored"] else 2
    if len(sys.argv) == 3 and sys.argv[1] == "_device":
        try:
            return execute_on_device(Path(sys.argv[2]))
        except Exception as error:
            folder = Path(sys.argv[2])
            error_file = "worker-error.json" if (folder / "result.json").exists() else "result.json"
            report = {"status": "ERROR", "error": str(error)}
            if preparation_not_started(folder):
                report.update(preparation=read_json(folder / "device-preparation.json"), external_cleanup_confirmed=True)
            write_json(folder / error_file, report)
            print(str(error), file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("EXECUTOR_URL", "http://127.0.0.1:8767"))
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("serve", "devices", "health", "jobs"):
        commands.add_parser(name)
    submit = commands.add_parser("submit")
    submit.add_argument("file")
    submit.add_argument("--wait", action="store_true")
    for name in ("status", "result", "logs"):
        commands.add_parser(name).add_argument("id")
    args = parser.parse_args()
    if args.action == "serve":
        from .backend import start_runtime

        start_runtime(os.environ.get("EXECUTOR_DATA", "/data"), os.environ.get("EXECUTOR_CONFIG", "/config/devices.json"))
        engine = Engine(os.environ.get("EXECUTOR_DATA", "/data"), os.environ.get("EXECUTOR_CONFIG", "/config/devices.json"))
        try:
            class ExecutorHTTPServer(ThreadingHTTPServer):
                """Use the existing server loop to reconcile durable completions."""

                def service_actions(self):
                    """Retry receipt commits and resource probes without a worker loop."""
                    engine.poll()

            server = ExecutorHTTPServer(
                (os.environ.get("EXECUTOR_BIND", "127.0.0.1"), int(os.environ.get("EXECUTOR_PORT", "8767"))), make_handler(engine)
            )

            def stop(*unused):
                engine.stopping.set()
                threading.Thread(target=server.shutdown, daemon=True).start()

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            print("Wireless evaluation executor listening on", server.server_address, flush=True)
            try:
                server.serve_forever(poll_interval=0.2)
            finally:
                server.server_close()
        finally:
            engine.close()
        return 0
    if args.action == "submit":
        body = json.load(sys.stdin) if args.file == "-" else read_json(args.file)
        job = http_call(args.url, "/v1/jobs", body)
        print(json.dumps(job, ensure_ascii=False, indent=2), flush=True)
        if not args.wait:
            return 0
        while job["state"] in ("queued", "running", "cancel_requested"):
            time.sleep(1)
            job = http_call(args.url, "/v1/jobs/" + job["id"])
        print(json.dumps(job, ensure_ascii=False, indent=2))
        if job["state"] == "completed":
            print(json.dumps(http_call(args.url, "/v1/jobs/" + job["id"] + "/result"), ensure_ascii=False, indent=2))
            return 0
        return 2
    path = {"devices": "/v1/devices", "health": "/health", "jobs": "/v1/jobs"}.get(args.action)
    if path is None:
        path = "/v1/jobs/" + args.id + ("" if args.action == "status" else "/" + args.action)
    print(json.dumps(http_call(args.url, path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
