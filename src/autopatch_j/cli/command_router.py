"""斜杠命令参数解析、合法性验证与分派。"""

from __future__ import annotations

import shlex

from autopatch_j.cli import command_handlers
from autopatch_j.cli import commands
from autopatch_j.cli import render


class CommandRouter:
    """斜杠命令路由器。

    只负责解析 `/xxx` 并把它分发给 CommandHandlers；具体命令行为不放在这里。
    """

    def __init__(
        self,
        handlers: command_handlers.CommandHandlers,
        renderer: render.CliRenderer,
    ) -> None:
        self.handlers = handlers
        self.renderer = renderer

    def handle_command(self, raw_cmd: str) -> None:
        """按注册信息解析和分派命令；无效参数只报告错误。"""
        try:
            parts = shlex.split(raw_cmd)
        except ValueError as exc:
            self.renderer.print_error(f"命令解析失败：{exc}")
            return
        if not parts:
            return

        cmd = parts[0].lower()

        command = commands.CLI_COMMAND_BY_NAME.get(cmd)
        if command is None:
            self.renderer.print_error(f"未知命令：{cmd}")
            return

        args = parts[1:]
        if args and not command.accepts_arguments:
            self.renderer.print_error(f"命令 {cmd} 不接受参数")
            return

        getattr(self.handlers, command.handler_name)(args)
