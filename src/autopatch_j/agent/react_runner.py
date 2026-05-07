from __future__ import annotations

from typing import Any

from autopatch_j.agent.callbacks import AgentCallbacks
from autopatch_j.agent.message_adapter import AgentMessageAdapter
from autopatch_j.agent.progress_guard import ReactProgressGuard, build_react_step_trace
from autopatch_j.agent.tool_executor import ToolExecutor
from autopatch_j.llm.client import LLMCallPurpose, LLMClient
from autopatch_j.tools.names import FunctionToolName


class ReActRunner:
    """
    ReAct 主循环执行器。

    这里集中处理 LLM 调用、消息历史追加、tool call 回写和无进展阻断。
    它不理解具体业务意图，也不负责构造任务 prompt。
    """

    max_steps: int = 10

    def __init__(
        self,
        llm: LLMClient | None,
        messages: list[dict[str, Any]],
        message_adapter: AgentMessageAdapter,
        tool_executor: ToolExecutor,
    ) -> None:
        self.llm = llm
        self.messages = messages
        self.message_adapter = message_adapter
        self.tool_executor = tool_executor

    def run(
        self,
        user_text: str,
        system_prompt: str,
        allowed_tool_names: tuple[FunctionToolName, ...],
        callbacks: AgentCallbacks,
    ) -> str:
        if not self.llm:
            return "LLM 配置缺失。请设置 AUTOPATCH_LLM_API_KEY 后重启。"

        self.messages.append({"role": "user", "content": user_text})
        progress_guard = ReactProgressGuard()
        allowed_tools = set(allowed_tool_names)

        for _ in range(self.max_steps):
            processed_messages = self.message_adapter.dehydrate_history(self.messages, system_prompt)
            response = self.llm.chat(
                messages=processed_messages,
                tools=self.message_adapter.tool_schemas(allowed_tool_names),
                purpose=LLMCallPurpose.REACT,
                on_content_delta=callbacks.on_token,
                on_reasoning_delta=callbacks.on_reasoning,
            )

            self.messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "...",
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

            for call in response.tool_calls:
                if callbacks.on_tool_start:
                    callbacks.on_tool_start(call.name)

                observation = self.tool_executor.execute(call, allowed_tools)
                if callbacks.on_observation:
                    callbacks.on_observation(observation.message, observation.summary)

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

                guard_result = progress_guard.record(build_react_step_trace(call, observation))
                if guard_result.blocked:
                    stuck_message = f"检测到工具调用无进展：{guard_result.reason}。已主动停止，请人工介入审查。"
                    if callbacks.on_observation:
                        callbacks.on_observation(stuck_message, "工具调用无进展，已阻断")
                    return stuck_message

        return "已达推理上限，请审阅当前结果。"
