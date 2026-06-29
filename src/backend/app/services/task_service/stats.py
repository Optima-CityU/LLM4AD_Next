"""任务统计、配置 schema 与结果渲染。

提供解的数量/分数统计、前端表单所需的配置 JSON Schema，以及结果可视化数据
的生成与缓存。
"""

import uuid
from typing import TYPE_CHECKING

from llm4ad.config import AppConfig
from loguru import logger
from sqlmodel import Session, select

from app import models
from app.core.config import settings
from app.schemas import task as schemas

from .auth import get_task_with_auth

if TYPE_CHECKING:
    from app.schemas.result_render import (
        ResultRenderGenerateRequest,
        ResultRenderGenerateResponse,
    )


def get_task_stats(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> schemas.TaskStatsResponse:
    """获取任务的基本统计信息：解的个数、平均分、最高分。

    解从 ``type=generated`` 的日志中提取，分数读取自 ``data.evaluation.score``。
    优先从数据库读取已持久化日志，运行中任务回退到 Redis。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。

    Returns:
        包含解的个数、平均分、最高分的统计响应。
    """
    task = get_task_with_auth(db, task_id, current_user)

    from app.models import TaskLog
    from app.utils.log_persist import task_log_to_dict

    rows = db.exec(select(TaskLog).where(TaskLog.task_id == task.id, TaskLog.type == "generated")).all()
    if rows:
        entries = [task_log_to_dict(row) for row in rows]
    else:
        from app.core.redis import read_all_logs

        entries = [e for e in read_all_logs(task.id) if e.get("type") == "generated"]

    scores: list[float] = []
    for entry in entries:
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        evaluation = data.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        score = evaluation.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.append(float(score))

    solution_count = len(entries)
    avg_score = sum(scores) / len(scores) if scores else None
    max_score = max(scores) if scores else None

    return schemas.TaskStatsResponse(
        task_id=task.id,
        solution_count=solution_count,
        avg_score=avg_score,
        max_score=max_score,
        input_args=task.input_args or {},
        created_time=task.created_time,
        updated_time=task.updated_time,
    )


def get_config_schema(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
) -> schemas.AppConfigSchemaResponse:
    """获取配置的schema，用于前端生成表单

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户

    Returns:
        返回配置的schema

    """
    # TODO 可根据任务进行动态识别不同的schema
    get_task_with_auth(db, task_id, current_user)
    return schemas.AppConfigSchemaResponse(config_schema=AppConfig.model_json_schema())


def _ensure_trajectory_embeddings(
    db: Session,
    defaults,
    generated_dir: str,
    embedding_dir: str,
) -> None:
    """Backfill missing trajectory embeddings from saved generated algorithms."""
    import asyncio
    from pathlib import Path

    from llm4ad.config.app import EmbeddingConfig
    from llm4ad.orchestrator.embedding_client import EmbeddingClient
    from llm4ad.orchestrator.embedding_utils import save_algorithm_embeddings
    from llm4ad.planner.base import Algorithm

    from app.services.task_service.execution import _build_embedding_config

    embedding_config = _build_embedding_config(db, defaults)
    if not embedding_config:
        raise ValueError("未找到可用的 embedding 配置")

    generated_path = Path(generated_dir)
    embedding_path = Path(embedding_dir)
    if not generated_path.exists():
        return
    embedding_path.mkdir(parents=True, exist_ok=True)

    missing_algorithms: list[Algorithm] = []
    for json_path in sorted(generated_path.glob("*.json")):
        try:
            algorithm = Algorithm.load(json_path)
        except Exception:
            logger.debug(f"跳过无法还原的 generated JSON: {json_path}")
            continue
        if list(embedding_path.glob(f"*_{algorithm.id}.json")):
            continue
        missing_algorithms.append(algorithm)

    if not missing_algorithms:
        return

    async def _backfill() -> None:
        client = EmbeddingClient(EmbeddingConfig.model_validate(embedding_config))
        try:
            for algorithm in missing_algorithms:
                await save_algorithm_embeddings(client, algorithm, embedding_path)
        finally:
            await client.shutdown()

    asyncio.run(_backfill())


def generate_result_render(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    request: "ResultRenderGenerateRequest",
) -> "ResultRenderGenerateResponse":
    """生成任务结果渲染数据。

    若 task.result_render 中已存在该类型的已完成结果，直接返回缓存数据。
    否则调用生成逻辑并将结果持久化到 result_render 字段。

    Args:
        db: 数据库会话。
        task_id: 任务 ID。
        current_user: 当前登录用户。
        request: 结果渲染生成请求。

    Returns:
        ResultRenderGenerateResponse: 包含状态和结果数据的响应。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.schemas.result_render import (
        ResultRenderGenerateResponse,
        ResultRenderStatus,
    )

    task = get_task_with_auth(db, task_id, current_user)
    rt = request.result_type.value
    cache_key = f"{rt}_{request.language}_{request.theme}"

    if request.result_type.value == "trajectory":
        from app.services.user_default_model_service import get_user_default_model

        defaults = get_user_default_model(db, current_user.id)
        if not defaults.embedding_enabled:
            return ResultRenderGenerateResponse(
                task_id=task.id,
                result_type=request.result_type,
                status=ResultRenderStatus.FAILED,
                message="暂不支持轨迹分析，如需使用请配置 embedding 模型",
                error_code="embedding_disabled",
                data=None,
            )
        if not defaults.embedding_provider_id:
            return ResultRenderGenerateResponse(
                task_id=task.id,
                result_type=request.result_type,
                status=ResultRenderStatus.FAILED,
                message="暂不支持轨迹分析，如需使用请配置 embedding 模型",
                error_code="embedding_not_configured",
                data=None,
            )

    existing = (task.result_render or {}).get(cache_key)
    if not request.force and existing and existing.get("status") == ResultRenderStatus.COMPLETED:
        return ResultRenderGenerateResponse(
            task_id=task.id,
            result_type=request.result_type,
            status=ResultRenderStatus.COMPLETED,
            message="结果已存在，直接返回缓存",
            data=existing.get("data"),
        )

    try:
        from llm4ad.frontend.visualization import VisualizationAPI

        container_name = f"code_user-{current_user.id}"
        user_home = f"{settings.DOCKER_PROJECT_HOME}{container_name}/"
        run_dir = f"{user_home}{task_id}/"
        result_dir = f"{run_dir}llm4ad/run/"
        if request.result_type.value == "trajectory":
            _ensure_trajectory_embeddings(
                db,
                defaults,
                generated_dir=f"{result_dir}generated",
                embedding_dir=f"{result_dir}embedding",
            )
        data = VisualizationAPI(
            generated_dir=f"{result_dir}generated", embedding_dir=f"{result_dir}embedding"
        ).generate_evaluation_trace_echarts_config(dark_mode=request.theme == "dark")
    except Exception as e:
        logger.error(f"生成结果渲染失败: task_id={task_id}, type={rt}, error={e}")
        return ResultRenderGenerateResponse(
            task_id=task.id,
            result_type=request.result_type,
            status=ResultRenderStatus.FAILED,
            message=f"生成失败: {e}",
            data=None,
        )

    render_dict = task.result_render or {}
    render_dict[cache_key] = {
        "status": ResultRenderStatus.COMPLETED,
        "data": data,
    }
    task.result_render = render_dict
    flag_modified(task, "result_render")
    db.add(task)
    db.commit()
    db.refresh(task)

    return ResultRenderGenerateResponse(
        task_id=task.id,
        result_type=request.result_type,
        status=ResultRenderStatus.COMPLETED,
        message="生成成功",
        data=data,
    )
