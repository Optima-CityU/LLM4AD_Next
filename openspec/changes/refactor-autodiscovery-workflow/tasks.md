## 1. Stage contracts and Skills

- [x] 1.1 Add four AutoDiscovery stages, run kinds, prerequisites, and stage-specific Skill registration while preserving legacy `discovery` links.
- [x] 1.2 Add versioned baseline, candidate and evaluation artifact schemas and publication handling; keep final packages in the existing proposal path.
- [x] 1.3 Add workspace-owned stage-artifact retrieval and Agent predecessor-artifact read access through the stage bridge.
- [x] 1.4 Write focused Skills for source baseline, candidate comparison, evaluation design and final package/evolution work; test stage-to-Skill and tool isolation.
- [x] 1.5 Permit a source-free baseline session, expose the existing arXiv MCP there, and import a downloaded paper into the owned source version before publication.

## 2. Linked task lifecycle

- [x] 2.1 Add workspace-scoped linked root/child task lookup, status/results and owner/family authorization tests.
- [x] 2.2 Add idempotent final-stage task import and embedded start/stop controls using existing task services; distinguish user stop from unexplained failure.
- [x] 2.3 Add non-activating child copy, configuration patch validation/diff and status-aware child tuning; test no mutation of running parent and no implicit active-version switch.
- [x] 2.4 Expose the final-stage scoped task tools to the Agent with fresh status checks and no credential disclosure.

## 3. Workbench presentation

- [x] 3.1 Reuse the research stage navigator for algorithm mode and provide bilingual stage guidance.
- [x] 3.2 Render source-grounded baseline, candidate comparison and evaluation contract as readable stage-specific results with stale/unknown states.
- [x] 3.3 Add a collapsible final-stage evolution area with linked task status, bounded logs/results, start/stop, child versions and optional advanced-project entry.
- [x] 3.4 Regenerate the TypeScript API client with openapi-ts and add frontend state/render tests without adding dependencies.
- [x] 3.5 Show both paper-upload and Agent-led arXiv intake paths in the initial workbench, with bilingual guidance.

## 4. Verification

- [x] 4.1 Run backend publication, service, ownership and task-lifecycle tests; fix regressions.
- [x] 4.2 Run frontend tests, typecheck/build, and the required `uv run --python 3.12 ruff check src/`.
- [x] 4.3 Validate OpenSpec scenarios against implementation, including legacy workspaces, source edits, stage revisions, and real task-state transitions.
