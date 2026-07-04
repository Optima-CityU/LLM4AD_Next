# Installation

This guide covers installing LLM4AD on your system.

## System Requirements

- **Python**: 3.12 recommended (pinned in `.python-version`). Supported: `>=3.10`, but the `chatv2` AI build agent requires `>=3.11`.
- **Operating System**: Linux, macOS, or Windows
- **Package Manager**: [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Installing uv (Recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and resolver. It's significantly faster than pip and provides better dependency management.

### Linux and macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows (PowerShell)

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After installation, restart your terminal or run:

```bash
source ~/.bashrc  # or ~/.zshrc
```

## Installation Methods

### Method 1: Clone from Source (Recommended for Development)

This method allows you to modify the code and contribute to the project.

```bash
# Clone the repository
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd LLM4AD

# Install in editable mode with all dependencies
uv sync

# Or with pip
pip install -e ".[all]"
```

### Method 2: Install from PyPI (Coming Soon)

```bash
pip install llm4ad
```

## Optional Dependencies

LLM4AD uses optional dependency groups to keep the core installation lightweight. You can install specific groups based on your needs:

### Available Groups

| Group | Description | Dependencies |
|-------|-------------|--------------|
| `infra` | Distributed computing infrastructure | Ray, Prometheus, psutil |
| `providers` | LLM provider integrations | OpenAI, Anthropic, tiktoken |
| `eval` | Evaluation and benchmarking tools | pandas |
| `dev` | Development tools | pytest, black, isort, mypy, ruff |
| `docs` | Documentation building tools | mkdocs, mkdocs-material |
| `all` | All optional dependencies | All of the above |

### Installing Specific Groups

```bash
# Install core + infra + providers
uv sync --extra infra --extra providers

# Install only development tools
uv sync --extra dev

# Install everything
uv sync --extra all
```

### With pip

```bash
# Install specific groups
pip install -e ".[infra,providers,dev]"

# Install everything
pip install -e ".[all]"
```

## Verifying Installation

Check that LLM4AD is installed correctly:

```bash
# Check version
python -c "import llm4ad; print('LLM4AD installed successfully')"

# Test CLI
llm4ad --help

# List registered components
llm4ad list
```

Expected output:

```
Usage: llm4ad [OPTIONS] COMMAND [ARGS]...

  LLM4AD - LLM for Algorithm Design

Options:
  --help  Show this message and exit.

Commands:
  list     List all registered components
  run      Run an algorithm design pipeline
  version  Show version information
```

## Setting Up API Keys

LLM4AD requires API keys for LLM providers. Set them as environment variables:

### OpenAI

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

### Anthropic

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

### For Persistent Configuration

Add to your shell configuration file (`~/.bashrc`, `~/.zshrc`, etc.):

```bash
# OpenAI
export OPENAI_API_KEY="your-openai-api-key"

# Anthropic
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

Then reload:

```bash
source ~/.bashrc  # or ~/.zshrc
```

## Development Setup

If you plan to contribute to LLM4AD, set up the development environment:

```bash
# Clone the repository
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd LLM4AD

# Install with development dependencies
uv sync --extra all

# Install pre-commit hooks (optional)
pip install pre-commit
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/llm4ad

# Run specific test file
pytest tests/evaluator/test_base.py
```

### Code Quality Checks

```bash
# Format code
black src/ tests/
isort src/ tests/

# Lint code
ruff check src/ tests/ --fix

# Type check
mypy src/
```

## Troubleshooting

### Python Version Issues

**Error**: `Python version 3.10 or higher required`

**Solution**:
```bash
# Check your Python version
python --version

# Install a newer version if needed
# Using pyenv (recommended)
pyenv install 3.11
pyenv global 3.11
```

### uv Installation Issues

**Error**: `uv: command not found`

**Solution**:
```bash
# Try installing uv with pip
pip install uv

# Or use pip directly instead of uv
pip install -e ".[all]"
```

### Dependency Conflicts

**Error**: `Could not resolve dependencies`

**Solution**:
```bash
# Clear uv cache
uv cache clean

# Try installing again
uv sync --extra all

# Or create a fresh virtual environment
python -m venv venv
source venv/bin/activate
uv sync --extra all
```

### Import Errors

**Error**: `ModuleNotFoundError: No module named 'llm4ad'`

**Solution**:
```bash
# Make sure you're in the project directory
cd LLM4AD

# Install in editable mode
pip install -e ".[all]"

# Or add src to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```

## Next Steps

After installation, proceed to:

- [Quick Start Guide](quickstart.md) - Run your first experiment
- [Configuration Guide](configuration.md) - Learn about configuration options
- [Writing Evaluators](evaluators.md) - Create custom evaluation functions

## Getting Help

If you encounter issues not covered here:

- 📖 [Documentation Home](../index.md)
- 💬 [Discussions](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- 🐛 [Issue Tracker](https://github.com/Optima-CityU/LLM4AD_Next/issues)
