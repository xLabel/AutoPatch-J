from __future__ import annotations

from typing import Any

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
from autopatch_j.agent.react_runner import AgentRunResult, ReActRunner
from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.task_profile import (
    ZERO_FINDING_REVIEW_PROFILE,
    TaskProfile,
    fetch_code_explain_profile,
    fetch_task_profile,
)
from autopatch_j.core.domain import FindingTask, CodeScope, IntentType, ReviewPatchItem
from autopatch_j.tools.names import FunctionToolName


class AgentTaskExecutor:
    """
    Agent 任务编排层。

    它把 Workflow 传入的领域对象转换为用户 prompt 和 TaskProfile，然后交给
    ReActRunner 执行。持久 turn 的开始/完成由上层 workflow 在展示边界处理。
    """

    def __init__(
        self,
        session: AgentSession,
        react_runner: ReActRunner,
    ) -> None:
        self.session = session
        self.react_runner = react_runner

    def perform_code_audit(
        self,
        raw_user_text: str,
        current_finding: FindingTask,
        force_reread: bool,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        prompt = build_code_audit_user_prompt(raw_user_text, current_finding, force_reread)
        return self._run_profile(fetch_task_profile(IntentType.CODE_AUDIT), prompt, callbacks)

    def perform_code_explain(
        self,
        raw_user_text: str,
        scope: CodeScope | None,
        project_context: str | None,
        allow_symbol_search: bool | None,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        effective_allow_symbol_search = (
            self.session.code_explain_allow_symbol_search
            if allow_symbol_search is None
            else allow_symbol_search
        )
        profile = fetch_code_explain_profile(effective_allow_symbol_search)
        prompt = build_code_explain_user_prompt(raw_user_text, scope, project_context) if scope else raw_user_text
        return self._run_profile(profile, prompt, callbacks)

    def perform_general_chat(self, raw_user_text: str, callbacks: AgentCallbacks) -> AgentRunResult:
        return self._run_profile(fetch_task_profile(IntentType.GENERAL_CHAT), raw_user_text, callbacks)

    def perform_zero_finding_review(
        self,
        raw_user_text: str,
        file_path: str,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        prompt = build_zero_finding_review_user_prompt(raw_user_text, file_path)
        return self._run_request(
            user_text=prompt,
            system_prompt=self._build_zero_finding_review_system_prompt(),
            allowed_tool_names=ZERO_FINDING_REVIEW_PROFILE.tool_names,
            callbacks=callbacks,
            initial_history=[],
        )

    def perform_patch_explain(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        prompt = build_patch_explain_user_prompt(current_item, raw_user_text)
        return self._run_profile(fetch_task_profile(IntentType.PATCH_EXPLAIN), prompt, callbacks)

    def perform_patch_revise(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        prompt = build_patch_revise_user_prompt(current_item, raw_user_text)
        return self._run_profile(fetch_task_profile(IntentType.PATCH_REVISE), prompt, callbacks)

    def _run_profile(
        self,
        profile: TaskProfile,
        user_text: str,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        return self._run_request(
            user_text=user_text,
            system_prompt=self._build_task_system_prompt(profile.intent),
            allowed_tool_names=profile.tool_names,
            callbacks=callbacks,
            initial_history=self.session.build_thread_history(profile.intent),
        )

    def _run_request(
        self,
        *,
        user_text: str,
        system_prompt: str,
        allowed_tool_names: tuple[FunctionToolName, ...],
        callbacks: AgentCallbacks,
        initial_history: list[dict[str, Any]],
    ) -> AgentRunResult:
        self.session.clear_request_cache()
        try:
            return self.react_runner.run(
                user_text=user_text,
                system_prompt=system_prompt,
                allowed_tool_names=allowed_tool_names,
                callbacks=callbacks,
                initial_history=initial_history,
            )
        finally:
            self.session.clear_request_cache()

    def _build_task_system_prompt(self, intent: IntentType) -> str:
        pending = self.session.workspace_manager.load_current_patch_draft()
        return build_task_system_prompt(
            intent=intent,
            pending_file=pending.file_path if pending else None,
            last_scan=self._fetch_latest_scan_artifact_id(),
            focus_paths=self.session.focus_paths,
            memory_context=self.session.build_memory_context(intent),
        )

    def _build_zero_finding_review_system_prompt(self) -> str:
        return build_zero_finding_review_system_prompt(
            last_scan=self._fetch_latest_scan_artifact_id(),
            focus_paths=self.session.focus_paths,
        )

    def _fetch_latest_scan_artifact_id(self) -> str | None:
        scan_files = sorted(self.session.artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        return scan_files[0].stem if scan_files else None

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
