"""受焦点和大小边界限制的源码全文读取。"""

from __future__ import annotations

from typing import Annotated

import pydantic

from autopatch_j.core.project import symbols
from autopatch_j.tools import contract
from autopatch_j.tools import names
from autopatch_j.tools.function_calls import _source_reading_base


class ReadSourceFileTool(_source_reading_base.SourceReadToolBase):
    """读取仓库内文件全文；用于 imports、字段和跨方法上下文取证。"""

    name = names.FunctionToolName.READ_SOURCE_FILE.value

    def execute(
        self,
        path: Annotated[
            str, pydantic.Field(description="仓库内文件相对路径。", strict=True)
        ],
    ) -> contract.ToolExecutionResult:
        """读取仓库内文件全文；用于 imports、字段和跨方法上下文取证。

        Returns:
            源码展示文本或拒绝原因；只读降级文本不能作为写入证据。
        """
        target = self._prepare_target(path)
        if isinstance(target, contract.ToolExecutionResult):
            return target
        cached_result = self._fetch_cached(
            target.context, target.path, line=None
        )
        if cached_result is not None:
            return cached_result
        code = target.context.code_fetcher.fetch_entry_source(
            symbols.SymbolIndexEntry(
                path=target.path, name=target.path, kind="file", line=0
            )
        )
        error = self._error_if_source_failed(code, target.path)
        if error is not None:
            return error
        result = contract.ToolExecutionResult(
            status="ok",
            message=f"已成功加载完整源代码 [路径: {target.path}]:\n\n```java\n{code}\n```",
            summary=f"已读取源代码全文: {target.path}",
        )
        self._persist_cached(
            target.context, target.path, line=None, result=result
        )
        return result
