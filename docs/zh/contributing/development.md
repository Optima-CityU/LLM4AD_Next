# 开发环境

本页带你把一个干净的 LLM4AD 开发环境从零跑起来 — 从 clone 到跑测试、跑文档站、用 CLI、跑 Web UI。

## 前置依赖

- **Python 3.12**（必需；CI 运行 3.12）
- **uv** — 快速的 Python 包管理器。按 [官方指引](https://docs.astral.sh/uv/) 安装。
- **git** ≥ 2.5（版本控制层依赖 `git worktree`）
- 跑 Web UI 还需：**bun** ≥ 1.0（前端）、**Docker**（容器化开发）

可选：**make**、**ripgrep**（高速搜索）、一个 LLM API key 如果想跑真实进化而不是 mock。

## clone + install

```bash
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd llm4ad

# 一次性装齐（dev + docs + all extras + provider SDK）
uv sync --extra all
```

只装你需要的：

```bash
uv sync                              # 仅核心
uv sync --extra dev                  # 加 lint/test 工具
uv sync --extra dev,providers,docs   # 典型贡献者组合
```

可选 extras（见 `pyproject.toml`）：`infra`、`providers`、`eval`、`tsp`、`lunarlander`、`dyca`、`meoh`、`dev`、`docs`、`all`。

## 冒烟测试

```bash
# 确认入口已经接好
uv run llm4ad version
uv run llm4ad list                   # 打印所有已注册 provider/planner/coder/...
```

应该至少看到内置组件（`openai_compatible`、`anthropic`、`mock`、`island_ga`、`dyca`、`meoh` 等）。

## 跑测试

```bash
# 单测（快）
uv run pytest -m unit

# 集成测试（较慢；部分需要 uv、git worktree、外部 CLI）
uv run pytest -m integration

# 全量 + 覆盖率
uv run pytest --cov=src/llm4ad

# 单文件或单用例
uv run pytest tests/frontend/test_cli.py
uv run pytest tests/frontend/test_cli.py::test_help_renders
```

测试通过 `pytest.ini` 中的 marker 隔离。`MockProvider` 是单测默认 provider，让测试快、可重现且离线。

## Lint 与类型检查

项目用 Ruff（快速 lint）、Black（格式化）、isort（import 顺序）、mypy（类型）。CI 也按同样顺序跑：

```bash
uv run ruff check src/ tests/ --fix
uv run black src/ tests/
uv run isort src/ tests/
uv run mypy src/
```

最简单的本地强制方式是 pre-commit：

```bash
uv run pre-commit install            # 一次性
uv run pre-commit run --all-files    # 临时全量过一遍
```

`CLAUDE.md` 中的硬要求是：每次代码改动后都跑一次 `uv run --python 3.12 ruff check src/`，把它报的问题修掉。

## 用真实 provider 跑 CLI

设好示例配置依赖的环境变量：

```bash
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="sk-..."
export LLM_MODEL="gpt-4o-mini"

uv run llm4ad run examples/applications/sorting_benchmark_python/config.yaml
```

要做更丰富的全局配置，把内容写到 `~/.llm4ad/settings.yaml`，让任务配置按名字引用 provider；详见[配置指南 § 全局设置](../guides/configuration.md#global-settings)。

## 构建文档

独立文档站用 MkDocs Material：

```bash
uv sync --extra docs
uv run mkdocs serve            # 实时预览，http://localhost:8000
uv run mkdocs build --strict   # 兼容 CI 的构建，警告即失败
```

前端 in-app 使用手册取自相同的 `docs/` 树，通过 Vite `import.meta.glob` 加载 — 详见[前端集成](../web-ui/frontend-integration.md)。

## 跑 Web UI

Web UI 由两部分组成：FastAPI 后端 + React 前端。两边都自带 Dockerfile。

```bash
# 后端（开发模式，自动重载）
cd src/backend
uv sync
uv run fastapi dev app/main.py            # http://localhost:8000

# 前端（开发模式，HMR）
cd src/frontend
bun install
bun run dev                               # http://localhost:5173
```

要更接近生产，用 Docker Compose；详见 [Web UI 概览](../web-ui/overview.md)。前端也可以通过 `bun run build` 构建成静态资源、用 nginx 提供（`src/frontend/Dockerfile`）。

如果要用 Docker 辅助本地开发，推荐在 macOS/Linux 使用 `docker/dev.sh infra`，在 Windows 使用 `docker/dev.ps1 infra` 启动共享基础设施，然后在宿主机运行后端和前端。完整栈调试端口和镜像部署方式见 [Docker 本地启动](docker-local.md)。

## 主要目录

| 路径 | 用途 |
|---|---|
| `src/llm4ad/` | 发布的 Python 库 |
| `src/backend/` | 把 CLI 包成 Web UI 的 FastAPI 服务 |
| `src/frontend/` | React + Vite 前端 |
| `examples/applications/` | 可运行示例项目（详见[示例](../examples/index.md)） |
| `tests/` | 测试套件（结构镜像 `src/llm4ad/`） |
| `docs/` | 双语文档（你正在读的这个） |
| `runs/`（示例配置默认创建） | 运行输出的默认 `base_dir` |

## 常见问题排查

| 现象 | 可能原因 | 解决 |
|---|---|---|
| `llm4ad: command not found` | shell 没用 uv 创建的 venv | `uv run llm4ad ...` 或 `source .venv/bin/activate` |
| 配置中报 `KeyError: '${LLM_API_KEY}'` | 环境变量没导出 | `llm4ad run` 之前 `export LLM_API_KEY=...` |
| `git worktree add` 报错 | 仓库没有 HEAD commit | 在已有 git 仓库里运行，或 `version_control.auto_initialize: true` |
| `mkdocs build --strict` 失败 | stub 链接或缺中文版 | `grep -r "Coming soon" docs/`、`diff <(ls docs/en) <(ls docs/zh)` |
| 前端使用手册某页空白 | `guide.config.ts` 加了 key 但没建文件 | 创建 `docs/{en,zh}/<key>.md` |

## 相关链接

- [贡献指南](guidelines.md) — PR 流程、commit 格式
- [Docker 本地启动](docker-local.md) — 本地基础设施、完整栈调试、镜像部署命令
- [代码风格](style.md) — Python / 前端 / markdown 规范
- [CLI 参考](../guides/cli.md)
- [配置指南](../guides/configuration.md)
