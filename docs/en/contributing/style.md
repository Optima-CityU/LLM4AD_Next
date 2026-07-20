# Code Style

This page documents the conventions LLM4AD enforces. The intent is to make code reviews mechanical: the linter and formatter make most decisions; reviewers can spend time on substance.

## Python

### Tooling

| Tool | Config | What it enforces |
|---|---|---|
| **Ruff** | `[tool.ruff.lint]` in `pyproject.toml` | Lint: unused imports, undefined names, deprecated APIs, simple bug patterns |
| **Black** | `line-length = 100`, `target-version = py312` | Formatting (whitespace, line breaks, string quotes) |
| **isort** | Black-compatible profile | Import ordering and grouping |
| **mypy** | `[tool.mypy]` in `pyproject.toml` | Static type checking |

Run them in this order before opening a PR:

```bash
uv run ruff check src/ tests/ --fix
uv run black src/ tests/
uv run isort src/ tests/
uv run mypy src/
```

CI runs the same suite. A clean run is a precondition for merge.

### Type hints

- Type-hint every public function and method (anything reachable from a module's `__init__.py`).
- Type-hint internal functions whenever the call site cannot trivially infer the types.
- Prefer `from __future__ import annotations` so annotations are strings (no runtime import cost).
- Use `Annotated[X, ...]`, `TypeAlias`, and PEP 604 (`X | None`) freely — the project requires Python 3.12.

```python
from __future__ import annotations

from typing import Literal

def aggregate(
    values: list[float],
    *,
    mode: Literal["mean", "median"] = "mean",
) -> float:
    ...
```

### Docstrings

Use Google-style docstrings (per `CLAUDE.md`). Keep them tight: a one-line summary, then `Args` / `Returns` / `Raises` only when they add information beyond the signature.

```python
def merge_with_global_settings(
    global_data: dict[str, Any],
    task_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge global settings into task configuration.

    For each provider entry in ``task_data["providers"]``, if a provider with
    the same name exists in ``global_data["providers"]``, the global definition
    is used as the base and task-level fields are overlaid on top.

    Args:
        global_data: Raw dict from the global settings file.
        task_data:   Raw dict from the task configuration file.

    Returns:
        A new dict with providers merged. Non-provider fields in ``task_data``
        are left untouched.
    """
```

All comments and docstrings are in **English**, even in Chinese-localized example projects (per `CLAUDE.md`).

### Logging

Use Loguru via the `llm4ad.utils.logging` helpers (or the standard `from loguru import logger` form). Don't use `print()` outside the CLI's Rich-rendered output.

```python
from loguru import logger

logger.info("provider call duration={:.1f}ms", elapsed)
```

### Async

Most provider/coder/evaluator entry points are async. Inside async code:

- Use `asyncio.gather` / `asyncio.create_task` rather than blocking.
- Be explicit about cancellation (`async with asyncio.timeout(...)`, or honor cancellation in `finally`).
- Don't mix `requests` / `urllib` with `aiohttp` — pick one stack.

### Error handling

- Raise typed exceptions (`AdvisorError`, `BuildError`, `ValueError` — never bare `Exception`).
- Use `from e` when re-raising so the chain is preserved.
- Don't swallow errors silently; if a path is intentionally best-effort (e.g. printing the `best/` path on completion), narrow the `except` to a comment-justified `Exception` and explain why with `# noqa: BLE001`.

### Tests

- Tests use the same Black/Ruff config as `src/`.
- Mark every test with `@pytest.mark.unit` or `@pytest.mark.integration`. CI relies on those.
- Use `MockProvider` for tests that exercise provider/coder/evaluator paths so they stay deterministic and offline.
- Async tests use `pytest-asyncio`'s `asyncio_mode = "auto"`; you can write `async def test_foo(): ...` directly.

## Frontend

The frontend (`src/frontend/`) is React + TypeScript + Vite, formatted by **Biome**.

```bash
cd src/frontend
bun run lint        # Biome lint
bun run format      # Biome format
bun run typecheck   # tsc --noEmit
```

Conventions:

- Components are PascalCase (`UserManualContent.tsx`).
- Hooks are camelCase prefixed with `use` (`useGuideState`).
- Configuration files are kebab-case (`guide.config.ts`).
- Translations live in `src/frontend/src/i18n/locales/{en,zh}.json`. Every UI label has both an `en` and a `zh` entry; missing one will cause a runtime fallback to the other locale.

## Markdown / docs

- Use ATX headings (`#`, `##`, …). Don't use Setext (`===`, `---`).
- One H1 per file (the page title). Subsections use H2 / H3.
- Inline code with backticks (`` `like_this` ``); fenced blocks with the language tag (` ```python `, ` ```yaml `).
- Internal cross-links use relative `(foo.md)` form. The frontend's `resolveDocLink` rewrites these to in-app navigation; absolute and `http://` links open in a new tab.
- File names are kebab-case (`web-ui/overview.md`, `examples/symbolic-regression.md`).
- Every English file has a Chinese sibling at the same relative path. Mirror the heading tree.
- "See also" footers are recommended on guide pages and required on API pages (with a final source-of-truth bullet pointing to `src/llm4ad/...`).

### Terminology

| Use | Avoid |
|---|---|
| Orchestrator / Planner / Coder / Evaluator | Coordinator / Designer / Generator / Tester |
| Island GA / DyCA / MEoH | island-ga / dyca / meoh in prose (those are registry names) |
| `EVOLVE` block / `EVOLVE_START` marker | "evolve region", "tag" |
| Worktree | "branch" (worktrees are git-specific, not branches) |
| Run directory | "output folder" |

## See also

- [Contribution Guidelines](guidelines.md) — PR flow
- [Development Setup](development.md) — running the toolchain locally
- `pyproject.toml` and `src/frontend/biome.json` — the configuration files behind these rules
