"""从 Java 索引定位范围内的符号候选，不推断源码事实。"""

from __future__ import annotations

from typing import Annotated

import pydantic

from autopatch_j.tools import contract
from autopatch_j.tools import names


class SearchSymbolsTool(contract.BusinessTool):
    """查询 Java 符号索引。

    只返回候选位置；需要源码内容时继续调用 read_source_block。
    """

    name = names.FunctionToolName.SEARCH_SYMBOLS.value

    def execute(
        self,
        query: Annotated[
            str,
            pydantic.Field(
                description="要搜索的类名、方法名、接口名或文件名关键词。",
                strict=True,
            ),
        ],
    ) -> contract.ToolExecutionResult:
        """查询 Java 符号索引。

        Returns:
            焦点内的有限符号候选；候选不替代当前源码证据。
        """
        context = self.context
        symbol_indexer = context.symbol_indexer
        results = [
            entry
            for entry in symbol_indexer.search(query, limit=10)
            if context.is_path_in_focus(entry.path)
        ]
        if not results:
            return contract.ToolExecutionResult(
                status="ok",
                message=f"未找到与 '{query}' 相关的符号。",
                summary=f"未找到符号: {query}",
            )
        header = f"为您找到以下与 '{query}' 相关的匹配项：\n"
        rows = [
            f"{index}. [{entry.kind}] {entry.name} -> "
            f"{entry.path}:{entry.line}\n"
            for index, entry in enumerate(results, 1)
        ]
        msg = header + "".join(rows)
        return contract.ToolExecutionResult(
            status="ok",
            message=msg,
            summary=f"已定位符号: {query}",
            payload={"paths": [entry.path for entry in results]},
        )
