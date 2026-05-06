from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from autopatch_j.cli.agent_request_runner import AgentRequestRunner
from autopatch_j.cli.command_handlers import CommandHandlers
from autopatch_j.cli.command_router import CommandRouter
from autopatch_j.cli.render import CliRenderer
from autopatch_j.cli.runtime import CliRuntime
from autopatch_j.cli.summary_provider import CliSummaryProvider
from autopatch_j.core.domain import ConversationRoute, IntentType


@dataclass(slots=True)
class InputRouteDecision:
    """
    单次用户输入经过会话路由和意图识别后的决策。

    route 描述当前输入是命令、新任务还是继续审核；intent 描述后续要进入的业务工作流。
    该对象只承载分类结果，不执行任何副作用。
    """

    route: ConversationRoute
    intent: IntentType | None


@dataclass(slots=True)
class WorkflowServices:
    """
    CLI workflow 层的显式依赖集合。

    workflow 不直接依赖 AutoPatchCli；它们只通过这里拿到 runtime、runner、
    summary provider、renderer 和命令处理器。
    """

    runtime: CliRuntime
    agent_runner: AgentRequestRunner
    summary_provider: CliSummaryProvider
    renderer: CliRenderer
    command_router: CommandRouter
    command_handlers: CommandHandlers
    debug_mode: Callable[[], bool]
