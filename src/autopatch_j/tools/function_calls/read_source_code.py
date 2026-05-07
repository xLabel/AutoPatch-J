from __future__ import annotations

from pathlib import Path

from autopatch_j.core.project import SymbolIndexEntry, UnsafeRepoPathError, resolve_repo_path, to_repo_relative_path
from autopatch_j.tools.contract import FunctionTool, FunctionToolSpec, ToolExecutionResult
from autopatch_j.tools.names import FunctionToolName


class ReadSourceCodeTool(FunctionTool):
    """
    读取当前仓库中的真实源码。

    这是 propose_patch/revise_patch 前确认 old_string 的前置工具。
    """

    spec = FunctionToolSpec(
        name=FunctionToolName.READ_SOURCE_CODE,
        description=(
            "读取仓库内指定 Java 文件或符号附近的源码。用于确认真实代码、定位 old_string，"
            "尤其是在调用 propose_patch 或 revise_patch 之前。只读取源码，不生成补丁。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "仓库内文件相对路径。"},
                "symbol": {
                    "type": "string",
                    "description": "可选，类名或方法名。提供后会尝试读取该符号对应的完整定义块。",
                },
                "line": {
                    "type": "integer",
                    "description": "可选，1-based 起始行号。通常来自 finding 详情或 search_symbols 结果。",
                },
            },
            "required": ["path"],
        },
    )

    def execute(self, path: str, symbol: str | None = None, line: int | None = None) -> ToolExecutionResult:
        context = self.require_context()
        code_fetcher = context.code_fetcher
        symbol_indexer = context.symbol_indexer

        try:
            full_path = resolve_repo_path(context.repo_root, path)
            path = to_repo_relative_path(context.repo_root, full_path)
        except UnsafeRepoPathError as exc:
            return ToolExecutionResult(status="error", message=f"读取失败：{exc}", summary=f"读取失败: {path}")

        if not full_path.exists():
            filename = Path(path).name
            results = symbol_indexer.search(filename, limit=1)
            if results:
                path = results[0].path

        if not context.is_path_in_focus(path):
            allowed = ", ".join(context.focus_paths)
            return ToolExecutionResult(
                status="error",
                message=f"焦点约束阻止越界读取：{path}\n不在当前允许范围内。允许路径：{allowed}",
                summary=f"读取越界: {path}",
            )

        cached_result = context.fetch_cached_source_read(path=path, symbol=symbol, line=line)
        if cached_result is not None:
            return cached_result

        if line:
            entry = SymbolIndexEntry(
                path=path,
                name=symbol or "targeted_code",
                kind="method" if symbol else "file",
                line=line,
            )
        else:
            entry = SymbolIndexEntry(path=path, name=path, kind="file", line=0)
        code = code_fetcher.fetch_entry_source(entry)

        if code.startswith("错误"):
            return ToolExecutionResult(status="error", message=code, summary=f"读取失败: {path}")

        result = ToolExecutionResult(
            status="ok",
            message=f"已成功加载源代码 [路径: {path}]:\n\n```java\n{code}\n```",
            summary=f"已读取源代码: {path}",
        )
        context.persist_cached_source_read(path=path, symbol=symbol, line=line, result=result)
        return result
