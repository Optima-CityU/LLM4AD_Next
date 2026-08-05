# 评估器 API

`llm4ad.evaluator` 在数据集上执行算法，并返回带分数的指标，驱动整个进化过程。

## 公共接口

| 符号 | 职责 | 源码 |
|---|---|---|
| `BaseEvaluator` | 自定义 Python 评估器的根基类；继承并实现 `evaluate(...)` | `src/llm4ad/evaluator/base.py` |
| `BaseBatchEvaluator` | 需要同时比较同代多个候选的评估器基类；实现 `evaluate_batch(...)` | `src/llm4ad/evaluator/base.py` |
| `PythonEvaluator` | 直接调用 Python 函数的便捷子类 | `src/llm4ad/evaluator/base.py` |
| `BenchmarkEvaluator` | 多实例聚合（数据集中每个文件一个评估实例） | `src/llm4ad/evaluator/base.py` |
| `LLMJudgeEvaluator` | 用 LLM 作为评分人的评估器，适合无法直接量化的输出 | `src/llm4ad/evaluator/llm_judge.py` |
| `PaperRevisionEvaluator` | 对论文局部改写执行静态保护、多 LLM 盲评或辩论选举 | `src/llm4ad/evaluator/paper_revision/` |
| `ExecutableEvaluator` | 运行外部命令并以正则匹配 stdout 抽取指标 | `src/llm4ad/evaluator/base.py` |
| `EvaluationDispatcher` | 按 `evaluator.type` + `module:` 分派到具体实现 | `src/llm4ad/evaluator/dispatcher.py` |
| `EvaluationResult` | 标准返回信封：`score`、`metrics`、`metadata`、`success` 等 | `src/llm4ad/evaluator/base.py` |
| `Metric`、`MetricType` | 单个指标定义（名称、方向、权重） | `src/llm4ad/evaluator/base.py` |
| `BehaviorData`、`BehaviorVisualization` | 多模态评估器返回的行为数据载荷 | `src/llm4ad/evaluator/behavior.py` |
| `BaseRenderer` | 把 `behavior_storage="raw"` 中的原始数据渲染成图像 | `src/llm4ad/evaluator/renderer.py` |

## 编写自定义 Python 评估器

```python
# my_eval.py
from llm4ad.evaluator import PythonEvaluator
from llm4ad.evaluator.base import EvaluationResult, Metric, MetricType

class SortEvaluator(PythonEvaluator):
    @property
    def metrics(self):
        return [Metric(name="comparisons", type=MetricType.MINIMIZE)]

    async def evaluate(self, cfg) -> EvaluationResult:
        # 在 cfg.project_root 上执行算法、收集统计
        n_cmp = run_algorithm_and_count(cfg.project_root, cfg.data_path)
        return EvaluationResult(
            score=-n_cmp,            # 进化总是 maximize score
            metrics={"comparisons": n_cmp},
            success=True,
            duration_ms=42.0,
        )
```

YAML 中：

```yaml
evaluator:
  type: custom
  module: my_eval:SortEvaluator
  metrics: ["comparisons"]
  dataset:
    mode: directory
    path: ./data
    recursive: true
```

`module` 字段支持两种语法：`pkg.module:ClassName` 或 `path/to/file.py:ClassName`。除了已知字段外，YAML 上的额外键（如 `api_config:`）会通过 `model_extra` 传给评估器构造函数。

## EvalContext

每次 `evaluate(cfg)` 调用都会拿到一个 `EvalContext`：

| 字段 | 含义 |
|---|---|
| `project_root` | 当前个体的 git 工作树根（由编码器创建） |
| `data_path` | 由 `DatasetConfig` 解析出的本次实例路径（依模式而定） |
| `timeout` | 软超时，秒 |
| `behavior_storage` | `"rendered"` / `"raw"` / `"none"` — 提示评估器是否应该收集行为数据 |
| `candidate_id` | 当前候选 ID；旧评估器可以忽略 |
| `generation` | 当前进化代数；用于可复现分配和记录来源 |
| `parent_ids` | 父候选 ID；用于追踪改写路径 |

## 论文局部改写评估器

`PaperRevisionEvaluator` 只负责评估，不负责 PDF/TeX 解析、生成改写、替换原文或直接写入记忆。上游先把用户选中的部分和 CSPaper 建议规范化为任务 JSON；每个 worktree 保存一个候选改写。

任务文件示例：

```json
{
  "task_id": "paper-1-methods",
  "document_id": "paper-1",
  "section_id": "methods",
  "section_title": "Methods",
  "language": "en",
  "original_text": "Original selected section...",
  "context_before": "Previous section ending...",
  "context_after": "Next section beginning...",
  "cspaper_findings": [
    {
      "id": "finding-1",
      "issue": "The mechanism is underspecified.",
      "suggestion": "Explain the causal steps.",
      "severity": "high"
    }
  ],
  "constraints": {
    "preserve_citations": true,
    "allow_new_citations": false,
    "preserve_numbers": true,
    "locked_terms": ["ANSL"]
  }
}
```

候选文件默认为 worktree 下的 `candidate.json`：

```json
{
  "candidate_id": "candidate-07",
  "section_id": "methods",
  "revised_text": "Revised selected section...",
  "generation": 3,
  "change_summary": "Clarified the mechanism"
}
```

YAML 配置：

```yaml
providers:
  - name: judge_openai
    type: openai_compatible
    api_key: ${OPENAI_API_KEY}
    model: your-model-a
  - name: judge_claude
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}
    model: your-model-b

evaluator:
  type: custom
  provider: judge_openai
  module: llm4ad.evaluator.paper_revision:PaperRevisionEvaluator
  mode: panel                  # panel 或 debate
  judges: [judge_openai, judge_claude]
  panel_size: 2
  min_judges: 2
  candidate_file: candidate.json
  random_seed: 42
  dataset:
    mode: files
    files: [paper-task.json]
```

`panel` 会把原文和改写匿名为 A/B、确定性随机交换顺序，然后汇总修改前后分数。`debate` 使用 `BaseBatchEvaluator` 同时接收同代候选，在独立评分后执行匿名交叉评议和最终投票。

固定输出指标包括 `baseline_score`、`revised_score`、`score_delta`、`judge_agreement` 和 `static_valid`。各维度分数、原始 Judge 报告、辩论票、静态检查结果和 `memory_candidates` 位于 `EvaluationResult.metadata`。

评估器不会直接持久化 `memory_candidates`。编排器应仅在胜者确定且通过用户确认后，将标记为 `recommended` 的经验转换为 `MemoryCard`，防止落选候选污染记忆。

## 多实例 / 基准式评估

`BenchmarkEvaluator` 会按 `dataset.mode = files | directory | glob` 中的每个数据集文件并行地调用 `evaluate_instance`，再调用 `aggregate(...)` 把分数和指标合并。

```python
class TSPBenchmark(BenchmarkEvaluator):
    metrics = [Metric(name="tour_length", type=MetricType.MINIMIZE)]

    async def evaluate_instance(self, algorithm, ctx, instance_path) -> EvaluationResult:
        ...

    def aggregate(self, results) -> EvaluationResult:
        avg = sum(r.metrics["tour_length"] for r in results) / len(results)
        return EvaluationResult(score=-avg, metrics={"tour_length": avg})
```

## 行为数据 / 多模态

`EvaluationResult.behavior` 让评估器把图像、轨迹或观察值返回给规划器。需要时启用 `multimodal.enabled` 即可让多模态采样器在提示词里使用这些数据 — 详见[多模态](../guides/multimodal.md)。
当 `behavior_storage="raw"` 时，必须注册 `BaseRenderer` 才能后续从原始数据重建图像；具体范例见 `src/llm4ad/evaluator/renderer.py`。

## 相关链接

- [评估器指南](../guides/evaluators.md) — 任务式实操
- [配置指南](../guides/configuration.md#evaluator) — `evaluator:` 配置块
- 源码权威：`src/llm4ad/evaluator/`
