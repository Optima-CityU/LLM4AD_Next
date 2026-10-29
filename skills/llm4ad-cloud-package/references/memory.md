# Memory in a cloud task package

Enable memory and automatic experience extraction unless the user explicitly
requests an offline/no-memory evolution run:

```yaml
memory:
  enabled: true
  auto_extraction:
    enabled: true
```

This is portable configuration. Do not embed MindMemOS URLs, API keys, user or
project identities, session IDs, or pinned card IDs from the author's account.
Do not force `mindmemos_cloud` in the package: web import resolves the backend
using the importing user's settings. Optional static cards can describe verified
task knowledge; do not invent successful or failed experiences before evaluation.

## Import and evolution

**New Task → Import ZIP** defaults to **Enable memory (recommended)** checked.
This explicit import choice overrides an old package's `memory.enabled: false`.
The user can uncheck it to disable memory for that task. Other task-specific
settings, including deliberate extraction tuning and static cards, are retained.

- A valid current-account model binding and an available MindMemOS service select
  long-term memory. Retrieval scopes and limits inherit the project's platform
  settings; cross-task experience is used only when those scopes permit it.
- Without a valid binding/service, task-local memory is enabled. It supports
  experience within the run; it is not a promise of cross-task retrieval.
- The import form offers the existing long-term model-binding editor when the
  platform supports it. Bind both a chat model and an embedding model there.
  This binding is separate from the model used for planning/code generation.
- The import result indicates the selected memory mode. Users can review and
  change task memory settings before starting evolution.

Automatic extraction can add model calls during evolution. Its existing
generation selection and extraction settings remain applicable; enabling memory
does not promise that every evaluated candidate produces a card. Do not change
extraction thresholds or limits arbitrarily when adapting a task.

## Local validation and handoff

`validate_and_pack.py` directly evaluates the baseline and executes
`test_evaluator.py`. It does not initialize the evolution planner or memory
manager, so memory can stay enabled in the delivered configuration while this
verification runs without model credentials.

A real local evolution run uses task-local memory unless separately configured
for another backend. Bind the local evolution provider as described in
`configuration.md`; do not copy cloud credentials into the package. Report local
baseline verification separately from any tested model or long-term integration.

In the task README and delivery message, state that memory is enabled, explain
the task-local fallback and optional long-term binding, and note that automatic
extraction uses model calls. Do not tell users that ZIP import has verified cloud
memory connectivity or run a complete evolution.
