"""供 ReAct Agent 通过 function_call 调用的工具适配层。"""

from autopatch_j.tools.catalog import FunctionToolCatalog
from autopatch_j.tools.contract import (
    FunctionTool,
    FunctionToolParameter,
    FunctionToolSpec,
    ToolExecutionResult,
    ToolRuntimeContext,
    build_function_parameters,
)
from autopatch_j.tools.names import FunctionToolName

__all__ = [
    "FunctionTool",
    "FunctionToolCatalog",
    "FunctionToolName",
    "FunctionToolParameter",
    "FunctionToolSpec",
    "ToolExecutionResult",
    "ToolRuntimeContext",
    "build_function_parameters",
]
