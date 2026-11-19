#!/usr/bin/env python3
"""测试用假 adb/hdc: 设备状态存在 $FAKE_STATE (JSON)。只认 farm 会发的那几种命令。"""
import json
import os
import re
import shlex
import sys

st_path = os.environ["FAKE_STATE"]
st = json.load(open(st_path))
argv = sys.argv[1:]
st.setdefault("calls", []).append(argv)


def save():
    json.dump(st, open(st_path, "w"))


def out(s, rc=0):
    save()
    sys.stdout.write(s)
    sys.exit(rc)


serial = None
while argv and argv[0] in ("-s", "-t"):
    serial = argv[1]
    argv = argv[2:]
if not argv:
    out("", 1)
sub = argv[0]
if sub in ("--version", "version"):
    out("fake 1.0\n")
if sub == "devices":
    out("List of devices attached\n" + "".join(f"{s}\tdevice\n" for s in st["online"]))
if sub == "list":
    out("".join(f"{s}\n" for s in st["online"]))
if serial not in st["online"]:
    out("error: device offline\n", 1)
if sub == "get-state":
    out("device\n")
if sub != "shell":
    out("", 0)
cmd = " ".join(argv[1:])
m = re.match(r"^su -c (.*)$", cmd, re.S)
if m:
    cmd = " ".join(shlex.split(m[1]))
dev = st["dev"].setdefault(serial, {"settings": {}, "props": {}, "files": {}, "dirs": {}})
w = shlex.split(cmd)
if w[:2] == ["settings", "get"]:
    out(f"{dev['settings'].get(w[2] + ':' + w[3], 'null')}\n")
if w[:2] == ["settings", "put"]:
    dev["settings"][w[2] + ":" + w[3]] = w[4]
    out("")
if w[0] == "getprop":
    out(f"{dev['props'].get(w[1], '')}\n")
if w[0] == "setprop":
    dev["props"][w[1]] = w[2] if len(w) > 2 else ""
    out("")
if w[0] == "cat":
    if w[1] in dev["files"]:
        out(dev["files"][w[1]] + "\n")
    out(f"cat: {w[1]}: No such file or directory\n", 1)
if w[0] == "echo" and ">" in w:
    i = w.index(">")
    dev["files"][w[i + 1]] = " ".join(w[1:i])
    out("")
if w[:2] == ["ls", "-1a"]:
    if w[2] in dev["dirs"]:
        out("".join(n + "\n" for n in [".", ".."] + dev["dirs"][w[2]]))
    out("ls: no such dir\n", 1)
if w[:2] == ["dumpsys", "battery"]:
    out(f"  level: 80\n  temperature: {dev.get('temp10', 312)}\n")
if w[:3] == ["cmd", "game", "set"]:
    out("")
out(f"fake: unknown {cmd}\n", 127)
