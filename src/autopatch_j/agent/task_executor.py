from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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
from autopatch_j.agent.context_manager import RequestContextBudget
from autopatch_j.agent.session import AgentSession
from autopatch_j.agent.task_profile import (
    ZERO_FINDING_REVIEW_PROFILE,
    TaskProfile,
    fetch_code_explain_profile,
    fetch_task_profile,
)
from autopatch_j.core.domain import FindingTask, CodeScope, IntentType, ReviewPatchItem
from autopatch_j.core.memory.errors import MemoryStorageError
from autopatch_j.core.memory.models import RecallQuery
from autopatch_j.config import GlobalConfig
from autopatch_j.llm.context_window import ModelContextProfile
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
        return self._run_profile(
            fetch_task_profile(IntentType.CODE_AUDIT),
            prompt,
            callbacks,
            RecallQuery(
                intent=IntentType.CODE_AUDIT.value,
                thread_id="",
                user_text=raw_user_text,
                paths=(current_finding.file_path,),
                finding_path=current_finding.file_path,
                check_id=current_finding.check_id,
                finding_message=current_finding.message,
                bound_finding_id=current_finding.finding_id,
            ),
        )

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
        return self._run_profile(
            profile,
            prompt,
            callbacks,
            RecallQuery(
                intent=IntentType.CODE_EXPLAIN.value,
                thread_id="",
                user_text=raw_user_text,
                paths=tuple(scope.focus_files) if scope else (),
            ),
        )

    def perform_general_chat(self, raw_user_text: str, callbacks: AgentCallbacks) -> AgentRunResult:
        return self._run_profile(
            fetch_task_profile(IntentType.GENERAL_CHAT),
            raw_user_text,
            callbacks,
            RecallQuery(
                intent=IntentType.GENERAL_CHAT.value,
                thread_id="",
                user_text=raw_user_text,
            ),
        )

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
            recall_query=RecallQuery(
                intent=IntentType.CODE_AUDIT.value,
                thread_id="",
                user_text=raw_user_text,
                paths=(file_path,),
                finding_path=file_path,
            ),
        )

    def perform_patch_explain(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        prompt = build_patch_explain_user_prompt(current_item, raw_user_text)
        runtime_constraints = self.session.build_runtime_patch_constraint_context(
            current_item.file_path
        )
        if runtime_constraints:
            prompt = f"{prompt}\n\n{runtime_constraints}"
        return self._run_profile(
            fetch_task_profile(IntentType.PATCH_EXPLAIN),
            prompt,
            callbacks,
            RecallQuery(
                intent=IntentType.PATCH_EXPLAIN.value,
                thread_id="",
                user_text=raw_user_text,
                paths=(current_item.file_path,),
                patch_path=current_item.file_path,
                bound_finding_id=(
                    current_item.finding_ids[0] if current_item.finding_ids else None
                ),
            ),
        )

    def perform_patch_revise(
        self,
        raw_user_text: str,
        current_item: ReviewPatchItem,
        callbacks: AgentCallbacks,
    ) -> AgentRunResult:
        existing_constraints = self.session.build_runtime_patch_constraint_context(
            current_item.file_path
        )
        self.session.record_runtime_patch_constraint(
            current_item.file_path,
            raw_user_text,
        )
        prompt = build_patch_revise_user_prompt(current_item, raw_user_text)
        if existing_constraints:
            prompt = f"{prompt}\n\n{existing_constraints}"
        return self._run_profile(
            fetch_task_profile(IntentType.PATCH_REVISE),
            prompt,
            callbacks,
            RecallQuery(
                intent=IntentType.PATCH_REVISE.value,
                thread_id="",
                user_text=raw_user_text,
                paths=(current_item.file_path,),
                patch_path=current_item.file_path,
                bound_finding_id=(
                    current_item.finding_ids[0] if current_item.finding_ids else None
                ),
            ),
        )

    def _run_profile(
        self,
        profile: TaskProfile,
        user_text: str,
        callbacks: AgentCallbacks,
        recall_query: RecallQuery,
    ) -> AgentRunResult:
        budget = self._request_context_budget()
        return self._run_request(
            user_text=user_text,
            system_prompt=self._build_task_system_prompt(profile.intent),
            allowed_tool_names=profile.tool_names,
            callbacks=callbacks,
            initial_history=self.session.build_thread_history(
                profile.intent,
                max_tokens=budget.recent_history_tokens,
            ),
            recall_query=recall_query,
            budget=budget,
        )

    def _run_request(
        self,
        *,
        user_text: str,
        system_prompt: str,
        allowed_tool_names: tuple[FunctionToolName, ...],
        callbacks: AgentCallbacks,
        initial_history: list[dict[str, Any]],
        recall_query: RecallQuery,
        budget: RequestContextBudget | None = None,
    ) -> AgentRunResult:
        self.session.clear_request_cache()
        try:
            thread_checkpoint, memory_map_provider = self._admit_memory_request(
                recall_query,
                budget or self._request_context_budget(),
            )
            return self.react_runner.run(
                user_text=user_text,
                system_prompt=system_prompt,
                allowed_tool_names=allowed_tool_names,
                callbacks=callbacks,
                initial_history=initial_history,
                thread_checkpoint=thread_checkpoint,
                advisory_context_provider=memory_map_provider,
            )
        finally:
            self.session.clear_memory_request()
            self.session.clear_request_cache()

    def _admit_memory_request(
        self,
        query: RecallQuery,
        budget: RequestContextBudget,
    ) -> tuple[str, Callable[[bool], str] | None]:
        manager = self.session.memory_manager
        if manager is None:
            return "", None
        try:
            thread_id = self.session.memory_thread_id
            if not thread_id:
                thread_id = manager.ensure_active_thread().id
            bound_query = replace(query, thread_id=thread_id)
            policy = manager.build_recall_policy(
                intent=bound_query.intent,
                thread_id=thread_id,
                durable_token_budget=budget.durable_recall_tokens,
                map_token_budget=budget.memory_map_tokens,
            )
            state = manager.open_memory_request(bound_query, policy)
            checkpoint = ""
            if policy.allow_thread_checkpoint:
                checkpoint = manager.active_thread_checkpoint(
                    thread_id,
                    max_tokens=budget.checkpoint_tokens,
                ).strip()
        except MemoryStorageError:
            return "", None
        self.session.bind_memory_request(state)

        def provide_memory_map(hard_rebuild: bool) -> str:
            try:
                memory_map = manager.refresh_memory_request(
                    state,
                    map_token_budget=(
                        max(0, policy.map_token_budget // 2)
                        if hard_rebuild
                        else policy.map_token_budget
                    ),
                )
            except MemoryStorageError:
                return ""
            return manager.render_memory_map(memory_map).strip()

        return checkpoint, provide_memory_map

    def _request_context_budget(self) -> RequestContextBudget:
        context_profile = getattr(self.react_runner.llm, "context_profile", None)
        if not isinstance(context_profile, ModelContextProfile):
            context_profile = GlobalConfig.resolve_llm_context_profile()
        return RequestContextBudget.from_profile(context_profile)

    def _build_task_system_prompt(self, intent: IntentType) -> str:
        pending = self.session.workspace_manager.load_current_patch_draft()
        return build_task_system_prompt(
            intent=intent,
            pending_file=pending.file_path if pending else None,
            last_scan=self._fetch_latest_scan_artifact_id(),
            focus_paths=self.session.focus_paths,
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
