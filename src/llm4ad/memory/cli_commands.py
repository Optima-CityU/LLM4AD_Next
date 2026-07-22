"""CLI commands for memory management - TUI only."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()
app = typer.Typer(name="memory", help="MindMemOS 记忆管理（交互式 TUI）")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config_file: str = typer.Option(
        None,
        "--config",
        "-f",
        help="任务 config.yaml 路径。提供后指向该任务的记忆(读取 task_id/project_name)，"
        "否则管理全局记忆。",
    ),
):
    """启动交互式记忆管理界面.

    默认管理全局记忆。用 -f 指定任务 config.yaml 即可管理该任务的记忆。
    未配置时不会报错，会进入界面引导用户填写连接与模型配置。
    """
    if ctx.invoked_subcommand:
        return

    try:
        from llm4ad.memory import config as memory_config
    except ImportError as exc:  # pragma: no cover
        console.print(f"[red]错误: {exc}[/red]")
        raise typer.Exit(1) from exc

    # Resolve task scope from the pipeline config, if provided.
    task_id: str | None = None
    if config_file:
        try:
            task_id = memory_config.resolve_task_id_from_config(config_file)
        except FileNotFoundError:
            console.print(f"[red]错误: 配置文件不存在: {config_file}[/red]")
            raise typer.Exit(1) from None
        if not task_id:
            console.print(
                f"[yellow]警告: {config_file} 未找到任务标识"
                "(memory.mindmemos_session_id / project_name)，将管理全局记忆。[/yellow]"
            )

    scope = "task" if task_id else "global"

    try:
        from llm4ad.memory import tui
    except ImportError as exc:
        console.print("[red]错误: textual 未安装[/red]")
        console.print("请运行: [cyan]pip install 'llm4ad[mindmemos]'[/cyan]")
        raise typer.Exit(1) from exc

    # Launch the TUI regardless of config state; the browser handles the
    # unconfigured case by showing a guidance screen instead of crashing.
    tui.run_memory_browser(scope, task_id)
