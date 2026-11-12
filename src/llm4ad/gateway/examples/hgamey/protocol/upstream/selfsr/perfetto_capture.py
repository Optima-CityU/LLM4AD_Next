"""Own a bounded Perfetto recorder within the existing leased A/B experiment."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path


def save(path: Path, value: dict) -> None:
    """Atomically retain capture intent and recovery receipts."""
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _info(text: str) -> dict:
    """Decode only the AIDL exception header and array size, retaining raw data."""
    match = re.fullmatch(r"\s*Result: Parcel\(\s*([0-9a-fA-F]{8})\s+([0-9a-fA-F]{8})(?:\s.*)?\)\s*", text, re.S)
    result = {"status": "unavailable", "raw": text}
    if match and int(match[1], 16) == 0:
        count = int(match[2], 16)
        if count < 0x80000000:
            result.update(status="advertised" if count else "unsupported", count=count)
    return result


def probe(dev, output: Path) -> dict:
    """Inspect actual data source content without changing charging or tracing.

    Args:
        dev: Existing Dev with the inherited guarded wireless transport.
        output: Owned evidence folder for raw capability replies.

    Returns:
        Capability observations; advertised channels are not measured energy.
    """
    output.mkdir(parents=True, exist_ok=True)
    result = {"schema": "llm4ad/android-capabilities@1", "energy_used_for_fitness": False}
    commands = {
        "identity": "getprop ro.serialno; cat /proc/sys/kernel/random/boot_id; getprop ro.build.fingerprint",
        "perfetto": "perfetto --version; perfetto --query",
        "powerstats": "dumpsys powerstats",
        "energy_consumers": "service call android.hardware.power.stats.IPowerStats/default 3",
        "energy_meters": "service call android.hardware.power.stats.IPowerStats/default 5",
        "battery": """dumpsys battery
for n in status voltage_now current_now current_avg charge_counter energy_now temp capacity; do
 f=/sys/class/power_supply/battery/$n
 if [ -r "$f" ]; then printf 'FIELD=%s\\n' "$n"; cat "$f"; fi
done""",
    }
    for label, command in commands.items():
        try:
            raw = dev.su(command, timeout=15)
            (output / f"{label}.txt").write_text(raw)
            result[label] = _info(raw) if label in {"energy_consumers", "energy_meters"} else {"raw": raw}
        except (OSError, subprocess.SubprocessError) as error:
            result[label] = {"status": "unavailable", "reason": str(error)}
    identity = result.get("identity", {}).get("raw", "").strip().splitlines()
    result["physical_serial"] = identity[0] if len(identity) >= 2 else None
    result["boot_id"] = identity[1] if len(identity) >= 2 else None
    query = result.get("perfetto", {}).get("raw", "")
    result["sources"] = [
        name
        for name in (
            "android.power",
            "android.surfaceflinger.frametimeline",
            "linux.ftrace",
            "linux.process_stats",
        )
        if re.search(r"(?m)^" + re.escape(name) + r"\s", query)
    ]
    result["perfetto_available"] = "linux.ftrace" in result["sources"] and "Service: Perfetto" in query
    result["battery"]["fields"] = dict(re.findall(r"(?m)^FIELD=([^\n]+)\n([^\n]+)", result["battery"].get("raw", "")))
    result["battery"]["units"] = "not inferred; preserve raw supply units"
    save(output / "capabilities.json", result)
    return result


def configuration(capabilities: dict, name: str, seconds: float) -> str:
    """Select available producers without assuming PowerStats measures energy."""
    config = f'''buffers {{ size_kb: 16384 fill_policy: RING_BUFFER }}
duration_ms: {int((seconds + 60) * 1000)}
unique_session_name: "{name}"
data_sources {{ config {{ name: "linux.ftrace" ftrace_config {{
 ftrace_events: "ftrace/print"
 ftrace_events: "sched/sched_switch"
 ftrace_events: "power/cpu_frequency"
 ftrace_events: "power/gpu_frequency"
 ftrace_events: "power/gpu_work_period"
}} }} }}
'''
    for source in ("android.surfaceflinger.frametimeline",):
        if source in capabilities["sources"]:
            config += f'data_sources {{ config {{ name: "{source}" }} }}\n'
    if "linux.process_stats" in capabilities["sources"]:
        config += (
            'data_sources { config { name: "linux.process_stats" process_stats_config { scan_all_processes_on_start: true } } }\n'
        )
    if "android.power" in capabilities["sources"]:
        rails = capabilities["energy_meters"].get("status") == "advertised"
        consumers = capabilities["energy_consumers"].get("status") == "advertised"
        config += """data_sources { config { name: "android.power" android_power_config {
 battery_poll_ms: 1000
 battery_counters: BATTERY_COUNTER_CAPACITY_PERCENT
 battery_counters: BATTERY_COUNTER_CHARGE
 battery_counters: BATTERY_COUNTER_CURRENT
 battery_counters: BATTERY_COUNTER_VOLTAGE
"""
        config += f" collect_power_rails: {str(rails).lower()}\n"
        config += f" collect_energy_estimation_breakdown: {str(consumers).lower()}\n"
        config += "} } }\n"
    return config


def marker_script(name: str, edge: str) -> str:
    """Write an ATrace marker, never a tracing control or charging setting."""
    if not re.fullmatch(r"llm4ad_window_[a-f0-9]{32}", name) or edge not in {"B", "E"}:
        raise ValueError("Invalid owned window marker")
    # Android printf can be an external command, so emit process-scoped async pairs.
    payload = f"{'S' if edge == 'B' else 'F'}|$$|{name}|1"
    return f'printf "{payload}" > /sys/kernel/tracing/trace_marker'


class Capture:
    """Persist recorder ownership before any remote start and reconcile it safely."""

    def __init__(self, dev, package: str, seconds: float, output: Path, arm: str):
        """Bind a fresh recorder to one named baseline/candidate arm."""
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package) or not 0 < seconds <= 120:
            raise ValueError("Invalid package or capture duration")
        self.dev, self.package, self.seconds = dev, package, seconds
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        token = uuid.uuid4().hex
        self.state = {
            "schema": "llm4ad/perfetto-capture@1",
            "arm": arm,
            "package": package,
            "remote": "/data/local/tmp/llm4ad_perfetto_" + token,
            "session": "llm4ad_perfetto_" + token,
            "marker": "llm4ad_window_" + token,
            "status": "pending",
            "cleanup_confirmed": True,
            "recovery_unknown": False,
            "submitted": False,
            "energy_used_for_fitness": False,
        }
        self.persist()

    def persist(self):
        """Update the independently discoverable cleanup receipt."""
        save(self.output / "perfetto-cleanup.json", self.state)

    def start(self) -> bool:
        """Probe and start a self-expiring owned recorder using background-wait."""
        caps = probe(self.dev, self.output)
        self.state["capabilities"] = caps
        self.state["boot_id"] = caps["boot_id"]
        if not caps["perfetto_available"] or not caps["boot_id"]:
            self.state.update(status="unavailable", reason="No usable Perfetto producer or boot identity")
            self.persist()
            return False
        config = configuration(caps, self.state["session"], self.seconds)
        (self.output / "config.pbtxt").write_text(config)
        self.state.update(submitted=True, cleanup_confirmed=False, recovery_unknown=True)
        self.persist()
        remote = self.state["remote"]
        script = f"""set -eu
[ "$(cat /proc/sys/kernel/random/boot_id)" = {shlex.quote(caps["boot_id"])} ]
mkdir {remote}
cat > {remote}/config.pbtxt <<'LLM4AD_PERFETTO_CONFIG'
{config}LLM4AD_PERFETTO_CONFIG
perfetto -D --txt -c {remote}/config.pbtxt -o {remote}/trace.pftrace > {remote}/pid 2> {remote}/start.log
cat {remote}/pid
"""
        try:
            raw = self.dev.su("sh -c " + shlex.quote(script), timeout=40)
            (self.output / "start.txt").write_text(raw)
            if not raw.strip().isdigit():
                raise ValueError("Perfetto did not confirm its background PID")
            self.state.update(pid=int(raw.strip()), status="recording")
            self.persist()
            return True
        except (Exception, KeyboardInterrupt) as error:
            self.state.update(status="error", reason=f"{type(error).__name__}: {error}")
            self.persist()
            self.stop()
            if isinstance(error, KeyboardInterrupt):
                raise
            return False

    def stop(self):
        """Stop only this recorder, retrieve the original trace and retain failures."""
        self.state = reconcile(self.dev, self.output)
        if self.state.get("recovery_unknown") and self.state.get("cleanup_error", "").startswith("TimeoutExpired:"):
            # Reconcile only the same owned recorder; never replay the experiment.
            self.state = reconcile(self.dev, self.output)
        return self.state

    def analyze(self):
        """Run bounded official Trace Processor analysis after remote cleanup."""
        trace = self.output / "trace.pftrace"
        if not self.state.get("trace_retrieved"):
            return self.state
        command = [
            sys.executable,
            str(Path(__file__).with_name("perfetto_analysis.py")),
            str(trace),
            "--marker",
            self.state["marker"],
            "--package",
            self.package,
            "--seconds",
            str(self.seconds),
        ]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=90)
            (self.output / "analysis.stderr").write_text(done.stderr)
            if done.returncode:
                raise ValueError(done.stderr[-2000:] or "Trace Processor failed")
            self.state["analysis"] = json.loads((self.output / "analysis.json").read_text())
            self.state["status"] = self.state["analysis"]["status"]
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.state.update(status="analysis_unavailable", analysis_error=str(error))
            self.state["analysis"] = {
                "schema": "llm4ad/perfetto-analysis@1",
                "status": "unavailable",
                "metrics": {},
                "reason": str(error),
                "energy_used_for_fitness": False,
            }
            save(self.output / "analysis.json", self.state["analysis"])
        self.persist()
        return self.state


def reconcile(dev, output: Path) -> dict:
    """Recover an exact persisted recorder after interruption, without replaying it.

    Args:
        dev: Existing leased Dev transport bound to the original phone.
        output: Directory containing the original capture intent.

    Returns:
        Updated cleanup receipt; uncertainty remains durable when transport fails.
    """
    output = Path(output)
    state = json.loads((output / "perfetto-cleanup.json").read_text())
    if not state.get("submitted") or state.get("cleanup_confirmed") is True:
        return state
    remote = state["remote"]
    if not re.fullmatch(r"/data/local/tmp/llm4ad_perfetto_[a-f0-9]{32}", remote):
        raise ValueError("Unsafe persisted recorder path")
    tag = state["session"]
    attempt = state.get("stop_attempts", 0) + 1
    state["stop_attempts"] = attempt
    try:
        physical = dev.shell("getprop ro.serialno").strip()
        if not physical:
            raise ConnectionError("Physical phone identity is unavailable over ADB; recovery remains pending")
        if physical != state["capabilities"]["physical_serial"]:
            raise ValueError("Physical phone changed; original recorder cannot be reconciled on this device")
        boot = dev.shell("cat /proc/sys/kernel/random/boot_id").strip()
        if not boot:
            raise ConnectionError("Device boot identity is unavailable over ADB; recovery remains pending")
        if boot != state["boot_id"]:
            state.update(
                status="interrupted",
                reason="Device rebooted; original recorder cannot still be running",
                cleanup_confirmed=True,
                recovery_unknown=False,
            )
            return state
        script = f'''set -eu
echo LLM4AD_STOP_STAGE=scan
owned() {{
 for p in $(pidof perfetto || true); do
  # Bound procfs input before toybox tr; direct procfs input can spin indefinitely.
  cmd=$(head -c 8192 /proc/$p/cmdline 2>/dev/null | tr '\\000' ' ')
  case "$cmd" in *"-c {remote}/config.pbtxt -o {remote}/trace.pftrace"*) echo "$p";; esac
 done
}}
for p in $(owned); do kill -INT "$p"; done
echo LLM4AD_STOP_STAGE=wait
i=0
while [ -n "$(owned)" ] && [ "$i" -lt 30 ]; do sleep 0.1; i=$((i+1)); done
[ -z "$(owned)" ]
echo LLM4AD_STOP_STAGE=query
query=$(perfetto --query)
case "$query" in *"{tag}"*) exit 4;; esac
if [ -f {remote}/trace.pftrace ]; then chmod 644 {remote}/trace.pftrace; echo TRACE_READY; fi
echo LLM4AD_PERFETTO_STOPPED
'''
        raw = dev.su("sh -c " + shlex.quote(script), timeout=45)
        (output / f"stop-{attempt}.txt").write_text(raw)
        (output / "stop.txt").write_text(raw)
        if not raw.rstrip().endswith("LLM4AD_PERFETTO_STOPPED"):
            raise ValueError("Owned recorder stop receipt is missing")
        state.update(cleanup_confirmed=True, recovery_unknown=False)
        if "TRACE_READY\n" in raw:
            local = output / "trace.pftrace"
            dev.adb("pull " + shlex.quote(remote + "/trace.pftrace") + " " + shlex.quote(str(local)), timeout=40, check=True)
            if local.is_file() and local.stat().st_size:
                state.update(
                    trace_retrieved=True,
                    trace_bytes=local.stat().st_size,
                    trace_sha256=hashlib.sha256(local.read_bytes()).hexdigest(),
                )
        removal = dev.su(f"rm -rf {remote}; [ ! -e {remote} ] && echo LLM4AD_PERFETTO_REMOVED", timeout=15)
        if not removal.rstrip().endswith("LLM4AD_PERFETTO_REMOVED"):
            raise ValueError("Owned staging cleanup could not be confirmed")
        state["staging_removed"] = True
        state.pop("cleanup_error", None)
        if state.get("status") == "recording":
            state["status"] = "captured" if state.get("trace_retrieved") else "unavailable"
    except (Exception, KeyboardInterrupt) as error:
        partial = getattr(error, "stdout", None) or getattr(error, "output", "") or ""
        if partial:
            text = partial.decode(errors="replace") if isinstance(partial, bytes) else partial
            (output / f"stop-{attempt}.txt").write_text(text)
        state.setdefault("stop_errors", []).append({"attempt": attempt, "error_type": type(error).__name__})
        state.update(cleanup_confirmed=False, recovery_unknown=True, cleanup_error=f"{type(error).__name__}: {error}")
    finally:
        save(output / "perfetto-cleanup.json", state)
    return state
