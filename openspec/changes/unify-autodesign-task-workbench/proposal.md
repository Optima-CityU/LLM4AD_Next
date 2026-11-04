## Why

AutoDiscovery, the legacy AI task builder, and project management expose separate
conversations and package formats. Existing tasks need an Agent that understands
their actual run files and can create a new experiment without overwriting the
parent. A task should retain its context when switching between conversation and
execution views.

## What Changes

- Rename project management to Algorithm Evolution Projects; offer AutoDesign
  only as a New Task build mode, without a separate navigation item.
- Add task-workbench/Agent views backed by a persistent task-specific session.
- Support taskless conversational design with optional references and launch verified Cloud packages
  into new or existing projects through the common importer.
- Give the Agent scoped tools for real files, child experiments, parameter edits,
  validation, publication, start and stop.
- Inject the Cloud package Skill and a new run-analysis Skill documenting actual
  artifacts and score semantics.
- Remove the legacy AI choice from new-task creation and direct new design actions
  to the same-page AutoDesign flow; preserve historical records.

## Impact

Add session metadata, an additive migration, scoped API/runtime tools and a
container validation entrypoint. Reuse CloudCLI, current task execution/versioning,
RustFS and existing UI components. No new project dependency is needed.
