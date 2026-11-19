#!/usr/bin/env python3
"""Navigate an existing gray-control owner; quality evidence stays with that owner."""
import argparse
import json
import os
import re
import sys
import time

from gray_control_client import Client as ControlClient, SessionInvocation, Failure, emit, integer


PROFILE = "phonefarm/genshin-quality-profile@1"
RESULT = "phonefarm/genshin-quality-client@1"
QUALITY_ROOT = "gray-control/quality"
POINTS = ("menu", "clock", "confirm", "close_clock", "back_world")
MODES = ("probe", "target", "passdump", "loadop", "frameaudit", "frameaudit-ts")
HELP = """
Requires an already-running gray-layer session and an explicitly verified profile.
begin starts while the character is standing in the world; it opens the clock.
After human inspection, finish drags the clock, records a checkpoint, confirms,
returns to the world and records the final image/marker. Neither command starts,
restarts, installs, stops, or restores the owner. The caller is responsible for
the initial scene; dimensions/rotation alone cannot establish that scene.

Remote: begin passdump LABEL --job JOB --node-url URL --profile FILE --actor-id agent
Local:  begin passdump LABEL --session-out DIR --profile FILE --actor-id agent
Finish: finish LABEL <same locator/profile/actor options> --swipe X1 Y1 X2 Y2
SWIPE="X1 Y1 X2 Y2" is also accepted. Swipe duration is the historical 2500 ms.
--node-url may be replaced by an explicitly set PF_NODE_URL; no production URL
is assumed. Credentials are handled only by pf-node. PHONEFARM_BIN/PF_NODE_BIN
or --phonefarm/--pf-node select executables, not shell command strings.

Profile JSON has exactly: schema="phonefarm/genshin-quality-profile@1", package,
width, height, rotation (0..3), points. points contains menu, clock, confirm,
close_clock and back_world, each [x,y] in the ORIGINAL image coordinates.
Supply measured dimensions and deliberate coordinates; no default profile or
coordinate scaling is provided. Label is 1..48 ASCII letters/digits/_/-.

Every input uses a fresh capture and exact profile geometry. Actor/epoch are
fixed at invocation start; a takeover, even followed by return, stops this
client. Requests use gq-LABEL-b00/f00 onward, with b99/f99 for final quality.
stderr records the profile and each complete request before its single POST.
Redirect stderr to keep this client log. Timeouts only query that original ID
and exit. A partial prior invocation is NEVER resumed or replayed here: inspect
its receipts and continue deliberately using Web/generic session actions.
finish requires latest_quality to be this label in phase begun; historical or
partial flows also use Web/generic actions. An already completed invocation
only returns its original result, including after the owner has stopped.

Owner artifacts remain under gray-control/quality/LABEL. begin corresponds to
clock_before, checkpoint to clock_after, finish to the world image. Each stage
keeps original.png and derived RGB stride2 preview-half.png separately. No .raw
files or legacy shot.png are synthesized, and nothing is downloaded by this
client. Collected evidence does not prove matching scenes, quality, or recovery.
"""


def load_profile(path):
    try:
        with open(path, "rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("profile exceeds 64 KiB")
        profile = json.loads(raw)
    except (OSError, ValueError) as error:
        raise Failure("invalid_profile", str(error)) from error
    if (not isinstance(profile, dict)
            or set(profile) != {"schema", "package", "width", "height", "rotation", "points"}
            or profile["schema"] != PROFILE
            or not isinstance(profile["package"], str)
            or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", profile["package"])
            or not integer(profile["width"], 1, 8192)
            or not integer(profile["height"], 1, 8192)
            or not integer(profile["rotation"], 0, 3)
            or not isinstance(profile["points"], dict)
            or set(profile["points"]) != set(POINTS)):
        raise Failure("invalid_profile", "profile must match the documented schema and measured geometry")
    for name, point in profile["points"].items():
        if (not isinstance(point, list) or len(point) != 2
                or not integer(point[0], 0, profile["width"] - 1)
                or not integer(point[1], 0, profile["height"] - 1)):
            raise Failure("invalid_profile", f"invalid original-image point: {name}")
    return profile


class Client(ControlClient):
    def label_exists(self, label):
        if not self.args.job:
            fds = []
            try:
                for path in (self.args.session_out, "gray-control", "quality"):
                    fds.append(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=fds[-1] if fds else None))
                try:
                    os.stat(label, dir_fd=fds[-1], follow_symlinks=False)
                    return True
                except FileNotFoundError:
                    return False
            except OSError as error:
                raise Failure("artifacts_unavailable", "cannot inspect the existing quality directory") from error
            finally:
                for fd in reversed(fds):
                    os.close(fd)
        cursor, seen = None, set()
        for _ in range(20):
            command = [self.args.pf_node, "artifacts", self.args.job, "--path", QUALITY_ROOT, "--limit", "100"]
            if cursor:
                command += ["--cursor", cursor]
            code, value = self.invoke(command)
            if (code != 0 or value.get("schema_version") != 1 or value.get("job") != self.args.job
                    or value.get("path") != QUALITY_ROOT or not isinstance(value.get("items"), list)):
                raise Failure("artifacts_unavailable", "quality directory listing failed; it is not treated as empty")
            for item in value["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise Failure("artifacts_unavailable", "invalid directory entry")
                if item["name"] == label:
                    return True
            cursor = value.get("next_cursor")
            if cursor is None:
                return False
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise Failure("artifacts_unavailable", "invalid/repeated directory cursor")
            seen.add(cursor)
        raise Failure("artifacts_unavailable", "quality directory exceeds the bounded metadata scan")


class Invocation(SessionInvocation):
    def __init__(self, args, profile, client):
        super().__init__(args, profile, client,
                         prefix=f"gq-{args.label}-{'b' if args.command == 'begin' else 'f'}",
                         actions={"quality", "capture", "tap", "swipe"}, capabilities={"quality": 1})
        if args.command == "begin" and self.initial.get("mode") != args.mode:
            raise Failure("session_mismatch", "begin mode must match the already-installed owner mode")

    def tap(self, capture_step, tap_step, point):
        image = self.capture(capture_step)
        x, y = self.profile["points"][point]
        self.input(tap_step, "tap", image, {"x": x, "y": y})

    def wait(self, seconds):
        self.guard(seconds)
        time.sleep(seconds)
        self.guard()

    def quality(self, step, op):
        request_id = self.request_id(step)
        result = self.submit(step, "quality", {"op": op, "label": self.args.label})
        self.image(result, request_id)
        quality = result.get("quality", {})
        expected = {"begin": "begun", "checkpoint": "checkpointed", "finish": "collected"}[op]
        if (result.get("collection", {}).get("status") != "complete" or quality.get("label") != self.args.label
                or quality.get("phase") != expected
                or quality.get("stages", {}).get(op, {}).get("request_id") != request_id):
            raise Failure("invalid_response", "completed quality receipt does not contain the requested stage", request_id=request_id)
        return result

    def run(self):
        first_id, final_id = self.request_id("00"), self.request_id("99")
        first, final = self.receipt(first_id), self.receipt(final_id)
        if first is not None or final is not None:
            result = (final or {}).get("result") or {}
            quality = result.get("quality") or {}
            expected = "begun" if self.args.command == "begin" else "collected"
            if (first is not None and final is not None and final.get("status") == "completed"
                    and result.get("collection", {}).get("status") == "complete"
                    and quality.get("label") == self.args.label and quality.get("phase") == expected):
                return self.output(result, repeated=True)
            raise Failure("partial_invocation", "this stage already started; inspect its receipts and continue deliberately with Web/generic actions",
                          first_request_id=first_id, final_request_id=final_id,
                          first_receipt=first, final_receipt=final)
        self.guard()
        if self.args.command == "begin":
            if self.client.label_exists(self.args.label):
                raise Failure("existing_label", "quality label already exists; navigation is not replayed")
        else:
            quality = self.initial.get("latest_quality") or {}
            if quality.get("label") != self.args.label or quality.get("phase") != "begun" or quality.get("active_attempt") is not None:
                raise Failure("quality_state", "finish requires this label to be the latest begun quality; use Web/generic actions for historical/partial flows")
        emit({"event": "invocation", "session_id": self.session_id, "job_id": self.initial.get("job_id"),
              "command": self.args.command, "label": self.args.label, "profile": self.profile,
              "actor_id": self.args.actor_id, "control_epoch": self.epoch}, stream=sys.stderr)
        if self.args.command == "begin":
            self.tap("00", "01", "menu")
            self.wait(3)
            self.tap("02", "03", "clock")
            self.wait(3)
            result = self.quality("99", "begin")
        else:
            image = self.capture("00")  # Never reuse the pre-human-wait frame.
            x1, y1, x2, y2 = self.args.swipe
            self.input("01", "swipe", image, {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": 2500})
            self.wait(1)
            self.quality("02", "checkpoint")
            self.tap("03", "04", "confirm")
            self.wait(12)
            self.tap("05", "06", "close_clock")
            self.wait(3)
            self.tap("07", "08", "back_world")
            self.wait(8)
            result = self.quality("99", "finish")
        return self.output(result, repeated=False)

    def output(self, result, *, repeated):
        return {"schema": RESULT, "status": "completed", "command": self.args.command,
                "label": self.args.label, "session_id": self.session_id, "job_id": self.initial.get("job_id"),
                "repeated": repeated, "result": result,
                "artifact_roles": {"begin": "clock_before", "checkpoint": "clock_after", "finish": "world"},
                "scene_comparison": "not_evaluated"}


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__, epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("begin", "finish"):
        command = commands.add_parser(name, epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
        if name == "begin":
            command.add_argument("mode", choices=MODES)
        command.add_argument("label")
        locator = command.add_mutually_exclusive_group(required=True)
        locator.add_argument("--job")
        locator.add_argument("--session-out")
        command.add_argument("--node-url")
        command.add_argument("--profile", required=True)
        command.add_argument("--actor-id", required=True)
        command.add_argument("--pf-node", default=os.environ.get("PF_NODE_BIN", "pf-node"))
        command.add_argument("--phonefarm", default=os.environ.get("PHONEFARM_BIN", "phonefarm"))
        if name == "finish":
            command.add_argument("--swipe", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.label) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.actor_id):
        raise Failure("invalid_arguments", "label must be 1..48 and actor 1..64 ASCII letters/digits/_/-")
    if args.command == "finish" and args.swipe is None:
        raw = os.environ.get("SWIPE", "").split()
        if len(raw) != 4 or any(not re.fullmatch(r"[0-9]+", v) for v in raw):
            raise Failure("invalid_arguments", "finish requires --swipe X1 Y1 X2 Y2 or four numeric SWIPE values")
        args.swipe = list(map(int, raw))
    return args


def main(argv=None):
    invocation = None
    try:
        args = arguments(sys.argv[1:] if argv is None else argv)
        profile = load_profile(args.profile)
        if args.command == "finish" and any(not integer(value, 0, profile[axis] - 1)
                                            for value, axis in zip(args.swipe, ("width", "height", "width", "height"))):
            raise Failure("invalid_arguments", "swipe coordinates must fit the explicit original-image profile")
        invocation = Invocation(args, profile, Client(args))
        emit(invocation.run())
        return 0
    except Failure as error:
        emit({"schema": RESULT, "status": "partial" if error.code == "partial_invocation" else "failed",
              "error": {"code": error.code, "message": str(error)},
              "last_request_id": invocation.last_request if invocation else None, **error.details})
        return 2
    except KeyboardInterrupt:
        emit({"schema": RESULT, "status": "unknown", "error": {"code": "interrupted", "message": "client interrupted; owner was not cancelled; query the original ID before further input"},
              "last_request_id": invocation.last_request if invocation else None})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
