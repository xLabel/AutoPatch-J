from __future__ import annotations

from typing import Any

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

        if cmd == "/init":
            self.handlers.handle_init()
        elif cmd == "/status":
            self.handlers.handle_status()
        elif cmd == "/reindex":
            self.handlers.handle_reindex()
        elif cmd == "/scanner":
            self.handlers.handle_scanners()
        elif cmd == "/doctor":
            self.handlers.handle_doctor()
        elif cmd == "/reset":
            self.handlers.handle_reset()
        elif cmd == "/help":
            self.handlers.handle_help()
        elif cmd == "/quit":
            self.handlers.host.request_exit()
        else:
            self.renderer.print_error(f"未知命令：{cmd}")
