"""模型可见的工具名称，独立于 Python 方法命名。"""

from __future__ import annotations

import enum


class FunctionToolName(str, enum.Enum):
    """暴露给模型的 function tool 名称。"""

    GET_FINDING_DETAIL = "get_finding_detail"
    READ_SOURCE_FILE = "read_source_file"
    READ_SOURCE_BLOCK = "read_source_block"
    READ_SOURCE_CONTEXT = "read_source_context"
    SEARCH_SYMBOLS = "search_symbols"
    MEMORY_SEARCH = "search_memory"
    MEMORY_READ = "read_memory"
    MEMORY_WRITE = "write_memory"
    MEMORY_DELETE = "delete_memory"
    PROPOSE_PATCH = "propose_patch"
    REVISE_PATCH = "revise_patch"


ToolNameLike = FunctionToolName | str


def tool_name_value(name: ToolNameLike) -> str:
    """返回模型协议使用的工具名，保留自由字符串的含义。"""
    if isinstance(name, FunctionToolName):
        return name.value
    return name
