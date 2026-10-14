## Why

AutoDiscovery currently combines paper analysis, evaluator design, and task-package construction in one publication step. Its only durable result is a runnable task, so users cannot inspect or compare research directions before building, and must leave the workbench to run or tune evolution. This duplicates project-management's task builder instead of offering a distinct research-to-experiment workflow.

## What Changes

- Replace the single AutoDiscovery stage with four backend-owned stages: paper baseline, candidate directions, evaluation design, and build/evolution.
- Publish source-grounded, versioned intermediate artifacts before any task package is built. Keep alternatives and unresolved assumptions visible rather than silently picking one direction.
- Build and validate the selected runnable package only in the final stage, then import it idempotently through the existing project/task service.
- Embed task status, logs/results, start/stop controls, child versions, and quick-open/close evolution details into that final stage; retain the ordinary project page as an advanced route.
- Give the stage Agent workspace-scoped task tools to inspect status, create a child, propose/apply parameter changes, and start or stop a linked task. Never grant it arbitrary task IDs or let it mutate the configuration of a running task.
- Distinguish technical package validation from the scientific adequacy of evaluation evidence. Do not invent measurements or claim improvement before a real run.

## Capabilities

### New Capabilities

- `autodiscovery-staged-research`: Source-grounded baseline, candidate comparison, and evaluation-contract artifacts with revision-aware navigation.
- `autodiscovery-build-and-evolve`: Validated package import and embedded lifecycle/parameter control for workspace-linked evolution tasks.

### Modified Capabilities

None. Existing project/task execution remains authoritative; this change adds a research-workspace entry point and scoped adapter.

## Impact

- Research workflow definitions, stage schemas/publication, stage-specific Skills and MCP bridge, and RustFS artifact retrieval.
- Paper workbench stage navigation, AutoDiscovery result panels, generated OpenAPI client, and bilingual guidance.
- Existing task copy, update, run, stop, tree, stats, and log services are reused through workspace-scoped APIs. Task ownership, source-version staleness, and already-linked legacy discovery proposals must remain safe.
- No new frontend dependency or separate evolution runner.
