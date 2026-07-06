"""
首页资讯服务。

从 GitHub Wiki 拉取 markdown、按固定格式解析为结构化条目、按语种分别写入 Redis 缓存。
提供后台周期性刷新循环（复刻 code_server_service.run_idle_cleanup_loop 范式）。

多语种策略：
- 每种语言（zh / en）配一条独立 wiki URL；
- 未配置（空串）的语种整体跳过：不拉取、不写 Redis、不占缓存 key —— 请求侧读到空后由前端 fallback；
- 任一语种拉取失败不影响其它语种；单次循环内串行拉取，wiki 内容很小、无并发压力。

日期策略：
- 只透传 wiki 中的原始字符串（如『2026年7月6日』/『Jul 6, 2026』），不再在后端解析成 datetime；
- wiki 里的日期书写风格多样，尝试统一解析很容易出错、且没有必要 ——
  前端在自身语言上下文里直接展示原文即可。

Wiki 格式（每条以 --- 分隔，按顺序）：
    ## [标题](微信链接)
    首段摘要…（可跨多行）
    <img src="封面图" ... />
    - **发布时间**: 2026年7月6日   （英文源可用 "Published": "Jul 6, 2026" 等，任意字符串都透传）
    - **标签**: 探索
    - **来源**: 某公众号
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Literal, get_args

import httpx
from loguru import logger

from app.core import redis as redis_core
from app.core.config import settings
from app.schemas.news import NewsItem, NewsList

# 支持的语种。新增语种只需：Literal 里加一个 + config 里加 URL + _wiki_url_for 里加映射。
Lang = Literal["zh", "en"]
SUPPORTED_LANGS: tuple[Lang, ...] = get_args(Lang)


def _wiki_url_for(lang: Lang) -> str:
    """返回该语种对应的 wiki URL；未配置（空串）时返回空串，调用方据此跳过拉取。"""
    return {
        "zh": settings.NEWS_WIKI_URL_ZH,
        "en": settings.NEWS_WIKI_URL_EN,
    }[lang].strip()


# ---- markdown 解析正则 ----
_TITLE_LINK_RE = re.compile(r"^##\s*\[([^\]]+)\]\(([^)]+)\)\s*$", re.MULTILINE)
_IMG_SRC_RE = re.compile(r'<img[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
_META_LINE_RE = re.compile(r"^-\s*\*\*([^*]+)\*\*\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# 中英两套 meta 键，按候选顺序命中第一个即用。查表走 lowercase 比较，
# 对 "PUBLISHED" / "publication date" / "tags" 等大小写变体都能识别。
_META_KEYS_DATE = (
    "发布时间", "发布日期", "日期",
    "Published", "Publication Date", "Publish Date", "Date",
)
_META_KEYS_TAG = ("标签", "Tag", "Tags", "Category")
_META_KEYS_SOURCE = ("来源", "Source", "From")


def _strip_html(text: str) -> str:
    """去掉行内 HTML 标签，把连续空白折叠为单空格。"""
    cleaned = _HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _lookup_meta(meta_ci: dict[str, str], keys: tuple[str, ...]) -> str | None:
    """按候选 key 顺序查 meta（大小写不敏感）；命中即返回。

    Args:
        meta_ci: 已经把 key 转成 lowercase 的 meta 字典。
        keys: 候选键名，会各自转为 lowercase 后再查。
    """
    for k in keys:
        v = meta_ci.get(k.lower())
        if v is not None:
            return v
    return None


def parse_news_markdown(markdown: str) -> list[NewsItem]:
    """把 wiki markdown 解析为 NewsItem 列表。

    强健性策略：
    - 缺任何单项字段（封面、日期、标签、来源）都不影响其它字段；
    - 无法识别标题行的段落直接跳过；
    - 中英两套 meta 字段名都识别，且键名大小写不敏感；
    - 日期原样透传，不做格式解析；
    - 保留 wiki 中的原始出现顺序（不去重，按需求）。
    """
    items: list[NewsItem] = []
    for raw_block in re.split(r"\n\s*---+\s*\n", markdown):
        block = raw_block.strip()
        if not block:
            continue

        title_match = _TITLE_LINK_RE.search(block)
        if not title_match:
            continue

        title, url = title_match.group(1).strip(), title_match.group(2).strip()
        meta = {k.strip(): v.strip() for k, v in _META_LINE_RE.findall(block)}
        meta_ci = {k.lower(): v for k, v in meta.items()}

        after_title = block[title_match.end():]
        summary_end = len(after_title)
        img_match = _IMG_SRC_RE.search(after_title)
        if img_match:
            summary_end = min(summary_end, img_match.start())
        list_match = re.search(r"^\s*-\s+\*\*", after_title, re.MULTILINE)
        if list_match:
            summary_end = min(summary_end, list_match.start())
        summary = _strip_html(after_title[:summary_end])

        cover = img_match.group(1).strip() if img_match else None

        items.append(
            NewsItem(
                title=title,
                url=url,
                summary=summary,
                cover_image=cover,
                published_at=_lookup_meta(meta_ci, _META_KEYS_DATE),
                tag=_lookup_meta(meta_ci, _META_KEYS_TAG),
                source=_lookup_meta(meta_ci, _META_KEYS_SOURCE),
            )
        )

    return items


async def fetch_wiki_markdown(url: str) -> str:
    """异步拉取指定 URL 的 wiki 原始 markdown。网络层失败时抛异常。"""
    async with httpx.AsyncClient(timeout=settings.NEWS_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def fetch_wiki_markdown_sync(url: str) -> str:
    """同步版本，用于 CLI 手动触发。"""
    with httpx.Client(timeout=settings.NEWS_HTTP_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def _build_news_list(items: list[NewsItem]) -> NewsList:
    return NewsList(items=items, updated_at=datetime.now(UTC))


async def refresh_news(lang: Lang) -> NewsList | None:
    """拉取一次指定语种，解析并写缓存。

    Returns:
        写入的 NewsList；若该语种未配置 URL，返回 None（不写缓存）。
        网络/协议层异常向上抛出，由调用方决定是否兜底。
    """
    url = _wiki_url_for(lang)
    if not url:
        logger.info(f"资讯语种 '{lang}' 未配置 wiki URL，跳过拉取")
        return None
    markdown = await fetch_wiki_markdown(url)
    items = parse_news_markdown(markdown)
    payload = _build_news_list(items)
    redis_core.set_cached_news(lang, payload.model_dump(mode="json"))
    logger.info(f"资讯缓存已刷新（lang={lang}）：共 {len(items)} 条")
    return payload


def refresh_news_sync(lang: Lang) -> NewsList | None:
    """同步刷新，供 CLI 使用。语义与 refresh_news 一致。"""
    url = _wiki_url_for(lang)
    if not url:
        logger.info(f"资讯语种 '{lang}' 未配置 wiki URL，跳过拉取")
        return None
    markdown = fetch_wiki_markdown_sync(url)
    items = parse_news_markdown(markdown)
    payload = _build_news_list(items)
    redis_core.set_cached_news(lang, payload.model_dump(mode="json"))
    logger.info(f"资讯缓存已刷新（sync, lang={lang}）：共 {len(items)} 条")
    return payload


async def refresh_all_news() -> None:
    """遍历所有已配置的语种拉一遍；单个语种失败仅记日志，不影响其它语种。"""
    for lang in SUPPORTED_LANGS:
        try:
            await refresh_news(lang)
        except Exception as exc:
            logger.warning(f"资讯拉取失败（lang={lang}），保留上次缓存: {exc}")


def get_cached_news(lang: Lang) -> NewsList:
    """读某语种缓存；无缓存时返回空 NewsList（前端 fallback）。"""
    raw = redis_core.get_cached_news(lang)
    if not raw:
        return NewsList()
    try:
        return NewsList.model_validate(raw)
    except Exception:
        logger.warning(f"资讯缓存反序列化失败（lang={lang}），返回空列表", exc_info=True)
        return NewsList()


async def run_news_refresh_loop() -> None:
    """周期性刷新所有已配置语种；异常仅记日志，循环不退出。

    应作为后台 task 在应用启动时拉起，关闭时取消。多 worker 部署时各进程都会
    自行拉取——wiki 内容小、覆盖幂等、Redis 写入原子，无副作用。
    """
    interval = settings.NEWS_REFRESH_INTERVAL_SECONDS
    configured = [lang for lang in SUPPORTED_LANGS if _wiki_url_for(lang)]
    logger.info(
        f"启动首页资讯刷新循环（间隔 {interval}s，已配置语种：{configured or '无'}）"
    )
    while True:
        try:
            await refresh_all_news()
        except Exception as exc:
            logger.warning(f"资讯刷新任务异常，将保留上次缓存: {exc}")
        await asyncio.sleep(interval)
