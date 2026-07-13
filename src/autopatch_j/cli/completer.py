from __future__ import annotations

import re
from typing import Iterable, Callable
from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.document import Document
from autopatch_j.cli.commands import CLI_COMMANDS
from autopatch_j.core.project import SymbolIndexEntry


class AutoPatchCompleter(Completer):
    """
    prompt_toolkit 补全器。

    职责边界：
    1. 输入 '/' 时补全系统命令。
    2. 输入 '@' 时基于本地索引补全文件、目录、类或方法。
    3. 不解析最终工作范围；@mention 到 CodeScope 的转换由 ScopeResolver 完成。
    """

    def __init__(self, search_func: Callable[[str], list[SymbolIndexEntry]]) -> None:
        self.search_func = search_func
        # 预编译正则，支持双前缀识别
        self.mention_pattern = re.compile(r'@[\w\.]*')
        self.command_pattern = re.compile(r'/[\w]*')
        
        self.commands = {
            command.name: command.completion_description
            for command in CLI_COMMANDS
            if command.show_in_completion
        }
        self.subcommands = {
            command.name: {
                subcommand.name: subcommand.completion_description
                for subcommand in command.subcommands
            }
            for command in CLI_COMMANDS
            if command.subcommands
        }

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor

        # --- 场景 A: 系统指令补全 (/) ---
        if text.startswith('/'):
            command_name, separator, argument_text = text.partition(" ")
            nested = self.subcommands.get(command_name.lower())
            if separator and nested is not None:
                query = argument_text.lstrip().lower()
                if " " in query:
                    return
                for subcommand, description in nested.items():
                    if subcommand.startswith(query):
                        yield Completion(
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
                        yield Completion(
                            command_body,
                            start_position=-len(typed_body),
                            display=cmd,
                            display_meta=desc
                        )
            return

        # --- 场景 B: 代码上下文补全 (@) ---
        mention_match = document.get_word_before_cursor(pattern=self.mention_pattern)
        if mention_match.startswith('@'):
            query = mention_match[1:]
            results = self.search_func(query)

            for entry in results:
                if entry.kind not in {"file", "dir"}:
                    continue
                display_meta = f"{entry.kind} | {entry.path}"
                display_text = f"{entry.name}"
                
                yield Completion(
                    entry.name,
                    start_position=-len(mention_match) + 1,
                    display=display_text,
                    display_meta=display_meta
                )
