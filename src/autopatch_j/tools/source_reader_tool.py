from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autopatch_j.core.index_service import IndexEntry
from autopatch_j.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from autopatch_j.agent.agent import AutoPatchAgent


class SourceReaderTool(Tool):
    """
    源码读取工具 (Reader)
    职责：从磁盘获取指定路径或符号的真实代码内容。
    """

    name = "read_source_code"
    description = (
        "读取指定路径下的源码内容。支持自动提取完整的类或方法定义。"
        "注意：在提出任何补丁提案 (propose_patch) 之前，你必须通过此工具获取目标代码的最准确内容。"
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
        assert self.context is not None
        fetcher = self.context.fetcher

        full_path = self.context.repo_root / path
        if not full_path.exists():
            filename = Path(path).name
            results = self.context.indexer.search(filename, limit=1)
            if results:
                path = results[0].path

        if not self.context.is_path_in_focus(path):
            allowed = ", ".join(self.context.focus_paths)
            return ToolResult(
                status="error",
                message=f"焦点约束阻止越界读取：{path}\n不在当前允许范围内。允许路径：{allowed}",
            )

        cached_result = self.context.fetch_cached_source_read(path=path, symbol=symbol, line=line)
        if cached_result is not None:
            return cached_result

        if line:
            entry = IndexEntry(
                path=path,
                name=symbol or "targeted_code",
                kind="method" if symbol else "file",
                line=line,
            )
        else:
            entry = IndexEntry(path=path, name=path, kind="file", line=0)
        code = fetcher.fetch_entry_source(entry)

        if code.startswith("错误"):
            return ToolResult(status="error", message=code)

        result = ToolResult(
            status="ok",
            message=f"已成功加载源代码 [路径: {path}]：\n\n```java\n{code}\n```",
        )
        self.context.persist_cached_source_read(path=path, symbol=symbol, line=line, result=result)
        return result
