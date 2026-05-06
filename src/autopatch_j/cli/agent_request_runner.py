from __future__ import annotations

from typing import Any, Callable

from autopatch_j.cli.agent_stream_presenter import AgentStreamPresenter
from autopatch_j.core.models import IntentType


class AgentRequestRunner:
    """
    Workflow 调用 Agent 的唯一入口。

    它把业务 workflow 的执行请求转交给 AgentStreamPresenter，避免 workflow
    直接依赖 CLI 应用对象的私有方法。
    """

    def __init__(self, presenter: AgentStreamPresenter) -> None:
        self.presenter = presenter

    def run(
        self,
        prompt: str,
        agent_call: Callable[..., str],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
        compact_observation: bool = False,
        answer_intent: IntentType | None = None,
        raw_user_text: str | None = None,
        show_chat_anchors: bool = False,
        plain_answer: bool = False,
        suppress_answer_output: bool = False,
    ) -> list[dict[str, Any]]:
        return self.presenter.run(
            prompt=prompt,
            agent_call=agent_call,
            scope_paths=scope_paths,
            render_no_issue_panel=render_no_issue_panel,
            compact_observation=compact_observation,
            answer_intent=answer_intent,
            raw_user_text=raw_user_text,
            show_chat_anchors=show_chat_anchors,
            plain_answer=plain_answer,
            suppress_answer_output=suppress_answer_output,
        )

