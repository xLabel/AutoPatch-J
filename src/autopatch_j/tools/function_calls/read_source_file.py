from __future__ import annotations

from typing import Annotated

from autopatch_j.core.project import SymbolIndexEntry
from autopatch_j.tools.contract import (
    ToolArg,
    ToolExecutionResult,
    function_tool,
)
from autopatch_j.tools.function_calls._source_reading_base import SourceReadToolBase
from autopatch_j.tools.names import FunctionToolName


class ReadSourceFileTool(SourceReadToolBase):
    """读取仓库内文件全文；用于 imports、字段和跨方法上下文取证。"""

    @function_tool(
        name=FunctionToolName.READ_SOURCE_FILE,
        description=(
            "读取仓库内指定文件的完整源码。仅在需要 imports、字段、类级上下文或跨方法关系时使用；"
            "如果只需要某个 finding 或符号附近的源码，优先使用 read_source_context 或 read_source_block。"
        ),
    )
    def execute(self, path: Annotated[str, ToolArg("仓库内文件相对路径。")]) -> ToolExecutionResult:
        target = self._prepare_target(path)
        if isinstance(target, ToolExecutionResult):
            return target

        cached_result = self._fetch_cached(target.context, target.path, line=None)
        if cached_result is not None:
            return cached_result

        code = target.context.code_fetcher.fetch_entry_source(
            SymbolIndexEntry(path=target.path, name=target.path, kind="file", line=0)
        )
        error = self._error_if_source_failed(code, target.path)
        if error is not None:
            return error

        result = ToolExecutionResult(
            status="ok",
            message=f"已成功加载完整源代码 [路径: {target.path}]:\n\n```java\n{code}\n```",
            summary=f"已读取源代码全文: {target.path}",
        )
        self._persist_cached(target.context, target.path, line=None, result=result)
        return result
