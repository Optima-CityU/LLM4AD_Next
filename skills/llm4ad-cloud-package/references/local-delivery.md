# Standalone local project delivery

Use this workflow outside the AutoDesign web runtime, when the user needs a
portable package to import into the platform. Read
[web-import.md](web-import.md) for the upload and model-binding flow.

## Build and validate

1. Resolve the problem, candidate function and I/O, objective and constraints,
   available data, local and target OS/architecture, language/runtime, and selected evolution method. Use the user's
   choices; inspect supplied materials first and ask about unresolved information
   that affects the runnable contract. A complete description authorizes generation;
   reuse an existing task's context without repeating intake or confirmation. Never
   substitute toy data, an unrelated example, or an invented scoring rule for
   missing requirements. Recommend a supported method with a reason if needed.
2. Create a separate task directory using the documented layout. Keep the
   evaluator and datasets outside `version_control.local_path`; place the actual
   evolvable code inside it. The evaluator must execute that candidate directory.
   Explicitly size the repository context and generation settings using the
   [context sizing guidance](configuration.md#context-and-generation-sizing).
   Do not inherit the 5-line repository context or 4K custom-coder defaults:
   inspect the actual code and helpers, allow growth during evolution, and record
   the intended model capacity and chosen values in the task README. Input
   context and output tokens are separate settings, not interchangeable limits.
3. For local validation, prepare Python 3.12 in an isolated environment, install the platform revision
   matching the target deployment, and install the final task requirements. The
   distribution is `llm4ad-next`, while the Python import is `llm4ad`. Do not infer
   a PyPI requirement named `llm4ad` from that import.
4. Locally, check dependency compatibility with `uv pip check --python <venv-python>`.
   Run the task's `test_evaluator.py`: exercise known valid and invalid examples,
   actual candidate execution, score direction and changes between candidates,
   and timeout handling when applicable. Check initial source syntax and any
   required build. A loader-only test or an agent's claim of success is insufficient.
5. Locally, run the bundled helper with that environment's Python (web runtime:
   use the MCP validation tool as described in [web-runtime.md](web-runtime.md)):

   ```bash
   <venv-python> <skill-dir>/scripts/validate_and_pack.py <task-dir> \
     --output <output-dir>/my-task.zip
   ```

   The helper snapshots the project into a ZIP, extracts it into a fresh temporary
   directory, validates its config and EVOLVE blocks, evaluates every configured
   data instance using a copy of the candidate directory, and checks that an empty
   candidate is rejected. It then runs the included `test_evaluator.py` in a
   separate process and requires a successful exit. Only after both pass does it
   add `validation.json` to the ZIP and write a
   sibling `my-task.validation.json`. It does not install dependencies or start
   LLM evolution. Supply `--timeout` when the actual evaluation needs more than
   the default 120 seconds for the complete verification.
6. If validation fails, inspect the concrete error, fix the package, and rerun.
   Deliver a ZIP only after the helper succeeds. Changing the files afterward
   requires regenerating the archive. If the required compiler, solver, dataset,
   or dependency is unavailable, report the missing verification instead of
   claiming success. Local validation records the host OS; cloud Linux
   compatibility needs a matching Linux environment when the task is OS-specific.

## Handoff

Provide the ZIP and report paths, problem and objective, method, baseline results,
tested platform/Python/OS, and web import instructions in the user's language.
Distinguish baseline/evaluator validation from a real LLM evolution run; the helper
does not establish that future generated candidates will improve the baseline.
The archive contains portable configuration and source/data, not model credentials,
local environments, past run artifacts, or platform account/task identifiers.

This Skill is independent of `llm4ad-task-builder`. Keep its existing in-platform
and portable behavior unchanged when working on this cloud packaging workflow.
