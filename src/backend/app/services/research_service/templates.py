"""ARC-Bench 课题模板子域：列模板 / 读详情 / 落 stage-07/08/09 产物。

数据与产物布局全部来自 ``researchclaw.arc_bench``（随 ``arc-templates`` extra 安装），
本模块只做薄封装，不复制任何 ARC 侧逻辑：

- 课题注册表（5 个 ``config/<域>/topics.yaml``）→ :func:`list_topics`；
- 课题 manifest（``<id>.yaml``）→ :func:`load_manifest`；
- stage-07/08/09 + checkpoint 产物 → :func:`materialize`，直接复用 ARC-Bench 自带的
  ``prepare_run.prepare``，因为那份产物布局（含 ``checkpoint.json`` 的
  ``last_completed_stage``）是 ARC pipeline 的隐式契约，必须与上游逐字一致。

**可选依赖**：``researchclaw`` 只装在 backend 镜像（``Dockerfile``）。未安装时本模块
所有入口返回空 / 抛 404，不阻断应用启动——前端据此隐藏「从模板创建」入口。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from loguru import logger

# ARC-Bench 课题 id 前缀 → 域目录名。语义与 prepare_run._PREFIX_MAP 一致（那边由
# researchclaw 提供，这里只用于 picker 过滤与展示分组）。
_DOMAIN_ORDER: tuple[str, ...] = (
    "ml",
    "physics",
    "biology",
    "statistics",
    "quantum",
)

_DOMAIN_LABELS: dict[str, str] = {
    "ml": "Machine Learning",
    "physics": "Physics",
    "biology": "Biology",
    "statistics": "Statistics",
    "quantum": "Quantum",
}

# prepare(topic_id, run_dir) 产出的签名文件。用于判断某会话是否已物化过模板产物：
# 该文件在 = 产物就绪（跳过），不在 = 需要补一次（profile 跨类切换清盘后、或首轮
# bootstrap 兜底）。纯磁盘判断，不落任何 session 字段。
_STAGE09_SENTINEL = "stage-09/exp_plan.yaml"
# 根目录的 manifest 快照（prepare 与 stage-07 下各写一份）。第 9 步产物被清后，它是
# 重建 stage-09 时反查课题 id 的凭据（题面从没被清过）。
_MANIFEST_SENTINEL = "topic_manifest.json"


# ---- researchclaw 惰性加载 ----


@functools.lru_cache(maxsize=1)
def _prepare_run() -> Any:
    """加载 ``researchclaw.arc_bench.scripts.prepare_run`` 模块（进程内缓存）。

    Returns:
        prepare_run 模块对象，含 ``prepare`` / ``load_manifest`` / ``_PREFIX_MAP``。

    Raises:
        HTTPException: 503，镜像未装 ``arc-templates`` extra 时。
    """
    try:
        from researchclaw.arc_bench.scripts import prepare_run  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="ARC-Bench topic templates unavailable: researchclaw not installed",
        ) from exc
    return prepare_run


def templates_available() -> bool:
    """模板功能是否可用（镜像装没装 ``arc-templates`` extra）。"""
    try:
        _prepare_run()
    except HTTPException:
        return False
    return True


# ---- 课题注册表 ----


@functools.lru_cache(maxsize=1)
def _load_registry() -> tuple[dict[str, Any], ...]:
    """聚合 5 个域 registry 的 topics，按域目录顺序去重。

    与 ``run_bench_init._load_topics`` 同语义，但不复用它：那边靠 ``sys.path`` 注入
    才能 import 同级脚本、且带着指向 git 仓库根的 ``REPO_ROOT`` / ``RESULTS_DIR``
    常量，在 API 进程里都是无意义的副作用。这里只读包内数据文件。

    Returns:
        每个 topic dict 额外带一个 ``domain`` 键（域目录名），供 picker 分组过滤。
    """
    import yaml

    module = _prepare_run()
    arc_bench_root = Path(module.ROOT)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for domain in _DOMAIN_ORDER:
        registry = arc_bench_root / "config" / domain / "topics.yaml"
        if not registry.is_file():
            continue
        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
        for topic in data.get("topics") or []:
            tid = topic.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append({**topic, "domain": domain})
    return tuple(out)


def list_topics(domain: str | None = None) -> list[dict[str, Any]]:
    """列出课题模板摘要。

    Args:
        domain: 可选域过滤（``ml`` / ``physics`` / ``biology`` / ``statistics`` /
            ``quantum``）；None 返回全部。

    Returns:
        摘要列表，每项含 ``id`` / ``title`` / ``topic`` / ``domains`` /
        ``metric_key`` / ``metric_direction`` / ``domain`` / ``domain_label``。
        未装 researchclaw 时返回空列表。
    """
    if not templates_available():
        return []
    items: list[dict[str, Any]] = []
    for topic in _load_registry():
        if domain and topic.get("domain") != domain:
            continue
        sub = topic.get("domain") or ""
        items.append(
            {
                "id": topic.get("id"),
                "title": _topic_title(topic),
                "topic": topic.get("topic") or "",
                "domains": topic.get("domains") or [],
                "metric_key": topic.get("metric_key") or "",
                "metric_direction": topic.get("metric_direction") or "",
                "domain": sub,
                "domain_label": _DOMAIN_LABELS.get(sub, sub),
            }
        )
    return items


def _topic_title(topic: dict[str, Any]) -> str:
    """课题展示名：优先 manifest 的 ``title``（干净、完整，最长 160 字符）。

    registry 的 ``topic`` 字段是完整题面（较长，picker 里当副标题展示），只作兜底。
    不按句号切分——``E. coli …`` / ``arXiv:hep-ph/9909255`` 这类缩写会被切出
    单字符标题。manifest 读不到时退回截断到 80 字符的题面。
    """
    tid = topic.get("id")
    if tid:
        try:
            manifest_title = str(load_manifest(str(tid)).get("title") or "").strip()
        except HTTPException:
            manifest_title = ""
        if manifest_title:
            return manifest_title[:160]
    text = (topic.get("topic") or "").strip()
    return text[:80] or str(tid or "")


# ---- 单模板详情 ----


def topic_exists(topic_id: str) -> bool:
    """课题 id 是否在注册表内。"""
    return any(t.get("id") == topic_id for t in _load_registry())


def load_manifest(topic_id: str) -> dict[str, Any]:
    """读取课题 manifest（完整题目 / synthesis / hypotheses / experiment_design）。

    Raises:
        HTTPException: 404，课题 id 不存在时。
    """
    if not topic_exists(topic_id):
        raise HTTPException(status_code=404, detail=f"topic not found: {topic_id}")
    return dict(_prepare_run().load_manifest(topic_id))


def get_template_detail(topic_id: str) -> dict[str, Any]:
    """模板详情：registry 摘要 + manifest 全文（供创建对话框做预览）。"""
    manifest = load_manifest(topic_id)
    summary = next(
        (t for t in list_topics() if t.get("id") == topic_id), None
    ) or {"id": topic_id, "domain": ""}
    return {
        **summary,
        "synthesis": manifest.get("synthesis") or "",
        "hypotheses": manifest.get("hypotheses") or [],
        "experiment_design": manifest.get("experiment_design") or {},
    }


# ---- 会话初始化数据 ----


def _synthesis_head(manifest: dict[str, Any], *, lines: int = 4, limit: int = 400) -> str:
    """synthesis 前 N 行拼成单行、截断——镜像 ``run_bench_init`` 的取法。"""
    synthesis = (manifest.get("synthesis") or "").strip().splitlines()
    return " ".join(synthesis[:lines])[:limit]


def derive_topic(manifest: dict[str, Any]) -> str:
    """由 manifest 派生会话 topic 文本。

    与 ``run_bench_init.materialize_config`` 写的 ``research.topic`` 同款公式
    （``config_builder.build_arc_config`` 会把它原样放进 ARC 的 ``research.topic``），
    只是把 ``ARC-Bench {id}`` 前缀去掉——那是 CLI 跑批的命名，对单会话无意义。
    """
    design = manifest.get("experiment_design") or {}
    question = design.get("research_question") or manifest.get("title") or ""
    return (
        f"{manifest.get('title', '')}. "
        f"Research question: {question}. "
        f"Context: {_synthesis_head(manifest)}"
    ).strip()


def resolve_domain(topic_id: str) -> str:
    """课题 id → 域目录名（复用 prepare_run 的前缀映射，不重复维护前缀表）。"""
    return _prepare_run()._resolve_topic_subdir(topic_id)  # noqa: SLF001


def build_domain_profile(topic_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """按域构造 ``stage-09/domain_profile.json`` 的内容。

    逐字镜像 ``prepare_run.write_exp_plan`` 的 domain 分支——该文件是 stage-10 代码
    生成消费的输入，字段名/取值必须与上游一致（ARC 升级需同步）。
    """
    domain = resolve_domain(topic_id)
    design = manifest.get("experiment_design") or {}
    if domain == "physics":
        return {
            "domain_id": "hep_ph",
            "display_name": "High Energy Physics Phenomenology (ARC-Bench)",
            "experiment_paradigm": "SIMULATION",
            "core_libraries": ["numpy", "scipy", "matplotlib"],
            "gpu_required": False,
        }
    if domain == "biology":
        return {
            "domain_id": "biology_metabolic",
            "display_name": "Constraint-Based Metabolic Modelling (ARC-Bench)",
            "experiment_paradigm": "SIMULATION",
            "core_libraries": ["cobra", "pandas", "numpy", "matplotlib", "escher"],
            "gpu_required": False,
        }
    if domain == "quantum":
        return {
            "domain_id": "quantum_ml",
            "display_name": "Quantum Machine Learning (ARC-Bench)",
            "experiment_paradigm": "BENCHMARK_EXPERIMENT",
            "core_libraries": [
                "numpy", "scipy", "sklearn", "matplotlib",
                "qiskit", "qiskit_aer", "qiskit_algorithms",
                "qiskit_machine_learning", "qiskit_nature",
            ],
            "gpu_required": False,
        }
    if domain == "statistics":
        return {
            "domain_id": "statistics_general",
            "display_name": "Statistical Methodology & Simulation (ARC-Bench)",
            "experiment_paradigm": "BENCHMARK_EXPERIMENT",
            "core_libraries": [
                "numpy", "scipy", "pandas", "sklearn", "statsmodels", "matplotlib",
            ],
            "gpu_required": False,
        }
    # ml 或未知前缀：与上游 docstring 一致，都当 ML 课题处理。
    return {
        "domain_id": "ml_general",
        "display_name": "Machine Learning (ARC-Bench)",
        "experiment_paradigm": "BENCHMARK_EXPERIMENT",
        "core_libraries": ["numpy", "scipy", "sklearn", "pandas"],
        "gpu_required": bool(
            (design.get("compute_requirements") or {}).get("gpu_required")
        ),
    }


def is_materialized(run_dir: Path) -> bool:
    """run_dir 内是否已有模板产物（见 :data:`_STAGE09_SENTINEL`）。"""
    return (run_dir / _STAGE09_SENTINEL).is_file()


def _topic_id_from_run_dir(run_dir: Path) -> str | None:
    """从 run_dir 里残留的 ``topic_manifest.json`` 反查课题 id。

    只用于「第 9 步产物被清、但题面还在」的补跑场景（见 :func:`rematerialize`）：
    manifest 的 ``id`` 字段与文件名不同（``ML01.yaml`` / ``id: T01``），故拿 ``id``
    与 ``title`` 去注册表反查，比解析文件名可靠。

    Returns:
        匹配到的课题 id；无 manifest / 字段缺失 / 反查不到时 None。
    """
    path = run_dir / _MANIFEST_SENTINEL
    if not path.is_file():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        logger.debug("read topic_manifest.json failed", exc_info=True)
        return None
    raw_id = data.get("id")
    title = data.get("title")
    if not raw_id and not title:
        return None
    for topic in _load_registry():
        if raw_id and topic.get("id") == raw_id:
            # 双保险：id 相同即认定同一课题（title 仅作 raw_id 缺失时的兜底）。
            return str(raw_id)
    if title:
        for topic in _load_registry():
            if topic.get("title") == title:
                return str(topic.get("id") or "") or None
    return None


def rematerialize(run_dir: Path) -> bool:
    """按 run_dir 里残留的题面补跑一次 :func:`materialize`。

    调用点在 :func:`app.tasks.research_runner.task._bootstrap`：``checkpoint.json``
    在、``stage-09/exp_plan.yaml`` 不在时触发（profile 跨类切换清过盘、或建会话时
    物化失败）。题面（``topic_manifest.json``）从不被清理，故 id 可反查成功。

    Returns:
        是否真的写了产物（无法反查课题 id 时返回 False）。
    """
    topic_id = _topic_id_from_run_dir(run_dir)
    if not topic_id:
        logger.warning(f"rematerialize skipped: no topic id in {run_dir}")
        return False
    return materialize(run_dir, topic_id, skip_if_ready=False)


def materialize(run_dir: Path, topic_id: str, *, skip_if_ready: bool = True) -> bool:
    """把课题模板落成 run_dir 下的 stage-07/08/09 + checkpoint 产物。

    幂等：``prepare_run.prepare`` 自身全部覆写，且 ``skip_if_ready=True`` 时已物化
    则直接短路。调用点：建会话时、profile 跨类切换清盘后、以及首轮 bootstrap 兜底。

    Args:
        run_dir: 宿主视角的会话产物目录。
        topic_id: 课题 id（如 ``ML01``）。
        skip_if_ready: 见到 :data:`_STAGE09_SENTINEL` 就跳过。补跑场景
            （:func:`rematerialize`）传 False——那里正是产物已缺、但想强制重写。

    Returns:
        本次是否真的写了产物（短路时返回 False）。**不抛异常**——产物写失败不应
        阻断建会话，调用方据返回值决定是否告警。
    """
    try:
        if skip_if_ready and is_materialized(run_dir):
            return False
        run_dir.mkdir(parents=True, exist_ok=True)
        _prepare_run().prepare(topic_id, run_dir)
        logger.info(f"materialized arc template {topic_id} into {run_dir}")
        return True
    except HTTPException:
        raise
    except Exception:
        logger.opt(exception=True).warning(
            f"materialize arc template failed topic={topic_id} run_dir={run_dir}"
        )
        return False
