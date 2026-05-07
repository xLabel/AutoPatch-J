from __future__ import annotations

from autopatch_j.agent.session import AgentSession
from autopatch_j.llm.dialect import ToolCall
from autopatch_j.tools.catalog import FunctionToolCatalog
from autopatch_j.tools.contract import FunctionTool, ToolExecutionResult
from autopatch_j.tools.names import ToolNameLike, tool_name_value


class ToolExecutor:
    """
    Agent 工具执行边界。

    它只负责把 LLM tool call 映射到本地 function tool，并执行白名单校验和异常归一化；
    工具注册与 schema 导出由 FunctionToolCatalog 负责。
    """

    def __init__(self, session: AgentSession) -> None:
        self.catalog = FunctionToolCatalog.for_context(session)
        self.available_tools: dict[str, FunctionTool] = self.catalog.tools

    def execute(self, call: ToolCall, allowed_tool_names: set[ToolNameLike]) -> ToolExecutionResult:
        allowed_names = {tool_name_value(name) for name in allowed_tool_names}
        if call.name not in allowed_names:
            return ToolExecutionResult(
                status="error",
                message=f"当前任务未开放工具：{call.name}",
            )

        tool = self.catalog.get(call.name)
        if tool is None:
            return ToolExecutionResult(status="error", message=f"未找到工具：{call.name}")

        try:
            return tool.execute(**call.arguments)
        except Exception as exc:
            return ToolExecutionResult(status="error", message=f"执行异常：{exc}")
