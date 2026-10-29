"""工作流需要的显式依赖与输入路由决策。"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses

from autopatch_j.cli import agent_stream_presenter
from autopatch_j.cli import command_handlers as cli_command_handlers
from autopatch_j.cli import command_router as cli_command_router
from autopatch_j.cli import render
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.cli import summary_provider as cli_summary_provider
from autopatch_j.core.domain import intent as domain_intent


@dataclasses.dataclass(slots=True)
class InputRoutingDecision:
    """单次用户输入经过会话路由和意图识别后的决策。

    route 描述当前输入是命令、新任务还是继续审核；intent 描述后续要进入的业务工作流。
    该对象只承载分类结果，不执行任何副作用。
    """

    route: domain_intent.ConversationRoute
    intent: domain_intent.IntentType | None


@dataclasses.dataclass(slots=True)
class WorkflowDependencies:
    """CLI workflow 层的显式依赖集合。

    workflow 不直接依赖 AutoPatchCli；它们只通过这里拿到 runtime、runner、
    summary provider、renderer 和命令处理器。
    """

    runtime: cli_runtime.CliRuntime
    agent_runner: agent_stream_presenter.AgentStreamPresenter
    summary_provider: cli_summary_provider.CliSummaryProvider
    renderer: render.CliRenderer
    command_router: cli_command_router.CommandRouter
    command_handlers: cli_command_handlers.CommandHandlers
    debug_mode: Callable[[], bool]
