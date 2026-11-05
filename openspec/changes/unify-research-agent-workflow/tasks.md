- [x] Repair shared subagent approval, definitions and background lifecycle; add regression coverage.
- [x] Add automatic workflow coordinator, stage MCP context/publication and explicit Skill injection.
- [x] Derive and relay actual agent/subagent progress from live and historical messages.
- [x] Replace proposal/rebuttal top stepper with a shared responsive right-side progress/results view.
- [x] Add Chinese/English UI text, regenerate OpenAPI client and verify tests/builds/browser behavior.

Validation: backend 32 tests, host 46 tests, CloudCLI provider and progress tests, TypeScript, Ruff, production builds and browser component checks. The real SDK completed a three-request parent/child/parent exchange through a local mock Anthropic gateway, including the child parent-tool link and returned report. Browser checks used the actual shared component with fixtures in light/dark themes and at 390px width. A paid-model end-to-end workflow has not been run.
