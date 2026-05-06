from __future__ import annotations

from pathlib import Path

from autopatch_j.core.project import UnsafeRepoPathError, resolve_repo_path, to_repo_relative_path
from autopatch_j.core.project import SymbolIndexEntry
from autopatch_j.tools.base import Tool, ToolResult


class SourceReaderTool(Tool):
    """
    Java 源码读取工具。

    根据路径、符号或行号读取当前工作区的真实源码，并维护本轮 ReAct 的读取缓存；
    propose/revise 补丁前应先通过它确认精确 old_string。
    """

    name = "read_source_code"
    description = (
        "读取指定路径下的源码内容。支持自动提取完整的类或方法定义。"
        "注意：在提出或修订任何补丁 (propose_patch/revise_patch) 之前，你必须通过此工具获取目标代码的最准确内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件的相对路径。"},
            "symbol": {
                "type": "string",
                "description": "（可选）类名或方法名。若提供，系统将尝试智能定位其完整定义块。",
            },
            "line": {
                "type": "integer",
                "description": "（可选）起始行号（1-based）。通常配合 symbol 使用，帮助精确定。",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str, symbol: str | None = None, line: int | None = None) -> ToolResult:
        context = self.require_context()
        code_fetcher = context.code_fetcher
        symbol_indexer = context.symbol_indexer

        try:
            full_path = resolve_repo_path(context.repo_root, path)
            path = to_repo_relative_path(context.repo_root, full_path)
        except UnsafeRepoPathError as exc:
            return ToolResult(status="error", message=f"读取失败：{exc}", summary=f"读取失败: {path}")

        if not full_path.exists():
            filename = Path(path).name
            results = symbol_indexer.search(filename, limit=1)
            if results:
                path = results[0].path

        if not context.is_path_in_focus(path):
            allowed = ", ".join(context.focus_paths)
            return ToolResult(
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
            return ToolResult(status="error", message=code, summary=f"读取失败: {path}")

        result = ToolResult(
            status="ok",
            message=f"已成功加载源代码 [路径: {path}]：\n\n```java\n{code}\n```",
            summary=f"已读取源代码: {path}",
        )
        context.persist_cached_source_read(path=path, symbol=symbol, line=line, result=result)
        return result
