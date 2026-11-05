"""只注册项目业务工具，schema、参数验证与纠错由原生 SDK 管理。"""

from pydantic_ai import tools
from pydantic_ai import toolsets

from autopatch_j.agent import session
from autopatch_j.tools import names
from autopatch_j.tools import project


def build_toolset(
    context: session.AgentSession,
) -> toolsets.FunctionToolset[session.AgentSession]:
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
                max_retries=2,
            )
            for name in names.FunctionToolName
            if name.value not in memory_names
        ]
    )
