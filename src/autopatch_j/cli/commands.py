from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliCommand:
    """CLI slash command declaration shared by routing, help and completion."""

    name: str
    handler_name: str
    help_description: str
    completion_description: str
    show_in_help: bool = True
    show_in_completion: bool = True


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
        help_description="清空工作台状态、Agent 对话历史和普通问答记忆",
        completion_description="重置工作台状态与对话历史",
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
