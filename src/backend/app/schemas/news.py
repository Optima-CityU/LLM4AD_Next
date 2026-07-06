"""
首页资讯 Schema。

定义从 GitHub Wiki 拉取并解析后返回给前端的资讯条目结构。
数据源为唯一真源的 markdown 文件，故此处只定义响应模型，不涉及请求/更新。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NewsItem(BaseModel):
    """单条资讯（对应 wiki 中一个 `## [标题](链接)` 块）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(description="资讯标题")
    url: str = Field(description="微信公众号文章 URL")
    summary: str = Field(default="", description="首段摘要（去除 HTML 后的纯文本）")
    cover_image: str | None = Field(default=None, description="封面图 URL；无则为 null")
    published_at: str | None = Field(default=None, description="发布日期原始字符串")
    tag: str | None = Field(default=None, description="标签，例如『探索』")
    source: str | None = Field(default=None, description="来源公众号名称")


class NewsList(BaseModel):
    """资讯列表响应。

    `updated_at` 反映后端最近一次成功从 wiki 拉取的时间；wiki 拉取失败但缓存
    仍有内容时，该字段保持上次成功时的时间，便于前端判断新鲜度。
    """

    items: list[NewsItem] = Field(default_factory=list, description="资讯条目列表")
    updated_at: datetime | None = Field(
        default=None, description="缓存的最近一次成功更新时间（UTC）"
    )
