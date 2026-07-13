from __future__ import annotations

import shlex
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
        try:
            parts = shlex.split(raw_cmd)
        except ValueError as exc:
            self.renderer.print_error(f"命令解析失败：{exc}")
            return
        if not parts:
            return

        cmd = parts[0].lower()

        command = CLI_COMMAND_BY_NAME.get(cmd)
        if command is None:
            self.renderer.print_error(f"未知命令：{cmd}")
            return

        args = parts[1:]
        if args and not command.accepts_arguments:
            self.renderer.print_error(f"命令 {cmd} 不接受参数")
            return

        getattr(self.handlers, command.handler_name)(args)
