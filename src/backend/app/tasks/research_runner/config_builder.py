"""ARC config dict 构造 + provider 解析 + HITL mode 映射。

三个层次的映射：
- **前端 HITL mode → ARC PROJECT_MODES**：把 co-pilot / step-by-step 等
  8 种前端选项收敛到 ARC 认的 docs-first / semi-auto / full-auto 三档。
- **LLM4AD ProviderType → ARC llm.provider 字符串**：openai_compatible
  → "openai-compatible" 之类。
- **provider_id + model_name → 完整凭证字典**：查加密 LLMProvider 表；
  找不到则回落到用户默认（UserDefaultModel），再兜底成 mock。

``build_arc_config`` 生产的字典喂给 ``RCConfig.from_dict``；字段错误
会立刻抛，不必等 pipeline 跑一半再挂。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.db import get_db_session

from .snapshots import SessionSnapshot, TurnSnapshot

# ARC 要求 llm.api_key_env 非空，但密钥实际走 config.llm.api_key inline 字段
# （调用方另填）；从不真设这个 env（worker concurrency>1 会串号泄漏）——纯占位符。
ARC_API_KEY_ENV = "RESEARCH_ARC_API_KEY"

# profile → experiment.mode=sandbox 的域集合。这些域的实验在容器内以 sandbox
# 直跑（而非 llm4ad_agent 演化），需额外注入顶层 sandbox 配置块。
_SANDBOX_PROFILES = {"ml_vision"}


def _detect_sandbox_python_path() -> str:
    """返回 sandbox 用的 venv python 相对路径（容器内可用）。

    pipeline / sandbox 实际**永远**跑在 Linux 容器里，故固定返回 POSIX 布局的
    ``.venv/bin/python3``。曾据宿主 ``sys.executable`` 猜平台，但那会让 Windows
    宿主注入 ``.venv/Scripts/python.exe``——Linux 容器里根本没有该解释器，sandbox
    直接找不到 python 而失败。返回相对容器工作目录的相对路径（宿主绝对路径在容器
    Linux 下无效）。

    Returns:
        容器内相对路径 ``.venv/bin/python3``。
    """
    return "/app/backend/.venv/bin/python3"


# ---- Stage guidance 写（inject_stage_guidance 端点用）----


def write_stage_guidance(run_dir: Path, stage_num: int, message: str) -> Path:
    """写 guidance 到 ``run_dir/stage-NN/hitl_guidance.md``。

    guidance 真正生效靠这份 ``.md`` 文件：容器内 ARC 的 ``_build_context_preamble``
    会 glob 所有 ``stage-*/hitl_guidance.md`` 注入 LLM prompt 前言（backend 不装
    researchclaw，故不在宿主侧同步 HITLStore）。文件写失败会抛 OSError 由调用方
    处理。返回落盘路径。
    """
    stage_dir = run_dir / f"stage-{stage_num:02d}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    path = stage_dir / "hitl_guidance.md"
    path.write_text(message, encoding="utf-8")
    return path


# ---- 前端 HITL mode → ARC project.mode 映射 ----
#
# 前端选项来自 ``autoResearch.mode.*`` i18n 键：
#   full-auto / gate-only / checkpoint / step-by-step / co-pilot /
#   express / thorough / learning
# ARC 的 project.mode 只有三档：docs-first / semi-auto / full-auto。
# 我们把「无人干预」类映射到 full-auto，把「需要 HITL」类映射到 semi-auto；
# docs-first（只跑文档不跑实验）前端目前没暴露。
_DEFAULT_HITL_MODE = "full-auto"
_ARC_PROJECT_MODES = {"docs-first", "semi-auto", "full-auto"}
_MODE_MAP_TO_ARC = {
    "full-auto": "full-auto",
    "express": "full-auto",
    "thorough": "full-auto",
    "co-pilot": "semi-auto",
    "gate-only": "semi-auto",
    "step-by-step": "semi-auto",
    "checkpoint": "semi-auto",
    "learning": "semi-auto",
}


def _to_arc_project_mode(mode: str | None) -> str:
    """把前端 HITL mode 映射到 ARC PROJECT_MODES 允许值。"""
    if not mode:
        return _DEFAULT_HITL_MODE
    if mode in _ARC_PROJECT_MODES:
        return mode
    return _MODE_MAP_TO_ARC.get(mode, _DEFAULT_HITL_MODE)


# ---- ProviderType → ARC llm.provider 映射 ----
#
# LLM4AD 的 ProviderType（openai/anthropic/openai_compatible/mock）需要翻译
# 成 ARC 的 llm.provider 字符串。ARC 侧只把这个字符串作为 SDK 选择器，认
# 识 "openai" / "openai-compatible" / "anthropic" / "minimax" / …；不认识
# 就退回默认 openai-compatible 处理。
_PROVIDER_TYPE_TO_ARC = {
    "openai": "openai",
    "anthropic": "anthropic",
    "openai_compatible": "openai-compatible",
    "mock": "mock",
}


def resolve_provider_for_arc(
    provider_id: str | None,
    model_name: str | None,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """把前端 provider_id / model_name 解析成 ARC ``llm:`` 段所需字段。

    契约：
      - ``"mock"`` / 空 / ``"default"`` / 无效 UUID → 走用户默认
        （``UserDefaultModel.report_provider_id``）或最终 mock 兜底
      - 真实 UUID → 查 ``LLMProvider`` 表
    """
    from app.models import LLMProvider
    from app.services.user_default_model_service import get_user_default_model

    def _mock() -> dict[str, Any]:
        return {
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:0/v1",
            "wire_api": "chat_completions",
            "api_key_env": ARC_API_KEY_ENV,
            "primary_model": model_name or "mock",
            "api_key": "",
        }

    if provider_id == "mock":
        return _mock()

    resolved_id: uuid.UUID | None = None
    resolved_model = model_name

    if provider_id and provider_id != "default":
        try:
            resolved_id = uuid.UUID(provider_id)
        except (TypeError, ValueError):
            logger.warning(f"invalid provider_id={provider_id!r}; falling back to user default")

    with get_db_session() as db:
        if resolved_id is None:
            try:
                defaults = get_user_default_model(db, user_id)
            except Exception:
                logger.opt(exception=True).warning(f"get_user_default_model failed user={user_id}")
                return _mock()
            resolved_id = defaults.report_provider_id
            if not resolved_id:
                logger.warning(f"user {user_id} has no default report provider; mock")
                return _mock()
            if not resolved_model and defaults.report_model_name:
                resolved_model = defaults.report_model_name

        provider = db.get(LLMProvider, resolved_id)
        if not provider:
            logger.warning(f"provider {resolved_id} not found; mock")
            return _mock()

        provider_type = _PROVIDER_TYPE_TO_ARC.get(
            provider.type.value if hasattr(provider.type, "value") else str(provider.type),
            "openai-compatible",
        )
        raw_model = (provider.model or "").split(";")[0].strip()
        return {
            "provider": provider_type,
            "base_url": provider.base_url or "https://api.openai.com/v1",
            "wire_api": "chat_completions",
            "api_key_env": ARC_API_KEY_ENV,
            "primary_model": resolved_model or raw_model or "gpt-4o",
            "api_key": provider.api_key or provider.auth_token or "",
            "auth_token": provider.auth_token or "",
            "timeout": provider.timeout,
        }


def proxy_provider_for_arc(
    provider_config: dict[str, Any],
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
) -> None:
    """若 LLM_PROXY_ENABLE，将真实凭据换发为代理 token（原地修改）。

    与演化任务的 _swap 逻辑一致：真实 api_key/base_url 存入 Redis（加密），
    容器只持有不透明代理 token，经 /llmproxy 反向代理调用大模型，真实密钥
    不进入容器。LLM_PROXY_ENABLE 为 False 时为空操作，不影响现有流程。

    Args:
        provider_config: resolve_provider_for_arc 返回的凭证字典，原地改写。
        user_id: 归属用户 ID（审计用）。
        task_id: 归属任务 ID（用于结束时批量吊销，此处传 turn_id）。
    """
    from app.core.config import settings
    from app.services import credential_broker

    if not settings.LLM_PROXY_ENABLE:
        return
    token = credential_broker.issue_token(
        user_id=user_id,
        task_id=task_id,
        ttl=settings.LLM_PROXY_TOKEN_TTL,
        provider_type=provider_config.get("provider", "openai-compatible"),
        base_url=provider_config.get("base_url") or "",
        api_key=provider_config.get("api_key") or "",
        auth_token=provider_config.get("auth_token") or "",
        model=provider_config.get("primary_model", ""),
        timeout=provider_config.get("timeout") or 60.0,
    )
    provider_config["api_key"] = token
    provider_config["auth_token"] = ""
    provider_config["base_url"] = settings.LLM_PROXY_BASE_URL.rstrip("/")


# ---- ARC config 主函数 ----


def build_arc_config(
    *,
    session: SessionSnapshot,
    turn: TurnSnapshot,
    run_dir: Path,
    provider_config: dict[str, Any],
) -> dict[str, Any]:
    """构造 ARC 用的 config dict（后续 dump 到 config.arc.yaml）。

    填齐 ARC 的 7 个 REQUIRED_FIELDS（见 ARC config.py:79）。要点：
    - ``project.mode`` 由前端 HITL mode 经 :func:`_to_arc_project_mode` 映射到
      ARC 三档；``experiment`` 段固定 ``llm4ad_agent``，子块按我们场景写死。
    - ``build_*`` 三件套复用 pipeline 主 LLM 的 provider_config（同网关同 key）。
    - api_key 由调用方另填到 inline ``llm.api_key``，config 里不落明文。
    """
    project_mode = _to_arc_project_mode(turn.mode or session.mode)

    config: dict[str, Any] = {
        "project": {
            "name": (session.title or "research")[:200],
            "mode": project_mode,
            "profile": session.profile,
        },
        "research": {
            "topic": session.topic,
        },
        "security": {
            # 门控实际由容器内原生 HITLSession 决定（见 research_container_runner），
            # 容器以 auto_approve_gates=True 关掉 ARC 粗门控，此字段不再被消费，纯占位。
            "hitl_required_stages": [],
        },
        "runtime": {
            "timezone": os.environ.get("RESEARCH_ARC_TIMEZONE", "UTC"),
            "max_parallel_tasks": 1,
            "approval_timeout_hours": 12,
            "retry_limit": 1,
        },
        "notifications": {
            # console 是 ARC 内置最保守的通道，写到 stdout 即可
            "channel": "console",
            "target": "",
            "on_stage_start": False,
            "on_stage_fail": False,
            "on_gate_required": False,
        },
        "knowledge_base": {
            "backend": "markdown",
            # 放在 run_dir/kb 下，与 stage 目录隔离。不用 .resolve()：run_dir 已是
            # 绝对路径，Windows 上 .resolve() 会规范成反斜杠致路径改写落空
            # （详见 research_pipeline_runner._rewrite_paths）。
            "root": (run_dir / "kb").as_posix(),
        },
        "llm": {
            "provider": provider_config["provider"],
            "base_url": provider_config["base_url"],
            "wire_api": provider_config.get("wire_api", "chat_completions"),
            "api_key_env": provider_config["api_key_env"],
            "primary_model": provider_config["primary_model"],
        },
        "experiment": {
            "mode": "llm4ad_agent",
            "time_budget_sec": 7200,
            "max_iterations": 5,
            "metric_key": "best_individual_score",
            "metric_direction": "maximize",
            "llm4ad_agent": {
                "llm4ad_dir": "",
                "working_dir": "llm4ad_workspace",
                "timeout_sec": 7200,
                "max_repair_attempts": 10,
                "build_max_tries": 3,
                "metric_direction": "maximize",
                # build 阶段（描述→任务目录）复用 pipeline 主 LLM 的凭证；
                # ARC 明文读取这三个字段，不走 env 兜底。
                "build_api_key": provider_config.get("api_key", ""),
                "build_base_url": provider_config["base_url"],
                "build_model": provider_config["primary_model"],
            },
            "figure_agent": {"use_docker": False},
            "opencode": {"enabled": False},
        },
        "prompts": {},
    }

    # ml_vision 等域走 sandbox 直跑：改 experiment.mode 并注入顶层 sandbox 块。
    # python_path 自动探测（相对容器工作目录），其余按需求固定。
    if session.profile in _SANDBOX_PROFILES:
        config["experiment"]["mode"] = "sandbox"
        config["experiment"]["sandbox"] = {
            "python_path": _detect_sandbox_python_path(),
            "gpu_required": False,
            "max_memory_mb": 4096,
        }

    return config
