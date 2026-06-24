"""
Celery 异步任务队列配置。

配置 Celery 应用实例，使用 Redis 作为消息代理和结果后端。
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "fastapi_celery",
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_BACKEND,
    include=["app.tasks.evolution"],
)

# Celery 运行时配置
celery_app.conf.update(
    enable_utc=True,  # 使用 UTC 时区
    task_serializer="json",  # 任务序列化格式
    result_serializer="json",  # 结果序列化格式
    accept_content=["json"],  # 接受的内容类型
    timezone="UTC",  # 时区
    task_track_started=True,  # 跟踪任务启动状态
    task_time_limit=settings.TASK_TIME_LIMIT,  # 任务硬超时（默认 7 天），见 config.TASK_TIME_LIMIT
    task_soft_time_limit=settings.TASK_SOFT_TIME_LIMIT,  # 任务软超时（默认 1 天），超时抛 `SoftTimeLimitExceeded`
    result_expires=30 * 24 * 3600,  # 结果过期时间：30 天
)
