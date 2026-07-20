# Development Setup

This page walks through getting a working LLM4AD development environment, from a fresh clone to running tests, the docs site, the CLI, and the Web UI.

## Prerequisites

- **Python 3.12** (required; CI runs 3.12)
- **uv** — fast Python package manager. Install per the [official guide](https://docs.astral.sh/uv/).
- **git** ≥ 2.5 (the version-control layer relies on `git worktree`)
- For the Web UI: **bun** ≥ 1.0 (frontend), **Docker** (containerized dev)

Optional: **make**, **ripgrep** (fast searching), an LLM API key if you want to run real evolution rather than mocked.

## Clone and install

```bash
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd llm4ad

# Install everything (dev + docs + all extras + provider SDKs)
uv sync --extra all
```

To install only what you need:

```bash
uv sync                              # core only
uv sync --extra dev                  # add lint/test tooling
uv sync --extra dev,providers,docs   # typical contributor mix
```

Available extras (see `pyproject.toml`): `infra`, `providers`, `eval`, `tsp`, `lunarlander`, `dyca`, `meoh`, `dev`, `docs`, `all`.

## Smoke test

```bash
# Confirm the entry point is wired up
uv run llm4ad version
uv run llm4ad list                   # prints all registered providers/planners/coders/...
```

You should see at least the built-in components (`openai_compatible`, `anthropic`, `mock`, `island_ga`, `dyca`, `meoh`, …).

## Running tests

```bash
# Unit tests (fast)
uv run pytest -m unit

# Integration tests (slower; some require uv, git worktrees, or external CLIs)
uv run pytest -m integration

# Whole suite with coverage
uv run pytest --cov=src/llm4ad

# A single file or test
uv run pytest tests/frontend/test_cli.py
uv run pytest tests/frontend/test_cli.py::test_help_renders
```

Tests are gated by markers in `pytest.ini`. The `MockProvider` is the default for unit tests so the suite stays deterministic and offline.

## Lint and type-check

The project uses Ruff (fast lint), Black (format), isort (imports), and mypy (types). The CI pipeline runs these in the same order:

```bash
uv run ruff check src/ tests/ --fix
uv run black src/ tests/
uv run isort src/ tests/
uv run mypy src/
```

A pre-commit hook is the easiest way to enforce them locally:

```bash
uv run pre-commit install            # one-time
uv run pre-commit run --all-files    # ad-hoc full pass
```

The `CLAUDE.md` requirement is to run `uv run --python 3.12 ruff check src/` after any code change and fix what it reports.

## Running the CLI against a real provider

Set the environment variables that the example configs read:

```bash
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="sk-..."
export LLM_MODEL="gpt-4o-mini"

uv run llm4ad run examples/applications/sorting_benchmark_python/config.yaml
```

For richer global config, populate `~/.llm4ad/settings.yaml` so task configs can reference providers by name; see [Configuration Guide](../guides/configuration.md#global-settings).

## Building the docs

The standalone docs site uses MkDocs Material:

```bash
uv sync --extra docs
uv run mkdocs serve            # live-reload at http://localhost:8000
uv run mkdocs build --strict   # CI-compatible build, fail on warnings
```

The in-app User Manual served by the frontend is sourced from the same `docs/` tree via Vite's `import.meta.glob` — see [Frontend Integration](../web-ui/frontend-integration.md).

## Running the Web UI

The Web UI has two halves: a FastAPI backend and a React frontend. Both ship with Dockerfiles.

```bash
# Backend (development; auto-reload)
cd src/backend
uv sync
uv run fastapi dev app/main.py            # http://localhost:8000

# Frontend (development; HMR)
cd src/frontend
bun install
bun run dev                               # http://localhost:5173
```

For a production-like run use Docker Compose; see [Web UI Overview](../web-ui/overview.md). The frontend can also be built into static assets via `bun run build`, served by nginx (`src/frontend/Dockerfile`).

For Docker-assisted local development, start the shared infrastructure with `docker/dev.sh infra` on macOS/Linux or `docker/dev.ps1 infra` on Windows, then run backend/frontend on the host. For full-stack debug ports or image-based deployment, see [Docker Local Startup](docker-local.md).

## Useful directories

| Path | Purpose |
|---|---|
| `src/llm4ad/` | The published Python library |
| `src/backend/` | FastAPI server that wraps the CLI for the Web UI |
| `src/frontend/` | React + Vite frontend |
| `examples/applications/` | Runnable example projects (see [Examples](../examples/index.md)) |
| `tests/` | Test suite (mirrors `src/llm4ad/` layout) |
| `docs/` | Bilingual documentation (this site) |
| `runs/` (created by example configs) | Default `base_dir` for run outputs |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `llm4ad: command not found` | shell not using the uv venv | `uv run llm4ad ...` or activate `.venv/bin/activate` |
| `KeyError: '${LLM_API_KEY}'` from a config | env var not exported | `export LLM_API_KEY=...` before `llm4ad run` |
| `git worktree add` errors | repo missing a HEAD commit | run inside an existing repo, or set `version_control.auto_initialize: true` |
| `mkdocs build --strict` fails | a stub link or missing zh sibling | `grep -r "Coming soon" docs/` and `diff <(ls docs/en) <(ls docs/zh)` |
| Frontend manual page is blank | new key in `guide.config.ts` without docs file | create `docs/{en,zh}/<key>.md` |

## See also

- [Contribution Guidelines](guidelines.md) — PR flow, commit format
- [Docker Local Startup](docker-local.md) — local infrastructure, full-stack debug, and image deployment commands
- [Code Style](style.md) — Python / frontend / markdown style
- [CLI Reference](../guides/cli.md)
- [Configuration Guide](../guides/configuration.md)
