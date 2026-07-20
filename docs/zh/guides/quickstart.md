# 快速入门指南

本指南带你跑完第一个 LLM4AD 算法设计实验。有两种路径：

- **路径 A — 自动构建（推荐新手）：** 用自然语言描述任务，让 `llm4ad chat` 帮你生成评估器、算法模板和配置。约 5 分钟。
- **路径 B — 手动构建：** 自己写评估器和配置。工作量更大，但完全可控。理解了自动构建的产物之后再用这条路。

两条路径最后都用同一条 `llm4ad run` 命令收尾。

## 前提条件

- ✅ Python 3.12 或更高版本
- ✅ 已安装 LLM4AD（参见[安装](installation.md)）
- ✅ OpenAI / Anthropic 的 API 密钥，或任何 OpenAI 兼容端点

---

## 路径 A — 自动构建（`llm4ad chat`）

`llm4ad chat` 是一个引导式向导：你描述问题，它产出一个完整可运行的应用目录。前期不需要写任何 Python 或 YAML。

### A.1 — 配置默认 provider

只需要写一次 `~/.llm4ad/settings.yaml`，后续 `llm4ad chat` 与 `llm4ad run` 都复用：

```yaml
providers:
  - name: "default"
    type: "openai_compatible"
    base_url: "https://api.openai.com/v1"
    api_key: "${OPENAI_API_KEY}"
    model: "gpt-4o"
```

然后导出 key：

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

如果用 Anthropic，把 `type` 改为 `"anthropic"`、`api_key` 用 `${ANTHROPIC_API_KEY}`。完整样例见 [`examples/config/settings.yaml`](https://github.com/Optima-CityU/LLM4AD_Next/blob/main/examples/config/settings.yaml)。

### A.2 — 运行向导

```bash
llm4ad chat
```

向导会：

1. 询问要进化什么算法（例如 *"最小化比较次数的排序"*）。
2. 询问输入/输出格式与评估准则。
3. 生成完整任务包：评估器、带 `EVOLVE` 标记的算法模板、示例数据、调试脚本、`config.yaml`。
4. 自检——跑一遍生成的 `debug_run.py` 和 `test_evaluator.py`。
5. 询问是否立即跑流水线。

如果你已经清楚要构建什么，可以跳过对话：

```bash
llm4ad chat --prompt "进化最小化比较次数的排序算法" \
  --output ./my_task/
```

产出目录形如：

```
my_task/sorting/
├── config.yaml                    # 直接给 `llm4ad run` 用
├── sorting_evaluator.py           # 自动生成的评估器
├── sorting_algorithm/sort.py      # 含 EVOLVE_START/END 标记的算法模板
├── debug_run.py                   # 快速冒烟测试
├── test_evaluator.py              # 评估器端到端测试
└── data/sample/                   # 示例输入
```

### A.3 — 运行

```bash
llm4ad run my_task/sorting/config.yaml
```

输出与结果检查与路径 B 相同 — 跳到[第 5 步：查看结果](#第-5-步查看结果)。

向导的完整 flag 列表、校验流水线以及 `--code-path` 模式（在已有代码上改造），见[自动构建](auto-builder.md)和 [CLI § chat](cli.md#chat)。

---

## 路径 B — 手动构建

如果你想自己把所有东西连起来，按下面 5 步走。最终产物和向导生成的功能一致。

### 第 1 步：设置 API 密钥

将您的 LLM 提供商 API 密钥设置为环境变量：

```bash
# 对于 OpenAI
export OPENAI_API_KEY="your-openai-api-key"

# 或对于 Anthropic
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

### 第 2 步：创建简单的评估器

创建文件 `my_evaluator.py`，包含一个简单的排序算法评估器：

```python
"""简单的排序算法评估器。"""

from llm4ad.evaluator.base import PythonEvaluator, EvaluationResult, Metric, MetricType


class SortingEvaluator(PythonEvaluator):
    """评估排序算法。"""

    def __init__(self, config):
        super().__init__(config)

    @property
    def name(self) -> str:
        return "sorting_evaluator"

    @property
    def metrics(self) -> list[Metric]:
        return [
            Metric(name="correctness", type=MetricType.MAXIMIZE, description="正确排序数组的比例"),
            Metric(name="avg_time", type=MetricType.MINIMIZE, description="平均排序时间（秒）"),
        ]

    async def evaluate(self, cfg) -> EvaluationResult:
        """评估排序算法。"""
        import time
        import random

        # 测试数据
        test_cases = [
            [3, 1, 4, 1, 5, 9, 2, 6],
            [1, 2, 3, 4, 5],  # 已排序
            [5, 4, 3, 2, 1],  # 逆序
            [42] * 10,  # 全部相同
            random.sample(range(100), 20),  # 随机
        ]

        total_time = 0.0
        correct = 0

        for arr in test_cases:
            # 复制数组进行排序
            arr_copy = arr.copy()
            expected = sorted(arr)

            # 计时排序
            start = time.time()

            try:
                # 执行排序函数
                # 假设算法定义了 'sort' 函数
                exec_globals = {"array": arr_copy}
                exec(cfg.project_root + "\nresult = sort(array)", exec_globals)
                sorted_arr = exec_globals["result"]

                elapsed = time.time() - start
                total_time += elapsed

                correct += 1 if sorted_arr == expected else 0
            except Exception as e:
                # 如果执行失败，计为不正确
                total_time += 1.0  # 惩罚

        correctness = correct / len(test_cases)
        avg_time = total_time / len(test_cases)

        return EvaluationResult(
            score=correctness * 100 - avg_time * 10,  # 组合得分
            metrics={
                "correctness": correctness,
                "avg_time": avg_time,
            },
            success=True,
        )
```

### 第 3 步：创建配置文件

创建 `quickstart_config.yaml`：

```yaml
# 快速入门配置

# 项目设置
project_name: "quickstart-demo"
base_dir: "./runs"
random_seed: 42

# LLM 提供商配置
providers:
  - name: "default"
    type: "openai"
    api_key: "${OPENAI_API_KEY}"  # 使用环境变量
    model: "gpt-4o"
    temperature: 0.7
    max_tokens: 4096
    timeout: 60.0
    max_retries: 3

# 评估器配置
evaluator:
  module: "my_evaluator:SortingEvaluator"  # 我们评估器的导入路径
  timeout: 30.0
  max_retries: 2
  parallel: true
  batch_size: 5

# 进化设置
evolution:
  type: "island_ga"
  planner_type: "llm_evolution"
  population_size: 10  # 小种群用于快速演示
  max_generations: 5   # 少代数用于快速演示
  elite_ratio: 0.2
  mutation_rate: 0.3
  crossover_rate: 0.5
  selection_strategy: "tournament"
  tournament_size: 3
  early_stop_patience: 10
  early_stop_threshold: 0.01
  checkpoint_interval: 2
  max_checkpoints: 3

# 规划器设置
planner:
  provider: "default"

# 编码器设置
coder:
  type: "custom"
  provider: "default"
  timeout: 120.0
  max_retries: 2

# 内存设置
memory:
  max_entries: 1000
  similarity_threshold: 0.8

# 工作区设置
workspace:
  auto_create: true

# 日志设置
logging:
  level: "INFO"
  console: true
```

### 第 4 步：运行实验

使用 CLI 执行实验：

```bash
llm4ad run quickstart_config.yaml
```

您应该看到类似的输出：

```
╭─────────────────────────────────────────────────────────────╮
│  LLM4AD - LLM for Algorithm Design                        │
╰─────────────────────────────────────────────────────────────╯

Project: quickstart-demo
Run ID: a1b2c3d4
Workspace: ./runs/quickstart-demo/a1b2c3d4

Configuration loaded successfully
Starting evolution...

Generation 1/5
  Population: 10 individuals
  Best score: 85.2
  Avg score: 72.4

Generation 2/5
  Population: 10 individuals
  Best score: 88.7
  Avg score: 76.1

...

Evolutionary completed!
Best snapshot: ./runs/quickstart-demo/a1b2c4d4/best
```

### 第 5 步：查看结果

实验完成后，检查结果：

```bash
# 查看最佳算法源码
cat ./runs/quickstart-demo/a1b2c3d4/best/code/<进化的文件>.py

# 查看结构化元数据（分数、代数、谱系、评估指标）
cat ./runs/quickstart-demo/a1b2c3d4/best/metadata.json

# 一页式人读摘要
cat ./runs/quickstart-demo/a1b2c3d4/best/summary.txt

# 查看日志
cat ./runs/quickstart-demo/a1b2c3d4/logs/run.log
```

---

## 理解输出

两种路径产出的工作区布局相同。

### 目录结构

LLM4AD 自动创建一个组织良好的工作区：

```
./runs/quickstart-demo/a1b2c3d4/
├── best/                # 最优个体稳定快照（MEoH 还含 Pareto 前沿）
│   ├── code/                   # 最优 worktree 的纯目录拷贝
│   ├── metadata.json           # 分数、代数、父代、评估指标
│   ├── summary.txt             # 一页式人读摘要
│   └── pareto/                 # 仅 MEoH：每个存档成员一个子目录
├── state/               # 用于 resume 的缓存状态（如 evolution_state.json）
├── logs/                # 日志文件
│   └── run.log
├── checkpoints/         # 进化检查点（按代写 JSON）
├── generated/           # 所有生成的算法（按个体 JSON + Markdown）
├── worktrees/           # coder 在进化期间维护的 git worktree
└── temp/                # 临时文件
```

### 进化摘要

`evolution_summary.json` 包含：

```json
{
  "best_score": 92.5,
  "best_generation": 4,
  "total_generations": 5,
  "population_size": 10,
  "total_evaluations": 50,
  "convergence_curve": [72.4, 76.1, 81.3, 88.7, 92.5],
  "best_metrics": {
    "correctness": 1.0,
    "avg_time": 0.0032
  }
}
```

## 下一步

现在您已经运行了第一个实验，探索更多高级功能：

### 尝试不同问题

- [排序算法示例](../examples/sorting.md) - 设计更好的排序算法
- [TSP 示例](../examples/tsp.md) - 探索 TSP 算法
- [符号回归示例](../examples/symbolic-regression.md) - 发现数学表达式

### 高级配置

- [配置指南](configuration.md) - 学习所有配置选项
- [编写评估函数](evaluators.md) - 创建自定义评估器
- [高级配置](advanced.md) - 高级使用模式

### 自定义您的工作流

- 使用不同的 LLM 提供商（OpenAI、Anthropic 或自定义）
- 根据您的问题调整进化参数
- 实现自定义选择策略
- 添加多目标优化

## 常见问题

### "API key not found"

确保在运行之前设置了环境变量：

```bash
export OPENAI_API_KEY="your-key"
llm4ad run quickstart_config.yaml
```

### "Module not found: my_evaluator"

确保 `my_evaluator.py` 在当前目录或 Python 路径中：

```bash
# 将当前目录添加到 Python 路径
export PYTHONPATH="${PYTHONPATH}:."
llm4ad run quickstart_config.yaml
```

### "Out of memory"

减少种群大小或禁用并行评估：

```yaml
evolution:
  population_size: 5  # 更小的种群

evaluator:
  parallel: false  # 禁用并行评估
```

## 成功技巧

1. **从小开始**：从小种群和少代数开始
2. **监控进度**：定期检查日志以了解发生了什么
3. **调整温度**：降低温度（0.3-0.5）以获得更确定的代码
4. **设置超时**：合理的超时可以防止在错误代码上挂起
5. **使用检查点**：启用检查点以恢复中断的运行

## 获取帮助

- 📖 [文档首页](../index.md)
- 💬 [讨论](https://github.com/Optima-CityU/LLM4AD_Next/discussions)
- 🐛 [问题跟踪](https://github.com/Optima-CityU/LLM4AD_Next/issues)
