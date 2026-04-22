from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from autopatch_j.agent.llm_client import LLMClient, LLMResponse, ToolCall, build_default_llm_client
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.project_scanner_tool import ProjectScannerTool
from autopatch_j.tools.patch_proposal_tool import PatchProposalTool
from autopatch_j.tools.symbol_search_tool import SymbolSearchTool
from autopatch_j.tools.source_reader_tool import SourceReaderTool
from autopatch_j.tools.finding_retriever_tool import FindingRetrieverTool
from autopatch_j.agent.prompts import SYSTEM_PROMPT, build_workbench_prompt


class AutoPatchAgent:
    """
    智能决策引擎 (ReAct 控制器)
    职责：实现 ReAct 循环，直接持有核心 Service 实例。
    """

    def __init__(
        self, 
        repo_root: Path,
        artifacts: Any,
        indexer: Any,
        patch_engine: Any,
        fetcher: Any,
        llm: LLMClient | None = None
    ) -> None:
        self.repo_root = repo_root
        self.artifacts = artifacts
        self.indexer = indexer
        self.patch_engine = patch_engine
        self.fetcher = fetcher
        self.llm = llm or build_default_llm_client()
        
        # 注册工具集
        self.available_tools: dict[str, Tool] = {
            t.name: t for t in [
                ProjectScannerTool(self),
                PatchProposalTool(self),
                SymbolSearchTool(self),
                SourceReaderTool(self),
                FindingRetrieverTool(self)
            ]
        }
        
        self.messages: list[dict[str, Any]] = []
        self.focus_paths: list[str] = []

    def chat(
        self, 
        user_text: str, 
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_observation: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str], None] | None = None
    ) -> str:
        """执行 ReAct 循环"""
        if not self.llm:
            return "LLM 配置缺失。请设置 LLM_API_KEY 环境变量后重启。"

        self.messages.append({"role": "user", "content": user_text})

        # 智能适配：如果是百炼 DeepSeek，开启思考链
        extra_body = {}
        from autopatch_j.config import GlobalConfig
        if "deepseek" in GlobalConfig.llm_model.lower() and "aliyuncs" in GlobalConfig.llm_base_url:
            extra_body["enable_thinking"] = True

        for _ in range(10):
            full_system_prompt = self._synthesize_system_prompt()
            processed_messages = self._dehydrate_history(full_system_prompt)

            response = self.llm.chat(
                messages=processed_messages,
                tools=self._get_tool_schemas(),
                extra_body=extra_body,
                on_token=on_token,
                on_reasoning_token=on_reasoning
            )

            # 🚀 健壮性：如果 content 为空（仅有 tool_calls），部分网关会报错，填充占位符
            assistant_content = response.content or "..."

            self.messages.append({
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": self._serialize_tool_calls(response.tool_calls) if response.tool_calls else None
            })

            if not response.tool_calls:
                return response.content

            # 执行观察 (Observation)
            for call in response.tool_calls:
                if on_tool_start:
                    on_tool_start(call.name)
                
                observation = self._execute_tool_call(call)
                
                if on_observation:
                    on_observation(observation.message)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": observation.message
                })

        return "已达推理上限，请审核目前进展。"

    def _execute_tool_call(self, call: ToolCall) -> ToolResult:
        """执行工具调用"""
        tool = self.available_tools.get(call.name)
        if not tool:
            return ToolResult(status="error", message=f"未找到工具：{call.name}")
        try:
            return tool.execute(**call.arguments)
        except Exception as e:
            return ToolResult(status="error", message=f"执行异常：{str(e)}")

    def _synthesize_system_prompt(self) -> str:
        """抓取项目状态"""
        pending = self.artifacts.fetch_pending_patch()
        scan_files = sorted(self.artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        last_scan_id = scan_files[0].stem if scan_files else None
        
        workbench = build_workbench_prompt(
            pending_file=pending.file_path if pending else None,
            last_scan=last_scan_id,
            focus_paths=self.focus_paths
        )
        return SYSTEM_PROMPT + workbench

    def _dehydrate_history(self, current_system_prompt: str) -> list[dict[str, Any]]:
        """
        物理级历史脱水：
        1. 确保历史永远从 'user' 角色开始。
        2. 保护 scan_project 的结果不被压缩。
        """
        raw_window = self.messages[-14:] # 稍微扩大
        
        # 🚀 核心对齐逻辑：寻找窗口内第一个 user 消息
        start_idx = 0
        for i, msg in enumerate(raw_window):
            if msg.get("role") == "user":
                start_idx = i
                break
        
        history_window = raw_window[start_idx:]
        
        result = [{"role": "system", "content": current_system_prompt}]
        for msg in history_window:
            new_msg = dict(msg)
            # 保护扫描结果
            if msg.get("role") == "tool" and msg.get("name") == "scan_project":
                result.append(new_msg)
                continue

            # 压缩旧的工具观察
            if msg["role"] == "tool" and msg in self.messages[:-3]:
                content = str(msg["content"])
                if len(content) > 200:
                    new_msg["content"] = content[:100] + "\n... [此处内容已脱水压缩] ..."
            result.append(new_msg)
        return result

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in self.available_tools.values()]

    def _serialize_tool_calls(self, calls: list[ToolCall]) -> list[dict[str, Any]]:
        return [{"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]

    def set_focus_paths(self, paths: list[str] | None) -> None:
        normalized: list[str] = []
        for path in paths or []:
            clean = self.normalize_repo_path(path)
            if clean and clean not in normalized:
                normalized.append(clean)
        self.focus_paths = normalized

    def normalize_repo_path(self, path: str) -> str:
        clean = path.replace("\\", "/").strip()
        if clean.startswith("./"):
            clean = clean[2:]
        return clean

    def is_focus_locked(self) -> bool:
        return bool(self.focus_paths)

    def is_path_in_focus(self, path: str) -> bool:
        if not self.focus_paths:
            return True
        return self.normalize_repo_path(path) in self.focus_paths

    @property
    def label(self) -> str:
        return self.llm.label if self.llm else "LLM Not Configured"
