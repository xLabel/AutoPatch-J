from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliSubcommand:
    """Nested slash-command declaration used by help and completion."""

    name: str
    completion_description: str


@dataclass(frozen=True, slots=True)
class CliCommand:
    """CLI slash command declaration shared by routing, help and completion."""

    name: str
    handler_name: str
    help_description: str
    completion_description: str
    show_in_help: bool = True
    show_in_completion: bool = True
    accepts_arguments: bool = False
    subcommands: tuple[CliSubcommand, ...] = ()


MEMORY_SUBCOMMANDS: tuple[CliSubcommand, ...] = (
    CliSubcommand("status", "查看 Memory 运行状态"),
    CliSubcommand("summary", "重建人类审阅用 Memory 摘要"),
    CliSubcommand("list", "列出 active Memory"),
    CliSubcommand("show", "查看单条 Memory 与来源"),
    CliSubcommand("forget", "忘记单条派生 Memory"),
    CliSubcommand("clear", "清空 Memory 数据集"),
    CliSubcommand("export", "导出一次性 RAW JSON 快照"),
)


CLI_COMMANDS: tuple[CliCommand, ...] = (
    CliCommand(
        name="/init",
        handler_name="handle_init",
        help_description="初始化当前目录为 Java 项目并建立索引",
        completion_description="初始化项目环境",
    ),
    CliCommand(
        name="/status",
        handler_name="handle_status",
        help_description="查看当前项目状态与运行诊断",
        completion_description="查看系统状态",
    ),
    CliCommand(
        name="/scanner",
        handler_name="handle_scanners",
        help_description="查看扫描器状态",
        completion_description="查看扫描器状态",
    ),
    CliCommand(
        name="/reindex",
        handler_name="handle_reindex",
        help_description="重建本地代码符号索引",
        completion_description="刷新代码符号索引",
    ),
    CliCommand(
        name="/reset",
        handler_name="handle_reset",
        help_description="重置工作台状态（保留 Memory、导出和 CLI history）",
        completion_description="重置工作台并保留 Memory",
    ),
    CliCommand(
        name="/new",
        handler_name="handle_new",
        help_description="结束当前工作状态并创建新的普通对话 thread",
        completion_description="新建普通对话 thread",
    ),
    CliCommand(
        name="/memory",
        handler_name="handle_memory",
        help_description="管理 Memory：status/summary/list/show/forget/clear/export",
        completion_description="查看和管理 Memory",
        accepts_arguments=True,
        subcommands=MEMORY_SUBCOMMANDS,
    ),
    CliCommand(
        name="/help",
        handler_name="handle_help",
        help_description="显示命令帮助",
        completion_description="显示命令帮助",
    ),
    CliCommand(
        name="/quit",
        handler_name="handle_quit",
        help_description="退出程序",
        completion_description="退出程序",
    ),
)


CLI_COMMAND_BY_NAME = {command.name: command for command in CLI_COMMANDS}
