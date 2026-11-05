---
name: llm4ad-cloud-package
description: "Build and verify runnable LLM4AD_Next algorithm projects with real evaluators, data, configuration and supported evolution methods. Use for in-platform AutoDesign construction or standalone project delivery."
---

# LLM4AD Cloud Project

In the **AutoDesign web runtime**, use the project, data and evaluator contracts
from this Skill, then follow [references/web-runtime.md](references/web-runtime.md)
for direct directory verification and task setup. The standalone delivery workflow
below does not apply in that runtime. The platform already provides execution,
dependencies and task creation.

For a **standalone local delivery request**, follow
[references/local-delivery.md](references/local-delivery.md). Its delivery and
import procedure applies only outside the web runtime.

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
- Read [references/memory.md](references/memory.md) before writing memory settings:
  enable memory and automatic extraction by default, keep bindings portable, and
  explain task memory versus long-term memory in the handoff.
- Start from [assets/knapsack](assets/knapsack) for a complete, small Python example.
  Adapt its problem, evaluator, inputs, and configuration to the user's task.

## In-platform construction

Establish the actual task, candidate I/O, objective and data before authoring.
Use the actual code and model capacity to size context and generation settings.
Keep trusted data and evaluator outside the evolvable directory. Maintain a
readable README in the workspace's reply language, including the browser
language for Auto, while preserving code identifiers and quoted sources.

Author and edit the project directory directly. Follow
[references/web-runtime.md](references/web-runtime.md) to verify dependencies,
source syntax, the real baseline and evaluator tests through the platform MCP.
Repair concrete failures before presenting the project as ready. The user then
opens the existing task setup to select models, inspect parameters and confirm
execution; verified files become task inputs directly.
