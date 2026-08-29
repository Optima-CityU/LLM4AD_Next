# CSPaper 引导的 ALNS/TSP 算法进化示例

本示例由一次真实的本地论文算法优化实验整理而来，目标是优化对称欧氏旅行商问题（TSP）中 ALNS 求解器的 `destroy_operator`，而不是改写整个算法工程。

论文和 CSPaper 评审仅作为不可信的搜索指导输入。候选算法是否合法、性能是否真正提升，完全由本地 evaluator 执行和判定。

## 原始输入

本目录保留了复现实验所需的最小输入集合：

- `paper.pdf`：实验使用的 ALNS 论文。
- `review.md`：CSPaper 风格的算法改进建议、评价目标、硬约束、数据要求和基线定义。
- `algorithm/ALNS-master/solve.py`：内层源码仓库 `master` 分支中进化前的基线算法，提交为 `b348b53407c2a5f63a19719fc9b9a5026d701731`。
- `data/train/`：5 个训练用 TSPLIB95 `EUC_2D` 实例。
- `data/validation/`：3 个验证实例。
- `private-test/`：3 个不参与进化的最终测试实例。
- `generate_tsp_datasets.py`：确定性数据生成器。
- `dataset-manifest.json`：11 个数据实例的随机种子、规模、分布和 SHA-256 校验值。
- `config.yaml`：LLM4AD 任务及 MEoH 进化配置。
- `task_evaluator.py`：候选算法的可执行评价器。

以下运行生成物没有放入示例：

- 内层 `.git` 对象和候选分支
- Git worktree
- `runs/` 和 `pipeline-*`
- 缓存和日志
- 已生成的候选算法及排行榜

ALNS 依赖固定为 `alns==7.0.0`。其 MIT 许可证和引用信息保存在基线源码旁边。

## 可移植性调整

从真实结果目录提取示例时，进行了两处不影响算法语义的调整：

1. Evaluator 不再默认使用某台电脑上的固定 Conda 路径，而是优先读取 `TSP_EVALUATOR_PYTHON`，未配置时使用当前 Python。
2. 数据生成器改为按 ASCII 字节写入 `.tsp` 文件，避免 Windows 的 `CRLF` 换行导致 SHA-256 清单与实际文件不一致。

基线 `solve.py` 保持原样，提取前后的 SHA-256 一致。

## 优化范围

LLM 只允许修改 `solve.py` 中下面两个标记之间的代码：

```python
# EVOLVE_START
def destroy_operator(...):
    ...
# EVOLVE_END
```

以下部分保持固定：

- ALNS 框架
- repair operator
- 初始解构造
- 接受准则
- 停止条件
- 数据加载
- 目标函数计算
- evaluator 和硬约束

## Evaluator 逻辑

Evaluator 会在独立 Python 进程中运行候选算法，并使用随机种子 `42`、`314` 和 `2026`，将候选 destroy operator 与固定的随机删边基线进行比较。

Evaluator 不相信候选代码返回的目标值，而是从 TSPLIB 坐标重新计算旅行路径长度。以下情况会直接判定候选失败：

- destroy operator 返回了错误的数据类型
- 节点或距离数据被修改
- 删除边数量为零或超过预算
- 最终结果不是完整 Hamilton 回路
- 节点缺失、重复或伪造
- 目标值不是有限正数
- 候选尝试访问文件、网络或外部进程
- 执行超过固定时间预算

主要评价指标为：

| 指标 | 方向 | 含义 |
| --- | --- | --- |
| `relative_tour_gap_pct` | 最小化 | 候选路径长度相对固定基线的平均百分比差距 |
| `runtime_ms` | 最小化 | 固定迭代预算下候选算法的中位运行时间 |

## 安装候选算法依赖

Evaluator 使用的候选 Python 需要安装 `alns`、`numpy` 和 `tsplib95`。

PowerShell：

```powershell
python -m pip install -r .\examples\integrations\cspaper_alns_demo\requirements.txt
```

Bash：

```bash
python3 -m pip install -r ./examples/integrations/cspaper_alns_demo/requirements.txt
```

也可以通过环境变量指定已经安装这些依赖的 Python：

```powershell
$env:TSP_EVALUATOR_PYTHON = "C:\path\to\python.exe"
```

```bash
export TSP_EVALUATOR_PYTHON=/path/to/python
```

## 无 LLM Dry-run

Dry-run 会执行以下步骤：

1. 将论文、评审、基线源码和数据复制到新的运行目录。
2. 设置小规模、可复现的 MEoH 预算。
3. 将 `review.md` 编译为新的 `AlgorithmDesignSpec`。
4. 校验所有输入路径并记录确认信息。
5. 生成 `config.cspaper.yaml` 并审核 evaluator 合同。
6. 在全部 5 个训练实例和 3 个验证实例上执行基线 evaluator。
7. 保存 Spec、评分结果和完整日志。

Dry-run 不读取 LLM API Key，也不会产生 LLM 调用费用。

### PowerShell

在 LLM4AD_Next 仓库根目录执行：

```powershell
.\examples\integrations\cspaper_alns_demo\run-demo.ps1
```

指定候选 Python：

```powershell
.\examples\integrations\cspaper_alns_demo\run-demo.ps1 `
  -CandidatePython "C:\path\to\python.exe"
```

指定输出目录：

```powershell
.\examples\integrations\cspaper_alns_demo\run-demo.ps1 `
  -RunRoot "D:\lenovo\LLM4AD_Next-demo-runs\my-alns-dryrun"
```

重新生成并校验数据：

```powershell
.\examples\integrations\cspaper_alns_demo\run-demo.ps1 `
  -RegenerateData
```

### Bash、WSL 或 Git Bash

```bash
bash ./examples/integrations/cspaper_alns_demo/run-demo.sh \
  --candidate-python /path/to/python
```

指定输出目录：

```bash
bash ./examples/integrations/cspaper_alns_demo/run-demo.sh \
  --candidate-python /path/to/python \
  --run-root /path/to/alns-dryrun
```

## 调用 LLM 进行真实进化

先确保以下环境变量已经配置：

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

PowerShell：

```powershell
.\examples\integrations\cspaper_alns_demo\run-demo.ps1 `
  -Evolve `
  -PopulationSize 2 `
  -MaxSamples 3 `
  -TopK 2
```

Bash：

```bash
bash ./examples/integrations/cspaper_alns_demo/run-demo.sh \
  --evolve \
  --population-size 2 \
  --max-samples 3 \
  --top-k 2
```

参数含义：

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `PopulationSize` / `--population-size` | 2 | MEoH 最终种群规模 |
| `MaxSamples` / `--max-samples` | 3 | 最多生成并评价的候选样本数 |
| `TopK` / `--top-k` | 2 | 最多导出的候选数量 |

只有显式提供 `-Evolve` 或 `--evolve` 才会调用 LLM。

## 输出文件

默认情况下，运行结果保存在仓库外部的时间戳目录：

```text
LLM4AD_Next-demo-runs/alns-paper-YYYYMMDD-HHMMSS/
```

Dry-run 主要输出：

```text
algorithm-design-spec.json
config.cspaper.yaml
dry-run-results.json
run-transcript.log
```

真实进化还会在 `runs/` 下生成：

```text
leaderboard.json
evolution-lineage.json
candidates/
```

`private-test/` 不属于 `config.yaml` 配置的进化数据集，只用于候选生成后的最终独立筛选。
