"""读取目标行所在 Java 语法块，复用请求内缓存。"""

from __future__ import annotations

from typing import Annotated

import pydantic

from autopatch_j.tools import contract
from autopatch_j.tools import names
from autopatch_j.tools.function_calls import _source_reading_base


class ReadSourceBlockTool(_source_reading_base.SourceReadToolBase):
    """读取指定行所在的 Java 方法、构造器或类型代码块。"""

    name = names.FunctionToolName.READ_SOURCE_BLOCK.value

    def execute(
        self,
        path: Annotated[
            str,
            pydantic.Field(
                description="仓库内 Java 文件相对路径。", strict=True
            ),
        ],
        line: Annotated[
            int,
            pydantic.Field(
                description="1-based 行号；可以是声明行，也可以是方法体内部行。",
                strict=True,
            ),
        ],
    ) -> contract.ToolExecutionResult:
        """读取指定行所在的 Java 方法、构造器或类型代码块。

        Returns:
            目标位置所属 Java 语法块或明确标记的行窗降级结果。
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
        code = target.context.code_fetcher.fetch_block_source(
            target.path, normalized_line
        )
        error = self._error_if_source_failed(code, target.path)
        if error is not None:
            return error
        result = contract.ToolExecutionResult(
            status="ok",
            message=f"已成功加载源代码块 [路径: {target.path}"
            f", 行: {normalized_line}"
            f"]:\n\n```java\n{code}"
            f"\n```",
            summary=f"已读取源代码块: {target.path}:{normalized_line}",
        )
        self._persist_cached(
            target.context, target.path, line=normalized_line, result=result
        )
        return result
