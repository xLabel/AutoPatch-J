"""把业务处理器交给官方 Toolset，SDK 负责 schema 与派发。"""

from pydantic_ai import tools
from pydantic_ai import toolsets

from autopatch_j.agent import session
from autopatch_j.tools import names
from autopatch_j.tools.function_calls import get_finding_detail
from autopatch_j.tools.function_calls import propose_patch
from autopatch_j.tools.function_calls import read_source_block
from autopatch_j.tools.function_calls import read_source_context
from autopatch_j.tools.function_calls import read_source_file
from autopatch_j.tools.function_calls import revise_patch
from autopatch_j.tools.function_calls import search_symbols


def build_toolset(
    context: session.AgentSession,
    allowed: tuple[names.FunctionToolName, ...],
) -> toolsets.FunctionToolset[session.AgentSession]:
    """仅注册当前任务可用的业务函数，不实现第二套执行器。"""
    candidates = [
        tools.Tool(
            get_finding_detail.GetFindingDetailTool(context).execute,
            name=names.FunctionToolName.GET_FINDING_DETAIL.value,
            sequential=True,
        ),
        tools.Tool(
            search_symbols.SearchSymbolsTool(context).execute,
            name=names.FunctionToolName.SEARCH_SYMBOLS.value,
            sequential=True,
        ),
        tools.Tool(
            read_source_file.ReadSourceFileTool(context).execute,
            name=names.FunctionToolName.READ_SOURCE_FILE.value,
            sequential=True,
        ),
        tools.Tool(
            read_source_block.ReadSourceBlockTool(context).execute,
            name=names.FunctionToolName.READ_SOURCE_BLOCK.value,
            sequential=True,
        ),
        tools.Tool(
            read_source_context.ReadSourceContextTool(context).execute,
            name=names.FunctionToolName.READ_SOURCE_CONTEXT.value,
            sequential=True,
        ),
        tools.Tool(
            propose_patch.ProposePatchTool(context).execute,
            name=names.FunctionToolName.PROPOSE_PATCH.value,
            sequential=True,
        ),
        tools.Tool(
            revise_patch.RevisePatchTool(context).execute,
            name=names.FunctionToolName.REVISE_PATCH.value,
            sequential=True,
        ),
    ]
    permitted = {name.value for name in allowed}
    return toolsets.FunctionToolset(
        [tool for tool in candidates if tool.name in permitted]
    )
