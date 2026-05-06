from __future__ import annotations

from autopatch_j.agent.session import AgentSession
from autopatch_j.llm.dialect import ToolCall
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.finding_retriever_tool import FindingRetrieverTool
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.patch_revision_tool import PatchRevisionTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool


class ToolExecutor:
    """
    Agent 工具注册与执行边界。

    它只负责把 LLM tool call 映射到本地 Tool，并执行白名单校验和异常归一化；
    是否推进任务、是否入队补丁、如何展示输出，都由更上层工作流决定。
    """

    def __init__(self, session: AgentSession) -> None:
        self.available_tools: dict[str, Tool] = {
            tool.name: tool
            for tool in [
                PatchProposalTool(session),
                PatchRevisionTool(session),
                SymbolSearchTool(session),
                SourceReaderTool(session),
                FindingRetrieverTool(session),
            ]
        }

    def execute(self, call: ToolCall, allowed_tool_names: set[str]) -> ToolResult:
        if call.name not in allowed_tool_names:
            return ToolResult(
                status="error",
                message=f"当前任务未开放工具：{call.name}",
            )

        tool = self.available_tools.get(call.name)
        if tool is None:
            return ToolResult(status="error", message=f"未找到工具：{call.name}")

        try:
            return tool.execute(**call.arguments)
        except Exception as exc:
            return ToolResult(status="error", message=f"执行异常：{exc}")
