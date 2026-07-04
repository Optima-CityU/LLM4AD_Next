# 代码风格

本页记录 LLM4AD 强制执行的代码规范。目的是把 review 变得机械化 — linter 和 formatter 替我们做大多数决定，review 时间就能花在实质内容上。

## Python

### 工具链

| 工具 | 配置 | 强制内容 |
|---|---|---|
| **Ruff** | `pyproject.toml` 中 `[tool.ruff.lint]` | Lint：未使用 import、未定义名称、过时 API、常见 bug 模式 |
| **Black** | `line-length = 100`、`target-version = py312` | 格式化（空白、换行、字符串引号） |
| **isort** | Black 兼容 profile | import 顺序与分组 |
| **mypy** | `pyproject.toml` 中 `[tool.mypy]` | 静态类型检查 |

提 PR 前按这个顺序跑：

```bash
uv run ruff check src/ tests/ --fix
uv run black src/ tests/
uv run isort src/ tests/
uv run mypy src/
```

CI 跑的是同一套，干净通过是合并的前置条件。

### 类型注解

- 给每个公开函数和方法都加类型注解（凡是从模块 `__init__.py` 可达的）。
- 内部函数若调用点无法轻松推断类型，也加上。
- 优先 `from __future__ import annotations`，让注解作为字符串（不增加运行时 import 成本）。
- 自由使用 `Annotated[X, ...]`、`TypeAlias`、PEP 604（`X | None`）— 项目要求 Python 3.12。

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

### 文档字符串

按 `CLAUDE.md` 要求使用 Google 风格。保持紧凑：一句概括，然后只在签名之外有信息时才写 `Args` / `Returns` / `Raises`。

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

所有注释和 docstring 都用**英文**，即使是中文化的示例项目（`CLAUDE.md` 要求）。

### 日志

通过 `llm4ad.utils.logging` 辅助，或直接 `from loguru import logger`。除 CLI 的 Rich 渲染输出外，不要用 `print()`。

```python
from loguru import logger

logger.info("provider call duration={:.1f}ms", elapsed)
```

### 异步

provider / coder / evaluator 的多数入口都是异步。在 async 代码里：

- 用 `asyncio.gather` / `asyncio.create_task`，不要阻塞调用。
- 显式处理取消（`async with asyncio.timeout(...)`，或在 `finally` 里尊重取消）。
- 不要把 `requests` / `urllib` 跟 `aiohttp` 混用 — 选一套栈。

### 错误处理

- 抛具名异常（`AdvisorError`、`BuildError`、`ValueError` — 不要裸 `Exception`）。
- 重新抛出时用 `from e`，保留原链。
- 不要静默吞错；如果某条路径有意是 best-effort（比如运行结束时打印 `best/` 路径），把 `except` 缩到加 `# noqa: BLE001` 注释、解释清楚的具体类型。

### 测试

- 测试用 `src/` 同款 Black/Ruff 配置。
- 每个测试都打 `@pytest.mark.unit` 或 `@pytest.mark.integration`。CI 依赖这些。
- 涉及 provider / coder / evaluator 的测试用 `MockProvider`，保持确定性、可离线。
- 异步测试通过 `pytest-asyncio` 的 `asyncio_mode = "auto"`；可以直接 `async def test_foo(): ...`。

## 前端

前端（`src/frontend/`）是 React + TypeScript + Vite，由 **Biome** 格式化。

```bash
cd src/frontend
bun run lint        # Biome lint
bun run format      # Biome format
bun run typecheck   # tsc --noEmit
```

约定：

- 组件用 PascalCase（`UserManualContent.tsx`）。
- Hook 用 camelCase 加 `use` 前缀（`useGuideState`）。
- 配置文件用 kebab-case（`guide.config.ts`）。
- 翻译位于 `src/frontend/src/i18n/locales/{en,zh}.json`。每个 UI 标签都有 `en` 和 `zh` 条目；缺一项会在运行时回退到另一种语言。

## Markdown / 文档

- 使用 ATX 风格标题（`#`、`##`、…）。不用 Setext（`===`、`---`）。
- 每个文件一个 H1（页面标题）。次级用 H2 / H3。
- 行内代码用反引号（`` `like_this` ``）；围栏代码块带语言标记（` ```python `、` ```yaml `）。
- 文档之间互链用相对 `(foo.md)`。前端的 `resolveDocLink` 会把它们重写为 in-app 导航；绝对路径和 `http://` 链接会新开标签页。
- 文件名用 kebab-case（`web-ui/overview.md`、`examples/symbolic-regression.md`）。
- 每个英文文件都要有同路径的中文文件，标题树保持一致。
- "相关链接"（See also）页脚在 guide 页面推荐，在 API 页面强制（最后一条要指向 `src/llm4ad/...` 作为权威源）。

### 术语

| 推荐 | 避免 |
|---|---|
| Orchestrator / Planner / Coder / Evaluator | Coordinator / Designer / Generator / Tester |
| Island GA / DyCA / MEoH | 行文里写 island-ga / dyca / meoh（这些是注册名，不是行文用语） |
| `EVOLVE` 块 / `EVOLVE_START` 标记 | "evolve 区域"、"标签" |
| Worktree（工作树） | "branch"（worktree 是 git 概念，不等于 branch） |
| 运行目录 | "输出文件夹" |

## 相关链接

- [贡献指南](guidelines.md) — PR 流程
- [开发环境](development.md) — 在本地跑工具链
- `pyproject.toml` 和 `src/frontend/biome.json` — 上述规则的实际配置文件
