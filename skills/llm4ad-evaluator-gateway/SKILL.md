---
name: llm4ad-evaluator-gateway
description: Configure and verify an LLM4AD Next evaluation gateway or implement a custom evaluator. Supports native and Docker deployment.
---

# LLM4AD Evaluator Gateway

1. Establish the platform URL, evaluation machine, operating system, and whether to
   reuse an evaluator or implement one. Confirm any required data, compilers, and devices.
2. For a new evaluator, follow the [SDK contract](references/evaluator.md). Keep the
   reference data and credentials with the gateway.
3. Use the matching [native](references/native.md) or [Docker](references/docker.md)
   workflow to run a real local evaluation before connecting. Connect only when the
   user has authorized the target platform and account IDs.
4. Check readiness and assist the user with selecting the evaluator on the platform.
   See [operations](references/operations.md) for progress, setup issues, and evidence.

Use `llm4ad --skill gateway` to discover the available topics, then request a single
topic with `--topic`. For a portable task ZIP that uses this gateway, read
`llm4ad --skill cloud-package --topic remote-evaluation`.
