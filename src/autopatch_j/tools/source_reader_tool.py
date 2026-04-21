from __future__ import annotations

from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.core.service_context import ServiceContext


class SourceReaderTool(Tool):
    """
    源码读取工具 (Reader)
    职责：从磁盘获取指定路径或符号的真实代码内容。
    """
    name = "read_source_code"
    description = (
        "读取指定路径下的源代码内容。支持自动提取完整的类或方法定义。 "
        "注意：在提出任何补丁提案 (propose_patch) 之前，你必须通过此工具获取目标代码的最准确内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件的相对路径。"},
            "symbol": {"type": "string", "description": "（可选）类名或方法名。若提供，系统将尝试智能定位其完整定义块。"},
            "line": {"type": "integer", "description": "（可选）起始行号（1-based）。通常配合 symbol 使用，帮助精确定位。"}
        },
        "required": ["path"]
    }

    def execute(self, context: ServiceContext, path: str, symbol: str | None = None, line: int | None = None) -> ToolResult:
        if line:
            from autopatch_j.core.index_service import IndexEntry
            entry = IndexEntry(path=path, name=symbol or "targeted_code", kind="method" if symbol else "file", line=line)
            code = context.fetcher.fetch_by_index_entry(entry)
        else:
            from autopatch_j.core.index_service import IndexEntry
            entry = IndexEntry(path=path, name=path, kind="file", line=0)
            code = context.fetcher.fetch_by_index_entry(entry)

        if code.startswith("错误"):
            return ToolResult(status="error", message=code)

        return ToolResult(status="ok", message=f"已成功加载源代码 [路径: {path}]：\n\n```java\n{code}\n```")
