#!/usr/bin/env python3
"""Isolated Android state for the real gray control CLI; never runs a shell."""
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys

root = Path(os.environ["FAKE_GRAY_ROOT"])
state_file = root / "android.json"
state = json.loads(state_file.read_text())
args = sys.argv[1:]
assert args[:2] == ["-s", "fake-a"], args
args = args[2:]
with (root / "adb.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")
files, directories, props = state["files"], state["directories"], state["properties"]

if args[0] == "push":
    files[args[2]] = Path(args[1]).read_text()
elif args[0] == "shell":
    command = shlex.split(args[1])
    if command[:2] == ["su", "-c"]:
        command = shlex.split(command[2])
    op = command[0]
    path = command[-1]
    if command[:2] == ["dumpsys", "package"]:
        print("legacyNativeLibraryDir=/data/app/fixture/lib")
    elif command[:3] == ["cmd", "package", "resolve-activity"]:
        print("com.example.fixture/.Main")
    elif op == "cat":
        print("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" if path == "/proc/sys/kernel/random/boot_id" else files[path], end="\n")
    elif command[:2] == ["ls", "-1A"]:
        assert path in directories, path
        for name in sorted({PurePosixPath(item).name for item in [*directories, *files]
                            if str(PurePosixPath(item).parent) == path and item != path}):
            print(name)
    elif command[:2] == ["ls", "-Zd"]:
        assert path in files
        print("u:object_r:apk_data_file:s0 " + path)
    elif op == "getprop":
        print(props.get(path, ""))
    elif op == "setprop":
        props[command[1]] = command[2]
    elif op == "mkdir":
        directories.append(path)
    elif op in ("cp", "mv"):
        files[path] = files[command[-2]]
        if op == "mv":
            del files[command[-2]]
    elif op in ("chmod", "chown", "chcon"):
        assert path in files
    elif op == "stat":
        assert path in files
        print("755 1000 1000")
    elif op == "printf" and ">" in command:
        files[path] = command[2]
    elif command[:2] == ["am", "force-stop"]:
        state["running"] = False
    elif command[:2] == ["am", "start"]:
        assert props["debug.vulkan.layers"] == "VK_LAYER_refknobs_readonly"
        assert "/data/app/fixture/lib/arm64/libVkLayer_refknobs.so" in files
        state["running"] = True
        (root / "attached").touch()
    elif op == "ps":
        print("NAME\n" + ("com.example.fixture" if state["running"] else ""))
    elif op == "rm":
        files.pop(path, None)
    elif op == "rmdir":
        directories.remove(path)
    else:
        raise AssertionError(command)
else:
    raise AssertionError(args)
state_file.write_text(json.dumps(state))
