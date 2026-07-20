# 贡献指南

LLM4AD 以 BSD 3-Clause 开源协议发布，欢迎各种贡献 — bug 报告、修复、新增 provider / coder / orchestrator / evaluator、文档改进、示例项目等。

如果是第一次配置环境，先看[开发环境](development.md)。代码风格参见[代码风格](style.md)。

## 贡献方式

| 类型 | 去哪儿 |
|---|---|
| Bug 报告 | [GitHub Issues](https://github.com/Optima-CityU/LLM4AD_Next/issues)，附复现步骤 |
| 特性请求 | GitHub Issues，附动机与示例用例 |
| 代码改动 | 针对 `main` 提 Pull Request |
| 新示例 | `examples/applications/<your_example>/` + 在 `docs/{en,zh}/examples/` 下加一页走读 |
| 新增 provider / coder / evaluator / orchestrator | 在对应 `src/llm4ad/...` 下加新文件 + 测试 |
| 文档修复 | 针对 `docs/` 提 PR |

## 写一份好 issue

有用的 bug 报告至少包括：

- LLM4AD 版本（`llm4ad version`）和 Python 版本
- 操作系统、shell
- 最小复现配置或命令
- 完整 traceback（如果有用，加上 `--log-level DEBUG`）
- 预期 vs 实际

可复现的 issue 会更快被处理。若你的问题依赖私有 LLM 端点，请尽量先用 `MockProvider` 复现。

## Pull Request 流程

1. **fork + 分支** — 从 `main` 拉分支并取个有信息量的名字。推荐模式 `<type>/<short-slug>`，例如 `feat/anthropic-streaming`、`fix/cli-best-path`、`ref/registry-cleanup`。
2. **聚焦的改动** — 一个 PR 一个逻辑改动。重构不应捎带行为变化。
3. **本地跑测试** — `pytest -m unit` 跑单测，`pytest -m integration` 跑较慢的集成测试；详见[开发环境](development.md)。
4. **lint 与类型检查** — `ruff check src/ tests/ --fix`、`black src/ tests/`、`isort src/ tests/`、`mypy src/`。CI 也跑这些。
5. **提 PR** — 简短描述，写明动机、改动概要、验证步骤。如果对应 issue 请附链接。
6. **响应 review** — 用 fixup commit 跟进；维护者 squash-merge。

CI 在 Ubuntu / macOS × Python 3.12 下运行（`.github/workflows/ci.yml`）。合并前 CI 必须通过。

## Commit 信息格式

遵循 Conventional Commits 风格，本项目接受的前缀：

```
(feat|fix|ref): title

1. <改动一>
2. <改动二>
3. ...
```

- `feat:` — 新增面向用户的能力
- `fix:` — bug 修复
- `ref:` — 不改变行为的重构
- 标题保持 ≤ 72 字符
- 数字列表是 PR 描述风格；超小改动一句话足够

git log 是[更新日志](../changelog.md)的事实源，所以请把标题写清楚。

## 新增一个组件

5 个可扩展家族用同一套范式：

1. 在 `src/llm4ad/<family>/` 下继承对应 `Base*`。
2. 用对应的 `register_*` 装饰或调用，让注册表识别。
3. 在 `src/llm4ad/config/<family>.py` 增加对应 schema（如属 orchestrator/evaluator/coder 的鉴别器，再在 `AppConfig` 里挂上）。
4. 在 `tests/<family>/` 增加测试。用 `MockProvider` 保持测试可重复且便宜。
5. 在对应 guide 页（如 `docs/{en,zh}/guides/providers.md`）把新组件介绍给用户，并在 API 参考页（`docs/{en,zh}/api/<family>.md`）补一句注解。

如果新组件需要额外依赖，请在 `pyproject.toml` 的 `[project.optional-dependencies]` 下新建一个组，并在安装文档中提到。

## 文档贡献

文档位于 `docs/en/` 和 `docs/zh/`，加上两个导航配置：

- `mkdocs.yml` — 独立文档站
- `src/frontend/src/components/Guide/guide.config.ts` — 网页 in-app 使用手册

每个英文文件必须有同路径的中文版；同一个 nav key 必须同时出现在两个 nav 配置里。标题与链接规范见[代码风格 § 文档](style.md#文档)。

## 行为守则

请保持尊重，假定善意，把技术讨论留给技术。维护者保留对偏题、敌意或骚扰内容加锁或删除的权利。完整规范遵循 Contributor Covenant（[contributor-covenant.org](https://www.contributor-covenant.org/)）。

## 许可

所有贡献都在仓库统一的 BSD 3-Clause 许可证下接受（见[许可证](../license.md)）。提交 PR 即表示你确认有权这样做。

## 相关链接

- [开发环境](development.md) — 把开发环境跑起来
- [代码风格](style.md) — Python、前端、markdown 规范
