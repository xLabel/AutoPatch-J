from __future__ import annotations

from enum import Enum


class FunctionToolName(str, Enum):
    """Names exposed to the LLM function_call interface."""

    GET_FINDING_DETAIL = "get_finding_detail"
    READ_SOURCE_CODE = "read_source_code"
    SEARCH_SYMBOLS = "search_symbols"
    PROPOSE_PATCH = "propose_patch"
    REVISE_PATCH = "revise_patch"


ToolNameLike = FunctionToolName | str


def tool_name_value(name: ToolNameLike) -> str:
    if isinstance(name, FunctionToolName):
        return name.value
    return name
