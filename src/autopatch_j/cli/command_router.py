from __future__ import annotations

from typing import Any

from autopatch_j.cli.commands import CLI_COMMAND_BY_NAME
from autopatch_j.cli.command_handlers import CommandHandlers


class CommandRouter:
    """
    斜杠命令路由器。

    只负责解析 `/xxx` 并把它分发给 CommandHandlers；具体命令行为不放在这里。
    """

    def __init__(self, handlers: CommandHandlers, renderer: Any) -> None:
        self.handlers = handlers
        self.renderer = renderer

    def handle_command(self, raw_cmd: str) -> None:
        parts = raw_cmd.split()
        cmd = parts[0].lower()

        command = CLI_COMMAND_BY_NAME.get(cmd)
        if command is None:
            self.renderer.print_error(f"未知命令：{cmd}")
            return

        getattr(self.handlers, command.handler_name)()
