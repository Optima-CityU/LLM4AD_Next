"""
首页资讯路由（公开端，免登录）。

- ``GET /news?lang=<zh|en>``：返回后台缓存的资讯列表。
  - 该语种未配置 wiki URL 时：返回空列表（不去拉，不写缓存）。
  - 缓存不存在但 URL 已配置时：同步拉一次做冷启动兜底。
  - 拉取仍失败：返回空列表；前端使用本地 mock 兜底。
"""

from fastapi import APIRouter, Query
from loguru import logger

from app.schemas.news import NewsList
from app.services import news_service
from app.services.news_service import Lang

router = APIRouter(prefix="/news", tags=["news"])


@router.get(
    "",
    response_model=NewsList,
    summary="首页资讯列表（公开）",
)
async def list_news(
    lang: Lang = Query(default="zh", description="语种：zh 或 en"),
) -> NewsList:
    """按语种返回资讯列表。

    正常路径：命中 Redis 缓存直接返回；后台循环负责周期性刷新。
    冷启动兜底：缓存不存在时同步拉一次；网络失败仅记录日志，返回空列表。
    未配置：该语种若未配置 wiki URL，直接返回空列表 —— 由前端兜底展示 mock。
    """
    cached = news_service.get_cached_news(lang)
    if cached.items:
        return cached

    try:
        refreshed = await news_service.refresh_news(lang)
    except Exception as exc:
        logger.warning(f"资讯冷启动兜底拉取失败（lang={lang}）: {exc}")
        return cached
    # refresh_news 在未配置 URL 时返回 None；此时 cached 也是空 —— 都返回空。
    return refreshed or cached
