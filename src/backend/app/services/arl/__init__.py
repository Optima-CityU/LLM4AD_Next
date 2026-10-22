"""
ARL (Agent Research Loop) 运行时管理服务。

提供 ARL 会话的容器管理、WebSocket 路由与状态同步。
"""

from .runtime import (
    ARLRuntimeService,
    get_arl_runtime,
    reconcile_arl_containers,
    run_arl_idle_cleanup_loop,
    stop_idle_arl_sessions,
)
from .types import ARLDaemonConfig, ARLRole, ARLSession, ARLSessionStatus

__all__ = [
    "ARLDaemonConfig",
    "ARLRole",
    "ARLRuntimeService",
    "ARLSession",
    "ARLSessionStatus",
    "get_arl_runtime",
    "reconcile_arl_containers",
    "run_arl_idle_cleanup_loop",
    "stop_idle_arl_sessions",
]
