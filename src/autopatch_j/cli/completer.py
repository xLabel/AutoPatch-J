"""项目路径、Java 索引和已注册命令的交互补全。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import Awaitable
import re
from typing import Callable
from typing import Iterable
from typing import override

from prompt_toolkit import completion
from prompt_toolkit import document as prompt_toolkit_document

from autopatch_j.cli import commands
from autopatch_j.core.project import symbols


class AutoPatchCompleter(completion.Completer):
    """prompt_toolkit 补全器。

    职责边界：
    1. 输入 '/' 时补全系统命令。
    2. 输入 '@' 时基于本地索引补全文件、目录、类或方法。
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
        try:
            for candidate in self.get_completions(document, complete_event):
                yield candidate
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.path_completion_failed = True
            if self.on_error is None:
                raise
            await self.on_error("路径补全", exc)

    @override
    def get_completions(
        self,
        document: prompt_toolkit_document.Document,
        complete_event: completion.CompleteEvent,
    ) -> Iterable[completion.Completion]:
        """按游标返回已注册命令或文件、目录候选；不把类方法作为范围入口。"""
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

        # --- 场景 B: 代码上下文补全 (@) ---
        mention_match = document.get_word_before_cursor(
            pattern=self.mention_pattern
        )
        if mention_match.startswith("@"):
            if (
                self.path_completion_failed
                and not complete_event.completion_requested
            ):
                return
            query = mention_match[1:]
            results = self.search_func(query)
            self.path_completion_failed = False

            for entry in results:
                if entry.kind not in {"file", "dir"}:
                    continue
                display_meta = f"{entry.kind} | {entry.path}"
                display_text = f"{entry.name}"

                yield completion.Completion(
                    entry.path,
                    start_position=-len(mention_match) + 1,
                    display=display_text,
                    display_meta=display_meta,
                )
