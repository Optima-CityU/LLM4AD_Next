# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Development Commands
All commands should be run from the repository root.

### Dependency Management
- Install all dependencies (including development extras): `uv sync`
- Install specific optional extras: `uv sync --extra <extra1>,<extra2>` (e.g. `uv sync --extra docs,providers`)
- Available extras: `infra`, `providers`, `eval`, `dev`, `docs`, `all`

### Comments
- write docstring and in Google format
- all comments in english

### Code Quality
- Format code:
  ```bash
  black src/ tests/
  isort src/ tests/
  ruff check src/ tests/ --fix
  ```
- Lint code: `ruff check src/ tests/` (ruff configured in pyproject.toml under [tool.ruff.lint] section)
- Type check: `mypy src/`

### Testing
- Run all tests: `pytest`
- Run tests with coverage report: `pytest --cov=src/llm4ad`
- Run only unit tests: `pytest -m unit`
- Run only integration tests: `pytest -m integration`
- Run specific test file: `pytest tests/path/to/test_file.py`
- Run specific test function: `pytest tests/path/to/test_file.py::test_function_name`
- Run CLI tests: `pytest tests/frontend/test_cli.py`

### Documentation
- Serve local documentation with live reload: `mkdocs serve` (available at http://localhost:8000)
- Build static documentation for deployment: `mkdocs build` (output to `site/` directory)

### CLI
- Run the main LLM4AD CLI: `llm4ad` (entrypoint: `llm4ad.frontend.cli:main`)
- Show CLI help: `llm4ad --help`
- List registered components: `llm4ad list`
- Run design pipeline: `llm4ad run <config-file>`
- Interactive configuration consultant: `llm4ad chat` (uses `--provider` to select provider from global settings)
  - `--provider/-p <name>`: Use a named provider from `~/.llm4ad/settings.yaml` (default: first provider)
  - `--resume <session_id>`: Resume a saved session
  - `--output/-o <path>`: Output config path (default: `config.yaml`)

### CI/CD
- GitHub Actions workflow runs on PRs and pushes to main: `.github/workflows/ci.yml`
- CI runs tests on Python 3.12 across Ubuntu, macOS, and Windows
- CI includes: ruff linting, mypy type checking, unit tests with coverage reporting

### Git Conventions
- Commit message format:
  ```
  (feat|fix|ref): title

  1. ...
  2. ...
  3. ...
  ```
- Use `feat` for new features, `fix` for bug fixes, `ref` for refactoring
- List changes as bullet points after a blank line

## High Level Architecture
LLM4AD is a modular platform for large language model based algorithm design, with clear separation of concerns across components:

1. **Core Components** (all implement base class interfaces for easy extension):
   - `config/`: Pydantic-based configuration schemas and loading system. Includes `settings.py` for global settings (`~/.llm4ad/settings.yaml`) that provides shared provider configurations across projects
   - `utils/`: Shared utilities, including a registry pattern for dynamic component registration
   - `provider/`: LLM provider integrations (OpenAI, Anthropic, etc.) with unified interface
   - `planner/`: High-level algorithm design planning system, includes memory management
   - `coder/`: Code generation components that convert design plans into runnable algorithm code
   - `evaluator/`: Benchmarking and evaluation system for testing generated algorithm performance, includes task definition framework
   - `orchestrator/`: Workflow orchestration layer that coordinates planner, coder, and evaluator components for end-to-end algorithm design pipelines
   - `consultant/`: Interactive LLM-powered configuration wizard that guides users through pipeline setup via multi-turn conversation. Features dynamic stages based on template level (minimal/standard/complete), context gathering from user files, multiple-choice interaction style, and session save/resume
   - `infra/`: Distributed computing infrastructure layer for scaling workloads using Ray

2. **Key Patterns**:
   - All extendable components use the registry pattern from `utils.registry` for dynamic discovery and swapping
   - Interfaces are defined as abstract base classes in each module's `base.py` file
   - Dependencies are grouped into optional extras to keep core installation lightweight
   - Automatic task directory organization: each run gets its own isolated workspace at `{base_dir}/{project_name}/{run_id}/` containing:
     - `best/`: Snapshot of the best individual at run-end — `code/` plain copy of the best worktree, `metadata.json`, and `summary.txt`. MEoH adds a `pareto/<idx>/` subdir per archive member.
     - `state/`: Cached evolution state (`evolution_state.json`) for resume and visualization
     - `logs/`: Log files from this run
     - `checkpoints/`: Evolution checkpoints
     - `generated/`: Generated code files
     - `temp/`: Temporary files

3. **Configuration Features**:
   - YAML configuration with Pydantic validation
   - Global settings file (`~/.llm4ad/settings.yaml`): shared provider configurations that are merged with per-task configs at raw dict level before Pydantic validation. Supports `${ENV_VAR}` expansion. Task configs reference providers by name; matching global providers supply defaults (api_key, base_url, model, etc.)
   - Multiple named LLM providers: configure different models for planning vs coding
   - Flexible dataset discovery: three modes
     - `files`: Explicit list of specific dataset files
     - `directory`: Automatically traverse all files in a directory with optional recursion
     - `glob`: Glob pattern matching for dataset files
   - Custom evaluator support: users can implement custom evaluators by subclassing the provided base classes:
     - `PythonEvaluator`: For direct Python code evaluation
     - `ExecutableEvaluator`: For evaluating compiled command-line executables
     - `BenchmarkEvaluator`: For standard benchmarks with multi-instance result aggregation
     - Evaluators can be pre-registered or dynamically imported from external modules via the configuration `module` field

## Requirements

1. Run `uv run --python 3.12 ruff check src/` every time when finish coding jobs, and fix the errors it reports