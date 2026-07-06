"""
资讯服务解析器 & 语种分流单测。

覆盖：
- 中英两套 meta 键；日期字段原样透传（不再在后端做格式解析）；
- 未配置 URL 时 refresh_news* 应跳过（返回 None，不写 Redis）；
- 已配置但拉取失败：异常向上抛，缓存不动；
- 已配置且拉取成功：写入对应语种 key。
"""

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services import news_service
from app.services.news_service import parse_news_markdown, refresh_news_sync

_ZH_BLOCK = """## [探索 LLM4AD_Next No.1｜从科学数据中发现数学公式](https://mp.weixin.qq.com/s/GZ-kfScXzpJZT1dE-6HIzg)

今天，我们来深入聊聊平台的重要应用场景之一：「符号回归」。符号回归要解决的问题是：给你一堆输入输出的数值数据，能不能自动找到一个数学表达式，来既准确又简洁地描述这些数据背后隐藏的规律？

<img src="https://mmbiz.qpic.cn/sz_mmbiz_jpg/abc/0?wx_fmt=jpeg" width="600" height="auto" />

- **发布时间**: 2026年7月6日
- **标签**: 探索
- **来源**: 学习优化和基础模型
"""

_EN_BLOCK = """## [Discovering formulas from scientific data](https://mp.weixin.qq.com/s/EN-DEMO)

Today we dive into one of the platform's key application scenarios: symbolic regression.

<img src="https://example.com/cover-en.jpg" />

- **Publication Date**: Jul 6, 2026
- **Tags**: Explore
- **Source**: Learning to Optimize
"""


# ---- 解析层 ----


def test_parse_zh_block_passes_date_through_as_string():
    """中文块的发布日期原样透传，不做任何格式转换。"""
    items = parse_news_markdown(_ZH_BLOCK)
    assert len(items) == 1
    it = items[0]
    assert it.title.startswith("探索 LLM4AD_Next No.1")
    assert it.published_at == "2026年7月6日"
    assert it.tag == "探索"
    assert it.source == "学习优化和基础模型"


def test_parse_en_block_passes_date_through_as_string():
    """英文块用 Publication Date / Tags / Source 作为 meta key；日期原样透传。"""
    items = parse_news_markdown(_EN_BLOCK)
    assert len(items) == 1
    it = items[0]
    assert it.title == "Discovering formulas from scientific data"
    assert it.url == "https://mp.weixin.qq.com/s/EN-DEMO"
    assert it.cover_image == "https://example.com/cover-en.jpg"
    assert it.published_at == "Jul 6, 2026"
    assert it.tag == "Explore"
    assert it.source == "Learning to Optimize"


def test_parse_meta_keys_are_case_insensitive():
    """meta 键使用大写、小写、混合大小写都应能命中。"""
    md = """## [t](https://example.com/x)

body

- **PUBLISHED**: yesterday
- **tags**: alpha
- **source**: place
"""
    items = parse_news_markdown(md)
    assert items[0].published_at == "yesterday"
    assert items[0].tag == "alpha"
    assert items[0].source == "place"


def test_parse_accepts_arbitrary_date_string():
    """就算 wiki 里写的是格式怪异的日期，也应原样透传给前端，不该丢字段。"""
    md = """## [标题](https://example.com/x)

摘要

- **发布时间**: 二〇二六年七月六日（周一）
"""
    items = parse_news_markdown(md)
    assert items[0].published_at == "二〇二六年七月六日（周一）"


def test_parse_preserves_duplicates_and_order():
    md = _ZH_BLOCK + "\n---\n" + _ZH_BLOCK.replace(
        "学习优化和基础模型", "来源公众号1"
    )
    items = parse_news_markdown(md)
    assert len(items) == 2
    assert items[0].source == "学习优化和基础模型"
    assert items[1].source == "来源公众号1"


def test_parse_skips_non_title_blocks():
    md = "just some intro text\n---\n" + _ZH_BLOCK
    items = parse_news_markdown(md)
    assert len(items) == 1
    assert items[0].tag == "探索"


def test_parse_tolerates_missing_optional_fields():
    md = """## [标题只带链接](https://example.com/x)

只有摘要正文，没图也没元数据。
"""
    items = parse_news_markdown(md)
    it = items[0]
    assert it.cover_image is None
    assert it.published_at is None
    assert it.tag is None
    assert it.source is None


def test_parse_empty_markdown_returns_empty_list():
    assert parse_news_markdown("") == []
    assert parse_news_markdown("\n\n---\n\n") == []


# ---- 语种分流 / 未配置跳过 ----


def test_refresh_sync_skips_when_url_missing(monkeypatch):
    """未配置 URL 时应返回 None，且不触发 HTTP、不触碰 Redis。"""
    monkeypatch.setattr(settings, "NEWS_WIKI_URL_EN", "")

    with (
        patch.object(news_service, "fetch_wiki_markdown_sync") as m_fetch,
        patch.object(news_service.redis_core, "set_cached_news") as m_set,
    ):
        result = refresh_news_sync("en")

    assert result is None
    m_fetch.assert_not_called()
    m_set.assert_not_called()


def test_refresh_sync_treats_whitespace_url_as_missing(monkeypatch):
    """URL 只有空白同样视为未配置。"""
    monkeypatch.setattr(settings, "NEWS_WIKI_URL_EN", "   ")

    with (
        patch.object(news_service, "fetch_wiki_markdown_sync") as m_fetch,
        patch.object(news_service.redis_core, "set_cached_news") as m_set,
    ):
        result = refresh_news_sync("en")

    assert result is None
    m_fetch.assert_not_called()
    m_set.assert_not_called()


def test_refresh_sync_writes_cache_when_url_configured(monkeypatch):
    """已配置 URL 时应拉取、解析并按 lang 写入 Redis。"""
    monkeypatch.setattr(settings, "NEWS_WIKI_URL_EN", "https://example.com/en.md")

    with (
        patch.object(news_service, "fetch_wiki_markdown_sync", return_value=_EN_BLOCK),
        patch.object(news_service.redis_core, "set_cached_news") as m_set,
    ):
        result = refresh_news_sync("en")

    assert result is not None
    assert len(result.items) == 1
    m_set.assert_called_once()
    lang_arg, payload = m_set.call_args.args
    assert lang_arg == "en"
    # 序列化产物里日期保持原样，不是 ISO datetime
    assert payload["items"][0]["title"] == "Discovering formulas from scientific data"
    assert payload["items"][0]["published_at"] == "Jul 6, 2026"


def test_refresh_sync_propagates_http_errors(monkeypatch):
    """拉取失败应抛异常给上层，缓存保持不动。"""
    monkeypatch.setattr(settings, "NEWS_WIKI_URL_ZH", "https://example.com/zh.md")

    with (
        patch.object(
            news_service,
            "fetch_wiki_markdown_sync",
            side_effect=RuntimeError("boom"),
        ),
        patch.object(news_service.redis_core, "set_cached_news") as m_set,
        pytest.raises(RuntimeError, match="boom"),
    ):
        refresh_news_sync("zh")

    m_set.assert_not_called()
