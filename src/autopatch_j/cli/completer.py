from __future__ import annotations

import re
from typing import Iterable, Callable
from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.document import Document
from autopatch_j.core.index_service import IndexEntry


class AutoPatchCompleter(Completer):
    """
    全能智能补全器 (Interaction Layer)
    职责：
    1. 基于 @ 符号提供代码索引补全。
    2. 基于 / 符号提供系统指令补全。
    """

    def __init__(self, search_func: Callable[[str], list[IndexEntry]]) -> None:
        self.search_func = search_func
        # 预编译正则，支持双前缀识别
        self.mention_pattern = re.compile(r'@[\w\.]*')
        self.command_pattern = re.compile(r'/[\w]*')
        
        # 定义所有可用指令及其图标
        self.commands = {
            "/init": "初始化项目环境",
            "/status": "查看系统状态",
            "/scanner": "扫描器管理看板",
            "/reindex": "刷新代码符号索引",
            "/help": "显示指令看板",
            "/quit": "退出程序",
            "/exit": "退出程序"
        }

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor

        # --- 场景 A: 系统指令补全 (/) ---
        if text.startswith('/'):
            cmd_match = self.command_pattern.search(text)
            if cmd_match:
                query = cmd_match.group(0).lower()
                for cmd, desc in self.commands.items():
                    if cmd.startswith(query):
                        yield Completion(
                            cmd,
                            start_position=-len(query),
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
                # 移除 Emoji 图标
                display_meta = f"{entry.kind} | {entry.path}"
                display_text = f"{entry.name}"

                yield Completion(
                    entry.name,
                    start_position=-len(mention_match) + 1,
                    display=display_text,
                    display_meta=display_meta
                )
