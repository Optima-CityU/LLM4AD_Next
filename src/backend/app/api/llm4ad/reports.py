"""演化洞察报告路由。

提供报告生成的触发、查询接口，以及通过 SSE 推送生成进度的端点。
同时包含进化块分析建议（Advisor）的生成与查询端点。
所有端点均需要用户登录，并基于任务归属做权限校验。
"""

import json
import uuid

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from app.api.deps import CurrentUser, SessionDep, TokenDep
from app.schemas.advisor import (
    AdvisorDetailResponse,
    AdvisorGenerateRequest,
    AdvisorGenerateResponse,
)
from app.schemas.report import (
    ReportDetailResponse,
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportStopResponse,
    ReportTemplatesResponse,
    ReportType,
)
from app.services import advisor_service, report_service

router = APIRouter(prefix="/tasks", tags=["llm4ad.reports"])


@router.get(
    "/reports/report-templates",
    response_model=ReportTemplatesResponse,
    summary="Get default prompt templates for all report types",
)
def get_report_templates():
    """返回所有报告类型的默认 user prompt 模板及可用变量列表。

    模板中的变量以 ``{variable_name}`` 形式标记，前端可展示给用户编辑。
    生成报告时若传入自定义模板，后台会将变量替换为实际值后作为 user_prompt。

    Returns:
        ReportTemplatesResponse: 各报告类型的模板与变量信息。
    """
    from app.services.report_prompts import REPORT_TEMPLATES

    return ReportTemplatesResponse(templates=REPORT_TEMPLATES)


@router.post(
    "/{task_id}/reports/generate",
    response_model=ReportGenerateResponse,
    status_code=202,
    summary="Trigger evolution insight report generation",
)
async def generate_report(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    task_id: uuid.UUID,
    request: ReportGenerateRequest,
):
    """触发指定任务的报告后台生成。

    交由 ``report_service`` 提交后台任务，立即返回 202 表示已受理。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        token: 当前登录 token，用于替换内置供应商 URL 中的占位。
        task_id: 目标任务 ID。
        request: 报告生成请求体，包含报告类型等参数。

    Returns:
        ReportGenerateResponse: 报告任务受理结果。
    """
    return report_service.generate_report(db, task_id, current_user, request, token)


@router.get(
    "/{task_id}/reports/{report_type}",
    response_model=ReportDetailResponse,
    summary="Get a specific report",
)
def get_report(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    report_type: ReportType,
):
    """从数据库读取指定类型的报告内容。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        task_id: 目标任务 ID。
        report_type: 报告类型枚举。

    Returns:
        ReportDetailResponse: 报告详情，包括状态与内容。
    """
    return report_service.get_report(
        db, task_id, current_user, report_type.value
    )


@router.post(
    "/{task_id}/reports/{report_type}/stop",
    response_model=ReportStopResponse,
    summary="Stop report generation",
)
def stop_report(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    report_type: ReportType,
):
    """停止指定类型报告的生成过程。

    用于用户主动取消正在进行中的报告生成任务。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        task_id: 目标任务 ID。
        report_type: 报告类型枚举。

    Returns:
        ReportStopResponse: 停止操作结果。
    """
    return report_service.stop_report_generation(
        db, task_id, current_user, report_type.value
    )


def _report_entry_handler(_entry_id: str, fields: dict) -> tuple[str, bool] | None:
    """解析报告流条目为 SSE 帧。

    id 行由 :func:`redis_sse_stream` 统一拼接，handler 只产出 event/data。
    """
    entry = json.loads(fields["data"])
    sse_text = f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
    is_terminal = isinstance(entry, dict) and entry.get("type") in (
        "done",
        "error",
        "cancelled",
    )
    return sse_text, is_terminal


@router.get(
    "/{task_id}/reports/{report_type}/stream",
    summary="SSE stream for report generation progress",
)
async def stream_report(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
    report_type: ReportType,
):
    """SSE 端点：实时推送报告生成进度。

    若报告已完成，则一次性返回 done 事件并结束。否则订阅 Redis Stream，
    持续推送增量内容，并周期性发送心跳避免连接超时。

    事件类型:
        - connected: 连接建立。
        - data: 报告生成的增量内容或 done/error 标记。
        - heartbeat: 周期性心跳保活（约 15s）。
        - done: 报告生成结束（completed/error/cancelled）。
        - timeout: 超过 5 分钟无新数据的安全兜底。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户，用于权限校验。
        task_id: 目标任务 ID。
        report_type: 报告类型枚举。

    Returns:
        StreamingResponse: text/event-stream 格式的 SSE 响应。
    """
    task = report_service.get_task_with_auth(db, task_id, current_user)
    rt = report_type.value

    report_data = (task.reports or {}).get(rt)
    if report_data and report_data.get("status") == "completed":
        async def _done_stream():
            # 报告已完成，直接推送一次 done 事件并关闭流
            yield (
                f"event: done\n"
                f"data: {json.dumps({'status': 'completed', 'content': report_data.get('content', '')}, ensure_ascii=False)}\n\n"
            )
        return StreamingResponse(_done_stream(), media_type="text/event-stream")

    from app.api.llm4ad.sse_utils import redis_sse_stream, sse_response
    from app.core.redis import report_stream_key

    return sse_response(
        redis_sse_stream(
            redis_key=report_stream_key(task_id, rt),
            connected_data={"task_id": str(task_id), "report_type": rt},
            entry_handler=_report_entry_handler,
            max_idle=300.0,
            use_draining=True,
        )
    )


# ---- Advisor endpoints ----


@router.post(
    "/{task_id}/advisor/advise/generate",
    response_model=AdvisorGenerateResponse,
    status_code=202,
    summary="Trigger evolve-block advise generation",
)
async def generate_advise(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    task_id: uuid.UUID,
    request: AdvisorGenerateRequest,
):
    """触发进化块分析建议的后台生成。

    自动扫描任务数据中的 EVOLVE 标记，对标记的代码块进行分析。
    结果缓存在 task.reports["block_advise"] 中。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户。
        token: 当前登录 token，用于替换内置供应商 URL 中的占位。
        task_id: 目标任务 ID。
        request: 生成请求体。

    Returns:
        AdvisorGenerateResponse: 任务受理结果。
    """
    return advisor_service.generate_advise(db, task_id, current_user, request, token)


@router.get(
    "/{task_id}/advisor/advise",
    response_model=AdvisorDetailResponse,
    summary="Get cached block advise result",
)
def get_advise(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """获取缓存的进化块分析建议结果。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户。
        task_id: 目标任务 ID。

    Returns:
        AdvisorDetailResponse: 分析建议结果。
    """
    return advisor_service.get_advisor_result(
        db, task_id, current_user, advisor_service.ADVISOR_TYPE_ADVISE,
    )


@router.post(
    "/{task_id}/advisor/recommend/generate",
    response_model=AdvisorGenerateResponse,
    status_code=202,
    summary="Trigger evolve-block recommend generation",
)
async def generate_recommend(
    db: SessionDep,
    current_user: CurrentUser,
    token: TokenDep,
    task_id: uuid.UUID,
    request: AdvisorGenerateRequest,
):
    """触发进化块推荐的后台生成。

    扫描任务数据仓库，推荐适合演化的代码块候选。
    结果缓存在 task.reports["block_recommend"] 中。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户。
        token: 当前登录 token，用于替换内置供应商 URL 中的占位。
        task_id: 目标任务 ID。
        request: 生成请求体。

    Returns:
        AdvisorGenerateResponse: 任务受理结果。
    """
    return advisor_service.generate_recommend(db, task_id, current_user, request, token)


@router.get(
    "/{task_id}/advisor/recommend",
    response_model=AdvisorDetailResponse,
    summary="Get cached block recommend result",
)
def get_recommend(
    db: SessionDep,
    current_user: CurrentUser,
    task_id: uuid.UUID,
):
    """获取缓存的进化块推荐结果。

    Args:
        db: 数据库会话依赖。
        current_user: 当前登录用户。
        task_id: 目标任务 ID。

    Returns:
        AdvisorDetailResponse: 推荐结果。
    """
    return advisor_service.get_advisor_result(
        db, task_id, current_user, advisor_service.ADVISOR_TYPE_RECOMMEND,
    )

