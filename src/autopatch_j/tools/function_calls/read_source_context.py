from __future__ import annotations

from typing import Annotated

from autopatch_j.tools.contract import (
    ToolArg,
    ToolExecutionResult,
    function_tool,
)
from autopatch_j.tools.function_calls._source_reading_base import SourceReadToolBase
from autopatch_j.tools.names import FunctionToolName


class ReadSourceContextTool(SourceReadToolBase):
    """读取指定行附近的固定窗口源码上下文。"""

    @function_tool(
        name=FunctionToolName.READ_SOURCE_CONTEXT,
        description=(
            "读取指定行附近的固定窗口源码上下文：默认包含目标行前 20 行和后 80 行。"
            "适合根据 finding 行号确认局部证据和 old_string；不需要模型传 end_line 或窗口大小。"
        ),
    )
    def execute(
        self,
        path: Annotated[str, ToolArg("仓库内文件相对路径。")],
        line: Annotated[int, ToolArg("1-based 目标行号，通常来自 finding 或 search_symbols。")],
    ) -> ToolExecutionResult:
        normalized_line = self._normalize_positive_line(line)
        if isinstance(normalized_line, ToolExecutionResult):
            return normalized_line

        target = self._prepare_target(path)
        if isinstance(target, ToolExecutionResult):
            return target

        cached_result = self._fetch_cached(target.context, target.path, line=normalized_line)
        if cached_result is not None:
            return cached_result

        source_range = target.context.code_fetcher.fetch_context_source(target.path, normalized_line)
        if source_range.total_lines == 0:
            return ToolExecutionResult(
                status="error",
                message=f"读取失败：找不到文件或文件为空：{target.path}",
                summary=f"读取失败: {target.path}",
            )

        result = ToolExecutionResult(
            status="ok",
            message=(
                f"已成功加载源代码上下文 [路径: {target.path}, 目标行: {normalized_line}, "
                f"实际范围: {source_range.start_line}-{source_range.end_line}]:\n\n"
                f"```java\n{source_range.code}\n```"
            ),
            summary=f"已读取源代码上下文: {target.path}:{source_range.start_line}-{source_range.end_line}",
            payload={
                "path": target.path,
                "target_line": normalized_line,
                "start_line": source_range.start_line,
                "end_line": source_range.end_line,
            },
        )
        self._persist_cached(target.context, target.path, line=normalized_line, result=result)
        return result
