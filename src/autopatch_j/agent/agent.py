from __future__ import annotations

from typing import Any

from autopatch_j.agent.callbacks import ObservationCallback, TextCallback
from autopatch_j.agent.message_adapter import AgentMessageAdapter
from autopatch_j.agent.react_runner import ReActRunner
from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.task_executor import AgentTaskExecutor, build_agent_callbacks
from autopatch_j.agent.tool_executor import ToolExecutor
from autopatch_j.core.memory.scheduler import MemorySummaryScheduler
from autopatch_j.core.domain import FindingTask, CodeScope, ReviewPatchItem
from autopatch_j.llm.client import LLMClient, build_default_llm_client
from autopatch_j.tools.contract import FunctionTool


class Agent:
    """
    AutoPatch-J Agent 门面。

    职责边界：
    1. 对 CLI/Workflow 暴露稳定的 perform_* 入口和消息历史。
    2. 组装 TaskExecutor、ReActRunner、ToolExecutor 等内部组件。
    3. 不直接承载 ReAct 循环、工具执行或任务 profile 规则。
    """

    def __init__(
        self,
        session: AgentSession,
        llm: LLMClient | None = None,
    ) -> None:
        self.session = session
        self.llm = llm or build_default_llm_client()
        self._messages: list[dict[str, Any]] = []

        self.tool_executor = ToolExecutor(self.session)
        self.available_tools: dict[str, FunctionTool] = self.tool_executor.available_tools
        self.message_adapter = AgentMessageAdapter(self.tool_executor.catalog)
        self.react_runner = ReActRunner(
            llm=self.llm,
            messages=self._messages,
            message_adapter=self.message_adapter,
            tool_executor=self.tool_executor,
        )
        self.memory_summary_scheduler = self._build_memory_summary_scheduler()
        self.task_executor = AgentTaskExecutor(
            session=self.session,
            react_runner=self.react_runner,
            memory_summary_scheduler_provider=lambda: self.memory_summary_scheduler,
        )

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        self._messages = value
        if hasattr(self, "react_runner"):
            self.react_runner.messages = self._messages

    @property
    def llm(self) -> LLMClient | None:
        return self._llm

    @llm.setter
    def llm(self, value: LLMClient | None) -> None:
        self._llm = value
        if hasattr(self, "react_runner"):
            self.react_runner.llm = self._llm

    def perform_code_audit(
        self,
        raw_user_text: str,
        current_finding: FindingTask,
        force_reread: bool,
        on_token: TextCallback | None = None,
        on_reasoning: TextCallback | None = None,
        on_observation: ObservationCallback | None = None,
        on_tool_start: TextCallback | None = None,
    ) -> str:
        return self.task_executor.perform_code_audit(
            raw_user_text=raw_user_text,
            current_finding=current_finding,
            force_reread=force_reread,
            callbacks=build_agent_callbacks(on_token, on_reasoning, on_observation, on_tool_start),
        )

    def perform_code_explain(
        self,
        raw_user_text: str,
        scope: CodeScope | None,
        project_context: str | None = None,
        allow_symbol_search: bool | None = None,
        on_token: TextCallback | None = None,
        on_reasoning: TextCallback | None = None,
        on_observation: ObservationCallback | None = None,
        on_tool_start: TextCallback | None = None,
    ) -> str:
        return self.task_executor.perform_code_explain(
            raw_user_text=raw_user_text,
            scope=scope,
            project_context=project_context,
            allow_symbol_search=allow_symbol_search,
            callbacks=build_agent_callbacks(on_token, on_reasoning, on_observation, on_tool_start),
        )

    def perform_general_chat(
        self,
        raw_user_text: str,
        on_token: TextCallback | None = None,
        on_reasoning: TextCallback | None = None,
        on_observation: ObservationCallback | None = None,
        on_tool_start: TextCallback | None = None,
    ) -> str:
        return self.task_executor.perform_general_chat(
            raw_user_text=raw_user_text,
            callbacks=build_agent_callbacks(on_token, on_reasoning, on_observation, on_tool_start),
        )

    def perform_zero_finding_review(
        self,
        raw_user_text: str,
        file_path: str,
        on_token: TextCallback | None = None,
        on_reasoning: TextCallback | None = None,
        on_observation: ObservationCallback | None = None,
        on_tool_start: TextCallback | None = None,
    ) -> str:
        return self.task_executor.perform_zero_finding_review(
            raw_user_text=raw_user_text,
            file_path=file_path,
            callbacks=build_agent_callbacks(on_token, on_reasoning, on_observation, on_tool_start),
        )

    def perform_patch_explain(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        on_token: TextCallback | None = None,
        on_reasoning: TextCallback | None = None,
        on_observation: ObservationCallback | None = None,
        on_tool_start: TextCallback | None = None,
    ) -> str:
        return self.task_executor.perform_patch_explain(
            raw_user_text=raw_user_text,
            current_item=current_item,
            callbacks=build_agent_callbacks(on_token, on_reasoning, on_observation, on_tool_start),
        )

    def perform_patch_revise(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        on_token: TextCallback | None = None,
        on_reasoning: TextCallback | None = None,
        on_observation: ObservationCallback | None = None,
        on_tool_start: TextCallback | None = None,
    ) -> str:
        return self.task_executor.perform_patch_revise(
            raw_user_text=raw_user_text,
            current_item=current_item,
            callbacks=build_agent_callbacks(on_token, on_reasoning, on_observation, on_tool_start),
        )

    def _build_memory_summary_scheduler(self) -> MemorySummaryScheduler | None:
        memory_manager = self.session.memory_manager
        if self.llm is None or memory_manager is None:
            return None
        return MemorySummaryScheduler(
            memory_manager=memory_manager,
            llm=self.llm,
            repo_root=self.session.repo_root,
        )

    def reset_history(self, clear_memory: bool = False) -> None:
        if clear_memory and self.memory_summary_scheduler is not None:
            self.memory_summary_scheduler.discard_pending_results()
        self.messages = []
        self.session.clear_cache(clear_memory=clear_memory)

    @property
    def model_label(self) -> str:
        return self.llm.model if self.llm else "LLM Not Configured"
