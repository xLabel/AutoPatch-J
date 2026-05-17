from __future__ import annotations

from autopatch_j.tools.contract import FunctionToolSpec, ToolExecutionResult
from autopatch_j.tools.function_calls.source_reading import SourceReadToolBase
from autopatch_j.tools.names import FunctionToolName


class ReadSourceBlockTool(SourceReadToolBase):
    """读取指定行所在的 Java 方法、构造器或类型代码块。"""

    spec = FunctionToolSpec(
        name=FunctionToolName.READ_SOURCE_BLOCK,
        description=(
            "读取指定行所在的 Java 方法、构造器、类、接口、record 或 enum 完整代码块。"
            "适合接在 search_symbols 的 path:line 结果之后，或在修改整个方法/类前确认 old_string。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "仓库内 Java 文件相对路径。"},
                "line": {"type": "integer", "description": "1-based 行号；可以是声明行，也可以是方法体内部行。"},
            },
            "required": ["path", "line"],
        },
    )

    def execute(self, path: str, line: int) -> ToolExecutionResult:
        normalized_line = self._normalize_positive_line(line)
        if isinstance(normalized_line, ToolExecutionResult):
            return normalized_line

        target = self._prepare_target(path)
        if isinstance(target, ToolExecutionResult):
            return target

        cached_result = self._fetch_cached(target.context, target.path, line=normalized_line)
        if cached_result is not None:
            return cached_result

        code = target.context.code_fetcher.fetch_block_source(target.path, normalized_line)
        error = self._error_if_source_failed(code, target.path)
        if error is not None:
            return error

        result = ToolExecutionResult(
            status="ok",
            message=f"已成功加载源代码块 [路径: {target.path}, 行: {normalized_line}]:\n\n```java\n{code}\n```",
            summary=f"已读取源代码块: {target.path}:{normalized_line}",
        )
        self._persist_cached(target.context, target.path, line=normalized_line, result=result)
        return result
