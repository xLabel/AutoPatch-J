from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from autopatch_j.core.models import CodeScope, ConversationRoute, IntentType, PatchReviewItem


@dataclass(slots=True)
class ChatInputDecision:
    """
    单次用户输入经过会话路由和意图识别后的决策。

    route 描述当前输入是命令、新任务还是继续审核；intent 描述后续要进入的业务工作流。
    该对象只承载分类结果，不执行任何副作用。
    """

    route: ConversationRoute
    intent: IntentType | None


class WorkflowControllerContext(Protocol):
    """
    Workflow 编排层依赖的 CLI 能力协议。

    该协议刻意只描述 workflow 需要调用的能力，避免 workflow 层直接依赖完整 CLI 实现。
    """

    renderer: Any
    agent: Any
    intent_detector: Any
    conversation_router: Any
    scope_service: Any
    scanner_runner: Any
    workspace_manager: Any
    backlog_manager: Any
    chat_filter: Any
    command_controller: Any

    def _run_agent_request(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    def _describe_scope_paths(self, scope: CodeScope) -> list[str]: ...
    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]: ...
    def _build_static_scan_summary(self) -> str: ...
    def _build_local_no_issue_summary(self) -> str: ...
    def _build_project_explain_context(self, scope: CodeScope) -> str: ...
