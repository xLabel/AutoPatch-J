from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from autopatch_j.agent.llm_client import LLMCallPurpose, LLMClient, build_default_llm_client
from autopatch_j.agent.dialect import ToolCall
from autopatch_j.agent.message_adapter import AgentMessageAdapter
from autopatch_j.agent.prompts import (
    build_task_system_prompt,
    build_zero_finding_review_system_prompt,
    build_code_audit_user_prompt,
    build_code_explain_user_prompt,
    build_patch_explain_user_prompt,
    build_patch_revise_user_prompt,
    build_zero_finding_review_user_prompt,
)
from autopatch_j.agent.session import AgentSession
from autopatch_j.core.models import IntentType, AuditFindingItem, CodeScope, PatchReviewItem
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.finding_retriever_tool import FindingRetrieverTool
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.patch_revision_tool import PatchRevisionTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool

ToolCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class TaskProfile:
    """
    Agent 任务的静态执行边界。

    intent 决定系统提示词，tool_names 决定本轮 ReAct 可调用的工具集合。
    它只描述任务配置，不保存运行时状态。
    """

    intent: IntentType
    tool_names: tuple[str, ...]


class Agent:
    """
    ReAct 执行引擎。

    职责边界：
    1. 根据 Workflow 指定的任务 profile 组织系统提示词、用户提示词和工具白名单。
    2. 驱动 LLM 多轮 ReAct 循环，解析 Tool Call 并调度工具执行。
    3. 管理短期消息历史和循环保护；不负责 CLI 路由、扫描调度或 workspace 队列推进。
    """

    TASK_PROFILES: dict[IntentType, TaskProfile] = {
        IntentType.CODE_AUDIT: TaskProfile(
            intent=IntentType.CODE_AUDIT,
            tool_names=(
                "get_finding_detail",
                "read_source_code",
                "propose_patch",
            ),
        ),
        IntentType.CODE_EXPLAIN: TaskProfile(
            intent=IntentType.CODE_EXPLAIN,
            tool_names=(
                "search_symbols",
                "read_source_code",
            ),
        ),
        IntentType.GENERAL_CHAT: TaskProfile(
            intent=IntentType.GENERAL_CHAT,
            tool_names=(),
        ),
        IntentType.PATCH_EXPLAIN: TaskProfile(
            intent=IntentType.PATCH_EXPLAIN,
            tool_names=(
                "search_symbols",
                "read_source_code",
            ),
        ),
        IntentType.PATCH_REVISE: TaskProfile(
            intent=IntentType.PATCH_REVISE,
            tool_names=(
                "search_symbols",
                "read_source_code",
                "get_finding_detail",
                "revise_patch",
            ),
        ),
    }
    TASK_TOOL_NAMES: dict[IntentType, tuple[str, ...]] = {
        intent: profile.tool_names for intent, profile in TASK_PROFILES.items()
    }
    CODE_EXPLAIN_SINGLE_FILE_PROFILE = TaskProfile(
        intent=IntentType.CODE_EXPLAIN,
        tool_names=("read_source_code",),
    )
    ZERO_FINDING_REVIEW_PROFILE = TaskProfile(
        intent=IntentType.CODE_AUDIT,
        tool_names=(
            "read_source_code",
            "propose_patch",
        ),
    )
    CODE_EXPLAIN_SINGLE_FILE_TOOL_NAMES: tuple[str, ...] = CODE_EXPLAIN_SINGLE_FILE_PROFILE.tool_names
    ZERO_FINDING_REVIEW_TOOL_NAMES: tuple[str, ...] = ZERO_FINDING_REVIEW_PROFILE.tool_names
    def __init__(
        self,
        session: AgentSession,
        llm: LLMClient | None = None,
    ) -> None:
        self.session = session
        self.llm = llm or build_default_llm_client()

        self.available_tools: dict[str, Tool] = {
            tool.name: tool
            for tool in [
                PatchProposalTool(self.session),
                PatchRevisionTool(self.session),
                SymbolSearchTool(self.session),
                SourceReaderTool(self.session),
                FindingRetrieverTool(self.session),
            ]
        }
        self.messages: list[dict[str, Any]] = []
        self.message_adapter = AgentMessageAdapter(self.available_tools)

    def perform_code_audit(
        self,
        raw_user_text: str,
        current_finding: AuditFindingItem,
        force_reread: bool,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        prompt = build_code_audit_user_prompt(raw_user_text, current_finding, force_reread)
        return self._run_profile(
            profile=self.TASK_PROFILES[IntentType.CODE_AUDIT],
            user_text=prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_code_explain(
        self,
        raw_user_text: str,
        scope: CodeScope | None,
        allow_symbol_search: bool | None = None,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        effective_allow_symbol_search = (
            self.session.code_explain_allow_symbol_search
            if allow_symbol_search is None
            else allow_symbol_search
        )
        profile = (
            self.TASK_PROFILES[IntentType.CODE_EXPLAIN]
            if effective_allow_symbol_search
            else self.CODE_EXPLAIN_SINGLE_FILE_PROFILE
        )
        prompt = build_code_explain_user_prompt(raw_user_text, scope) if scope else raw_user_text
        return self._run_profile(
            profile=profile,
            user_text=prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_general_chat(
        self,
        raw_user_text: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        return self._run_profile(
            profile=self.TASK_PROFILES[IntentType.GENERAL_CHAT],
            user_text=raw_user_text,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_zero_finding_review(
        self,
        raw_user_text: str,
        file_path: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        prompt = build_zero_finding_review_user_prompt(raw_user_text, file_path)
        return self._run_react_loop(
            user_text=prompt,
            system_prompt=self._build_zero_finding_review_system_prompt(),
            allowed_tool_names=self.ZERO_FINDING_REVIEW_PROFILE.tool_names,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_patch_explain(
        self,
        raw_user_text: str,
        current_item: PatchReviewItem,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        prompt = build_patch_explain_user_prompt(current_item, raw_user_text)
        return self._run_profile(
            profile=self.TASK_PROFILES[IntentType.PATCH_EXPLAIN],
            user_text=prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_patch_revise(
        self,
        raw_user_text: str,
        current_item: PatchReviewItem,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        prompt = build_patch_revise_user_prompt(current_item, raw_user_text)
        return self._run_profile(
            profile=self.TASK_PROFILES[IntentType.PATCH_REVISE],
            user_text=prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def _run_task(
        self,
        intent: IntentType,
        user_text: str,
        on_token: ToolCallback | None,
        on_reasoning: ToolCallback | None,
        on_observation: ToolCallback | None,
        on_tool_start: ToolCallback | None,
        tool_names_override: tuple[str, ...] | None = None,
    ) -> str:
        profile = TaskProfile(
            intent=intent,
            tool_names=tool_names_override or self.TASK_PROFILES[intent].tool_names,
        )
        return self._run_profile(
            profile=profile,
            user_text=user_text,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def _run_profile(
        self,
        profile: TaskProfile,
        user_text: str,
        on_token: ToolCallback | None,
        on_reasoning: ToolCallback | None,
        on_observation: ToolCallback | None,
        on_tool_start: ToolCallback | None,
    ) -> str:
        system_prompt = self._build_task_system_prompt(profile.intent)
        return self._run_react_loop(
            user_text=user_text,
            system_prompt=system_prompt,
            allowed_tool_names=profile.tool_names,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def _run_react_loop(
        self,
        user_text: str,
        system_prompt: str,
        allowed_tool_names: tuple[str, ...],
        on_token: ToolCallback | None,
        on_reasoning: ToolCallback | None,
        on_observation: ToolCallback | None,
        on_tool_start: ToolCallback | None,
    ) -> str:
        if not self.llm:
            return "LLM 配置缺失。请设置 AUTOPATCH_LLM_API_KEY 后重启。"

        self.messages.append({"role": "user", "content": user_text})

        for _ in range(10):
            processed_messages = self.message_adapter.dehydrate_history(self.messages, system_prompt)
            response = self.llm.chat(
                messages=processed_messages,
                tools=self.message_adapter.tool_schemas(allowed_tool_names),
                purpose=LLMCallPurpose.REACT,
                on_content_delta=on_token,
                on_reasoning_delta=on_reasoning,
            )

            assistant_content = response.content or "..."
            self.messages.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": (
                        self.message_adapter.serialize_tool_calls(response.tool_calls)
                        if response.tool_calls
                        else None
                    ),
                    "reasoning_content": response.reasoning_content,
                }
            )

            if not response.tool_calls:
                return response.content

            fingerprint = "|".join([f"{call.name}:{call.raw_arguments}" for call in response.tool_calls])
            self.session.record_action(fingerprint)
            if self.session.is_stuck_in_loop():
                stuck_message = "检测到大模型陷入死循环（连续 3 次执行相同的不合法操作），已主动阻断以节省成本。请人工介入审查。"
                if on_observation:
                    on_observation(stuck_message, "陷入死循环被阻断")
                return stuck_message

            for call in response.tool_calls:
                if on_tool_start:
                    on_tool_start(call.name)
                observation = self._execute_tool_call(call, set(allowed_tool_names))
                if on_observation:
                    on_observation(observation.message, observation.summary)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": observation.message,
                        "tool_status": observation.status,
                        "tool_payload": observation.payload,
                    }
                )

        return "已达推理上限，请审阅当前结果。"

    def _execute_tool_call(self, call: ToolCall, allowed_tool_names: set[str]) -> ToolResult:
        if call.name not in allowed_tool_names:
            return ToolResult(
                status="error",
                message=f"当前任务未开放工具：{call.name}",
            )
        tool = self.available_tools.get(call.name)
        if tool is None:
            return ToolResult(status="error", message=f"未找到工具：{call.name}")
        try:
            return tool.execute(**call.arguments)
        except Exception as exc:
            return ToolResult(status="error", message=f"执行异常：{exc}")

    def _build_task_system_prompt(self, intent: IntentType) -> str:
        pending = self.session.workspace_manager.load_pending_patch()
        last_scan_id = self._fetch_latest_scan_artifact_id()
        return build_task_system_prompt(
            intent=intent,
            pending_file=pending.file_path if pending else None,
            last_scan=last_scan_id,
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

    def _dehydrate_history(self, current_system_prompt: str) -> list[dict[str, Any]]:
        return self.message_adapter.dehydrate_history(self.messages, current_system_prompt)

    def _fetch_llm_message(self, message: dict[str, Any]) -> dict[str, Any]:
        return self.message_adapter.fetch_llm_message(message)

    def _get_tool_schemas(self, allowed_tool_names: tuple[str, ...]) -> list[dict[str, Any]]:
        return self.message_adapter.tool_schemas(allowed_tool_names)

    def _serialize_tool_calls(self, calls: list[ToolCall]) -> list[dict[str, Any]]:
        return self.message_adapter.serialize_tool_calls(calls)

    def reset_history(self) -> None:
        self.messages = []
        self.session.clear_cache()

    @property
    def model_label(self) -> str:
        return self.llm.model if self.llm else "LLM Not Configured"
