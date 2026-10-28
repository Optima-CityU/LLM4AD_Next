# Windows, macOS and Linux

## Two different environments

Ask or detect both the author's local OS/CPU and the deployment's task-runner
OS/CPU. The browser can upload the same ZIP from Windows, macOS or Linux; the
algorithm and its dependencies still execute in the server's task container.
A local macOS/Windows success is evidence for that host, not a Linux result.
WSL is a Linux verification environment even when the browser runs on Windows.

The Python helper uses argument lists, `sys.executable`, `pathlib`, `tempfile`
and `zipfile`; it does not require Bash, `zip`, `chmod`, or shell activation.
It records Python, OS and architecture in `validation.json`. Use Python 3.12
and Git for real evolution, plus any toolchain required by the chosen task.

## Local commands

Replace the paths with the user's own paths. Use an isolated verification
environment and a deliverable path outside the source task directory.

### Windows PowerShell

```powershell
$PlatformRepo = "C:\Work\LLM4AD"
$TaskRoot = "C:\Work\My task"
$SkillRoot = Join-Path $PlatformRepo "skills\llm4ad-cloud-package"
$CheckEnv = Join-Path $env:TEMP ("llm4ad-check-" + [guid]::NewGuid().ToString("N"))
$CheckPython = Join-Path $CheckEnv "Scripts\python.exe"

uv venv --python 3.12 "$CheckEnv"
uv pip install --python "$CheckPython" "$PlatformRepo"
uv pip install --python "$CheckPython" -r "$TaskRoot\requirements.txt"
uv pip check --python "$CheckPython"
& "$CheckPython" -X utf8 "$TaskRoot\test_evaluator.py"
& "$CheckPython" -X utf8 "$SkillRoot\scripts\validate_and_pack.py" "$TaskRoot" --output "C:\Work\deliverables\my-task.zip"
```

No `Activate.ps1` or execution-policy change is necessary. Use the PowerShell
call operator `&` for a quoted interpreter path. Check command exit codes and
fix failures before continuing. Do not use Bash `export`, `source`, or `\` line
continuations in PowerShell. Optional real-model test credentials use
`$env:LLM_API_KEY`, `$env:LLM_BASE_URL`, `$env:LLM_MODEL`, not a packaged `.env`.

### macOS / Linux (zsh or Bash)

```bash
cloud_platform_repo="/path/to/LLM4AD"
cloud_task_root="/path/to/My task"
cloud_check_env="$(mktemp -d -t llm4ad-check.XXXXXXXX)"
cloud_check_python="$cloud_check_env/bin/python"

uv venv --python 3.12 "$cloud_check_env"
uv pip install --python "$cloud_check_python" "$cloud_platform_repo"
uv pip install --python "$cloud_check_python" -r "$cloud_task_root/requirements.txt"
uv pip check --python "$cloud_check_python"
"$cloud_check_python" -X utf8 "$cloud_task_root/test_evaluator.py"
"$cloud_check_python" -X utf8 "$cloud_platform_repo/skills/llm4ad-cloud-package/scripts/validate_and_pack.py" "$cloud_task_root" --output "/path/to/deliverables/my-task.zip"
```

On Apple Silicon, record arm64 versus an x86_64 interpreter running under
Rosetta. Matching an OS alone does not establish native dependency compatibility.
Install the chosen method's optional platform extras where required; the default
example uses only the standard library in its candidate and evaluator.

## Portable project content

- Native CLI input/output paths may contain spaces, Unicode or Windows drive
  letters. **Inside config.yaml and the ZIP**, use relative forward-slash paths
  such as `algorithm`, `scoring/evaluator.py:Evaluator`, `data/sample/*.json`.
  Do not write `C:\...`, `/Users/...`, `/home/...`, or backslash-separated config
  paths. The helper always emits ZIP member names using `/`.
- Use `pathlib` in Python and `sys.executable` to run Python candidates. Avoid
  embedded shell commands such as `python3`, `timeout`, `cp`, or `rm` for operations
  the Python standard library can perform. Do not assume a Unix executable bit.
- Keep filenames case-consistent; a file that resolves on a case-insensitive
  Windows/macOS volume can fail on Linux. Avoid filenames differing only by case
  and Windows-reserved names. Include real files rather than symlinks/junctions.
- Prefer UTF-8 text. CRLF and UTF-8 BOM Python source are accepted by the helper's
  compilation check. PowerShell 5 `Out-File` can produce UTF-16; configure UTF-8
  output when creating source/config files. Linux shell scripts need LF endings.
- Do not freeze an entire host environment into requirements. Declare the actual
  task dependencies; use supported Python environment markers for truly optional
  platform-specific dependencies. A Windows-only solver cannot be made runnable
  on Linux merely by omitting its dependency. Supply a supported implementation
  or declare the target incompatible.
- Deliver source, not a macOS binary or Windows `.exe` as the only candidate.
  Compile in each target environment. Keep `.venv`, caches and previous runs out
  of the package. The helper/importer ignores Finder/Explorer metadata such as
  `.DS_Store`, `__MACOSX`, `._*`, `Thumbs.db` and `desktop.ini`.

## Compiled or platform-specific tasks

`planner.build.script` accepts `linux`, `darwin` and `windows` commands. These
are executed by `/bin/sh` on POSIX and the default Windows command shell (usually
`cmd.exe`), not automatically by PowerShell. For example, with CMake installed:

```yaml
planner:
  build:
    script:
      linux: cmake -S . -B build && cmake --build build --config Release
      darwin: cmake -S . -B build && cmake --build build --config Release
      windows: cmake -S . -B build && cmake --build build --config Release
    timeout: 120
```

The evaluator must resolve that build's real output path (`.exe` and
multi-configuration `Release/` directories where applicable). The helper selects
the **local** OS command and fails clearly if a configured build has no command
for it. It does not silently skip a missing platform build or cross-compile.

For a Linux cloud deployment, also verify native libraries, GPU/solver runtimes
and compiled tasks in a matching Linux task-runner/container when available.
Use a second output filename for each validation; each report describes the
environment actually used. If the target runtime is unavailable, state that
target verification is pending rather than fabricating a cross-platform pass.
