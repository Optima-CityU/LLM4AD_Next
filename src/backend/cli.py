import click


@click.group()
def main():
    pass


@main.group(help="数据管理")
def data():
    pass


@data.command(help="初始化数据")
def init_db():
    from app.utils.init_db import init_all

    init_all()


@data.command(help="创建超级管理员账户，已存在则直接提升权限")
@click.option("--email", prompt=True, type=str, help="邮箱", required=True)
@click.option("--password", prompt=True, type=str, help="密码，至少8位", required=True)
def create_superuser(email, password):
    from app.utils.init_db import create_superuser as create_superuser_

    create_superuser_(email=email, password=password)


@data.command(help="为缺失默认模型配置的历史用户补齐一行配置")
def backfill_default_model():
    from app.utils.init_db import backfill_user_default_models

    backfill_user_default_models()


@main.group(help="服务管理")
def server():
    pass


@server.command(help="预启动")
def pre_start():
    from app.backend_pre_start import pre_start as backend_pre_start

    backend_pre_start()


@main.group(help="首页资讯（GitHub Wiki 拉取）")
def news():
    pass


@news.command("refresh", help="手动触发一次 wiki 拉取 + 解析 + 写入 Redis 缓存")
@click.option(
    "--lang",
    "lang",
    type=click.Choice(["zh", "en", "all"], case_sensitive=False),
    default="all",
    show_default=True,
    help="要刷新的语种；all 表示遍历所有已配置语种",
)
@click.option(
    "--print/--no-print",
    "print_items",
    default=False,
    help="拉取后把解析出的条目打印到 stdout，便于本地核对（不影响写缓存）",
)
def news_refresh(lang: str, print_items: bool):
    """手动触发资讯刷新。

    与后台定时循环走同一路径，因此结果、日志、缓存 key 完全一致。适用场景：
    - 本地开发/联调，不想等下一次定时刷新；
    - wiki 刚发新文章，希望立刻在首页看到；
    - 排查解析异常（配合 ``--print`` 查看解析后的结构）。

    未配置该语种 wiki URL 时会打印提示并跳过，不写入 Redis。
    """
    from app.services.news_service import SUPPORTED_LANGS, Lang, refresh_news_sync

    lang_lower = lang.lower()
    targets: tuple[Lang, ...] = (
        SUPPORTED_LANGS if lang_lower == "all" else (lang_lower,)  # type: ignore[assignment]
    )

    for target in targets:
        result = refresh_news_sync(target)
        if result is None:
            click.echo(f"⊘ 语种 '{target}' 未配置 wiki URL，已跳过")
            continue
        click.echo(
            f"✓ 已刷新缓存 lang={target}：{len(result.items)} 条，updated_at={result.updated_at}"
        )
        if print_items:
            for i, item in enumerate(result.items, 1):
                click.echo(
                    f"\n[{i}] {item.title}\n    {item.url}\n"
                    f"    published_at={item.published_at}  tag={item.tag}  source={item.source}\n"
                    f"    cover={item.cover_image}\n"
                    f"    summary={item.summary[:120]}"
                )


@news.command("show", help="打印当前 Redis 缓存中的资讯（不触发拉取）")
@click.option(
    "--lang",
    "lang",
    type=click.Choice(["zh", "en", "all"], case_sensitive=False),
    default="all",
    show_default=True,
    help="要查看的语种；all 表示所有支持的语种",
)
def news_show(lang: str):
    """只读地查看当前 Redis 里缓存的资讯，用于确认后台循环是否正常写入。"""
    from app.services.news_service import SUPPORTED_LANGS, Lang, get_cached_news

    lang_lower = lang.lower()
    targets: tuple[Lang, ...] = (
        SUPPORTED_LANGS if lang_lower == "all" else (lang_lower,)  # type: ignore[assignment]
    )

    for target in targets:
        result = get_cached_news(target)
        click.echo(
            f"\n== lang={target} ==  条目数：{len(result.items)}  updated_at={result.updated_at}"
        )
        for i, item in enumerate(result.items, 1):
            click.echo(f"[{i}] {item.title}  ({item.published_at})  {item.url}")


if __name__ == "__main__":
    main()
