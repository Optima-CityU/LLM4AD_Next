"""任务运行、停止与结果查询。

负责解析供应商配置、准备运行目录、提交 Celery 任务，以及中止运行中任务、
回收容器并同步 Celery 执行结果到数据库。
"""

import uuid
from datetime import UTC, datetime

from celery.result import AsyncResult
from fastapi import HTTPException, status
from llm4ad.config.app import EmbeddingConfig
from loguru import logger
from sqlalchemy import or_
from sqlmodel import Session, select

from app import models
from app.core.celery import celery_app
from app.core.config import settings
from app.core.constants import CELERY_STATUS_MAP
from app.models import TaskStatus
from app.schemas import task as schemas
from app.tasks.evolution import run_evolution

from .auth import get_task_with_auth


def _resolve_providers(
    db: Session,
    input_args: dict,
    current_user: models.User,
    access_token: str | None = None,
    task_id: uuid.UUID | str | None = None,
) -> dict:
    """解析供应商列表并处理 planner/coder 中的供应商引用，用于构建 run_args。

    1. 从当前用户的 LLMProvider 记录构建 ``input_args["providers"]``。
       内置供应商通过 LiteLLM 按当前用户/团队动态查询可用模型；非内置
       供应商使用数据库中以 ``;`` 分隔的模型列表。每个模型拆分为独立的
       ProviderConfig，name 格式为 ``{provider.id}{model}``。
    2. 解析 ``planner.provider`` / ``coder.provider`` / ``evaluator.provider``：
       - ``"default"`` 或空：从 UserDefaultModel 读取默认配置
         （evaluator 无专属槽位，回退到 ``other`` 默认模型）。
       - 其他值：将供应商 ID 与对应模型名拼接。
    3. 内置供应商的 ``base_url`` 中的 ``{accessToken}`` 占位会被替换为当前
       登录 token；其他占位（如 ``{teamId}``）保留原样由上游处理。
    4. 若启用 LLM 凭据代理（``settings.LLM_PROXY_ENABLE``）：把每个下发到容器的
       供应商真实 ``api_key``/``auth_token`` 替换为一次性代理 token，``base_url``
       改写为指向 backend 代理端点。真实凭据不再进入隔离容器，从根本上消除用户
       评测脚本窃取大模型 key 的路径（详见 :mod:`app.services.credential_broker`）。

    Args:
        db: 数据库会话。
        input_args: task.input_args 的可变副本。
        current_user: 当前认证用户。
        access_token: 当前登录 token，用于替换内置供应商 URL 中的占位。
        task_id: 业务任务 ID，用于把发放的代理 token 归集到该任务，便于结束时
            批量吊销；为空时不启用代理改写。

    Returns:
        处理后的 *input_args* 字典。
    """
    from app.models import LLMProvider
    from app.services.provider_service import fetch_builtin_provider_models, resolve_builtin_base_url
    from app.services.user_default_model_service import get_user_default_model

    input_args["providers"] = []
    providers = db.exec(
        select(LLMProvider).where(
            or_(
                LLMProvider.user_id == current_user.id,
                LLMProvider.is_builtin.is_(True) & LLMProvider.visible_to_all.is_(True),  # type: ignore[union-attr]
            ),
        ),
    ).all()

    provider_configs: list[dict] = []
    provider_configs_map: dict[str, dict] = {}
    for p in providers:
        base_url = p.base_url
        if p.is_builtin:
            base_url = resolve_builtin_base_url(base_url, access_token)
        if p.is_builtin:
            models_list = fetch_builtin_provider_models(
                p,
                access_token,
                user_id=str(current_user.id),
            )
        else:
            models_list = [m.strip() for m in p.model.split(";") if m.strip()]
        for model_name in models_list:
            name = f"{p.id}{model_name}"
            model_data = {
                "name": name,
                "type": p.type,
                "api_key": p.api_key,
                "auth_token": p.auth_token,
                "base_url": base_url,
                "model": model_name,
                "temperature": p.temperature,
                "max_tokens": p.max_tokens,
                "timeout": p.timeout,
                "max_retries": p.max_retries,
            }
            provider_configs.append(model_data)
            provider_configs_map[name] = model_data
    # 添加mock供应商
    mock_data = {
        "name": "mock",
        "type": "mock",
        "api_key": "",
        "auth_token": "",
        "base_url": "",
        "model": "mock",
        "temperature": 0.7,
        "max_tokens": 32768,
        "timeout": 60,
        "max_retries": 3,
    }
    provider_configs.append(mock_data)
    provider_configs_map["mock"] = mock_data

    planner_config = input_args.get("planner", {})
    coder_config = input_args.get("coder", {})
    evaluator_config = input_args.get("evaluator", {})

    planner_provider = planner_config.get("provider", "default")
    planner_model = planner_config.get("provider_model", "")
    coder_provider = coder_config.get("provider", "default")
    coder_model = coder_config.get("provider_model", "")
    evaluator_provider = evaluator_config.get("provider", "default")
    evaluator_model = evaluator_config.get("provider_model", "")

    need_defaults = (
        (not planner_provider or planner_provider == "default")
        or (not coder_provider or coder_provider == "default")
        or (not evaluator_provider or evaluator_provider == "default")
    )
    defaults = get_user_default_model(db, current_user.id, access_token) if need_defaults else None

    if not planner_provider or planner_provider == "default":
        if defaults and defaults.planner_provider_id and defaults.planner_model_name:
            planner_config["provider"] = f"{defaults.planner_provider_id}{defaults.planner_model_name}"
        else:
            raise HTTPException(
                status_code=400,
                detail="未配置默认 Planner 供应商，请先在用户设置中配置",
            )
    else:
        planner_config["provider"] = f"{planner_provider}{planner_model}"

    if not coder_provider or coder_provider == "default":
        if defaults and defaults.coder_provider_id and defaults.coder_model_name:
            coder_config["provider"] = f"{defaults.coder_provider_id}{defaults.coder_model_name}"
        else:
            raise HTTPException(
                status_code=400,
                detail="未配置默认 Coder 供应商，请先在用户设置中配置",
            )
    else:
        coder_config["provider"] = f"{coder_provider}{coder_model}"

    # 评估器无专属默认槽位，回退到用户的“其它”默认模型（other）
    if not evaluator_provider or evaluator_provider == "default":
        if defaults and defaults.other_provider_id and defaults.other_model_name:
            evaluator_config["provider"] = f"{defaults.other_provider_id}{defaults.other_model_name}"
        else:
            raise HTTPException(
                status_code=400,
                detail="未配置默认评估器供应商，请先在用户设置中配置",
            )
    else:
        evaluator_config["provider"] = f"{evaluator_provider}{evaluator_model}"

    planner_config.pop("provider_model", None)
    coder_config.pop("provider_model", None)
    evaluator_config.pop("provider_model", None)

    # 按需追加实际用到的 provider configs（按 name 去重，避免 planner/coder/evaluator 使用同一 provider 时重复添加）
    seen_provider_names: set[str] = set()
    for role, name in (
        ("Planner", planner_config.get("provider")),
        ("Coder", coder_config.get("provider")),
        ("Evaluator", evaluator_config.get("provider")),
    ):
        if not name:
            continue
        if name not in provider_configs_map:
            raise HTTPException(
                status_code=400,
                detail=f"{role} provider/model {name} is not available in LiteLLM provider models",
            )
        if name not in seen_provider_names:
            seen_provider_names.add(name)
            input_args["providers"].append(provider_configs_map[name])
    input_args["planner"] = planner_config
    input_args["coder"] = coder_config
    input_args["evaluator"] = evaluator_config
    # 运行强制使用默认 embedding 模型，并始终经 LiteLLM Gateway 注入用户 key。
    # 不读取内置供应商 URL 或 Jina 直连 key，避免任务容器绕过统一网关鉴权。
    embedding_base_url = _build_gateway_embedding_base_url(access_token)
    embedding_kwargs: dict = {
        "type": "openai_compatible",
        "base_url": embedding_base_url,
        "api_key": "EMPTY",
        "model": settings.EMBEDDING_MODEL,
        "dim": 2048,
        "embedding_func_max_async": 2,
    }
    input_args["embedding"] = EmbeddingConfig(**embedding_kwargs).model_dump()

    # 启用凭据代理：把下发到容器的真实 LLM 凭据替换为一次性代理 token + 代理 base_url。
    # embedding 固定直连本地 gateway，不走 backend llmproxy，避免任务容器回连 backend。
    if settings.LLM_PROXY_ENABLE and task_id is not None:
        _apply_credential_proxy(input_args, current_user, task_id)
    return input_args


def _build_gateway_embedding_base_url(access_token: str | None) -> str:
    """Build the per-user LiteLLM Gateway URL used by task embedding calls."""
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少访问令牌，无法通过 LiteLLM Gateway 配置 embedding",
        )

    gateway_base_url = settings.LITELLM_GATEWAY_BASE_URL.strip().rstrip("/")
    if not gateway_base_url:
        raise HTTPException(
            status_code=500,
            detail="LITELLM_GATEWAY_BASE_URL 未配置，无法通过 LiteLLM Gateway 配置 embedding",
        )

    team_id = settings.TEAM_ID.strip()
    if not team_id:
        raise HTTPException(
            status_code=500,
            detail="TEAM_ID 未配置，无法通过 LiteLLM Gateway 配置 embedding",
        )

    return f"{gateway_base_url}/litellm_proxy/{team_id}/{access_token}/v1"


def _apply_credential_proxy(
    input_args: dict,
    current_user: models.User,
    task_id: uuid.UUID | str,
) -> None:
    """就地把 run_args 中各供应商的真实凭据替换为一次性代理 token。

    对 ``input_args["providers"]`` 中每个非 mock 供应商发放代理 token
    （真实凭据加密存入 Redis，由 broker 管理），并改写
    ``api_key``/``auth_token``/``base_url``，使容器内代码经 backend 反向代理调用
    大模型，真实凭据不下发。

    Args:
        input_args: 待改写的 run_args（原地修改）。
        current_user: 当前用户，用于 token 归属。
        task_id: 任务 ID，用于 token 归集与结束时吊销。
    """
    from app.services import credential_broker

    if not settings.LLM_PROXY_BASE_URL:
        # 纵深防御兜底：启动期 _enforce_llm_proxy_config 已拦截此配置错误。
        # 正常路径不会触达此处；仅当 settings 在运行时被改写（settings 非 frozen）
        # 时兜底，避免以空 base_url 静默改写出不可用的容器配置。
        raise HTTPException(
            status_code=500,
            detail="LLM_PROXY_ENABLE 已开启但未配置 LLM_PROXY_BASE_URL，已阻止任务以明文凭据下发到容器",
        )

    proxy_base = settings.LLM_PROXY_BASE_URL.rstrip("/")
    ttl = settings.LLM_PROXY_TOKEN_TTL

    def _swap(provider_cfg: dict) -> None:
        """用真实凭据换发一个代理 token，并就地改写 provider_cfg。"""
        token = credential_broker.issue_token(
            user_id=current_user.id,
            task_id=task_id,
            ttl=ttl,
            provider_type=provider_cfg.get("type", "openai_compatible"),
            base_url=provider_cfg.get("base_url") or "",
            api_key=provider_cfg.get("api_key") or "",
            auth_token=provider_cfg.get("auth_token") or "",
            model=provider_cfg.get("model", ""),
            timeout=provider_cfg.get("timeout") or 60.0,
        )
        provider_cfg["api_key"] = token
        provider_cfg["auth_token"] = ""
        provider_cfg["base_url"] = proxy_base

    # 收集所有需脱敏的 LLM 配置：非 mock 供应商。
    # embedding 使用本地 gateway 的用户访问令牌，保持任务容器直连 gateway。
    to_scrub: list[dict] = [
        cfg for cfg in input_args.get("providers", []) if cfg.get("type") != "mock"
    ]

    for cfg in to_scrub:
        _swap(cfg)


def run_task(
    db: Session,
    task_id: uuid.UUID,
    current_user: models.User,
    access_token: str | None = None,
) -> schemas.TaskRunResponse:
    """运行任务：准备运行目录、从 S3 下载数据、提交 Celery 任务。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户
        access_token: 当前登录 token，用于替换内置供应商 URL 中的占位

    Returns:
        任务运行提交响应

    Raises:
        HTTPException: 任务正在运行（409）、数据缺失（404）或下载失败（404）
    """
    task = get_task_with_auth(db, task_id, current_user)

    if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
        raise HTTPException(status_code=409, detail="任务正在运行中，请勿重复提交")

    # 解析供应商列表及 evolution 中的供应商引用
    input_args = dict(task.input_args) if task.input_args else {}
    input_args = _resolve_providers(db, input_args, current_user, access_token, task_id=task_id)

    # 准备运行参数
    container_name = f"code_user-{current_user.id}"
    user_home = f"{settings.DOCKER_PROJECT_HOME}{container_name}/"
    run_dir = f"{user_home}{task_id}/"

    input_args["project_name"] = "llm4ad"
    input_args["base_dir"] = "/task/data/"
    input_args["run_id"] = "run"
    task_args = {
        "task_id": task_id,
        "project_id": task.project_id,
        "user_id": current_user.id,
        "user_home": user_home,
        "container_name": container_name,
        "run_dir": run_dir,
        "run_args": input_args,
    }

    if not task.input_data_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="未包含数据，请上传数据再试！")

    task_args["input_data_path"] = task.input_data_path

    from app.core.redis import delete_task_logs, push_log_entry

    # 先更新任务 model：清空旧日志、队列，更新状态
    delete_task_logs(str(task.id))

    from sqlmodel import delete

    from app.models import TaskLog

    db.exec(delete(TaskLog).where(TaskLog.task_id == task.id))
    task.status = TaskStatus.PENDING
    # 清空上一次运行残留的报告与结果渲染缓存，避免与新一轮的产物错配
    task.reports = None
    task.result_render = None
    celery_task_id = str(uuid.uuid4())
    task.celery_task_id = celery_task_id
    task.updated_time = datetime.now(UTC)
    db.add(task)
    db.commit()

    push_log_entry(
        str(task.id),
        {
            "type": "status",
            "status": TaskStatus.PENDING.value,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    # 使用预生成的 ID 派发 Celery 异步任务
    celery_result = run_evolution.apply_async(args=[task_args], task_id=celery_task_id)
    db.refresh(task)

    return schemas.TaskRunResponse(
        task_id=task.id,
        celery_task_id=celery_result.id,
        status=task.status,
    )


def _force_stop_celery_task(db: Session, task: models.Task, *, reason: str | None = None) -> None:  # noqa: ARG001
    """中止 Celery 任务、强制停止其容器，并同步持久化 Redis 日志。

    为了让停止接口快速响应（亚秒级），本函数：

    1. 先把可选的中断说明日志推送到 Redis；
    2. 通过 Celery abort/revoke 通知 worker 停止任务；
    3. 使用 ``SIGKILL`` 立即终止任务容器（不等优雅退出）；
    4. **同步**调用 :func:`app.tasks.evolution._finalize_task`，把 Redis
       中的运行日志持久化到 DB 并写入 ``FAILED`` 终态。

    第 4 步保证调用方在收到响应时，Redis 日志已经落库；同时由于
    ``_finalize_task`` 通过任务终态做幂等检查，worker 端的 finally
    再次调用时会被短路，不会重复写入。

    Args:
        db: 数据库会话（保留以兼容调用方签名，本函数当前不使用）。
        task: Task 模型实例（必须处于 PENDING 或 RUNNING 状态）。
        reason: 可选的中断原因描述；不为空时作为 error 日志推送到 Redis。
    """
    from app.core.redis import push_log_entry
    from app.tasks.evolution import _finalize_task

    if reason:
        push_log_entry(
            str(task.id),
            {
                "type": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "error_type": "TaskInterrupted",
                "error_message": reason,
            },
        )

    if task.celery_task_id:
        if settings.TASK_CONTAINER_ENABLE:
            from celery.contrib.abortable import AbortableAsyncResult

            AbortableAsyncResult(task.celery_task_id, app=celery_app).abort()
        else:
            celery_app.control.revoke(task.celery_task_id, terminate=True, signal="SIGTERM")

    if settings.TASK_CONTAINER_ENABLE:
        try:
            from app.services.container_service import kill_task_container

            kill_task_container(str(task.id))
        except Exception as e:
            logger.warning(f"强制停止任务容器失败: {e}")

    if task.celery_task_id:
        _finalize_task(task.celery_task_id, TaskStatus.FAILED)


def stop_task(db: Session, task_id: uuid.UUID, current_user: models.User) -> models.Task:
    """停止正在运行或等待中的任务。

    返回前会同步完成：abort Celery 任务、SIGKILL 任务容器、把 Redis 中
    的日志持久化到 DB 并把任务状态置为 ``FAILED``。Worker 端 finally
    再次调用 finalize 时会因终态短路，不会重复写入。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户

    Returns:
        最新任务对象（状态已为 ``FAILED``，运行日志已落 DB）。

    Raises:
        HTTPException: 任务不存在（404）、无权访问（403）或当前状态不可停止（409）
    """
    task = get_task_with_auth(db, task_id, current_user)

    if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
        raise HTTPException(status_code=409, detail="当前任务状态无法停止")

    _force_stop_celery_task(db, task, reason=f"任务 {task_id} 已被用户 {current_user.id} 手动停止")
    db.refresh(task)

    logger.info(f"任务 {task_id} 已被用户 {current_user.id} 手动停止")
    return task


def get_task_result(db: Session, task_id: uuid.UUID, current_user: models.User) -> schemas.TaskResultResponse:
    """获取 Celery 执行结果，并将状态同步回数据库。

    Args:
        db: 数据库会话
        task_id: 任务 ID
        current_user: 当前登录用户

    Returns:
        任务执行结果响应
    """
    task = get_task_with_auth(db, task_id, current_user)

    # 未提交过 Celery 任务
    if not task.celery_task_id:
        return schemas.TaskResultResponse(
            task_id=task.id,
            celery_task_id=None,
            celery_status="NOT_DISPATCHED",
            status=task.status,
            result=None,
            error=None,
        )

    async_result = AsyncResult(task.celery_task_id, app=celery_app)
    celery_state = async_result.state

    # 映射 Celery 状态并同步到数据库
    mapped_status = CELERY_STATUS_MAP.get(celery_state, task.status)
    if mapped_status != task.status:
        task.status = mapped_status
        task.updated_time = datetime.now(UTC)
        db.add(task)
        db.commit()
        db.refresh(task)

    # 提取结果或错误信息
    result = None
    error = None
    if async_result.successful():
        result = async_result.result
    elif async_result.failed():
        error = str(async_result.result)

    return schemas.TaskResultResponse(
        task_id=task.id,
        celery_task_id=task.celery_task_id,
        celery_status=celery_state,
        status=task.status,
        result=result,
        error=error,
    )
