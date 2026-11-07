"""只注册项目业务工具，schema、参数验证与纠错由原生 SDK 管理。"""

from pydantic_ai import tools
from pydantic_ai import toolsets

from autopatch_j.agent import run_context
from autopatch_j.tools import names
from autopatch_j.tools import project


def build_toolset(
    context: run_context.AgentRunContext,
) -> toolsets.FunctionToolset[run_context.AgentRunContext]:
    business = project.ProjectTools(context)
    memory_names = {
        "search_memory",
        "read_memory",
        "write_memory",
        "delete_memory",
    }
    return toolsets.FunctionToolset(
        [
            tools.Tool(
                getattr(business, name.value),
                name=name.value,
                sequential=True,
            )
            for name in names.FunctionToolName
            if name.value not in memory_names
        ]
    )
