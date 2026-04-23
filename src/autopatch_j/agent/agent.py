from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from autopatch_j.agent.llm_client import LLMClient, ToolCall, build_default_llm_client
from autopatch_j.agent.prompts import build_task_system_prompt
from autopatch_j.config import GlobalConfig
from autopatch_j.core.models import IntentType
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.finding_retriever_tool import FindingRetrieverTool
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool

ToolCallback = Callable[[str], None]


class AutoPatchAgent:
    """
    智能决策引擎
    职责：在明确任务类型下执行 ReAct 循环，并遵守任务级工具白名单。
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

    def __init__(
        self,
        repo_root: Path,
        artifacts: Any,
        indexer: Any,
        patch_engine: Any,
        fetcher: Any,
        llm: LLMClient | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.artifacts = artifacts
        self.indexer = indexer
        self.patch_engine = patch_engine
        self.fetcher = fetcher
        self.llm = llm or build_default_llm_client()

        self.available_tools: dict[str, Tool] = {
            tool.name: tool
            for tool in [
                PatchProposalTool(self),
                SymbolSearchTool(self),
                SourceReaderTool(self),
                FindingRetrieverTool(self),
            ]
        }
        self.messages: list[dict[str, Any]] = []
        self.focus_paths: list[str] = []
        self.source_read_cache: dict[tuple[str, str | None, int | None], ToolResult] = {}
        self.code_explain_allow_symbol_search = True

    def perform_code_audit(
        self,
        user_text: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        return self._run_task(
            intent=IntentType.CODE_AUDIT,
            user_text=user_text,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_code_explain(
        self,
        user_text: str,
        allow_symbol_search: bool | None = None,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        effective_allow_symbol_search = (
            self.code_explain_allow_symbol_search
            if allow_symbol_search is None
            else allow_symbol_search
        )
        tool_names = (
            self.TASK_TOOL_NAMES[IntentType.CODE_EXPLAIN]
            if effective_allow_symbol_search
            else self.CODE_EXPLAIN_SINGLE_FILE_TOOL_NAMES
        )
        return self._run_task(
            intent=IntentType.CODE_EXPLAIN,
            user_text=user_text,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
            tool_names_override=tool_names,
        )

    def perform_general_chat(
        self,
        user_text: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        return self._run_task(
            intent=IntentType.GENERAL_CHAT,
            user_text=user_text,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_patch_explain(
        self,
        user_text: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        return self._run_task(
            intent=IntentType.PATCH_EXPLAIN,
            user_text=user_text,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

    def perform_patch_revise(
        self,
        user_text: str,
        on_token: ToolCallback | None = None,
        on_reasoning: ToolCallback | None = None,
        on_observation: ToolCallback | None = None,
        on_tool_start: ToolCallback | None = None,
    ) -> str:
        return self._run_task(
            intent=IntentType.PATCH_REVISE,
            user_text=user_text,
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
                }
            )

            if not response.tool_calls:
                return response.content

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
        pending = self.artifacts.fetch_pending_patch()
        last_scan_id = self._fetch_latest_scan_artifact_id()
        return build_task_system_prompt(
            intent=intent,
            pending_file=pending.file_path if pending else None,
            last_scan=last_scan_id,
            focus_paths=self.focus_paths,
        )

    def _fetch_latest_scan_artifact_id(self) -> str | None:
        scan_files = sorted(self.artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        return scan_files[0].stem if scan_files else None

    def _build_llm_extra_body(self) -> dict[str, Any]:
        if "deepseek" in GlobalConfig.llm_model.lower() and "aliyuncs" in GlobalConfig.llm_base_url:
            return {"enable_thinking": True}
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
            return llm_message
        if role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.get("tool_call_id", ""),
                "name": message.get("name", ""),
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

    def set_focus_paths(self, paths: list[str] | None) -> None:
        normalized: list[str] = []
        for path in paths or []:
            clean = self.normalize_repo_path(path)
            if clean and clean not in normalized:
                normalized.append(clean)
        self.focus_paths = normalized

    def reset_history(self) -> None:
        self.messages = []
        self.source_read_cache = {}
        self.code_explain_allow_symbol_search = True

    def set_code_explain_symbol_search_enabled(self, enabled: bool) -> None:
        self.code_explain_allow_symbol_search = enabled

    def normalize_repo_path(self, path: str) -> str:
        clean = path.replace("\\", "/").strip()
        if clean.startswith("./"):
            clean = clean[2:]
        return clean

    def fetch_cached_source_read(
        self,
        path: str,
        symbol: str | None,
        line: int | None,
    ) -> ToolResult | None:
        key = (self.normalize_repo_path(path), symbol, line)
        return self.source_read_cache.get(key)

    def persist_cached_source_read(
        self,
        path: str,
        symbol: str | None,
        line: int | None,
        result: ToolResult,
    ) -> None:
        key = (self.normalize_repo_path(path), symbol, line)
        self.source_read_cache[key] = result

    def is_focus_locked(self) -> bool:
        return bool(self.focus_paths)

    def is_path_in_focus(self, path: str) -> bool:
        if not self.focus_paths:
            return True
        return self.normalize_repo_path(path) in self.focus_paths

    @property
    def model_label(self) -> str:
        return self.llm.model if self.llm else "LLM Not Configured"
