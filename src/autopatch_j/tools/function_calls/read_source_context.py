"""读取 1-based 行号前后的有界源码窗口。"""

from __future__ import annotations

from typing import Annotated

import pydantic

from autopatch_j.tools import contract
from autopatch_j.tools import names
from autopatch_j.tools.function_calls import _source_reading_base


class ReadSourceContextTool(_source_reading_base.SourceReadToolBase):
    """读取指定行附近的固定窗口源码上下文。"""

    name = names.FunctionToolName.READ_SOURCE_CONTEXT.value

    def execute(
        self,
        path: Annotated[
            str, pydantic.Field(description="仓库内文件相对路径。", strict=True)
        ],
        line: Annotated[
            int,
            pydantic.Field(
                description="1-based 目标行号，通常来自 finding 或 search_symbols。",
                strict=True,
            ),
        ],
    ) -> contract.ToolExecutionResult:
        """读取指定行附近的固定窗口源码上下文。

        Returns:
            目标行前后的源码窗口、实际行号范围或拒绝原因。
        """
        normalized_line = self._normalize_positive_line(line)
        if isinstance(normalized_line, contract.ToolExecutionResult):
            return normalized_line
        target = self._prepare_target(path)
        if isinstance(target, contract.ToolExecutionResult):
            return target
        cached_result = self._fetch_cached(
            target.context, target.path, line=normalized_line
        )
        if cached_result is not None:
            return cached_result
        source_range = target.context.code_fetcher.fetch_context_source(
            target.path, normalized_line
        )
        if source_range.total_lines == 0:
            return contract.ToolExecutionResult(
                status="error",
                message=f"读取失败：找不到文件或文件为空：{target.path}",
                summary=f"读取失败: {target.path}",
            )
        result = contract.ToolExecutionResult(
            status="ok",
            message=f"已成功加载源代码上下文 [路径: {target.path}"
            f", 目标行: {normalized_line}"
            f", 实际范围: {source_range.start_line}"
            f"-{source_range.end_line}"
            f"]:\n\n```java\n{source_range.code}"
            f"\n```",
            summary=f"已读取源代码上下文: {target.path}"
            f":{source_range.start_line}"
            f"-{source_range.end_line}",
            payload={
                "path": target.path,
                "target_line": normalized_line,
                "start_line": source_range.start_line,
                "end_line": source_range.end_line,
            },
        )
        self._persist_cached(
            target.context, target.path, line=normalized_line, result=result
        )
        return result
