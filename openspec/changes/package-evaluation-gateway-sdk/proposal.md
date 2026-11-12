# Why

The gateway should run directly from the installed LLM4AD SDK with an inspectable structure. Optional templates remain useful for users who want starter evaluator or Docker files, but they must not become a required project-generation step. HGamey is an optional application case, not a dependency of the core gateway.

# What Changes

- Consolidate protocol/transport/execution under `llm4ad/gateway/sdk/` with compatibility aliases for existing `llm4ad.remote` imports.
- Separate reusable templates from complete examples; co-locate each example source and deployment under `examples/`.
- Include the HGamey native source actually used, native build scripts and a source-build Dockerfile; document phone Root, wireless authorization, game and test APK installation in README.
- Support direct installed adapter startup, custom file or module evaluators, source inspection and explicit local checks.
- Retain optional template generation over the same SDK core for system, Docker or user-defined launchers.
- Preserve the existing HGamey evaluation and device lifecycle while packaging its Python implementation inside LLM4AD.

# Capabilities

## New Capabilities
- `packaged-evaluation-gateway`: Installable direct gateway startup, optional templates and local validation.

# Impact

SDK packaging, CLI, examples, documentation and tests only. No platform API or database migration, mandatory native game dependency, new Python dependency or automatic PyPI release.
