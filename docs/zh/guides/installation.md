# 安装

本指南介绍如何在您的系统上安装 LLM4AD。

## 系统要求

- **Python**：3.12 或更高版本（必需；已在 `.python-version` 中固定）。
- **操作系统**: Linux、macOS 或 Windows
- **包管理器**: [uv](https://github.com/astral-sh/uv)（推荐）或 pip

## 安装 uv（推荐）

[uv](https://github.com/astral-sh/uv) 是一个快速的 Python 包安装程序和解析器。它比 pip 快得多，并提供更好的依赖管理。

### Linux 和 macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows (PowerShell)

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装后，重启终端或运行：

```bash
source ~/.bashrc  # 或 ~/.zshrc
```

## 安装方法

### 方法 1：从源代码克隆（推荐用于开发）

此方法允许您修改代码并为项目做出贡献。

```bash
# 克隆仓库
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd LLM4AD

# 以可编辑模式安装并包含所有依赖
uv sync

# 或使用 pip
pip install -e ".[all]"
```

### 方法 2：从 PyPI 安装（即将推出）

```bash
pip install llm4ad
```

## 可选依赖

LLM4AD 使用可选依赖组来保持核心安装轻量。您可以根据需要安装特定组：

### 可用组

| 组 | 描述 | 依赖 |
|---|---|---|
| `infra` | 分布式计算基础设施 | Ray、Prometheus、psutil |
| `providers` | LLM 提供商集成 | OpenAI、Anthropic、tiktoken |
| `eval` | 评估和基准测试工具 | pandas |
| `dev` | 开发工具 | pytest、black、isort、mypy、ruff |
| `docs` | 文档构建工具 | mkdocs、mkdocs-material |
| `all` | 所有可选依赖 | 以上所有 |

### 安装特定组

```bash
# 安装核心 + infra + providers
uv sync --extra infra --extra providers

# 仅安装开发工具
uv sync --extra dev

# 安装所有内容
uv sync --extra all
```

### 使用 pip

```bash
# 安装特定组
pip install -e ".[infra,providers,dev]"

# 安装所有内容
pip install -e ".[all]"
```

## 验证安装

检查 LLM4AD 是否正确安装：

```bash
# 检查版本
python -c "import llm4ad; print('LLM4AD 安装成功')"

# 测试 CLI
llm4ad --help

# 列出已注册的组件
llm4ad list
```

预期输出：

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

## 设置 API 密钥

LLM4AD 需要 LLM 提供商的 API 密钥。将它们设置为环境变量：

### OpenAI

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

### Anthropic

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

### 持久配置

添加到您的 shell 配置文件（`~/.bashrc`、`~/.zshrc` 等）：

```bash
# OpenAI
export OPENAI_API_KEY="your-openai-api-key"

# Anthropic
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

然后重新加载：

```bash
source ~/.bashrc  # 或 ~/.zshrc
```

## 开发设置

如果您计划为 LLM4AD 做出贡献，请设置开发环境：

```bash
# 克隆仓库
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd LLM4AD

# 安装开发依赖
uv sync --extra all

# 安装 pre-commit hooks（可选）
pip install pre-commit
pre-commit install
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并生成覆盖率报告
pytest --cov=src/llm4ad

# 运行特定测试文件
pytest tests/evaluator/test_base.py
```

### 代码质量检查

```bash
# 格式化代码
black src/ tests/
isort src/ tests/

# 检查代码
ruff check src/ tests/ --fix

# 类型检查
mypy src/
```

## 故障排除

### Python 版本问题

**错误**: `需要 Python 3.12 或更高版本`

**解决方案**:
```bash
# 检查您的 Python 版本
python --version

# 如有需要，安装较新版本
# 使用 pyenv（推荐）
pyenv install 3.12
pyenv global 3.12
```

### uv 安装问题

**错误**: `uv: command not found`

**解决方案**:
```bash
# 尝试使用 pip 安装 uv
pip install uv

# 或直接使用 pip 代替 uv
pip install -e ".[all]"
```

### 依赖冲突

**错误**: `无法解析依赖`

**解决方案**:
```bash
# 清除 uv 缓存
uv cache clean

# 再次尝试安装
uv sync --extra all

# 或创建新的虚拟环境
python -m venv venv
source venv/bin/activate
uv sync --extra all
```

### 导入错误

**错误**: `ModuleNotFoundError: No module named 'llm4ad'`

**解决方案**:
```bash
# 确保您在项目目录中
cd LLM4AD

# 以可编辑模式安装
pip install -e ".[all]"

# 或将 src 添加到 PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
```

## 下一步

安装后，继续进行：

- [快速入门指南](quickstart.md) - 运行您的第一个实验
- [配置指南](configuration.md) - 了解配置选项
- [编写评估函数](evaluators.md) - 创建自定义评估函数

## 获取帮助

如果您遇到此处未涵盖的问题：

- 📖 [文档首页](../index.md)
- 💬 [讨论](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- 🐛 [问题跟踪](https://github.com/Optima-CityU/LLM4AD_Next/issues)
