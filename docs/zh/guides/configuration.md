# 配置指南

本指南提供了 LLM4AD 所有配置选项的全面参考。

## 配置文件结构

LLM4AD 使用 YAML 配置文件，具有以下结构：

```yaml
# 通用设置
project_name: "my-project"
run_id: "experiment-001"
random_seed: 42
base_dir: "./runs"

# LLM 提供商
providers:
  - name: "default"
    type: "openai"
    # ... 提供商设置

# 评估器
evaluator:
  # ... 评估器设置

# 进化
evolution:
  # ... 进化设置

# 编码器
coder:
  # ... 编码器设置

# 内存
memory:
  # ... 内存设置

# 工作区
workspace:
  # ... 工作区设置

# 日志
logging:
  # ... 日志设置
```

## 配置节

### 1. 通用设置

#### `project_name`
- **类型**: `string`
- **必需**: 是
- **默认值**: `"llm4ad"`
- **描述**: 项目名称，用于组织输出目录

```yaml
project_name: "sorting-algorithm-design"
```

#### `run_id`
- **类型**: `string`
- **必需**: 否
- **默认值**: 自动生成的 8 字符 UUID
- **描述**: 此次运行的唯一标识符，用于跟踪实验

```yaml
run_id: "experiment-001"  # 或留空以自动生成
```

#### `random_seed`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `42`
- **描述**: 用于可重复性的随机种子

```yaml
random_seed: 42
```

#### `base_dir`
- **类型**: `string`
- **必需**: 否
- **默认值**: `"./runs"`
- **描述**: 创建任务目录的基础目录

```yaml
base_dir: "./experiments"
```

---

### 2. LLM 提供商 (`providers`)

配置一个或多个可被其他组件引用的 LLM 提供商。

#### `providers[].name`
- **类型**: `string`
- **必需**: 是
- **描述**: 此提供商的唯一名称，被 `planner.provider` 和 `coder.provider` 引用

```yaml
providers:
  - name: "planner"  # 引用为 "planner"
```

#### `providers[].type`
- **类型**: `enum ["openai", "anthropic", "openai_compatible"]`
- **必需**: 是
- **默认值**: `"openai"`
- **描述**: LLM 提供商类型

```yaml
providers:
  - type: "openai"              # OpenAI API
  - type: "anthropic"           # Anthropic API
  - type: "openai_compatible"   # OpenAI 兼容 API（如本地模型）
```

#### `providers[].api_key`
- **类型**: `string`
- **必需**: 是（除非通过环境变量设置）
- **描述**: 用于身份验证的 API 密钥。可以使用 `${VAR_NAME}` 使用环境变量

```yaml
providers:
  - api_key: "${OPENAI_API_KEY}"  # 从环境变量
  - api_key: "sk-..."            # 直接值（不推荐）
```

#### `providers[].base_url`
- **类型**: `string`
- **必需**: 否
- **默认值**: 提供商特定的默认值
- **描述**: API 的自定义基础 URL（适用于本地模型或代理）

```yaml
providers:
  - type: "openai_compatible"
    base_url: "http://localhost:8000/v1"  # 本地模型
```

#### `providers[].model`
- **类型**: `string`
- **必需**: 是
- **默认值**: `"gpt-4"`
- **描述**: 模型标识符

```yaml
providers:
  - model: "gpt-4o"                    # OpenAI
  - model: "claude-3-5-sonnet-20241022"  # Anthropic
  - model: "llama-3-70b"                # 本地模型
```

#### `providers[].temperature`
- **类型**: `float`
- **必需**: 否
- **默认值**: `0.7`
- **范围**: `[0.0, 2.0]`
- **描述**: 采样温度。越低 = 越确定，越高 = 越有创意

```yaml
providers:
  - temperature: 0.3  # 更确定（适合代码）
  - temperature: 0.8  # 更有创意（适合规划）
```

#### `providers[].max_tokens`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `32768`
- **描述**: 每次请求生成的最大 token 数

```yaml
providers:
  - max_tokens: 4096   # 较短的响应
  - max_tokens: 32768  # 较长的响应
```

#### `providers[].timeout`
- **类型**: `float`
- **必需**: 否
- **默认值**: `60.0`
- **描述**: 请求超时时间（秒）

```yaml
providers:
  - timeout: 120.0  # 2 分钟
```

#### `providers[].max_retries`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `3`
- **描述**: 失败请求的最大重试次数

```yaml
providers:
  - max_retries: 5
```

---

### 3. 评估器 (`evaluator`)

配置如何评估算法。

#### `evaluator.module`
- **类型**: `string`
- **必需**: 否（对于内置评估器）
- **描述**: 自定义评估器的导入路径。格式：`"module.path:ClassName"`

```yaml
evaluator:
  module: "my_evaluators:CustomEvaluator"
```

#### `evaluator.dataset`
- **类型**: `object`
- **必需**: 是
- **描述**: 具有三种模式的数据集配置

**模式 1：显式文件列表**
```yaml
evaluator:
  dataset:
    mode: "files"
    files:
      - "./data/test1.json"
      - "./data/test2.json"
```

**模式 2：目录遍历**
```yaml
evaluator:
  dataset:
    mode: "directory"
    path: "./data/benchmark/"
    recursive: true  # 搜索子目录
```

**模式 3：Glob 模式**
```yaml
evaluator:
  dataset:
    mode: "glob"
    pattern: "./data/**/*.json"
```

#### `evaluator.metrics`
- **类型**: `list[string]`
- **必需**: 否
- **描述**: 要计算的指标列表

```yaml
evaluator:
  metrics:
    - "accuracy"
    - "runtime"
    - "memory_usage"
```

#### `evaluator.timeout`
- **类型**: `float`
- **必需**: 否
- **默认值**: `60.0`
- **描述**: 每个实例的评估超时时间（秒）

```yaml
evaluator:
  timeout: 30.0  # 每个测试用例 30 秒
```

#### `evaluator.max_retries`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `1`
- **描述**: 失败评估的最大重试次数

```yaml
evaluator:
  max_retries: 3
```

#### `evaluator.parallel`
- **类型**: `boolean`
- **必需**: 否
- **默认值**: `true`
- **描述**: 在 CPU 核心上启用并行评估

```yaml
evaluator:
  parallel: true   # 并行评估
  parallel: false  # 顺序评估
```

#### `evaluator.batch_size`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `10`
- **描述**: 并行评估的批大小

```yaml
evaluator:
  batch_size: 5  # 并行评估 5 个实例
```

---

### 4. 进化 (`evolution`)

配置进化算法参数。

#### `evolution.type`
- **类型**: `string`
- **必需**: 否
- **默认值**: `"island_ga"`
- **取值**: `"island_ga"`、`"dyca"`、`"meoh"`
- **描述**: 编排器选择。决策矩阵见[编排方法概览](orchestration.md)；各编排器的字段分别在 [Island GA](island-ga.md)、[DyCA](dyca.md)、[MEoH](meoh.md)。

```yaml
evolution:
  type: "island_ga"  # 或 "dyca" 或 "meoh"
```

#### `evolution.planner_type`
- **类型**: `string`
- **必需**: 否
- **默认值**: `"llm_evolution"`
- **描述**: 要使用的规划器类型

```yaml
evolutionary:
  planner_type: "llm_evolution"
```

#### `evolution.population_size`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `50`
- **范围**: `[2, ∞)`
- **描述**: 每代中的个体数量

```yaml
evolution:
  population_size: 20  # 较小的种群
  population_size: 100  # 较大的种群
```

#### `evolution.max_generations`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `100`
- **范围**: `[1, ∞)`
- **描述**: 要运行的最大代数

```yaml
evolution:
  max_generations: 50  # 较少的代数
  max_generations: 200  # 较多的代数
```

#### `evolution.elite_ratio`
- **类型**: `float`
- **必需**: 否
- **默认值**: `0.1`
- **范围**: `[0.0, 1.0]`
- **描述**: 保留不变的前沿个体的比例

```yaml
evolution:
  elite_ratio: 0.1  # 保留前 10%
  elite_ratio: 0.2  # 保留前 20%
```

#### `evolution.mutation_rate`
- **类型**: `float`
- **必需**: 否
- **默认值**: `0.3`
- **范围**: `[0.0, 1.0]`
- **描述**: 应用变异的概率

```yaml
evolution:
  mutation_rate: 0.3  # 30% 变异率
```

#### `evolution.crossover_rate`
- **类型**: `float`
- **必需**: 否
-**默认值**: `0.5`
- **范围**: `[0.0, 1.0]`
- **描述**: 应用交叉的概率

```yaml
evolution:
  crossover_rate: 0.5  # 50% 交叉率
```

#### `evolution.selection_strategy`
- **类型**: `enum ["tournament", "roulette", "rank"]`
- **必需**: 否
- **默认值**: `"tournament"`
- **描述**: 选择父代的选择策略

```yaml
evolution:
  selection_strategy: "tournament"  # 锦标赛选择
  selection_strategy: "roulette"     # 轮盘赌选择
  selection_strategy: "rank"         # 基于排名的选择
```

#### `evolution.tournament_size`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `3`
- **范围**: `[2, ∞)`
- **描述**: 锦标赛选择的锦标赛大小

```yaml
evolution:
  tournament_size: 5  # 从 5 个随机个体中选择
```

#### `planner.provider`
- **类型**: `string`
- **必需**: 否
- **默认值**: `"default"`
- **描述**: 用于规划的 LLM 提供商名称

```yaml
planner:
  provider: "planner"  # 必须匹配 providers[].name
```

#### `coder.provider`
- **类型**: `string`
- **必需**: 否
- **默认值**: `"default"`
- **描述**: 用于编码的 LLM 提供商名称

```yaml
coder:
  provider: "coder"  # 必须匹配 providers[].name
```

#### `evolution.early_stop_patience`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `20`
- **范围**: `[1, ∞)`
- **描述**: 停止前没有改进的代数

```yaml
evolution:
  early_stop_patience: 15  # 15 代没有改进后停止
```

#### `evolution.early_stop_threshold`
- **类型**: `float`
- **必需**: 否
- **默认值**: `1e-6`
- **范围**: `[0, ∞)`
- **描述**: 重置提前停止计数器的最小改进

```yaml
evolution:
  early_stop_threshold: 0.001  # 需要 0.001 的改进
```

#### `evolution.checkpoint_interval`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `10`
- **范围**: `[1, ∞)`
- **描述**: 检查点保存之间的代数

```yaml
evolution:
  checkpoint_interval: 5  # 每 5 代保存检查点
```

#### `evolution.max_checkpoints`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `5`
- **范围**: `[1, ∞)`
- **描述**: 要保留的最大检查点数

```yaml
evolution:
  max_checkpoints: 3  # 仅保留最后 3 个检查点
```

---

### 5. 编码器 (`coder`)

配置代码生成设置。

#### `coder.type`
- **类型**: `enum ["claude_code", "opencode", "custom"]`
- **必需**: 否
- **默认值**: `"claude_code"`
- **描述**: 要使用的编码器类型

```yaml
coder:
  type: "claude_code"  # Claude Code 代理
  type: "opencode"     # OpenCode 代理
  type: "custom"       # 自定义朴素编码器
```

#### `coder.timeout`
- **类型**: `float`
- **必需**: 否
- **默认值**: `300.0`
- **描述**: 每次尝试代码生成的最长时间（秒）

```yaml
coder:
  timeout: 120.0  # 2 分钟
```

#### `coder.max_retries`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `2`
- **描述**: 失败代码生成的最大重试次数

```yaml
coder:
  max_retries: 3
```

#### `coder.log_to_console`
- **类型**: `boolean`
- **必需**: 否
- **默认值**: `false`
- **描述**: 启用控制台日志记录

```yaml
coder:
  log_to_console: true
```

---

### 6. 内存 (`memory`)

配置用于存储和检索过去设计的内存系统。

#### `memory.max_entries`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `10000`
- **描述**: 内存中的最大条目数

```yaml
memory:
  max_entries: 5000  # 较小的内存
  max_entries: 20000  # 较大的内存
```

#### `memory.similarity_threshold`
- **类型**: `float`
- **必需**: 否
- **默认值**: `0.8`
- **范围**: `[0.0, 1.0]`
- **描述**: 检索条目的相似度阈值

```yaml
memory:
  similarity_threshold: 0.7  # 更宽松的匹配
  similarity_threshold: 0.9  # 更严格的匹配
```

#### `memory.decay_factor`
- **类型**: `float`
- **必需**: 否
- **默认值**: `0.99`
- **范围**: `[0.0, 1.0]`
- **描述**: 内存相关性随时间的衰减因子

```yaml
memory:
  decay_factor: 0.99  # 慢衰减
  decay_factor: 0.95  # 快衰减
```

#### `memory.max_prompt_cards`
- **类型**: `integer`
- **必需**: 否
- **默认值**: `5`
- **描述**: 每次采样器提示中注入的最大 memory card 数量

```yaml
memory:
  max_prompt_cards: 5
```

#### `memory.persist`
- **类型**: `boolean`
- **必需**: 否
- **默认值**: `true`
- **描述**: 是否将自动抽取的 memory card 持久化到磁盘（存储在 `memory/` 子目录中）

```yaml
memory:
  persist: true
```

#### `memory.static_cards`
- **类型**: `list[MemoryCardConfig]`
- **必需**: 否
- **默认值**: `[]`
- **描述**: 在配置文件中内联定义的静态 memory card 列表。每个 card 包含 `type`、`title`、`content` 等字段

```yaml
memory:
  static_cards:
    - type: "domain_knowledge"
      title: "平台约束"
      content: "目标平台栈深度有限（1024 帧），请优先使用迭代算法。"
      tags: [constraints, platform]
    - type: "general_insight"
      title: "排序建议"
      content: "混合方法（快排 + 小数组插入排序）通常表现更好。"
      tags: [sorting, hybrid]
```

支持的 `type` 值:
- `domain_knowledge`: 任务领域的背景知识
- `general_insight`: 通用算法设计经验
- `good_algorithm`: 表现好的算法模式
- `error_reflection`: 需要避免的错误模式

#### `memory.auto_extraction`
- **类型**: `AutoExtractionConfig`
- **必需**: 否
- **描述**: 配置 LLM 自动从评估后的算法中抽取 memory card

```yaml
memory:
  auto_extraction:
    enabled: true               # 启用自动抽取
    max_cards_per_generation: 3  # 每代最多抽取的 card 数量
    extraction_temperature: 0.3  # LLM 抽取温度

    # 好的算法抽取（学习什么有效）
    extract_good: true           # 从高分算法中抽取经验
    good_score_threshold: null   # 绝对分数阈值（null 表示使用相对阈值）
    good_relative_threshold: 0.8 # 百分位阈值（取种群 top 20%）

    # 差的算法抽取（学习什么要避免）
    extract_bad: true            # 从低分算法中抽取教训
    bad_score_threshold: null    # 绝对分数阈值（null 表示使用相对阈值）
    bad_relative_threshold: 0.2  # 百分位阈值（取种群 bottom 20%）
    extract_on_failure: true     # 从评估失败的算法中抽取教训
```

---

### 7. 工作区 (`workspace`)

配置自动工作区目录创建。

#### `workspace.auto_create`
- **类型**: `boolean`
- **必需**: 否
- **默认值**: `true`
- **描述**: 自动创建任务目录结构

```yaml
workspace:
  auto_create: true
```

#### `workspace.subdirs`
- **类型**: `object`
- **必需**: 否
- **默认值**: 见下文
- **描述**: 任务目录中的子目录名称

```yaml
workspace:
  subdirs:
    state: "state"
    logs: "logs"
    checkpoints: "checkpoints"
    generated: "generated"
    temp: "temp"
    memory: "memory"            # 自动抽取的 memory card 持久化目录
```

---

### 8. 日志 (`logging`)

配置日志记录行为。

#### `logging.level`
- **类型**: `enum ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]`
- **必需**: 否
- **默认值**: `"TRACE"`
- **描述**: 日志级别

```yaml
logging:
  level: "INFO"        # 标准信息
  level: "DEBUG"       # 详细调试
  level: "WARNING"     # 仅警告和错误
```

#### `logging.format`
- **类型**: `string`
- **必需**: 否
- **默认值**: Loguru 默认格式
- **描述**: 日志格式字符串

```yaml
logging:
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

#### `logging.file`
- **类型**: `string`
- **必需**: 否
- **默认值**: 由工作区自动设置
- **描述**: 日志文件路径

```yaml
logging:
  file: "./logs/experiment.log"
```

#### `logging.console`
- **类型**: `boolean`
- **必需**: 否
- **默认值**: `true`
- **描述**: 启用控制台日志记录

```yaml
logging:
  console: true
```

#### `logging.json_format`
- **类型**: `boolean`
- **必需**: 否
- **默认值**: `false`
- **描述**: 使用 JSON 格式进行结构化日志记录

```yaml
logging:
  json_format: true  # JSON 日志用于日志聚合系统
```

---

### 9. 嵌入 (`embedding`)

`embedding:` 块配置算法相似度、DyCA 聚类、MEoH 多样性惩罚以及 consultant/advisor/recommender 的语义检索使用的嵌入服务。详见[嵌入与轨迹](embeddings.md)。

```yaml
embedding:
  type: "openai_compatible"
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"
  model: "text-embedding-3-small"
  dim: 1536                   # 必须与模型维度匹配
  embedding_func_max_async: 4
```

支持的 `type`：`openai`、`openai_compatible`、`jina`、`mock`、`local`。`local` 模式下文本和代码嵌入可通过 `text_config:` 与 `code_config:` 子块走不同部署（PR #90）；详见[嵌入指南 § local 模式](embeddings.md#local-模式文本与代码分流)。

### 10. 多模态 (`multimodal`)

```yaml
multimodal:
  enabled: false                  # 总开关
  max_images_per_prompt: 3
  image_max_size_kb: 512
  include_observation_text: true
  two_step_refinement: false
  behavior_storage: "rendered"    # rendered | raw | none
```

`enabled: true` 时，多模态采样器（`multimodal_init_sampler`、`multimodal_mutation_sampler`、`multimodal_crossover_sampler`）变得可用，评估器返回的行为图像/轨迹会被拼入提示词。详见[多模态指南](multimodal.md)。

### 11. 版本控制 (`version_control`)

为每个个体创建一次性 git 工作树，把并发候选互相隔离，主分支保持干净。多数用户无需修改这块，默认即可。

```yaml
version_control:
  enabled: true
  type: "git_worktree"
  local_path: "tsp_algorithm"     # 算法代码包路径
  remote_url: null                # 可选：从远端 clone
  revision: null                  # 可选：要 checkout 的 branch/tag
  auto_initialize: true           # 没有 git 时自动初始化
  auto_cleanup: true              # 运行结束清理工作树
  max_worktree_age_days: 7
  max_worktrees: 100
  default_branch: "main"
  commit_message_template: "Algorithm {algorithm_id}: {description}"
```

运行结束时写出的 `best/` 快照目录（PR #95）不受 `auto_cleanup` 影响。CLI 在结束时打印路径，详见[架构数据流 § 运行目录](../architecture/data-flow.md#运行目录布局)。

### 12. 编排器专属字段

`evolution:` 块还接受按 `evolution.type` 路由的专属字段。只有匹配的字段会被校验：

| `evolution.type` 为 | 字段参考 |
|---|---|
| `island_ga` | [Island GA](island-ga.md) — `num_islands`、`island_population_size`、`migration_*`、`parallel_islands`、`per_island_config` |
| `dyca` | [DyCA](dyca.md) — `n_clusters`、`clustering_method`、`recluster_interval`、`ari_threshold`、`n_anchors`、`*_pool_size`、`sos_stagnation_threshold`、`using_mode` |
| `meoh` | [MEoH](meoh.md) — `population_size`、`selection_num`、`max_sample_nums`、`objective_metrics`、`use_e2_operator`、`use_m1_operator`、`use_m2_operator`、`seed_path`、`code_generation_mode`、`active_population_ratio` |

多目标运行仅 MEoH 支持；通过 `objective_metrics: [...]` 定义 Pareto 维度。

### 13. 评估器计时（`evaluator.timing_metrics`）

`evaluator:` 块支持可选的 `timing_metrics:` 子块，控制细粒度计时收集与是否把时间纳入 score。默认仅观测、不影响 score。详细参考：[计时与指标](timing-metrics.md)。

```yaml
evaluator:
  timing_metrics:
    enabled: true
    include_in_score: false
    score_components: ["candidate_runtime_ms"]
    aggregation: "sum"
    weights:
      candidate_runtime_ms: 1.0
```

---

## 环境变量

LLM4AD 支持在配置文件中使用 `${VAR_NAME}` 语法进行环境变量替换：

```yaml
providers:
  - api_key: "${OPENAI_API_KEY}"
  - base_url: "${CUSTOM_API_URL}"

evaluator:
  dataset:
    path: "${DATA_DIR}/benchmark"
```

在运行之前设置环境变量：

```bash
export OPENAI_API_KEY="your-key"
export CUSTOM_API_URL="http://localhost:8000/v1"
export DATA_DIR="./data"

llm4ad run config.yaml
```

## 示例配置

### 最小配置

```yaml
project_name: "minimal-example"

providers:
  - name: "default"
    type: "openai"
    api_key: "${OPENAI_API_KEY}"

evaluator:
  module: "my_evaluator:MyEvaluator"

evolution:
  population_size: 10
  max_generations: 5
```

### 多提供商配置

```yaml
project_name: "multi-provider"

providers:
  - name: "planner"
    type: "anthropic"
    model: "claude-3-5-sonnet-20241022"
    temperature: 0.3  # 更确定用于规划

  - name: "coder"
    type: "openai"
    model: "gpt-4o"
    temperature: 0.1  # 非常低用于代码生成

planner:
  provider: "planner"
coder:
  provider: "coder"
```

### 完整配置

请参阅项目根目录下的 `examples/config/config.complete.yaml` 获取包含所有选项的完整示例。

## 最佳实践

1. **使用环境变量**：永远不要在配置文件中硬编码 API 密钥
2. **从小开始**：从小种群和少代数开始
3. **调整温度**：使用较低的温度（0.1-0.3）进行代码生成
4. **设置超时**：合理的超时可以防止挂起
5. **启用检查点**：保存检查点以恢复中断的运行
6. **监控内存**：根据您的需求调整 `max_entries`
7. **并行评估**：在多核系统上启用以加快评估

## 下一步

- [快速入门指南](quickstart.md) - 运行您的第一个实验
- [编写评估函数](evaluators.md) - 创建自定义评估器
- [高级配置](advanced.md) - 高级使用模式
