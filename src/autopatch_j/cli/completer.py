"""项目路径、Java 索引和已注册命令的交互补全。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
import re
from typing import Callable
from typing import Iterable
from typing import override

from prompt_toolkit import completion
from prompt_toolkit import document as prompt_toolkit_document
from prompt_toolkit.application import current

from autopatch_j.cli import commands
from autopatch_j.core.project import symbols


class AutoPatchCompleter(completion.Completer):
    """prompt_toolkit 补全器。

    职责边界：
    1. 输入 '/' 时补全系统命令。
    2. 输入 '@' 时基于本地索引补全文件、目录。
    3. 只补全输入线索，不选择任务或授权写入范围。
    """

    def __init__(
        self,
        search_func: Callable[[str], list[symbols.SymbolIndexEntry]],
        on_error: Callable[[str, Exception], Awaitable[None]] | None = None,
    ) -> None:
        self.search_func = search_func
        self.on_error = on_error
        self.path_completion_failed = False
        # 预编译正则，支持双前缀识别
        self.mention_pattern = re.compile(r"@[^\s@]*")
        self.command_pattern = re.compile(r"/[\w]*")

        self.commands = {
            command.name: command.completion_description
            for command in commands.CLI_COMMANDS
            if command.show_in_completion
        }
        self.subcommands = {
            command.name: {
                subcommand.name: subcommand.completion_description
                for subcommand in command.subcommands
            }
            for command in commands.CLI_COMMANDS
            if command.subcommands
        }

    @override
    async def get_completions_async(
        self,
        document: prompt_toolkit_document.Document,
        complete_event: completion.CompleteEvent,
    ) -> AsyncGenerator[completion.Completion]:
        """隔离后台补全故障，不结束原生 prompt 或丢弃用户输入。"""
        if document.text_before_cursor.startswith("/"):
            for candidate in self._command_completions(document):
                yield candidate
            return
        mention = self._path_mention(document, complete_event)
        if not mention:
            return
        # 原生 buffer 丢弃过期候选；故障标记和回调也必须绑定原输入会话。
        app = current.get_app_or_none()
        buffer = app.current_buffer if app is not None else None
        state = buffer.complete_state if buffer is not None else None

        def is_current() -> bool:
            return app is None or (
                app.is_running
                and buffer is not None
                and buffer.document == document
                and buffer.complete_state is state
            )

        try:
            results = await asyncio.to_thread(self.search_func, mention[1:])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if not is_current():
                return
            self.path_completion_failed = True
            if self.on_error is None:
                raise
            await self.on_error("路径补全", exc)
            return
        if not is_current():
            return
        self.path_completion_failed = False
        for candidate in self._path_completions(mention, results):
            yield candidate

    @override
    def get_completions(
        self,
        document: prompt_toolkit_document.Document,
        complete_event: completion.CompleteEvent,
    ) -> Iterable[completion.Completion]:
        """按游标返回已注册命令或文件、目录候选；不把类方法作为范围入口。"""
        if document.text_before_cursor.startswith("/"):
            yield from self._command_completions(document)
            return
        mention = self._path_mention(document, complete_event)
        if mention:
            results = self.search_func(mention[1:])
            self.path_completion_failed = False
            yield from self._path_completions(mention, results)

    def _command_completions(
        self,
        document: prompt_toolkit_document.Document,
    ) -> Iterable[completion.Completion]:
        text = document.text_before_cursor

        # --- 场景 A: 系统指令补全 (/) ---
        if text.startswith("/"):
            command_name, separator, argument_text = text.partition(" ")
            nested = self.subcommands.get(command_name.lower())
            if separator and nested is not None:
                query = argument_text.lstrip().lower()
                if " " in query:
                    return
                for subcommand, description in nested.items():
                    if subcommand.startswith(query):
                        yield completion.Completion(
                            subcommand,
                            start_position=-len(query),
                            display=f"{command_name.lower()} {subcommand}",
                            display_meta=description,
                        )
                return

            cmd_match = self.command_pattern.search(text)
            if cmd_match:
                query = cmd_match.group(0).lower()
                for cmd, desc in self.commands.items():
                    if cmd.startswith(query):
                        # '/' 已经在输入框里，补全时只替换其后的命令主体，避免出现 //init
                        command_body = cmd[1:]
                        typed_body = query[1:]
                        yield completion.Completion(
                            command_body,
                            start_position=-len(typed_body),
                            display=cmd,
                            display_meta=desc,
                        )
            return

    def _path_mention(
        self,
        document: prompt_toolkit_document.Document,
        complete_event: completion.CompleteEvent,
    ) -> str:
        mention = document.get_word_before_cursor(pattern=self.mention_pattern)
        if not mention.startswith("@") or (
            self.path_completion_failed
            and not complete_event.completion_requested
        ):
            return ""
        return mention

    @staticmethod
    def _path_completions(
        mention: str,
        results: list[symbols.SymbolIndexEntry],
    ) -> Iterable[completion.Completion]:
        for entry in results:
            if entry.kind in {"file", "dir"}:
                yield completion.Completion(
                    entry.path,
                    start_position=-len(mention) + 1,
                    display=entry.name,
                    display_meta=f"{entry.kind} | {entry.path}",
                )
