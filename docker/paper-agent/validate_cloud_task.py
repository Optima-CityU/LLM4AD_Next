"""Check one Cloud task in ordered stages inside the actual execution image."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

CHECK_IDS = ("config", "dependencies", "sources", "evaluation")
VERIFIER = "/app/skills/llm4ad-cloud-package/scripts/validate_and_pack.py"


def _run_check(code: str, project: Path, timeout: int) -> None:
    """Run preflight imports in a separate interpreter, avoiding stale wheels."""
    result = subprocess.run([sys.executable, "-c", code, str(project)], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or "Task check failed")


def main() -> int:
    """Validate configuration, install dependencies, check sources, then evaluate."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    checks = [{"id": name, "status": "pending"} for name in CHECK_IDS]
    current = checks[0]

    def progress() -> None:
        path = args.output.with_suffix(".progress.json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"checks": checks}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def begin(index: int) -> dict:
        check = checks[index]
        check["status"] = "running"
        progress()
        return check

    try:
        current = begin(0)
        _run_check("""
import sys,yaml
from pathlib import Path
from llm4ad.config import AppConfig
project=Path(sys.argv[1])
data=yaml.safe_load((project/'config.yaml').read_text(encoding='utf-8-sig'))
if not isinstance(data,dict):raise ValueError('config.yaml must contain a mapping')
AppConfig.from_dict(data)
""", args.project, args.timeout)
        current["status"] = "passed"
        current = begin(1)
        # Keep numerical libraries out of this interpreter until wheels settle.
        from app.tasks.container_runner import _install_dependencies

        _install_dependencies(str(args.project))
        _run_check("import numpy.random; from llm4ad.llm4ad import LLM4AD", args.project, args.timeout)
        current["status"] = "passed"
        current = begin(2)
        _run_check(f"""
import sys,yaml,importlib.util
from pathlib import Path
from llm4ad.config import AppConfig
project=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('cloud_verifier',{VERIFIER!r})
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
files=module.package_files(project)
for relative in files:
 if relative.suffix=='.py':compile((project/relative).read_bytes(),str(relative),'exec')
config=AppConfig.from_dict(yaml.safe_load((project/'config.yaml').read_text(encoding='utf-8-sig')))
module.dataset_files(project,config.evaluator.dataset)
""", args.project, args.timeout)
        current["status"] = "passed"
        current = begin(3)
        result = subprocess.run([
            sys.executable, VERIFIER, str(args.project), "--verify-only", "--output", str(args.output), "--timeout", str(args.timeout),
        ], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        current["status"] = "passed"
        progress()
        report_path = args.output
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["checks"] = checks
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        print(result.stdout, flush=True)
        return 0
    except Exception as exc:
        current.update(status="failed", error=str(exc))
        progress()
        diagnostic = {"status": "failed", "error": str(exc), "checks": checks, "traceback": traceback.format_exc()}
        args.output.with_suffix(".error.json").write_text(json.dumps(diagnostic, ensure_ascii=False), encoding="utf-8")
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
