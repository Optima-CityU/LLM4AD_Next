---
name: llm4ad-cloud-package
description: "Build, locally validate, and ZIP a portable algorithm task for the LLM4AD_Next cloud platform. Use when a user wants a project package to upload or import into the web platform, including its evaluator, data, configuration, and supported evolution method."
---

# LLM4AD Cloud Project Package

Generate a self-contained task directory, execute its baseline through the installed
platform evaluator, and deliver the validated ZIP with its validation report. This
is a portable Skill for a local coding agent with file and command access. It does
not require an AutoDiscovery runtime tool or a second model for generation.

## Read the relevant contracts

- Start with [references/task-definition.md](references/task-definition.md):
  understand the actual task and resolve material unknowns before generating code.
- Before creating the project, read [references/configuration.md](references/configuration.md)
  for the archive layout, every configuration section, dependency setup, and the
  distinction between a cloud model binding and a local provider.
- Read [references/platforms.md](references/platforms.md) for native Windows
  PowerShell, macOS and Linux commands, portable paths/dependencies, and how local
  validation differs from verification in the deployment's task container.
- Before implementing an evaluator, read
  [references/evaluator-contract.md](references/evaluator-contract.md).
- Before selecting or creating inputs, read
  [references/dataset-contract.md](references/dataset-contract.md) for data schemas,
  path discovery, provenance, scoring aggregation and real verification inputs.
- To select or change an evolution method, read
  [references/algorithms.md](references/algorithms.md). It lists the seven methods
  implemented by the platform, their matching planners, and small starting configs.
- For delivery, read [references/web-import.md](references/web-import.md). It
  describes **New Task → Import ZIP**, model selection, and the fallback
  directory/YAML route for older deployments.
- Start from [assets/knapsack](assets/knapsack) for a complete, small Python example.
  Adapt its problem, evaluator, inputs, and configuration to the user's task.

## Build and validate

1. Resolve the problem, candidate function and I/O, objective and constraints,
   available data, local and target OS/architecture, language/runtime, and selected evolution method. Use the user's
   choices; inspect supplied materials first and ask about unresolved information
   that affects the runnable contract. Agree on the brief before generating;
   an already complete, approved brief does not need another confirmation. Never
   substitute toy data, an unrelated example, or an invented scoring rule for
   missing requirements. Recommend a supported method with a reason if needed.
2. Create a separate task directory using the documented layout. Keep the
   evaluator and datasets outside `version_control.local_path`; place the actual
   evolvable code inside it. The evaluator must execute that candidate directory.
   Explicitly size the repository context and generation settings using the
   [context sizing guidance](references/configuration.md#context-and-generation-sizing).
   Do not inherit the 5-line repository context or 4K custom-coder defaults:
   inspect the actual code and helpers, allow growth during evolution, and record
   the intended model capacity and chosen values in the task README. Input
   context and output tokens are separate settings, not interchangeable limits.
3. Prepare Python 3.12 in an isolated environment, install the platform revision
   matching the target deployment, and install the final task requirements. The
   distribution is `llm4ad-next`, while the Python import is `llm4ad`. Do not infer
   a PyPI requirement named `llm4ad` from that import.
4. Check dependency compatibility with `uv pip check --python <venv-python>`.
   Run the task's `test_evaluator.py`: exercise known valid and invalid examples,
   actual candidate execution, score direction and changes between candidates,
   and timeout handling when applicable. Check initial source syntax and any
   required build. A loader-only test or an agent's claim of success is insufficient.
5. Run the bundled helper with that environment's Python:

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
