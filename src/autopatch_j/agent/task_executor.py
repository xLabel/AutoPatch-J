from __future__ import annotations

from typing import Callable

from autopatch_j.agent.callbacks import AgentCallbacks, ObservationCallback, TextCallback
from autopatch_j.agent.prompts import (
    build_code_audit_user_prompt,
    build_code_explain_user_prompt,
    build_patch_explain_user_prompt,
    build_patch_revise_user_prompt,
    build_task_system_prompt,
    build_zero_finding_review_system_prompt,
    build_zero_finding_review_user_prompt,
)
from autopatch_j.agent.react_runner import ReActRunner
from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.task_profile import (
    ZERO_FINDING_REVIEW_PROFILE,
    TaskProfile,
    fetch_code_explain_profile,
    fetch_task_profile,
)
from autopatch_j.core.memory.scheduler import MemorySummaryScheduler
from autopatch_j.core.domain import FindingTask, CodeScope, CodeScopeKind, IntentType, ReviewPatchItem


class AgentTaskExecutor:
    """
    Agent 任务编排层。

    它把 Workflow 传入的领域对象转换为用户 prompt 和 TaskProfile，然后交给
    ReActRunner 执行。普通问答记忆的写入和摘要触发也集中在这里。
    """

    def __init__(
        self,
        session: AgentSession,
        react_runner: ReActRunner,
        memory_summary_scheduler_provider: Callable[[], MemorySummaryScheduler | None],
    ) -> None:
        self.session = session
        self.react_runner = react_runner
        self.memory_summary_scheduler_provider = memory_summary_scheduler_provider

    def perform_code_audit(
        self,
        raw_user_text: str,
        current_finding: FindingTask,
        force_reread: bool,
        callbacks: AgentCallbacks,
    ) -> str:
        prompt = build_code_audit_user_prompt(raw_user_text, current_finding, force_reread)
        return self._run_profile(fetch_task_profile(IntentType.CODE_AUDIT), prompt, callbacks)

    def perform_code_explain(
        self,
        raw_user_text: str,
        scope: CodeScope | None,
        project_context: str | None,
        allow_symbol_search: bool | None,
        callbacks: AgentCallbacks,
    ) -> str:
        effective_allow_symbol_search = (
            self.session.code_explain_allow_symbol_search
            if allow_symbol_search is None
            else allow_symbol_search
        )
        profile = fetch_code_explain_profile(effective_allow_symbol_search)
        prompt = build_code_explain_user_prompt(raw_user_text, scope, project_context) if scope else raw_user_text
        answer = self._run_profile(profile, prompt, callbacks)

        self.session.append_memory_turn(
            intent=IntentType.CODE_EXPLAIN,
            user_text=raw_user_text,
            answer=answer,
            scope_paths=scope.focus_files if scope else None,
        )
        self._summarize_memory_if_needed(
            raw_user_text,
            force_project_code_explain=scope is not None and scope.kind is CodeScopeKind.PROJECT,
        )
        return answer

    def perform_general_chat(self, raw_user_text: str, callbacks: AgentCallbacks) -> str:
        answer = self._run_profile(fetch_task_profile(IntentType.GENERAL_CHAT), raw_user_text, callbacks)
        self.session.append_memory_turn(
            intent=IntentType.GENERAL_CHAT,
            user_text=raw_user_text,
            answer=answer,
        )
        self._summarize_memory_if_needed(raw_user_text)
        return answer

    def perform_zero_finding_review(
        self,
        raw_user_text: str,
        file_path: str,
        callbacks: AgentCallbacks,
    ) -> str:
        prompt = build_zero_finding_review_user_prompt(raw_user_text, file_path)
        return self.react_runner.run(
            user_text=prompt,
            system_prompt=self._build_zero_finding_review_system_prompt(),
            allowed_tool_names=ZERO_FINDING_REVIEW_PROFILE.tool_names,
            callbacks=callbacks,
        )

    def perform_patch_explain(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        callbacks: AgentCallbacks,
    ) -> str:
        prompt = build_patch_explain_user_prompt(current_item, raw_user_text)
        return self._run_profile(fetch_task_profile(IntentType.PATCH_EXPLAIN), prompt, callbacks)

    def perform_patch_revise(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        callbacks: AgentCallbacks,
    ) -> str:
        prompt = build_patch_revise_user_prompt(current_item, raw_user_text)
        return self._run_profile(fetch_task_profile(IntentType.PATCH_REVISE), prompt, callbacks)

    def _run_profile(self, profile: TaskProfile, user_text: str, callbacks: AgentCallbacks) -> str:
        return self.react_runner.run(
            user_text=user_text,
            system_prompt=self._build_task_system_prompt(profile.intent, user_text),
            allowed_tool_names=profile.tool_names,
            callbacks=callbacks,
        )

    def _build_task_system_prompt(self, intent: IntentType, current_user_text: str) -> str:
        pending = self.session.workspace_manager.load_current_patch_draft()
        return build_task_system_prompt(
            intent=intent,
            pending_file=pending.file_path if pending else None,
            last_scan=self._fetch_latest_scan_artifact_id(),
            focus_paths=self.session.focus_paths,
            memory_context=self.session.build_memory_context(intent, current_user_text),
        )

    def _build_zero_finding_review_system_prompt(self) -> str:
        return build_zero_finding_review_system_prompt(
            last_scan=self._fetch_latest_scan_artifact_id(),
            focus_paths=self.session.focus_paths,
        )

    def _fetch_latest_scan_artifact_id(self) -> str | None:
        scan_files = sorted(self.session.artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        return scan_files[0].stem if scan_files else None

    def _summarize_memory_if_needed(
        self,
        last_user_text: str,
        force_project_code_explain: bool = False,
    ) -> None:
        if self.session.memory_manager is None:
            return
        scheduler = self.memory_summary_scheduler_provider()
        if scheduler is None:
            return
        trigger = self.session.memory_manager.find_summary_trigger(
            last_user_text=last_user_text,
            force_project_code_explain=force_project_code_explain,
        )
        scheduler.submit_if_needed(trigger, last_user_text)


def build_agent_callbacks(
    on_token: TextCallback | None,
    on_reasoning: TextCallback | None,
    on_observation: ObservationCallback | None,
    on_tool_start: TextCallback | None,
) -> AgentCallbacks:
    return AgentCallbacks(
        on_token=on_token,
        on_reasoning=on_reasoning,
        on_observation=on_observation,
        on_tool_start=on_tool_start,
    )
