"""Small existing-owner client shared by gray reference and quality recipes."""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

ACTION = "phonefarm/gray-control-action@1"
RECEIPT = "phonefarm/gray-control-action-result@1"


class Failure(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def emit(value, *, stream=sys.stdout):
    print(json.dumps(value, ensure_ascii=False), file=stream, flush=True)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def stamp(value):
    if not isinstance(value, str):
        raise Failure("invalid_response", "missing timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return parsed.timestamp()
    except ValueError as error:
        raise Failure("invalid_response", "invalid timestamp") from error


class Client:
    def __init__(self, args):
        self.args = args
        self.env = os.environ.copy()
        if args.job:
            if not re.fullmatch(r"[0-9a-f]{12}", args.job):
                raise Failure("invalid_locator", "job must be 12 lowercase hexadecimal characters")
            url = args.node_url or os.environ.get("PF_NODE_URL")
            if not url:
                raise Failure("invalid_locator", "remote mode requires --node-url or explicit PF_NODE_URL")
            parts = urlsplit(url)
            if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                raise Failure("invalid_locator", "node URL must be an explicit HTTP(S) endpoint without embedded credentials/query")
            self.env["PF_NODE_URL"] = url.rstrip("/")
        elif args.node_url:
            raise Failure("invalid_locator", "--node-url is only valid with --job")

    def invoke(self, command, request=None):
        try:
            result = subprocess.run(command, input=None if request is None else json.dumps(request),
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=self.env, timeout=40)
        except subprocess.TimeoutExpired as error:
            raise Failure("tool_timeout", "client command timed out; its outcome is not inferred") from error
        except OSError as error:
            raise Failure("tool_unavailable", str(error)) from error
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError("not an object")
        except ValueError as error:
            raise Failure("invalid_response", "tool did not return a JSON object; stderr is not parsed as status") from error
        return result.returncode, value

    def decode(self, code, value, *, missing=False):
        if code == 0:
            return value
        if self.args.job:
            detail = value.get("error")
            if not isinstance(detail, dict):
                raise Failure("invalid_response", "remote error lacks structured error data")
            if missing and value.get("http_status") == 404 and detail.get("code") == "not_found":
                return None
            raise Failure("status_unavailable", str(detail.get("message", "remote request failed")),
                          remote_code=detail.get("code"), http_status=value.get("http_status"))
        if (missing and code == 2 and value.get("schema") == "phonefarm/gray-control-error@1"
                and value.get("code") == "not_found"):
            return None
        raise Failure("status_unavailable", str(value.get("error", "local request failed")),
                      local_code=value.get("code"))

    def status(self, request_id=None):
        if self.args.job:
            command = [self.args.pf_node, "session-status", self.args.job]
        else:
            command = [self.args.phonefarm, "gray-layer", "session-status", "--out", self.args.session_out]
        if request_id:
            command += ["--request-id", request_id]
        command += ["--json"]
        code, value = self.invoke(command)
        return self.decode(code, value, missing=request_id is not None)

    def post(self, request):
        command = ([self.args.pf_node, "session-action", self.args.job]
                   if self.args.job else
                   [self.args.phonefarm, "gray-layer", "session-action", "--out", self.args.session_out])
        command += ["--request", "-"]
        if not self.args.job:
            command += ["--json"]
        code, value = self.invoke(command, request)
        if code != 0:
            raise Failure("action_unconfirmed", "action client returned nonzero; only its original receipt may resolve it")
        return value



class SessionInvocation:
    def __init__(self, args, profile, client, *, prefix, actions, capabilities):
        self.args, self.profile, self.client = args, profile, client
        self.actions, self.capabilities = set(actions), capabilities
        self.initial = client.status()
        descriptor = self.initial
        if (descriptor.get("schema") != "phonefarm/gray-control@1" or descriptor.get("producer") != "phonefarm"
                or not isinstance(descriptor.get("session_id"), str)
                or descriptor.get("package") != profile["package"]
                or (args.job and descriptor.get("job_id") != args.job)):
            raise Failure("session_mismatch", "descriptor does not identify the requested session/package")
        self.session_id = descriptor["session_id"]
        self.epoch = descriptor.get("controller", {}).get("epoch")
        if not integer(self.epoch, 0, 2**64 - 1):
            raise Failure("invalid_response", "descriptor lacks a valid control epoch")
        self.prefix, self.last_request = prefix, None

    def request_id(self, step):
        return self.prefix + step

    def receipt(self, request_id):
        value = self.client.status(request_id)
        if value is not None:
            self.validate_receipt(value, request_id)
        return value

    def validate_receipt(self, receipt, request_id):
        if (receipt.get("schema") != RECEIPT or receipt.get("session_id") != self.session_id
                or receipt.get("request_id") != request_id):
            raise Failure("invalid_response", "action receipt identity mismatch", request_id=request_id)

    def guard(self, minimum_seconds=0):
        until = time.monotonic() + 2
        while True:
            d = self.client.status()
            if (d.get("session_id") != self.session_id or d.get("package") != self.profile["package"]
                    or d.get("mode") != self.initial.get("mode")):
                raise Failure("session_changed", "session identity changed; no further actions sent")
            controller = d.get("controller", {})
            if controller.get("actor_id") != self.args.actor_id or controller.get("epoch") != self.epoch:
                raise Failure("control_changed", "actor/epoch changed; this invocation does not adopt new control")
            if controller.get("expires_at") is not None and stamp(controller["expires_at"]) <= time.time():
                raise Failure("control_changed", "controller TTL has expired")
            if (d.get("live_owner") is not True or d.get("accepting") is not True
                    or any(d.get("action_capabilities", {}).get(name) != version
                           for name, version in self.capabilities.items())
                    or not self.actions.issubset(d.get("allowed_actions", []))):
                raise Failure("owner_unavailable", "owner must be live and support the requested actions")
            if stamp(d.get("deadline_at")) <= time.time() + minimum_seconds:
                raise Failure("budget_exhausted", "session budget does not cover the next step/wait")
            if d.get("phase") == "ready":
                return d
            # A completed receipt is published just before descriptor.ready.
            # Only observe this short publication gap; never wait through a
            # measurement/recovery phase or adopt a changed controller.
            if d.get("phase") != "executing" or time.monotonic() >= until:
                raise Failure("owner_unavailable", "owner did not become ready; no further actions sent")
            time.sleep(0.1)

    def submit(self, step, action, params, *, wait_seconds=120, minimum_seconds=0):
        self.guard(minimum_seconds)
        request_id = self.request_id(step)
        self.last_request = request_id
        request = {"schema": ACTION, "request_id": request_id, "session_id": self.session_id,
                   "actor_id": self.args.actor_id, "control_epoch": self.epoch,
                   "action": action, "params": params}
        emit({"event": "request_before_post", "request": request}, stream=sys.stderr)
        try:
            receipt = self.client.post(request)
            self.validate_receipt(receipt, request_id)
        except Failure as error:
            try:
                observed = self.receipt(request_id)
            except Failure as lookup_error:
                observed = {"lookup_error": lookup_error.code}
            raise Failure("action_result_unknown", "POST outcome unconfirmed; queried only the original ID and stopped",
                          request_id=request_id, cause=error.code, observed_receipt=observed) from error
        deadline = time.monotonic() + wait_seconds
        while receipt.get("status") in ("accepted", "started"):
            if time.monotonic() >= deadline:
                raise Failure("action_result_unknown", "original request is still pending; stopped without replay",
                              request_id=request_id, observed_receipt=receipt)
            time.sleep(0.25)
            receipt = self.receipt(request_id)
            if receipt is None:
                raise Failure("action_result_unknown", "accepted request is no longer readable", request_id=request_id)
        if receipt.get("status") != "completed":
            raise Failure("action_failed", "action did not complete; no further actions sent",
                          request_id=request_id, observed_receipt=receipt)
        return receipt.get("result")

    def image(self, result, request_id):
        image = result.get("image") if isinstance(result, dict) else None
        if (not isinstance(image, dict) or image.get("request_id") != request_id
                or any(type(image.get(k)) is not int or image[k] != self.profile[k]
                       for k in ("width", "height", "rotation"))):
            raise Failure("geometry_mismatch", "fresh original image does not exactly match the explicit profile",
                          request_id=request_id, observed_image=image)
        if not 0 <= time.time() - stamp(image.get("captured_at")) <= 30:
            raise Failure("stale_image", "fresh capture became stale before use", request_id=request_id)
        return image

    def capture(self, step):
        return self.image(self.submit(step, "capture", {}), self.request_id(step))

    def input(self, step, action, image, params):
        self.image({"image": image}, image["request_id"])
        d = self.guard()
        if (d.get("latest_image") or {}).get("request_id") != image["request_id"]:
            raise Failure("image_changed", "another capture replaced this frame; no input sent")
        return self.submit(step, action, {**params, "frame_id": image["request_id"]})

