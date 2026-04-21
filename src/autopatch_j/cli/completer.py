from __future__ import annotations

import re
from typing import Iterable, Callable
from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.document import Document
from autopatch_j.core.index_service import IndexEntry


class MentionCompleter(Completer):
    """
    智能补全器 (Interaction Layer)
    职责：基于 ProjectIndexer 的查询结果提供 @ 符号的实时自动补全。
    """

    def __init__(self, search_func: Callable[[str], list[IndexEntry]]) -> None:
        self.search_func = search_func
        # 🚀 预编译正则，防止 prompt_toolkit 报错
        self.mention_pattern = re.compile(r'@[\w\.]*')

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        # 使用预编译的正则对象
        text_before_cursor = document.get_word_before_cursor(pattern=self.mention_pattern)
        if not text_before_cursor.startswith('@'):
            return

        query = text_before_cursor[1:]
        results = self.search_func(query)

        for entry in results:
            # 根据符号类型选择图标
            icon = {
                "file": "📄",
                "dir": "📁",
                "class": "🏛️",
                "method": "⚙️"
            }.get(entry.kind, "•")

            display_meta = f"{entry.kind} | {entry.path}"
            display_text = f"{icon} {entry.name}"
            
            # 对于类和方法，补全后保留其完整名称，以便后续 fetcher 识别
            yield Completion(
                entry.name,
                start_position=-len(text_before_cursor) + 1, # 保留 @ 符号
                display=display_text,
                display_meta=display_meta
            )
