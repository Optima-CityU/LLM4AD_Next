#!/usr/bin/env python3
"""Collect a reference arm through an existing passdump control session."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from gray_control_client import Client, SessionInvocation, Failure, emit, integer


PROFILE = "phonefarm/gray-reference-profile@1"
RESULT = "phonefarm/gray-reference-client@1"
HELP = """
Connect to an existing passdump owner; this client never installs, launches,
restarts, logs in, changes game settings, creates a job, or stops the session.
The caller must first place the game in the world and select the intended high
render quality. Package, marker counts and image size do not prove that state.

Remote: LABEL --job JOB --node-url URL --profile FILE --actor-id agent
              --expect-power-policy battery
Local:  LABEL --session-out DIR --profile FILE --actor-id agent
              --expect-power-policy observe
PF_NODE_URL may explicitly supply the remote endpoint. Credentials remain with
pf-node. --phonefarm/--pf-node (or PHONEFARM_BIN/PF_NODE_BIN) select executables.
Profile JSON has exactly schema="phonefarm/gray-reference-profile@1", package,
width, height and rotation (0..3), measured in ORIGINAL image coordinates.

The sequence is fresh capture -> finite-input sample -> marker. --input defaults
to workload_spin_touch_v1.json: sleep 3s, three 12s swipes, sleep 1s (40s total).
--seconds defaults to 30 (1..120); the existing owner validates the finite plan,
coordinates and seconds+10 minimum, with a 600s maximum. No input is shortened,
padded, scaled or executed by this client. Set --input for your actual geometry.
For old invocations, explicit WL supplies the --input default and CAPDUR supplies
the --seconds default; command-line options take precedence. LEAD may be unset
or exactly 6; other values are rejected before any session query because the
owner fixes trace lead at 6s. ROOT/SERIAL do not supply a locator or device.
--render-comm is optional: omitted means unknown frame metrics, not Unity.
--expect-power-policy observe|battery is required and only checks the owner's
fixed policy; it never enables/disables charging. Whole-sampler and qualified
trace power windows follow the current sample protocol, not legacy perf's
independent delayed window. GPU-submit metrics are not verified displayed FPS;
per-frame energy and matching scenes remain unverified.

Label is 1..48 ASCII letters/digits/_/-. Requests are gr-LABEL-00 (capture),
gr-LABEL-01 (sample), gr-LABEL-02 (marker). stderr records each request before
its single POST. Complete retries return the three original results, even after
the owner stops; partial/unknown attempts never resume or re-sample here. A lost
POST response only queries its original ID, then exits. Inspect Web/generic
session receipts to continue deliberately. Actor/epoch cannot change mid-run.

All PNG, sample/plan/raw data and marker artifacts stay in the original job/out.
No legacy shot_idle.raw, power.json or summary.json is synthesized/downloaded.
This client NEVER requests stop, including on failure. Use the existing Web
stop or generic session-action explicitly; client exit 0 is not job recovery.
"""


def read_json(path, kind):
    try:
        with open(path, "rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError(f"{kind} exceeds 64 KiB")
        return json.loads(raw)
    except (OSError, ValueError) as error:
        raise Failure(f"invalid_{kind}", str(error)) from error


def load_profile(path):
    value = read_json(path, "profile")
    if (not isinstance(value, dict)
            or set(value) != {"schema", "package", "width", "height", "rotation"}
            or value["schema"] != PROFILE
            or not isinstance(value["package"], str)
            or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", value["package"])
            or not integer(value["width"], 1, 8192)
            or not integer(value["height"], 1, 8192)
            or not integer(value["rotation"], 0, 3)):
        raise Failure("invalid_profile", "profile must match the documented schema and measured geometry")
    return value


class Invocation(SessionInvocation):
    def __init__(self, args, profile, client):
        super().__init__(args, profile, client, prefix=f"gr-{args.label}-",
                         actions={"capture", "sample", "marker"}, capabilities={"sample": 2})
        if self.initial.get("mode") != "passdump":
            raise Failure("session_mismatch", "reference requires an existing passdump owner")
        self.ids = {name: self.request_id(step) for name, step in
                    (("capture", "00"), ("sample", "01"), ("marker", "02"))}

    def guard(self, minimum_seconds=0):
        descriptor = super().guard(minimum_seconds)
        if (descriptor.get("power") or {}).get("policy") != self.args.expect_power_policy:
            raise Failure("power_policy_mismatch", "owner power policy does not match the explicit expectation")
        return descriptor

    def completed_results(self, receipts):
        if any(r is None or r.get("status") != "completed" for r in receipts.values()):
            return None
        results = {name: r.get("result") for name, r in receipts.items()}
        if not all(isinstance(r, dict) for r in results.values()):
            return None
        capture, sample, marker = (results[name] for name in ("capture", "sample", "marker"))
        if ((capture.get("image") or {}).get("request_id") != self.ids["capture"]
                or (sample.get("initial_frame") or {}).get("request_id") != self.ids["capture"]
                or sample.get("input_kind") != "finite"
                or (sample.get("collection") or {}).get("status") != "complete"
                or marker.get("kind") != "marker" or not isinstance(marker.get("path"), str)):
            return None
        return results

    def run(self):
        receipts = {name: self.receipt(request_id) for name, request_id in self.ids.items()}
        if any(r is not None for r in receipts.values()):
            results = self.completed_results(receipts)
            if results is not None:
                return self.output(results, repeated=True)
            raise Failure("partial_invocation", "reference already started; inspect original receipts and continue deliberately with Web/generic actions",
                          request_ids=self.ids, receipts=receipts)
        self.guard(self.args.seconds + 10)
        # The owner's Plan parser is authoritative; never duplicate its script
        # expansion or interpret legacy exec strings in this client.
        plan = read_json(self.args.input, "input")
        emit({"event": "invocation", "session_id": self.session_id, "job_id": self.initial.get("job_id"),
              "label": self.args.label, "profile": self.profile, "actor_id": self.args.actor_id,
              "control_epoch": self.epoch, "seconds": self.args.seconds,
              "expect_power_policy": self.args.expect_power_policy, "render_comm": self.args.render_comm}, stream=sys.stderr)
        capture = self.submit("00", "capture", {})
        image = self.image(capture, self.ids["capture"])
        descriptor = self.guard(self.args.seconds + 10)
        if (descriptor.get("latest_image") or {}).get("request_id") != image["request_id"]:
            raise Failure("image_changed", "another capture replaced this frame; no sample sent")
        params = {"seconds": self.args.seconds, "input": plan, "frame_id": image["request_id"]}
        if self.args.render_comm is not None:
            params["render_comm"] = self.args.render_comm
        # The owner accepts at most 600s of input. Allow its bounded deployment
        # and collection tail; quality retains the common 120s default.
        sample = self.submit("01", "sample", params, wait_seconds=720,
                             minimum_seconds=self.args.seconds + 10)
        if not isinstance(sample, dict) or (sample.get("collection") or {}).get("status") != "complete":
            raise Failure("collection_failed", "sample collection failed; no marker requested and no sample replayed",
                          request_ids=self.ids, capture=capture, sample=sample)
        marker = self.submit("02", "marker", {})
        results = {"capture": capture, "sample": sample, "marker": marker}
        if self.completed_results({name: {"status": "completed", "result": result}
                                   for name, result in results.items()}) is None:
            raise Failure("invalid_response", "completed actions do not identify the requested reference evidence",
                          request_ids=self.ids, results=results)
        return self.output(results, repeated=False)

    def output(self, results, *, repeated):
        sample = results["sample"]
        # These values describe stored evidence, never a repeated caller's argv.
        parameters = {"seconds": sample.get("requested_seconds"),
                      "planned_input_ms": sample.get("planned_input_ms"),
                      "power_policy": (sample.get("power") or {}).get("policy"),
                      "render_comm": ((sample.get("metric_provenance") or {}).get("frame") or {}).get("render_comm")}
        return {"schema": RESULT, "status": "completed", "label": self.args.label,
                "session_id": self.session_id, "job_id": self.initial.get("job_id"),
                "repeated": repeated, "request_ids": self.ids, "results": results, "parameters": parameters,
                "stop_requested": False, "scene_comparison": "not_evaluated", "render_quality": "not_verified",
                "protocol": {"reference_layer": "passdump", "trace_lead_seconds": 6,
                             "power_windows": "sample whole-sampler and qualified trace windows",
                             "legacy_delayed_perf_equivalent": False, "frame_semantics": "gpu_submit_proxy",
                             "energy_per_frame": "not_verified"}}


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__, epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label")
    locator = parser.add_mutually_exclusive_group(required=True)
    locator.add_argument("--job")
    locator.add_argument("--session-out")
    parser.add_argument("--node-url")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--expect-power-policy", required=True, choices=("observe", "battery"))
    parser.add_argument("--seconds", type=int, default=os.environ.get("CAPDUR", "30"))
    parser.add_argument("--input", default=os.environ.get("WL", str(Path(__file__).with_name("workload_spin_touch_v1.json"))))
    parser.add_argument("--render-comm")
    parser.add_argument("--pf-node", default=os.environ.get("PF_NODE_BIN", "pf-node"))
    parser.add_argument("--phonefarm", default=os.environ.get("PHONEFARM_BIN", "phonefarm"))
    args = parser.parse_args(argv)
    if os.environ.get("LEAD", "6") != "6":
        raise Failure("invalid_arguments", "LEAD must be unset or exactly 6; the existing owner fixes trace lead at 6 seconds")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.label) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.actor_id):
        raise Failure("invalid_arguments", "label must be 1..48 and actor 1..64 ASCII letters/digits/_/-")
    if not 1 <= args.seconds <= 120:
        raise Failure("invalid_arguments", "seconds must be 1..120")
    if args.render_comm is not None and (not 1 <= len(args.render_comm.encode("utf-8")) <= 64
                                       or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in args.render_comm)):
        raise Failure("invalid_arguments", "render-comm must be 1..64 bytes without control characters")
    return args


def main(argv=None):
    invocation = None
    try:
        args = arguments(sys.argv[1:] if argv is None else argv)
        invocation = Invocation(args, load_profile(args.profile), Client(args))
        emit(invocation.run())
        return 0
    except Failure as error:
        emit({"schema": RESULT, "status": "partial" if error.code == "partial_invocation" else "failed",
              "error": {"code": error.code, "message": str(error)},
              "last_request_id": invocation.last_request if invocation else None, "stop_requested": False, **error.details})
        return 2
    except KeyboardInterrupt:
        emit({"schema": RESULT, "status": "unknown",
              "error": {"code": "interrupted", "message": "client interrupted; query the original ID; owner was not stopped"},
              "last_request_id": invocation.last_request if invocation else None, "stop_requested": False})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
