# Standalone local project delivery

Use this workflow outside the AutoDesign web runtime, when the user needs a
portable package to import into the platform. Read
[web-import.md](web-import.md) for the upload and model-binding flow.

## Build and validate

1. Choose where evaluations run before writing code. Offer the platform task
   container for self-contained evaluators and a gateway on the user's machine
   for local hardware, private data, or a dedicated toolchain. For a gateway,
   confirm native or Docker startup. Resolve the problem, candidate I/O,
   objective, constraints, data, runtime and evolution method. Use the user's
   choices; inspect supplied materials first and ask about unresolved information
   that affects the runnable contract. A complete description authorizes generation;
   reuse an existing task's context without repeating intake or confirmation. Never
   substitute toy data, an unrelated example, or an invented scoring rule for
   missing requirements. Recommend a supported method with a reason if needed.
2. Create a separate task directory. Place the evolvable code under
   `version_control.local_path`. Keep platform evaluators and datasets under
   the task root, outside the candidate directory. For a gateway, use the
   separate layout in [remote-evaluation.md](remote-evaluation.md) and keep
   evaluator code, installed resources, data, credentials and gateway state on
   the user's machine. Both evaluators must execute the supplied candidate.
   Explicitly size the repository context and generation settings using the
   [context sizing guidance](configuration.md#context-and-generation-sizing).
   Do not inherit the 5-line repository context or 4K custom-coder defaults:
   inspect the actual code and helpers, allow growth during evolution, and record
   the intended model capacity and chosen values in the task README. Input
   context and output tokens are separate settings, not interchangeable limits.
3. For platform-container evaluation, prepare Python 3.12 in an isolated environment, install the platform revision
   matching the target deployment, and install the final task requirements. The
   distribution is `llm4ad-next`, while the Python import is `llm4ad`. For
   native or Docker gateway verification, follow [remote-evaluation.md](remote-evaluation.md).
4. For a platform-container task, check dependency compatibility with `uv pip check --python <venv-python>`.
   Run the task's `test_evaluator.py`: exercise known valid and invalid examples,
   actual candidate execution, score direction and changes between candidates,
   and timeout handling when applicable. Check initial source syntax and any
   required build. A loader-only test or an agent's claim of success is insufficient.
5. For a platform-container task, run the bundled helper with that local environment's Python:

   ```bash
   <venv-python> <skill-dir>/scripts/validate_and_pack.py <task-dir> \
     --output <output-dir>/my-task.zip
   ```

   The helper snapshots the project into a ZIP, extracts it into a fresh temporary
   directory, validates its config and EVOLVE blocks, evaluates every configured
   data instance using a copy of the candidate directory, and checks that an empty
   candidate is rejected. It then runs the included `test_evaluator.py` in a
   separate process and requires a successful exit. Only after both pass does it
   add `validation.json` to the ZIP and write a sibling `my-task.validation.json`.
   For a gateway task, the same helper uses the SDK worker offline with the
   evaluator and dependencies in the chosen environment. Both paths require
   successful execution before export. Supply `--timeout` if the full check
   needs more than 120 seconds. Install dependencies before running the helper.
6. If validation fails, inspect the concrete error, fix the package, and rerun.
   Deliver a ZIP only after the helper succeeds. Changing the files afterward
   requires regenerating the archive. If the required compiler, solver, dataset,
   or dependency is unavailable, report the missing verification instead of
   claiming success. Local validation records the host OS; cloud Linux
   compatibility needs a matching Linux environment when the task is OS-specific.

## Handoff

Provide the ZIP and report paths, problem and objective, method, verification
results, tested environment and web import instructions in the user's language.
Label gateway results as offline smoke results and include the startup and binding
instructions for after import. Both paths leave evolution for the platform. Include
only portable task configuration and task inputs in the ZIP. Keep model credentials,
local environments, past runs and account/task identifiers out of the archive.

This Skill is independent of `llm4ad-task-builder`. Keep its existing in-platform
and portable behavior unchanged when working on this cloud packaging workflow.
