<h1 align="center">OpenLoopX · LLM4AD Next</h1>


<p align="center">
  <strong>从问题描述到可运行的进化算法搜索 —— 只需一条命令。</strong><br>
  基于大语言模型驱动的自动化算法设计与进化优化
</p>

<p align="center">
  <a href="https://llm4ad-next.cn/">官方网站</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="docs/en/index.md">文档</a> ·
  <a href="https://github.com/Optima-CityU/LLM4AD_Next/wiki">Wiki</a> 
</p>

<p align="center">
  <a href="https://pypi.org/project/llm4ad-next/">
    <img src="https://img.shields.io/pypi/v/llm4ad-next?color=blue" alt="PyPI Version">
  </a>
  <a href="https://pypi.org/project/llm4ad-next/">
    <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python Versions">
  </a>
  <a href="https://github.com/Optima-CityU/LLM4AD_Next/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-BSD--3--Clause-blue" alt="License">
  </a>
  <a href="https://github.com/Optima-CityU/LLM4AD_Next/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/Optima-CityU/LLM4AD_Next/ci.yml" alt="CI">
  </a>
</p>

<p align="center">
  <a href="./README.md">English</a> | <strong>中文</strong>
</p>

---

<p align="center">
  <strong>⭐ 在 GitHub 上给我们点 Star，即可获得 <a href="https://llm4ad-next.cn/">在线网站</a> 10 美元奖励 Token！</strong>
</p>

---

## 🔥 最新动态

- 🔬 [2026.07][新功能]：**搜索方法已迁移** —— EoH、MEoH、ReEvo 和 MCTS-AHD 现已作为独立编排器可用。请参阅[搜索方法](#搜索方法自动启发式设计)。
- 🧠 [2026.07][新功能]：基于 **[MindMemOS](https://github.com/dadastory/MindMemOS) 的长期记忆**现已可用，支持全局、项目和任务记忆范围，并可配置聊天和嵌入模型绑定。请参阅[记忆指南](docs/en/guides/memory.md)。
- 🚀 [2026.07][新版本]：**LLM4AD_Next 在线试用**现已上线 [https://llm4ad-next.cn/](https://llm4ad-next.cn/) —— 无需本地安装，直接在浏览器中体验完整的问题到算法工作流。
- ✨ [2026.07][新功能]：推出**交互式问题到项目工作流**，将自然语言问题描述转化为可运行的进化算法搜索项目。
- 🐳 [2026.07][新功能]：版本化的 **Docker Hub 部署镜像**现已与 GitHub Release 标签对齐，支持可复现的本地部署。

## 🚀 为什么选择 LLM4AD_Next？

传统上，使用大语言模型进行自动化算法设计（LLM4AD）需要繁琐的多步配置流程。**LLM4AD_Next 彻底消除了这一入门障碍。**

<div align="center">
  <img src="docs/en/process.png" alt="LLM4AD 与 LLM4AD_Next 流程概览" width="850">
</div>


使用 **LLM4AD_Next**，在创建目录后，所有这些繁琐的步骤都通过交互式对话终端完全自动化。只需运行：

```bash
uv run llm4ad chat
```

我们内置的 AI 驱动顾问将对您进行访谈，即时理解您的需求，并自动生成一个开箱即用的流水线（评估器、算法骨架、配置和调试器），让您可以直接开始生成有用的算法。

## 🎯 核心特性概览

* 🧠 **LLM 驱动的设计** & 🧬 **进化优化** 相结合，自动进化出高性能代码。
* 💬 **交互式配置 (`llm4ad chat`)** —— 您的对话式 AI 顾问，一键生成完整的可运行应用框架。
* 🔍 **进化块顾问与推荐器** —— 将 LLM4AD_Next 指向任意代码仓库，它将扫描、评分并精确推荐*哪些*代码块最有可能通过进化来实现您的目标。

## 搜索方法（自动启发式设计）

从原始 [LLM4AD](https://github.com/Optima-CityU/LLM4AD/tree/main/llm4ad) 平台迁移的自动启发式设计（AHD）搜索方法的迁移状态。

| 方法 | 状态 | 方法 | 状态 |
|--------|--------|--------|--------|
| **IslandGA** | ✅ 可用 | **FunSearch** | ⏳ 待迁移 |
| **MEoH** | ✅ 可用 | **HillClimb** | ⏳ 待迁移 |
| **DyCA** | ✅ 可用 | **LHNS** | ⏳ 待迁移 |
| **EoH** | ✅ 可用 | **LLaMEA** | ⏳ 待迁移 |
| **ReEvo** | ✅ 可用 | **MLES** | ⏳ 待迁移 |
| **MCTS-AHD** | ✅ 可用 | **MOEA/D** | ⏳ 待迁移 |
| | | **NSGA-II** | ⏳ 待迁移 |
| | | **PartEvo** | ⏳ 待迁移 |
| | | **RandSample** | ⏳ 待迁移 |

### 使用已迁移的方法

在配置文件中设置 `evolution.type`，然后运行 `llm4ad run <config.yaml>`。完整示例请参阅 `examples/config/config.complete.yaml`。

```yaml
evolution:
  type: "eoh"  # 可选值: "eoh", "meoh", "reevo", "mcts_ahd", "island_ga", "dyca"
```

## 快速开始

<table>
  <tr>
    <td align="center" width="33%">
      <strong>在线试用</strong><br>
    </td>
    <td align="center" width="33%">
      <strong>观看教程</strong><br>
    </td>
    <td align="center" width="33%">
      <strong>阅读文档</strong><br>
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      在浏览器中运行 LLM4AD_Next，无需安装或 API 密钥。
    </td>
    <td align="center" width="33%">
      在安装或配置本地环境之前，先观看介绍视频。
    </td>
    <td align="center" width="33%">
      使用文档路线图进行设置、配置、查看示例和 Web UI 部署。
    </td>
  </tr>
  <tr>
    <td align="center" width="33%">
      <a href="https://llm4ad-next.cn/">
        <img src="https://img.shields.io/badge/启动在线演示-立即体验-2ea44f?style=for-the-badge"
             alt="启动在线演示">
      </a>
    </td>
    <td align="center" width="33%">
      <a href="https://youtu.be/x47kEosu0jk" target="_blank" rel="noopener noreferrer">
        <img src="https://img.shields.io/badge/观看教程-YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white"
             alt="在 YouTube 上观看教程视频">
      </a>
    </td>
    <td align="center" width="33%">
      <a href="docs/en/index.md">
        <img src="https://img.shields.io/badge/查看文档-立即阅读-0969da?style=for-the-badge"
             alt="查看文档">
      </a>
    </td>
  </tr>
</table>

## 教程视频

<div align="center">
  <a href="https://youtu.be/x47kEosu0jk" target="_blank" rel="noopener noreferrer">
    <img src="https://img.youtube.com/vi/x47kEosu0jk/maxresdefault.jpg"
         alt="LLM4AD_Next 教程视频"
         width="720"
         height="405">
  </a>
</div>


## 运行 LLM4AD

### 方式 A：在线演示（无需安装）

使用[快速开始](#快速开始)中的在线演示，或直接打开：
[启动在线演示](https://llm4ad-next.cn/)。

无需任何设置，无需 API 密钥 —— 只需打开链接即可开始设计算法。

### 方式 B：本地安装

需要 **Python 3.12+**（在 `.python-version` 中锁定）和 [uv](https://github.com/astral-sh/uv)（推荐）或 pip。简单执行 `uv sync` 即可完成所有设置，包括 `chatv2` AI 构建代理。

```bash
# 克隆仓库
git clone https://github.com/Optima-CityU/LLM4AD_Next.git
cd LLM4AD_Next

# 安装依赖
uv sync

# 配置您的 LLM 提供商（请参阅下方全局设置部分）
# 或直接设置环境变量：
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="your-api-key"
export LLM_MODEL="gpt-4o"

# 方式 1：交互式配置（推荐新用户使用）
llm4ad chat

# 方式 2：使用现有配置文件运行
llm4ad run examples/applications/tsp_benchmark_python/config.yaml
```

如需可选依赖组（`infra`、`providers`、`eval`、`dev`、`docs`、`all`）和 uv 安装说明，请参阅[安装指南](docs/en/guides/installation.md)。

## 全局设置

创建 `~/.llm4ad/settings.yaml` 来配置跨项目共享的提供商：

```yaml
providers:
  - name: default
    type: openai
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
  - name: anthropic
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-20250514
```

任务配置只需提供商名称 —— 凭据和模型会自动从全局设置中解析。

有关 CLI 命令、交互式聊天工作流、进化块顾问/推荐器和 Python API，请参阅[文档](docs/en/index.md)。

## 文档

- [文档首页](docs/en/index.md)
- [快速开始指南](docs/en/guides/quickstart.md)
- [配置指南](docs/en/guides/configuration.md)
- [编写评估器](docs/en/guides/evaluators.md)

### 本地开发

```bash
# 启动文档服务（支持实时重载）
mkdocs serve

# 构建静态文档
mkdocs build
```

## 项目结构

```
LLM4AD/
├── src/llm4ad/          # 主源代码
│   ├── config/           # 配置模式和全局设置
│   ├── consultant/       # 交互式配置向导
│   ├── builder/          # 任务构建器（分析器、创建器、验证器、写入器）
│   ├── advisor/          # 进化块顾问与推荐器
│   ├── provider/         # LLM 提供商实现
│   ├── planner/          # 算法规划层
│   ├── coder/            # 代码生成层
│   ├── evaluator/        # 评估层
│   ├── orchestrator/     # 工作流编排
│   ├── infra/            # 基础设施（Ray、监控）
│   └── utils/            # 工具库
├── examples/             # 示例配置和应用
├── tests/                # 测试套件
└── docs/                 # 文档
```

## 参与贡献

欢迎贡献！请阅读我们的[贡献指南](docs/en/contributing/guidelines.md)了解详情。

```bash
# 搭建开发环境
uv sync --extra all

# 运行测试
pytest

# 格式化代码
black src/ tests/
ruff check src/ tests/ --fix
```

## 许可证

本项目基于 BSD 3-Clause 许可证开源 - 详情请参阅 [LICENSE](LICENSE) 文件。

## 支持

- [文档](docs/en/index.md)
- [讨论区](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- [问题追踪](https://github.com/Optima-CityU/LLM4AD_Next/issues)

## 加入社区

使用微信扫描二维码加入 LLM4AD_Next 社区群。

<div align="center">
  <img src="docs/assets/live-qr-20260818-033420.png"
       alt="LLM4AD_Next 微信社区二维码"
       width="220">
</div>
