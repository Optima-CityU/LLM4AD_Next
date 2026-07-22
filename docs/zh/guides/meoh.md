# MEoH 方法指南

本指南介绍如何在 LLM4AD 中使用新增的 `meoh` 方法，包括：

- 方法定位与当前实现范围
- 核心执行流程
- 关键配置项
- `reuse_coder` 与 `direct_code` 两种代码生成模式
- 多目标配置与 seed 使用方式
- 最小示例配置

## 1. 方法定位

当前仓库中的 `meoh` 是将外部 MEoH 思路迁移到 LLM4AD 现有架构中的一个新方法实现。

它不是对外部仓库的逐行复刻，而是基于当前项目已有的：

- `planner -> coder -> evaluator -> orchestrator`
- repository EVOLVE block
- version control worktree
- state tracker

做的最小可跑迁移版。

这一版的目标是：

- 可以作为新的 `evolution.type`
- 可以实际接入当前框架运行
- 支持多目标选择
- 支持 `seed`
- 支持两种代码生成路径

这一版没有完整覆盖外部实现的所有能力，例如：

- 不追求完全一致的 prompt 细节
- 不完整迁移外部 profiler 体系
- checkpoint/resume 只做轻量支持
- `direct_code` 是基于 EVOLVE block replacement 的简化实现

## 2. 核心结构

这部分功能主要由以下文件组成：

- `src/llm4ad/orchestrator/meoh.py`
- `src/llm4ad/orchestrator/meoh_population.py`
- `src/llm4ad/planner/meoh_evolution.py`
- `src/llm4ad/planner/sampler/meoh_init_sampler.py`
- `src/llm4ad/planner/sampler/meoh_e1_sampler.py`
- `src/llm4ad/planner/sampler/meoh_e2_sampler.py`
- `src/llm4ad/planner/sampler/meoh_m1_sampler.py`
- `src/llm4ad/planner/sampler/meoh_m2_sampler.py`
- `src/llm4ad/planner/sampler/meoh_prompt_templates.py`

## 3. 执行流程

### 3.1 调用入口

当配置中指定：

```yaml
evolution:
  type: "meoh"
  planner_type: "meoh_evolution"
```

主链路会变成：

```text
CLI
-> LLM4AD
-> BasePlanner.create("meoh_evolution")
-> BaseOrchestrator.create("meoh")
-> MEoHOrchestrator.run()
```

### 3.2 generation 的定义

`meoh` 和 `island_ga` 最大的区别之一是 generation 语义不同。

在 `meoh` 中：

- 不是“每生成一个 candidate 算一代”
- 也不是“每执行一次 operator 算一代”
- 而是“每发生一次 `survival()` 算一代”

也就是：

- 新个体先进入 `next_gen_population`
- 当 `next_gen_population` 累积到 `population_size`
- 触发一次 `survival()`
- 然后 `generation += 1`

### 3.3 operator 调度

当前 `meoh` 支持 5 个 operator：

- `i1`
- `e1`
- `e2`
- `m1`
- `m2`

它们分别对应：

- `i1`：初始化算法思路
- `e1`：从多个 parent 生成形式明显不同的新算法
- `e2`：从多个 parent 的共同骨架出发生成新算法
- `m1`：对单个 parent 做结构级 mutation
- `m2`：对单个 parent 做参数/局部策略级 mutation

在初始化阶段：

- 只使用 `i1`

在演化阶段：

- 默认按固定顺序尝试 `e1 -> e2 -> m1 -> m2`
- `e2` / `m1` / `m2` 是否启用由配置控制

## 4. Population 逻辑

`MEoHPopulation` 维护三类集合：

- `population`
- `next_gen_population`
- `elitist_archive`

它们的含义分别是：

- `population`：当前 active population，用于选择 parent
- `next_gen_population`：当前 survival 事件之前暂存的新个体
- `elitist_archive`：全局非支配前沿

### 4.1 survival

`survival()` 的核心流程是：

1. 合并 `population + next_gen_population`
2. 基于 `objective_metrics` 做 Pareto 非支配前沿更新
3. 用 CodeBLEU 相似性惩罚和支配关系做 active population 截断
4. 保留 `max(1, int(population_size * active_population_ratio))` 个 active 个体
5. 清空 `next_gen_population`
6. `generation += 1`

### 4.2 selection

`selection()` 只从有效个体中选择 parent。

这里的“有效”指：

- 已评测成功
- 目标指标可读取

无效个体会被保留在流程中，但不会参与 parent selection。

### 4.3 duplicate / dominated clone

当前实现会把以下情况视为重复或应丢弃候选：

- 代码字符串完全相同
- objective vector 完全相同，且已有个体的标量 `score` 不差于该候选

## 5. 多目标机制

`meoh` 不直接用 `Algorithm.score` 做 Pareto 选择，而是使用显式配置的 `objective_metrics`。

例如：

```yaml
evolution:
  objective_metrics:
    - "tour_length"
    - "candidate_runtime_ms"
```

这些指标来自 evaluator 返回的：

- `EvaluationResult.metrics`

指标方向由 evaluator 的 `Metric` 定义决定：

- `MetricType.MINIMIZE`
- `MetricType.MAXIMIZE`

当前实现会把它们统一转换到 maximize-space 后再比较。

### 5.1 与框架原有 `score` 的关系

虽然 `meoh` 的 Pareto 逻辑独立于标量 `score`，但当前框架仍然保留 `evaluation.score`，用于：

- 日志
- state tracker
- `best_individual`
- history 输出

也就是说：

- 多目标选择看 `objective_metrics`
- 兼容层输出仍保留单一 `score`

## 6. 两种代码生成模式

`meoh` 支持两种代码生成方式：

```yaml
evolution:
  code_generation_mode: "reuse_coder"
```

或：

```yaml
evolution:
  code_generation_mode: "direct_code"
```

### 6.1 `reuse_coder`

这是更贴近当前 LLM4AD 主架构的方式。

流程是：

```text
planner.init()
-> planner.plan(operator=...)
-> planner.implement()
-> coder.generate(...)
-> evaluator
```

优点：

- 复用现有 coder
- 与当前系统结构更一致
- 更适合后续继续扩展

### 6.2 `direct_code`

这是一个更轻量的直连实现。

流程是：

```text
planner.init()
-> planner.plan(operator=...)
-> planner.generate_direct_code()
-> 直接替换 EVOLVE block
-> evaluator
```

这里不会调用外部 coder，而是让 planner 自己通过 provider 生成 EVOLVE block replacement code。

当前它的定位是：

- 快速验证
- 贴近外部“直接产代码”的味道

但它仍然是基于当前项目的 EVOLVE block 工作流，不是完整复刻外部 function/program 体系。

## 7. Seed 支持

当前 `meoh` 支持 seed 初始化，但只支持本项目自己的 `Algorithm` JSON 格式。

配置示例：

```yaml
evolution:
  seed_path: "./seeds/meoh_seed.json"
```

seed 文件应是：

- `Algorithm.model_dump()` 的列表
- 或包含 `algorithms` 字段的 JSON 对象

当前不兼容外部旧格式 seed。

## 8. CodeBLEU 依赖

`meoh` 默认使用 CodeBLEU 语法相似性做 diversity penalty。

项目中已新增可选依赖：

- `meoh`

安装方式：

```powershell
uv sync --extra meoh
```

如果你还要运行 TSP 样例，可以一起安装：

```powershell
uv sync --extra meoh --extra tsp
```

如果没有安装 `codebleu`，运行 `meoh` 时会在相似性计算阶段报错。

## 9. 最小配置示例

仓库中已经提供了一个最小示例：

- [`examples/config/config.meoh.yaml`](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/examples/config/config.meoh.yaml)

核心片段如下：

```yaml
evolution:
  type: "meoh"
  planner_type: "meoh_evolution"
  max_generations: 5
  population_size: 8
  selection_num: 2
  max_sample_nums: 32
  objective_metrics:
    - "tour_length"
    - "candidate_runtime_ms"
  code_generation_mode: "reuse_coder"
  active_population_ratio: 0.25
  use_e2_operator: true
  use_m1_operator: true
  use_m2_operator: true

planner:
  provider: "default"

coder:
  provider: "default"
```

## 10. 配置字段说明

`MEoHConfig` 定义在：

- `src/llm4ad/config/evolution.py`

关键字段如下。

### `population_size`

- 每次 survival 前累计的新候选阈值
- `next_gen_population` 达到这个大小时触发 `survival()`

### `selection_num`

- `e1` 和 `e2` 默认选择多少个 parent

### `max_sample_nums`

- 本轮 run 最多允许生成多少个 candidate

### `num_samplers`

- 每轮 operator 尝试时生成多少个候选

### `objective_metrics`

- 多目标选择使用的指标名列表
- 必须和 evaluator 输出的 `metrics` 对齐

### `use_e2_operator`

- 是否启用 `e2`

### `use_m1_operator`

- 是否启用 `m1`

### `use_m2_operator`

- 是否启用 `m2`

### `seed_path`

- seed 文件路径

### `active_population_ratio`

- active population 在 survival 后保留比例
- 默认 `0.25`

### `generation_mode`

- 当前固定为 `"survival"`

### `code_generation_mode`

- 可选 `"reuse_coder"` 或 `"direct_code"`

## 11. 如何运行

在仓库根目录下：

```powershell
uv sync --extra meoh --extra tsp
uv run llm4ad run .\examples\config\config.meoh.yaml
```

如果你使用自定义 provider / coder，需要确保这些环境变量已经配置好，例如：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## 12. 当前实现建议

如果你要基于这版继续做研究或扩展，建议优先按下面的顺序推进：

1. 先用 `reuse_coder` 跑通一个真实 benchmark。
2. 再细化 operator prompt，使其更接近原始 MEoH。
3. 再考虑是否增强 `direct_code`，让它更贴近外部 function/program 生成逻辑。

如果你的目标是“先把方法接进平台并验证接口链路”，当前这版已经足够。

如果你的目标是“复现外部论文或仓库里的全部 MEoH 行为”，这版还只是第一步。

## 13. 相关文件

- `src/llm4ad/orchestrator/meoh.py`
- `src/llm4ad/orchestrator/meoh_population.py`
- `src/llm4ad/planner/meoh_evolution.py`
- `src/llm4ad/planner/sampler/meoh_prompt_templates.py`
- [`examples/config/config.meoh.yaml`](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/examples/config/config.meoh.yaml)
