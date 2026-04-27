from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from autopatch_j.agent.llm_client import LLMClient, ToolCall, build_default_llm_client
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
from autopatch_j.config import GlobalConfig
from autopatch_j.core.models import IntentType, AuditFindingItem, CodeScope, PatchReviewItem
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.finding_retriever_tool import FindingRetrieverTool
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool

ToolCallback = Callable[[str], None]


class AutoPatchAgent:
    """
    大模型智能决策引擎 (ReAct Execution Engine)。
    核心职责：
    1. 在 Workflow 赋予的明确任务类型和范围边界下，执行纯粹的 ReAct 循环。
    2. 解析 LLM 返回的 Tool Calls 并调度执行。
    3. 管理会话历史 (History Dehydration) 和上下文防爆。
    """

    TASK_TOOL_NAMES: dict[IntentType, tuple[str, ...]] = {
        IntentType.CODE_AUDIT: (
            "get_finding_detail",
            "read_source_code",
            "propose_patch",
        ),
        IntentType.CODE_EXPLAIN: (
            "search_symbols",
            "read_source_code",
        ),
        IntentType.GENERAL_CHAT: (),
        IntentType.PATCH_EXPLAIN: (
            "search_symbols",
            "read_source_code",
        ),
        IntentType.PATCH_REVISE: (
            "search_symbols",
            "read_source_code",
            "get_finding_detail",
            "propose_patch",
        ),
    }
    CODE_EXPLAIN_SINGLE_FILE_TOOL_NAMES: tuple[str, ...] = ("read_source_code",)
    ZERO_FINDING_REVIEW_TOOL_NAMES: tuple[str, ...] = (
        "read_source_code",
        "propose_patch",
    )

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
                SymbolSearchTool(self.session),
                SourceReaderTool(self.session),
                FindingRetrieverTool(self.session),
            ]
        }
        self.messages: list[dict[str, Any]] = []

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
        return self._run_task(
            intent=IntentType.CODE_AUDIT,
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
        tool_names = (
            self.TASK_TOOL_NAMES[IntentType.CODE_EXPLAIN]
            if effective_allow_symbol_search
            else self.CODE_EXPLAIN_SINGLE_FILE_TOOL_NAMES
        )
        prompt = build_code_explain_user_prompt(raw_user_text, scope) if scope else raw_user_text
        return self._run_task(
            intent=IntentType.CODE_EXPLAIN,
            user_text=prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
            tool_names_override=tool_names,
        )

    def perform_general_chat(
        self,
        raw_user_text: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        return self._run_task(
            intent=IntentType.GENERAL_CHAT,
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
            allowed_tool_names=self.ZERO_FINDING_REVIEW_TOOL_NAMES,
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
        return self._run_task(
            intent=IntentType.PATCH_EXPLAIN,
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
        remaining_items: list[PatchReviewItem],
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        prompt = build_patch_revise_user_prompt(current_item, remaining_items, raw_user_text)
        return self._run_task(
            intent=IntentType.PATCH_REVISE,
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
        system_prompt = self._build_task_system_prompt(intent)
        tool_names = tool_names_override or self.TASK_TOOL_NAMES[intent]
        return self._run_react_loop(
            user_text=user_text,
            system_prompt=system_prompt,
            allowed_tool_names=tool_names,
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
            return "LLM 配置缺失。请设置 LLM_API_KEY 后重启。"

        self.messages.append({"role": "user", "content": user_text})
        extra_body = self._build_llm_extra_body()

        for _ in range(10):
            processed_messages = self._dehydrate_history(system_prompt)
            response = self.llm.chat(
                messages=processed_messages,
                tools=self._get_tool_schemas(allowed_tool_names),
                extra_body=extra_body,
                on_token=on_token,
                on_reasoning_token=on_reasoning,
            )

            assistant_content = response.content or "..."
            self.messages.append(
                {
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": self._serialize_tool_calls(response.tool_calls) if response.tool_calls else None,
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
                    on_observation(stuck_message)
                return stuck_message

            for call in response.tool_calls:
                if on_tool_start:
                    on_tool_start(call.name)
                observation = self._execute_tool_call(call, set(allowed_tool_names))
                if on_observation:
                    on_observation(observation.message)
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
        pending = self.session.artifacts.load_pending_patch()
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
        scan_files = sorted(self.session.artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        return scan_files[0].stem if scan_files else None

    def _build_llm_extra_body(self) -> dict[str, Any]:
        import json
        try:
            return json.loads(GlobalConfig.llm_extra_body)
        except json.JSONDecodeError:
            return {}

    def _dehydrate_history(self, current_system_prompt: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [{"role": "system", "content": current_system_prompt}]

        # 保留完整角色序列，避免消息窗口裁剪后破坏 tool_call 对应关系。
        for i, message in enumerate(self.messages):
            new_message = self._fetch_llm_message(message)

            if message.get("role") == "tool":
                is_recent = i >= len(self.messages) - 5
                is_scan = message.get("name") == "scan_project"

                # 压缩旧的工具观察，但保护 scan_project 结果。
                if not is_recent and not is_scan:
                    content = str(message.get("content", ""))
                    if len(content) > 200:
                        new_message["content"] = content[:100] + "\n... [已脱水压缩] ..."

            result.append(new_message)

        return result

    def _fetch_llm_message(self, message: dict[str, Any]) -> dict[str, Any]:
        role = str(message.get("role", ""))
        if role == "assistant":
            llm_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content", ""),
            }
            if message.get("tool_calls") is not None:
                llm_message["tool_calls"] = message["tool_calls"]
            
            # DeepSeek V4 深度思考模式契约要求：如果产生了思考链，必须在多轮对话中原样传回，否则会报 400 错误。
            # 如果配置了思考力度或显式开启了思考，即便为空也建议带上字段以符合 V4 API 的强校验。
            reasoning = message.get("reasoning_content")
            if reasoning is not None:
                llm_message["reasoning_content"] = reasoning
            elif GlobalConfig.llm_reasoning_effort or "thinking" in GlobalConfig.llm_extra_body:
                llm_message["reasoning_content"] = ""
                
            return llm_message

        if role == "tool":
            # OpenAI/DeepSeek 标准：role为 tool 时，仅允许 tool_call_id 和 content 字段。
            # 这里的 name, tool_status, tool_payload 仅供本地业务使用，发送给 API 前必须剔除。
            return {
                "role": "tool",
                "tool_call_id": message.get("tool_call_id", ""),
                "content": message.get("content", ""),
            }

        return {
            "role": role,
            "content": message.get("content", ""),
        }

    def _get_tool_schemas(self, allowed_tool_names: tuple[str, ...]) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool_name in allowed_tool_names:
            tool = self.available_tools.get(tool_name)
            if tool is None:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return schemas

    def _serialize_tool_calls(self, calls: list[ToolCall]) -> list[dict[str, Any]]:
        return [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments,
                },
            }
            for call in calls
        ]

    def reset_history(self) -> None:
        self.messages = []
        self.session.clear_cache()

    @property
    def model_label(self) -> str:
        return self.llm.model if self.llm else "LLM Not Configured"
