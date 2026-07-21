from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from rich.table import Table

from llm4ad.config.ui import ui


class EvolutionConfig(BaseModel):
    """Evolution algorithm configuration.

    Allows extra fields for algorithm-specific configuration that will
    be passed to the appropriate concrete config class.

    Registered via the registry system where evolution.type maps to the
    appropriate config class.
    """

    model_config = {"extra": "allow"}

    # Evolution orchestrator type
    type: Literal["default"] = Field(
        default="default",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="进化算法的类型，决定使用哪种进化策略",
            desc_en="Type of evolution algorithm, determines which evolution strategy to use",
        ),
    )

    population_size: int = Field(
        default=4, ge=2, description="Population size",
        json_schema_extra=ui(
            label_zh="种群大小", label_en="Population Size",
            desc_zh="每代进化的个体数量，值越大搜索空间越广但计算开销越大",
            desc_en="Number of individuals per generation; larger values explore more but cost more compute",
            hidden=True,
        ),
    )
    max_generations: int = Field(
        default=100, ge=1, description="Maximum generations",
        json_schema_extra=ui(
            label_zh="最大代数", label_en="Max Generations",
            desc_zh="进化运行的最大代数，达到后自动停止",
            desc_en="Maximum number of generations to run before stopping automatically",
        ),
    )
    elite_ratio: float = Field(
        default=0.1, ge=0.0, le=1.0, description="Elite preservation ratio",
        json_schema_extra=ui(
            label_zh="精英保留比例", label_en="Elite Ratio",
            desc_zh="每代中直接保留到下一代的最优个体比例，防止优秀解丢失",
            desc_en="Fraction of top individuals preserved unchanged into the next generation to prevent loss of good solutions",
        ),
    )
    mutation_rate: float = Field(
        default=0.3, ge=0.0, le=1.0, description="Mutation probability",
        json_schema_extra=ui(
            label_zh="变异概率", label_en="Mutation Rate",
            desc_zh="个体发生变异的概率，较高值增加多样性但可能破坏优秀解",
            desc_en="Probability of mutating an individual; higher values increase diversity but may disrupt good solutions",
        ),
    )
    crossover_rate: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Crossover probability",
        json_schema_extra=ui(
            label_zh="交叉概率", label_en="Crossover Rate",
            desc_zh="两个父代个体进行交叉重组的概率，用于组合不同解的优势",
            desc_en="Probability of recombining two parent individuals to combine strengths of different solutions",
        ),
    )
    survival_selection_strategy: Literal["tournament", "roulette", "rank", "random", "truncation"] = Field(
        default="truncation",
        description="Strategy for selecting survivors from offspring and non-elite parents",
        json_schema_extra=ui(
            label_zh="存活选择策略", label_en="Survival Selection Strategy",
            desc_zh="从后代和非精英父代中选择存活个体的策略，如锦标赛、轮盘赌、排名等",
            desc_en="Strategy for selecting survivors from offspring and non-elite parents, e.g. tournament, roulette, rank",
        ),
    )
    tournament_size: int = Field(
        default=3, ge=2, description="Tournament size",
        json_schema_extra=ui(
            label_zh="锦标赛大小", label_en="Tournament Size",
            desc_zh="锦标赛选择中每轮参与竞争的个体数量，仅在选择策略为锦标赛时生效",
            desc_en="Individuals competing per tournament round; only used when strategy is tournament",
        ),
    )

    # Early stopping
    early_stop_patience: int = Field(
        default=20, ge=1, description="Early stopping patience",
        json_schema_extra=ui(
            label_zh="早停耐心值", label_en="Early Stop Patience",
            desc_zh="连续多少代没有显著改进后触发早停，避免无效计算",
            desc_en="Number of consecutive generations without significant improvement before triggering early stop",
        ),
    )
    early_stop_threshold: float = Field(
        default=1e-6, ge=0, description="Improvement threshold",
        json_schema_extra=ui(
            label_zh="早停阈值", label_en="Early Stop Threshold",
            desc_zh="判定为有效改进的最小适应度变化量，低于此值视为无改进",
            desc_en="Minimum fitness improvement to count as progress; changes below this threshold are treated as stagnation",
        ),
    )

    # Checkpointing
    checkpoint_interval: int = Field(
        default=10, ge=1, description="Checkpoint interval",
        json_schema_extra=ui(
            label_zh="检查点间隔", label_en="Checkpoint Interval",
            desc_zh="每隔多少代保存一次检查点，用于断点续跑",
            desc_en="Save a checkpoint every N generations to enable resuming from interruptions",
        ),
    )
    max_checkpoints: int = Field(
        default=5, ge=1, description="Maximum checkpoints to keep",
        json_schema_extra=ui(
            label_zh="最大检查点数", label_en="Max Checkpoints",
            desc_zh="磁盘上保留的最大检查点文件数量，超出后自动删除最旧的",
            desc_en="Maximum number of checkpoint files kept on disk; oldest are deleted when exceeded",
        ),
    )

    # Concurrency
    max_llm_concurrency: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Maximum number of concurrent individual generation pipelines "
            "(plan + code + evaluate). Defaults to None (no limit)."
        ),
        json_schema_extra=ui(
            label_zh="最大 LLM 并发数", label_en="Max LLM Concurrency",
            desc_zh="同时运行的最大个体生成流水线数（规划+编码+评估），为空则不限制",
            desc_en="Maximum concurrent individual generation pipelines (plan + code + evaluate); None means unlimited",
        ),
    )

    # Display
    human_readable_timing: bool = Field(
        default=True,
        description="Display timing as 'Xh Xmin Xs Xms' instead of raw milliseconds",
        json_schema_extra=ui(
            label_zh="可读时间格式", label_en="Human Readable Timing",
            desc_zh="以人类可读格式（如 1h 2min 3s）显示耗时，而非原始毫秒数",
            desc_en="Display elapsed time in human-readable format (e.g. 1h 2min 3s) instead of raw milliseconds",
            hidden=True,
        ),
    )

    def to_table(self) -> Table:
        """Create a rich Table representation of the evolution config.

        Returns:
            Rich Table with evolution configuration
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="bold white")
        table.add_column("value", style="yellow")

        table.add_row("Evolution Type:", self.type)
        table.add_row("Generations:", str(self.max_generations))
        table.add_row("Population Size:", str(self.population_size))
        table.add_row("Mutation Rate:", str(self.mutation_rate))
        table.add_row("Crossover Rate:", str(self.crossover_rate))
        table.add_row("Survival Strategy:", self.survival_selection_strategy)
        table.add_row("Early Stop Patience:", str(self.early_stop_patience))

        return table


class MigrationStrategy(Enum):
    """Strategy for selecting migrants between islands."""

    BEST = "best"  # Migrate best individuals
    RANDOM = "random"  # Migrate random individuals
    ELITE = "elite"  # Migrate top N% individuals
    WORST = "worst"  # Migrate worst individuals (for replacement)


class MigrationTopology(Enum):
    """Topology for migration between islands."""

    RING = "ring"  # Each island sends to next island in ring
    FULLY_CONNECTED = "full"  # All islands can send to each other
    HIERARCHICAL = "hierarchy"  # Islands arranged in tree structure
    MESH = "mesh"  # Custom neighbor connections

class IslandGAConfig(EvolutionConfig):
    """Configuration for Island Genetic Algorithm.

    Extends base EvolutionConfig with IGA-specific parameters.
    """

    type: Literal["island_ga"] = Field(
        default="island_ga",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="岛屿遗传算法，通过多个独立种群并行进化并定期迁移个体来增强搜索能力",
            desc_en="Island GA; independent populations evolve in parallel with periodic migration",
            hidden=True,
        ),
    )  # type: ignore[assignment]

    # Island settings
    num_islands: int = Field(
        default=5,
        json_schema_extra=ui(
            order=1,
            label_zh="岛屿数量",
            label_en="Number of Islands",
            desc_zh="并行进化的独立岛屿数量，每个岛屿维护独立种群",
            desc_en="Number of independent islands evolving in parallel, each maintaining its own population",
        ),
    )
    island_population_size: int = Field(
        default=20,
        json_schema_extra=ui(
            order=2,
            label_zh="每岛种群大小",
            label_en="Island Population Size",
            desc_zh="每个岛屿上的个体数量，总种群大小 = 岛屿数 × 每岛种群大小",
            desc_en="Number of individuals on each island; total population = num_islands x island_population_size",
        ),
    )

    # Migration settings
    migration_interval: int = Field(
        default=5,
        json_schema_extra=ui(
            label_zh="迁移间隔（代）", label_en="Migration Interval",
            desc_zh="每隔多少代执行一次岛屿间个体迁移",
            desc_en="Number of generations between inter-island migration events",
        ),
    )
    migration_rate: float = Field(
        default=0.1,
        json_schema_extra=ui(
            label_zh="迁移比例", label_en="Migration Rate",
            desc_zh="每次迁移时从源岛屿迁出的个体比例",
            desc_en="Fraction of individuals migrated from the source island during each migration event",
        ),
    )
    migration_strategy: MigrationStrategy = Field(
        default=MigrationStrategy.BEST,
        json_schema_extra=ui(
            label_zh="迁移策略", label_en="Migration Strategy",
            desc_zh="选择迁移个体的策略：最优、随机、精英或最差个体",
            desc_en="Strategy for selecting migrants: best, random, elite, or worst individuals",
        ),
    )
    migration_topology: MigrationTopology = Field(
        default=MigrationTopology.RING,
        json_schema_extra=ui(
            label_zh="迁移拓扑", label_en="Migration Topology",
            desc_zh="岛屿间迁移的连接拓扑结构：环形、全连接、层次或网格",
            desc_en="Connection topology for inter-island migration: ring, fully connected, hierarchical, or mesh",
        ),
    )

    # Optional: Allow per-island evolution parameters
    per_island_config: dict[int, dict[str, Any]] | None = Field(
        default=None,
        json_schema_extra=ui(
            label_zh="每岛独立配置", label_en="Per-Island Config",
            desc_zh="为特定岛屿覆盖默认进化参数，键为岛屿索引，值为参数字典",
            desc_en="Override evolution parameters for specific islands; keys are island indices",
            hidden=True,
        ),
    )

    # Parallel execution
    parallel_islands: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="并行进化岛屿", label_en="Parallel Islands",
            desc_zh="是否并行执行各岛屿的进化过程，关闭则按顺序依次执行",
            desc_en="Whether to evolve islands in parallel; when disabled, islands are processed sequentially",
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_table(self) -> Table:
        """Create a rich Table representation of the island GA config.

        Returns:
            Rich Table with island GA configuration
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="bold white")
        table.add_column("value", style="yellow")

        table.add_row("Evolution Type:", self.type)
        table.add_row("Generations:", str(self.max_generations))
        table.add_row("Num Islands:", str(self.num_islands))
        table.add_row("Island Population:", str(self.island_population_size))
        table.add_row("Migration Interval:", str(self.migration_interval))
        table.add_row("Migration Rate:", str(self.migration_rate))
        table.add_row("Migration Strategy:", self.migration_strategy.value)
        table.add_row("Migration Topology:", self.migration_topology.value)
        table.add_row("Parallel Islands:", str(self.parallel_islands))

        return table

class MEoHConfig(EvolutionConfig):
    """Configuration for the MEoH (Multi-objective Evolution of Heuristics) orchestrator.

    Extends base EvolutionConfig with MEoH-specific parameters for
    multi-objective population management, operator selection, and
    survival-based evolution.
    """

    type: Literal["meoh"] = Field(
        default="meoh",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="多目标启发式进化算法，支持多目标种群管理和算子选择",
            desc_en="Multi-objective Evolution of Heuristics with population management and operator selection",
            hidden=True,
        ),
    )  # type: ignore[assignment]

    planner_type: str = Field(
        default="meoh_evolution",
        json_schema_extra=ui(
            label_zh="规划器类型", label_en="Planner Type",
            desc_zh="MEoH 使用的规划器类型，通常无需修改",
            desc_en="Planner type used by MEoH; typically does not need to be changed",
            hidden=True,
        ),
    )
    pop_size: int = Field(
        default=8,
        json_schema_extra=ui(
            label_zh="种群大小", label_en="Population Size",
            desc_zh="MEoH 种群中维护的个体数量，影响多目标搜索的覆盖范围",
            desc_en="Number of individuals maintained in the MEoH population; affects coverage of multi-objective search",
        ),
    )
    selection_num: int = Field(
        default=2,
        json_schema_extra=ui(
            label_zh="选择数量", label_en="Selection Number",
            desc_zh="每次进化操作中从种群选择的父代个体数量",
            desc_en="Number of parent individuals selected from the population for each evolution operation",
        ),
    )
    max_sample_nums: int = Field(
        default=100,
        json_schema_extra=ui(
            label_zh="最大采样数", label_en="Max Sample Numbers",
            desc_zh="整个进化过程中允许的最大 LLM 采样次数",
            desc_en="Maximum number of LLM sampling calls allowed across the entire evolution run",
        ),
    )
    num_samplers: int = Field(
        default=1,
        json_schema_extra=ui(
            label_zh="采样器数量", label_en="Number of Samplers",
            desc_zh="并行运行的采样器数量，增加可提高吞吐量但消耗更多资源",
            desc_en="Number of samplers running in parallel; more samplers increase throughput but consume more resources",
        ),
    )
    objective_metrics: list[str] = Field(
        default_factory=list,
        json_schema_extra=ui(
            label_zh="目标指标", label_en="Objective Metrics",
            desc_zh="多目标优化中需要追踪的指标名称列表，如准确率、运行时间等",
            desc_en="List of metric names to track in multi-objective optimization, e.g. accuracy, runtime",
        ),
    )
    use_e2_operator: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="使用 E2 算子", label_en="Use E2 Operator",
            desc_zh="是否启用 E2（交叉进化）算子，用于组合两个父代的优势",
            desc_en="Whether to enable the E2 (crossover evolution) operator for combining strengths of two parents",
        ),
    )
    use_m1_operator: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="使用 M1 算子", label_en="Use M1 Operator",
            desc_zh="是否启用 M1（短程变异）算子，对个体进行小幅局部修改",
            desc_en="Whether to enable the M1 (short-range mutation) operator for small local modifications",
        ),
    )
    use_m2_operator: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="使用 M2 算子", label_en="Use M2 Operator",
            desc_zh="是否启用 M2（长程变异）算子，对个体进行较大幅度的探索性修改",
            desc_en="Whether to enable the M2 (long-range mutation) operator for larger exploratory modifications",
        ),
    )
    seed_path: str | None = Field(
        default=None,
        json_schema_extra=ui(
            label_zh="种子路径", label_en="Seed Path",
            desc_zh="初始种子算法文件的路径，用于热启动进化过程",
            desc_en="Path to an initial seed algorithm file for warm-starting the evolution process",
        ),
    )
    generation_mode: Literal["survival"] = Field(
        default="survival",
        json_schema_extra=ui(
            label_zh="生成模式", label_en="Generation Mode",
            desc_zh="个体生成模式，survival 表示基于存活机制的生成策略",
            desc_en="Individual generation mode; survival means generation based on survival-based strategy",
            hidden=True,
        ),
    )
    code_generation_mode: Literal["reuse_coder", "direct_code"] = Field(
        default="reuse_coder",
        json_schema_extra=ui(
            label_zh="代码生成模式", label_en="Code Generation Mode",
            desc_zh="代码生成方式：reuse_coder 复用已有编码器，direct_code 由 LLM 直接生成完整代码",
            desc_en="reuse_coder reuses the existing coder; direct_code has LLM generate code directly",
        ),
    )
    batch_per_operator: bool = Field(
        default=False,
        json_schema_extra=ui(
            label_zh="按算子批量生成", label_en="Batch Per Operator",
            desc_zh="是否每步只使用一个算子循环生成所有个体，而非每步使用所有算子",
            desc_en="Whether to cycle through operators one at a time per step instead of using all operators each step",
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_table(self) -> Table:
        """Create a rich Table representation of the MEoH config.

        Returns:
            Rich Table with MEoH configuration
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="bold white")
        table.add_column("value", style="yellow")

        table.add_row("Evolution Type:", self.type)
        table.add_row("Generations:", str(self.max_generations))
        table.add_row("Population Size:", str(self.pop_size))
        table.add_row("Selection Num:", str(self.selection_num))
        table.add_row("Max Samples:", str(self.max_sample_nums))
        table.add_row("Objective Metrics:", ", ".join(self.objective_metrics) or "none")
        table.add_row("E2 Operator:", str(self.use_e2_operator))
        table.add_row("M1 Operator:", str(self.use_m1_operator))
        table.add_row("M2 Operator:", str(self.use_m2_operator))
        table.add_row("Code Gen Mode:", self.code_generation_mode)

        return table


class DyCAConfig(EvolutionConfig):
    """Configuration for DyCA orchestrator.

    Extends base EvolutionConfig with DyCA-specific parameters for
    clustering, resource allocation, and pool management.
    """
    type: Literal["dyca"] = Field(
        default="dyca",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="动态聚类自适应进化算法，通过实例聚类和资源分配优化搜索效率",
            desc_en="Dynamic Clustering Adaptive evolution; optimizes search via instance clustering and resource allocation",
            hidden=True,
        ),
    )  # type: ignore[assignment]

    # Clustering settings
    n_clusters: int = Field(
        default=3, ge=2, description="Number of instance clusters",
        json_schema_extra=ui(
            label_zh="聚类数量", label_en="Number of Clusters",
            desc_zh="将问题实例划分为多少个聚类，每个聚类对应一组专家算法",
            desc_en="Number of clusters to partition problem instances into; each cluster gets its own specialist algorithms",
        ),
    )
    clustering_method: str = Field(
        default="kmeans",
        description="Clustering algorithm ('kmeans' or 'agglomerative')",
        json_schema_extra=ui(
            label_zh="聚类方法", label_en="Clustering Method",
            desc_zh="实例聚类使用的算法，支持 kmeans 和 agglomerative（层次聚类）",
            desc_en="Algorithm used for instance clustering; supports kmeans and agglomerative (hierarchical clustering)",
        ),
    )
    recluster_interval: int = Field(
        default=5, ge=1, description="Generations between reclustering checks",
        json_schema_extra=ui(
            label_zh="重聚类间隔", label_en="Recluster Interval",
            desc_zh="每隔多少代重新检查并更新聚类划分",
            desc_en="Number of generations between reclustering checks to update cluster assignments",
        ),
    )
    ari_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="ARI threshold for cluster stability",
        json_schema_extra=ui(
            label_zh="ARI 稳定性阈值", label_en="ARI Threshold",
            desc_zh="调整兰德指数阈值，高于此值认为聚类已稳定，无需重新聚类",
            desc_en="Adjusted Rand Index threshold; clusters above this value are considered stable and skip reclustering",
        ),
    )
    n_anchors: int = Field(
        default=5, ge=1, description="Number of anchor algorithms for clustering",
        json_schema_extra=ui(
            label_zh="锚点算法数量", label_en="Number of Anchors",
            desc_zh="用于构建实例特征向量的锚点算法数量，影响聚类质量",
            desc_en="Number of anchor algorithms used to build instance feature vectors; affects clustering quality",
        ),
    )

    # Pool settings
    generalist_pool_size: int = Field(
        default=10, ge=1, description="Maximum generalist pool size",
        json_schema_extra=ui(
            label_zh="通用池大小", label_en="Generalist Pool Size",
            desc_zh="通用算法池的最大容量，存放在所有聚类上表现均衡的算法",
            desc_en="Maximum capacity of the generalist pool, which stores algorithms that perform well across all clusters",
        ),
    )
    specialist_pool_size: int = Field(
        default=10, ge=1, description="Maximum specialist pool size per cluster",
        json_schema_extra=ui(
            label_zh="专家池大小", label_en="Specialist Pool Size",
            desc_zh="每个聚类的专家算法池最大容量，存放针对特定聚类优化的算法",
            desc_en="Maximum capacity of the specialist pool per cluster, storing algorithms optimized for that specific cluster",
        ),
    )
    complementary_pool_size: int = Field(
        default=10, ge=1, description="Maximum complementary pool size",
        json_schema_extra=ui(
            label_zh="互补池大小", label_en="Complementary Pool Size",
            desc_zh="互补算法池的最大容量，存放能与其他算法互补协作的算法",
            desc_en="Max capacity of the complementary pool for algorithms that complement others",
        ),
    )
    elite_archive_size: int = Field(
        default=5, ge=1, description="Elite archive size per cluster",
        json_schema_extra=ui(
            label_zh="精英存档大小", label_en="Elite Archive Size",
            desc_zh="每个聚类保留的精英算法数量，用于保存历史最优解",
            desc_en="Number of elite algorithms archived per cluster to preserve historically best solutions",
        ),
    )

    # SOS settings
    sos_stagnation_threshold: int = Field(
        default=3, ge=1, description="Generations without improvement to trigger SOS",
        json_schema_extra=ui(
            label_zh="SOS 停滞阈值", label_en="SOS Stagnation Threshold",
            desc_zh="连续多少代无改进后触发 SOS（紧急搜索）机制以跳出局部最优",
            desc_en="Generations without improvement before triggering SOS to escape local optima",
        ),
    )

    # Resource allocation
    base_complementary_ratio: float = Field(
        default=0.2, ge=0.0, le=1.0,
        description="Base fraction of resources for complementary pool",
        json_schema_extra=ui(
            label_zh="互补资源基础比例", label_en="Base Complementary Ratio",
            desc_zh="分配给互补算法池的基础资源比例，剩余资源分配给通用和专家池",
            desc_en="Base resource fraction for the complementary pool; rest goes to other pools",
        ),
    )

    # Offspring per generation
    offspring_per_generation: int = Field(
        default=5, ge=1, description="Number of new individuals per generation",
        json_schema_extra=ui(
            label_zh="每代新个体数", label_en="Offspring Per Generation",
            desc_zh="每代通过进化操作产生的新个体数量",
            desc_en="Number of new individuals produced by evolution operators per generation",
        ),
    )

    # Using mode
    using_mode: bool = Field(
        default=False, description="If True, freeze clustering and only run specialist evolution",
        json_schema_extra=ui(
            label_zh="使用模式", label_en="Using Mode",
            desc_zh="启用后冻结聚类划分，仅运行专家进化，适用于聚类已稳定的场景",
            desc_en="Freezes cluster assignments and only runs specialist evolution",
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class EoHConfig(EvolutionConfig):
    """Configuration for the EoH (Evolution of Heuristics) orchestrator.

    Single-objective sibling of MEoH. Reuses the ``meoh_evolution`` planner
    and the ``meoh_*`` operator samplers, but drives them with a
    single-objective, generational loop: each generation runs the E1 (and
    optionally E2/M1/M2) operators sequentially, offspring are scored on a
    single fitness value, and survival is rank-based top-k truncation.

    Reference:
        Fei Liu et al. "Evolution of Heuristics: Towards Efficient Automatic
        Algorithm Design Using Large Language Model." ICML 2024.
    """

    type: Literal["eoh"] = Field(
        default="eoh",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="启发式进化算法（单目标），使用 I1/E1/E2/M1/M2 算子逐代进化",
            desc_en="Evolution of Heuristics (single-objective) using I1/E1/E2/M1/M2 operators",
            hidden=True,
        ),
    )  # type: ignore[assignment]

    planner_type: str = Field(
        default="eoh_evolution",
        json_schema_extra=ui(
            label_zh="规划器类型", label_en="Planner Type",
            desc_zh="EoH 专用规划器，负责算子分发，通常无需修改",
            desc_en="EoH's dedicated planner for operator dispatch; typically unchanged",
            hidden=True,
        ),
    )
    pop_size: int = Field(
        default=5,
        ge=2,
        json_schema_extra=ui(
            label_zh="种群大小", label_en="Population Size",
            desc_zh="EoH 种群维护的个体数量",
            desc_en="Number of individuals maintained in the EoH population",
        ),
    )
    selection_num: int = Field(
        default=2,
        ge=1,
        json_schema_extra=ui(
            label_zh="选择数量", label_en="Selection Number",
            desc_zh="E1/E2 交叉算子每次从种群选择的父代个体数量",
            desc_en="Number of parents selected for the E1/E2 crossover operators",
        ),
    )
    max_sample_nums: int = Field(
        default=100,
        ge=1,
        json_schema_extra=ui(
            label_zh="最大采样数", label_en="Max Sample Numbers",
            desc_zh="整个进化过程中允许的最大 LLM 采样次数",
            desc_en="Maximum number of LLM sampling calls allowed across the run",
        ),
    )
    num_samplers: int = Field(
        default=1,
        ge=1,
        json_schema_extra=ui(
            label_zh="采样器数量", label_en="Number of Samplers",
            desc_zh="每个算子每代并行生成的候选数量",
            desc_en="Number of candidates generated in parallel per operator per generation",
        ),
    )
    use_e2_operator: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="使用 E2 算子", label_en="Use E2 Operator",
            desc_zh="是否启用 E2（受共同骨架启发的交叉）算子",
            desc_en="Whether to enable the E2 (backbone-motivated crossover) operator",
        ),
    )
    use_m1_operator: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="使用 M1 算子", label_en="Use M1 Operator",
            desc_zh="是否启用 M1（结构变异）算子",
            desc_en="Whether to enable the M1 (structural mutation) operator",
        ),
    )
    use_m2_operator: bool = Field(
        default=True,
        json_schema_extra=ui(
            label_zh="使用 M2 算子", label_en="Use M2 Operator",
            desc_zh="是否启用 M2（参数变异）算子",
            desc_en="Whether to enable the M2 (parameter mutation) operator",
        ),
    )
    seed_path: str | None = Field(
        default=None,
        json_schema_extra=ui(
            is_path=True,
            label_zh="种子路径", label_en="Seed Path",
            desc_zh="初始种子算法文件的路径，用于热启动进化过程",
            desc_en="Path to an initial seed algorithm file for warm-starting evolution",
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def to_table(self) -> Table:
        """Create a rich Table representation of the EoH config.

        Returns:
            Rich Table with EoH configuration.
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="bold white")
        table.add_column("value", style="yellow")

        table.add_row("Evolution Type:", self.type)
        table.add_row("Generations:", str(self.max_generations))
        table.add_row("Population Size:", str(self.pop_size))
        table.add_row("Selection Num:", str(self.selection_num))
        table.add_row("Max Samples:", str(self.max_sample_nums))
        table.add_row("E2 Operator:", str(self.use_e2_operator))
        table.add_row("M1 Operator:", str(self.use_m1_operator))
        table.add_row("M2 Operator:", str(self.use_m2_operator))

        return table


class ReEvoConfig(EvolutionConfig):
    """Configuration for the ReEvo (Reflective Evolution) orchestrator.

    ReEvo augments genetic search with two reflection signals:
    a short-term reflection (comparing a worse/better parent pair) that
    guides crossover, and a long-term reflection (accumulated across the
    run) that guides elite mutation.

    Reference:
        Ye et al. "ReEvo: Large Language Models as Hyper-Heuristics with
        Reflective Evolution." NeurIPS 2024.
    """

    type: Literal["reevo"] = Field(
        default="reevo",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="反思式进化：短期反思指导交叉，长期反思指导精英变异",
            desc_en="Reflective Evolution: short-term reflection guides crossover, long-term guides elite mutation",
            hidden=True,
        ),
    )  # type: ignore[assignment]

    planner_type: str = Field(
        default="reevo_evolution",
        json_schema_extra=ui(
            label_zh="规划器类型", label_en="Planner Type",
            desc_zh="ReEvo 专用规划器，通常无需修改",
            desc_en="ReEvo-specific planner; typically unchanged",
            hidden=True,
        ),
    )
    pop_size: int = Field(
        default=8,
        ge=2,
        json_schema_extra=ui(
            label_zh="种群大小", label_en="Population Size",
            desc_zh="ReEvo 种群维护的个体数量",
            desc_en="Number of individuals maintained in the ReEvo population",
        ),
    )
    mutation_rate: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        json_schema_extra=ui(
            label_zh="变异率", label_en="Mutation Rate",
            desc_zh="每代精英变异次数占种群大小的比例",
            desc_en="Fraction of pop_size used for elite mutations each generation",
        ),
    )
    max_sample_nums: int = Field(
        default=100,
        ge=1,
        json_schema_extra=ui(
            label_zh="最大采样数", label_en="Max Sample Numbers",
            desc_zh="整个进化过程中允许的最大 LLM 采样次数",
            desc_en="Maximum number of LLM sampling calls allowed across the run",
        ),
    )
    num_samplers: int = Field(
        default=1,
        ge=1,
        json_schema_extra=ui(
            label_zh="采样器数量", label_en="Number of Samplers",
            desc_zh="每步并行生成的交叉候选数量",
            desc_en="Number of crossover candidates generated in parallel per step",
        ),
    )
    seed_path: str | None = Field(
        default=None,
        json_schema_extra=ui(
            is_path=True,
            label_zh="种子路径", label_en="Seed Path",
            desc_zh="初始种子算法文件的路径，用于热启动进化过程",
            desc_en="Path to an initial seed algorithm file for warm-starting evolution",
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def to_table(self) -> Table:
        """Create a rich Table representation of the ReEvo config.

        Returns:
            Rich Table with ReEvo configuration.
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="bold white")
        table.add_column("value", style="yellow")

        table.add_row("Evolution Type:", self.type)
        table.add_row("Generations:", str(self.max_generations))
        table.add_row("Population Size:", str(self.pop_size))
        table.add_row("Mutation Rate:", str(self.mutation_rate))
        table.add_row("Max Samples:", str(self.max_sample_nums))

        return table


class MCTSAHDConfig(EvolutionConfig):
    """Configuration for the MCTS-AHD orchestrator.

    Monte Carlo Tree Search for Automatic Heuristic Design. Builds a search
    tree over algorithms, selecting nodes via UCT and expanding them with
    the e1/e2/m1/m2/s1 operators. Not population-generational; ``max_generations``
    is interpreted as the number of MCTS iterations.

    Reference:
        Zheng et al. "Monte Carlo Tree Search for Comprehensive Exploration
        in LLM-based Automatic Heuristic Design." ICML 2025.
    """

    type: Literal["mcts_ahd"] = Field(
        default="mcts_ahd",
        json_schema_extra=ui(
            label_zh="进化类型", label_en="Evolution Type",
            desc_zh="基于蒙特卡洛树搜索的算法设计，通过 UCT 选择与算子扩展探索算法空间",
            desc_en="MCTS for Automatic Heuristic Design; explores via UCT selection and operator expansion",
            hidden=True,
        ),
    )  # type: ignore[assignment]

    planner_type: str = Field(
        default="mcts_ahd_evolution",
        json_schema_extra=ui(
            label_zh="规划器类型", label_en="Planner Type",
            desc_zh="MCTS-AHD 专用规划器，通常无需修改",
            desc_en="MCTS-AHD-specific planner; typically unchanged",
            hidden=True,
        ),
    )
    init_size: int = Field(
        default=4,
        ge=1,
        json_schema_extra=ui(
            label_zh="初始节点数", label_en="Init Size",
            desc_zh="根节点下初始展开的子节点（算法）数量",
            desc_en="Number of initial child nodes (algorithms) expanded under the root",
        ),
    )
    pop_size: int = Field(
        default=10,
        ge=2,
        json_schema_extra=ui(
            label_zh="种群大小", label_en="Population Size",
            desc_zh="MCTS 维护的活跃算法池大小",
            desc_en="Size of the active algorithm pool maintained by MCTS",
        ),
    )
    selection_num: int = Field(
        default=2,
        ge=1,
        json_schema_extra=ui(
            label_zh="选择数量", label_en="Selection Number",
            desc_zh="e1/e2 算子每次使用的父代个体数量",
            desc_en="Number of parents used by the e1/e2 operators",
        ),
    )
    max_sample_nums: int = Field(
        default=100,
        ge=1,
        json_schema_extra=ui(
            label_zh="最大采样数", label_en="Max Sample Numbers",
            desc_zh="整个搜索过程中允许的最大 LLM 采样次数",
            desc_en="Maximum number of LLM sampling calls allowed across the search",
        ),
    )
    alpha: float = Field(
        default=0.5,
        ge=0.0,
        json_schema_extra=ui(
            label_zh="UCT alpha", label_en="UCT alpha",
            desc_zh="UCT 渐进加宽参数，控制节点扩展的宽度",
            desc_en="UCT progressive-widening parameter controlling expansion breadth",
        ),
    )
    lambda_0: float = Field(
        default=0.1,
        ge=0.0,
        json_schema_extra=ui(
            label_zh="UCT lambda_0", label_en="UCT lambda_0",
            desc_zh="UCT 探索常数基值，随剩余预算衰减",
            desc_en="UCT exploration constant base, decayed by remaining budget",
        ),
    )
    max_depth: int = Field(
        default=10,
        ge=1,
        json_schema_extra=ui(
            label_zh="最大树深", label_en="Max Tree Depth",
            desc_zh="MCTS 树的最大深度",
            desc_en="Maximum depth of the MCTS tree",
        ),
    )
    seed_path: str | None = Field(
        default=None,
        json_schema_extra=ui(
            is_path=True,
            label_zh="种子路径", label_en="Seed Path",
            desc_zh="初始种子算法文件的路径，用于热启动搜索",
            desc_en="Path to an initial seed algorithm file for warm-starting the search",
        ),
    )

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def to_table(self) -> Table:
        """Create a rich Table representation of the MCTS-AHD config.

        Returns:
            Rich Table with MCTS-AHD configuration.
        """
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="bold white")
        table.add_column("value", style="yellow")

        table.add_row("Evolution Type:", self.type)
        table.add_row("MCTS Iterations:", str(self.max_generations))
        table.add_row("Init Size:", str(self.init_size))
        table.add_row("Pool Size:", str(self.pop_size))
        table.add_row("Max Samples:", str(self.max_sample_nums))
        table.add_row("UCT alpha/lambda0:", f"{self.alpha}/{self.lambda_0}")

        return table
