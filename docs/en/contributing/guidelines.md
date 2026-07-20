# Contribution Guidelines

LLM4AD is open source under the BSD 3-Clause License. Contributions are welcome — bug reports, fixes, new providers / coders / orchestrators / evaluators, docs improvements, and example projects.

If you are setting up your environment for the first time, start with [Development Setup](development.md). For style conventions see [Code Style](style.md).

## Ways to contribute

| Type | Where it goes |
|---|---|
| Bug report | [GitHub Issues](https://github.com/Optima-CityU/LLM4AD_Next/issues) with reproduction steps |
| Feature request | GitHub Issues with motivation and example use case |
| Code change | Pull request against `main` |
| New example | `examples/applications/<your_example>/` + a walkthrough page under `docs/{en,zh}/examples/` |
| New provider / coder / evaluator / orchestrator | New file under the matching `src/llm4ad/...` directory + tests |
| Docs fix | PR against `docs/` |

## Filing a good issue

A useful bug report includes:

- LLM4AD version (`llm4ad version`) and Python version
- Operating system and shell
- Minimal reproducing config or command
- Full traceback (run with `--log-level DEBUG` if relevant)
- What you expected vs. what happened

Reproducible issues are triaged faster. If your issue depends on a private LLM endpoint, please reproduce against `MockProvider` first when possible.

## Pull request flow

1. **Fork and branch** — branch off `main` with a descriptive name. Recommended pattern: `<type>/<short-slug>`, e.g. `feat/anthropic-streaming`, `fix/cli-best-path`, `ref/registry-cleanup`.
2. **Make a focused change** — one logical change per PR. Refactors should not bundle behavior changes.
3. **Test locally** — `pytest -m unit` for unit tests, `pytest -m integration` for slower tests; see [Development Setup](development.md).
4. **Lint and type-check** — `ruff check src/ tests/ --fix`, `black src/ tests/`, `isort src/ tests/`, `mypy src/`. CI runs the same checks.
5. **Open a PR** — small description with motivation, summary of changes, and any verification steps. Link the relevant issue if applicable.
6. **Address review** — push fixups; maintainers squash-merge.

CI runs on Python 3.12 across Ubuntu and macOS (`.github/workflows/ci.yml`). A passing CI is required before merge.

## Commit message format

Follow Conventional Commits, with the project's accepted prefixes:

```
(feat|fix|ref): title

1. <change one>
2. <change two>
3. ...
```

- `feat:` — new user-facing capability
- `fix:` — bug fix
- `ref:` — refactor without behavior change
- Keep the title under 72 characters
- The numbered list is the PR-body convention; for tiny PRs a single sentence is fine

The git log is the source of truth for the [Changelog](../changelog.md), so please keep titles meaningful.

## Adding a new component

The five extensible families all use the same pattern:

1. Subclass the `Base*` class under `src/llm4ad/<family>/`.
2. Decorate or call the matching `register_*` so the registry picks it up.
3. Add the corresponding config schema under `src/llm4ad/config/<family>.py` (and wire it into `AppConfig` if it's an orchestrator/evaluator/coder discriminator).
4. Add tests under `tests/<family>/`. Use `MockProvider` to keep tests cheap and deterministic.
5. Document the new component on the relevant guide page (e.g. `docs/{en,zh}/guides/providers.md`) and add a short note to the API reference page (`docs/{en,zh}/api/<family>.md`).

If your new component needs an extra dependency, declare it as a new `[project.optional-dependencies]` group in `pyproject.toml` and reference it in the install instructions.

## Documentation contributions

Docs live in `docs/en/` and `docs/zh/`, plus the navigation configs:

- `mkdocs.yml` for the standalone documentation site
- `src/frontend/src/components/Guide/guide.config.ts` for the in-app User Manual

Every English file must have a Chinese sibling at the matching path; the same nav key has to appear in both nav configs. See [Code Style](style.md#documentation) for the heading and link conventions.

## Code of conduct

Be respectful. Assume good faith. Keep technical discussions technical. The maintainers reserve the right to lock or remove off-topic, hostile, or abusive content. The full text of the Contributor Covenant ([contributor-covenant.org](https://www.contributor-covenant.org/)) applies.

## License

All contributions are accepted under the BSD 3-Clause License that covers the rest of the repository (see [LICENSE](../license.md)). By submitting a pull request you confirm that you have the right to do so.

## See also

- [Development Setup](development.md) — get the dev environment running
- [Code Style](style.md) — Python, frontend, and markdown conventions
