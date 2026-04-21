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
from autopatch_j.core.service_context import ServiceContext


class AutoPatchAgent:
    """
    智能决策引擎 (V2.1 - 具备状态感知与历史脱水功能)
    """

    def __init__(self, context: ServiceContext, llm: LLMClient | None = None) -> None:
        self.context = context
        self.llm = llm or build_default_llm_client()
        
        if not self.llm:
            raise ValueError("LLM 配置缺失。")

        # 注册统一命名后的工具集
        self.available_tools: dict[str, Tool] = {
            t.name: t for t in [
                ProjectScannerTool(),
                PatchProposalTool(),
                SymbolSearchTool(),
                SourceReaderTool(),
                FindingRetrieverTool()
            ]
        }
        
        # 原始对话历史（未压缩）
        self.messages: list[dict[str, Any]] = []

    def chat(
        self, 
        user_text: str, 
        on_thought_token: Callable[[str], None] | None = None,
        on_tool_start: Callable[[str], None] | None = None
    ) -> str:
        """执行 ReAct 循环，并在发送前动态合成 Prompt 与管理历史"""
        self.messages.append({"role": "user", "content": user_text})

        for _ in range(5):
            # --- 1. 动态合成系统提示词 (State Injection) ---
            full_system_prompt = self._synthesize_system_prompt()
            
            # --- 2. 历史脱水 (Context Dehydration) ---
            processed_messages = self._dehydrate_history(full_system_prompt)

            # --- 3. 调用模型 (流式输出 Thought) ---
            response = self.llm.chat(
                messages=processed_messages,
                tools=self._get_tool_schemas(),
                on_token=on_thought_token # 将流式 Token 传回给 CLI 渲染
            )

            self.messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": self._serialize_tool_calls(response.tool_calls) if response.tool_calls else None
            })

            if not response.tool_calls:
                return response.content

            # --- 4. 执行观察 (Observation) ---
            for call in response.tool_calls:
                if on_tool_start:
                    on_tool_start(call.name) # 发出工具执行信号
                
                observation = self._execute_tool_call(call)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": observation.message
                })

        return "已达推理上限，请审核目前进展。"

    def _synthesize_system_prompt(self) -> str:
        """实时从 context 抓取项目状态，合成最新的 SYSTEM_PROMPT"""
        pending = self.context.artifacts.load_pending_patch()
        scan_files = sorted(self.context.artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        last_scan_id = scan_files[0].stem if scan_files else None
        
        workbench = build_workbench_prompt(
            pending_file=pending.file_path if pending else None,
            last_scan=last_scan_id
        )
        return SYSTEM_PROMPT + workbench

    def _dehydrate_history(self, current_system_prompt: str) -> list[dict[str, Any]]:
        """
        对历史消息进行压缩脱水：
        - 始终使用最新的 System Prompt。
        - 对超过 3 轮以前的 Observation（工具返回内容）进行截断或摘要化。
        """
        result = [{"role": "system", "content": current_system_prompt}]
        
        # 仅处理最近的 N 条消息
        history_window = self.messages[-10:] 
        
        for msg in history_window:
            new_msg = dict(msg)
            # 如果是工具返回结果，且不在最近 3 条消息内，执行脱水
            if msg["role"] == "tool" and msg in self.messages[:-3]:
                content = str(msg["content"])
                if len(content) > 200:
                    # 仅保留前 100 个字符并标记脱水
                    new_msg["content"] = content[:100] + "\n... [此处内容已由于历史过久而脱水压缩] ..."
            
            result.append(new_msg)
            
        return result

    def _execute_tool_call(self, call: ToolCall) -> ToolResult:
        """执行单个工具调用，将单例 context 注入其中"""
        tool = self.available_tools.get(call.name)
        if not tool:
            return ToolResult(status="error", message=f"未找到工具：{call.name}")
        try:
            return tool.execute(self.context, **call.arguments)
        except Exception as e:
            return ToolResult(status="error", message=f"执行异常：{str(e)}")

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in self.available_tools.values()]

    def _serialize_tool_calls(self, calls: list[ToolCall]) -> list[dict[str, Any]]:
        return [{"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]

    @property
    def label(self) -> str:
        return self.llm.label
