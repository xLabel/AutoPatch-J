from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from autopatch_j.agent.callbacks import AgentCallbacks
from autopatch_j.agent.context_manager import (
    ContextCapacityError,
    RequestContextBudget,
    RequestContextManager,
    clip_text_to_tokens,
    is_context_overflow_error,
)
from autopatch_j.agent.message_adapter import AgentMessageAdapter
from autopatch_j.agent.messages import AgentMessage
from autopatch_j.agent.progress_guard import ReactProgressGuard, build_react_step_trace
from autopatch_j.agent.tool_executor import ToolExecutor
from autopatch_j.llm.client import LLMClient
from autopatch_j.llm.context_window import (
    ModelContextProfile,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from autopatch_j.llm.models import LLMResponse
from autopatch_j.llm.options import LLMCallPurpose
from autopatch_j.config import GlobalConfig
from autopatch_j.tools.names import FunctionToolName


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """一次 ReAct 请求的最终回答和请求级执行轨迹。"""

    final_answer: str
    trace_messages: list[dict[str, Any]]


class ReActRunner:
    """
    ReAct 主循环执行器。

    这里集中处理 LLM 调用、请求级消息追加、tool call 回写和无进展阻断。
    它不理解具体业务意图，也不负责构造任务 prompt。
    """

    max_steps: int = 10

    def __init__(
        self,
        llm: LLMClient | None,
        message_adapter: AgentMessageAdapter,
        tool_executor: ToolExecutor,
    ) -> None:
        self.llm = llm
        self.message_adapter = message_adapter
        self.tool_executor = tool_executor

    def run(
        self,
        user_text: str,
        system_prompt: str,
        allowed_tool_names: tuple[FunctionToolName, ...],
        callbacks: AgentCallbacks,
        initial_history: list[dict[str, Any]] | None = None,
        advisory_context: str = "",
        thread_checkpoint: str = "",
        advisory_context_provider: Callable[[bool], str] | None = None,
    ) -> AgentRunResult:
        messages = [dict(message) for message in initial_history or []]
        trace_start = len(messages)
        messages.append(AgentMessage.user(user_text).to_record())

        if not self.llm:
            answer = "LLM 配置缺失。请设置 AUTOPATCH_LLM_API_KEY 后重启。"
            messages.append(AgentMessage.assistant(answer, None, None).to_record())
            return AgentRunResult(final_answer=answer, trace_messages=messages[trace_start:])

        progress_guard = ReactProgressGuard()
        allowed_tools = set(allowed_tool_names)
        tool_schemas = self.message_adapter.tool_schemas(allowed_tool_names)
        context_profile = getattr(self.llm, "context_profile", None)
        if not isinstance(context_profile, ModelContextProfile):
            context_profile = GlobalConfig.resolve_llm_context_profile()
        context_manager = RequestContextManager(context_profile, self.message_adapter)
        hard_recall = False

        for _ in range(self.max_steps):
            current_advisory_context = (
                advisory_context_provider(hard_recall)
                if advisory_context_provider is not None
                else advisory_context
            )
            try:
                prepared = context_manager.prepare(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tool_schemas,
                    initial_history_count=trace_start,
                    checkpoint_builder=self._build_runtime_checkpoint,
                    advisory_context=current_advisory_context,
                    thread_checkpoint=thread_checkpoint,
                )
            except ContextCapacityError:
                hard_recall = True
                prepared = context_manager.prepare(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tool_schemas,
                    initial_history_count=trace_start,
                    checkpoint_builder=self._build_runtime_checkpoint,
                    force_hard_rebuild=True,
                    advisory_context=(
                        advisory_context_provider(True)
                        if advisory_context_provider is not None
                        else advisory_context
                    ),
                    thread_checkpoint=thread_checkpoint,
                )
            try:
                response = self._chat_react(prepared.messages, tool_schemas, callbacks)
            except Exception as exc:
                if not is_context_overflow_error(exc):
                    raise
                hard_recall = True
                hard_rebuilt = context_manager.prepare(
                    messages=messages,
                    system_prompt=system_prompt,
                    tools=tool_schemas,
                    initial_history_count=trace_start,
                    checkpoint_builder=self._build_runtime_checkpoint,
                    force_hard_rebuild=True,
                    advisory_context=(
                        advisory_context_provider(True)
                        if advisory_context_provider is not None
                        else advisory_context
                    ),
                    thread_checkpoint=thread_checkpoint,
                )
                response = self._chat_react(hard_rebuilt.messages, tool_schemas, callbacks)

            messages.append(
                AgentMessage.assistant(
                    content=response.content or "...",
                    tool_calls=(
                        self.message_adapter.serialize_tool_calls(response.tool_calls)
                        if response.tool_calls
                        else None
                    ),
                    reasoning_content=response.reasoning_content,
                ).to_record()
            )

            if not response.tool_calls:
                return AgentRunResult(
                    final_answer=response.content or "",
                    trace_messages=messages[trace_start:],
                )

            for call in response.tool_calls:
                if callbacks.on_tool_start:
                    callbacks.on_tool_start(call.name)

                observation = self.tool_executor.execute(call, allowed_tools)
                if callbacks.on_observation:
                    callbacks.on_observation(observation.message, observation.summary)

                messages.append(
                    AgentMessage.tool(
                        tool_call_id=call.call_id,
                        name=call.name,
                        content=observation.message,
                        status=observation.status,
                        summary=observation.summary,
                        payload=observation.payload,
                    ).to_record()
                )

                guard_result = progress_guard.record(build_react_step_trace(call, observation))
                if guard_result.blocked:
                    stuck_message = f"检测到工具调用无进展：{guard_result.reason}。已主动停止，请人工介入审查。"
                    if callbacks.on_observation:
                        callbacks.on_observation(stuck_message, "工具调用无进展，已阻断")
                    messages.append(AgentMessage.assistant(stuck_message, None, None).to_record())
                    return AgentRunResult(
                        final_answer=stuck_message,
                        trace_messages=messages[trace_start:],
                    )

        answer = "已达推理上限，请审阅当前结果。"
        messages.append(AgentMessage.assistant(answer, None, None).to_record())
        return AgentRunResult(final_answer=answer, trace_messages=messages[trace_start:])

    def _chat_react(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        callbacks: AgentCallbacks,
    ) -> LLMResponse:
        assert self.llm is not None
        return self.llm.chat(
            messages=messages,
            tools=tools,
            purpose=LLMCallPurpose.REACT,
            on_content_delta=callbacks.on_token,
            on_reasoning_delta=callbacks.on_reasoning,
        )

    def _build_runtime_checkpoint(
        self,
        older_messages: list[dict[str, Any]],
        previous_checkpoint: str | None,
    ) -> str:
        assert self.llm is not None
        transcript = [
            {
                key: value
                for key, value in message.items()
                if key != "reasoning_content"
            }
            for message in older_messages
        ]
        serialized = json.dumps(transcript, ensure_ascii=False, separators=(",", ":"))
        profile = getattr(self.llm, "context_profile", None)
        if not isinstance(profile, ModelContextProfile):
            profile = GlobalConfig.resolve_llm_context_profile()
        checkpoint_budget = RequestContextBudget.from_profile(profile).checkpoint_tokens
        checkpoint = clip_text_to_tokens(previous_checkpoint or "", checkpoint_budget)
        remaining = serialized
        while remaining:
            wrapper_messages = _compaction_messages("", checkpoint)
            fragment_budget = min(
                int(profile.input_capacity * 0.60),
                profile.input_capacity - estimate_messages_tokens(wrapper_messages),
            )
            if fragment_budget <= 0:
                raise ContextCapacityError(
                    "runtime compaction 包装内容超过 LLM input capacity。"
                )
            fragment = _split_compaction_text(remaining, fragment_budget)[0]
            compaction_messages = _compaction_messages(fragment, checkpoint)
            if estimate_messages_tokens(compaction_messages) > profile.input_capacity:
                raise ContextCapacityError(
                    "runtime compaction fragment 超过 LLM input capacity。"
                )
            response = self.llm.chat(
                messages=compaction_messages,
                tools=None,
                purpose=LLMCallPurpose.CONTEXT_COMPACTION,
            )
            checkpoint = clip_text_to_tokens(
                response.content.strip(),
                checkpoint_budget,
            )
            if not checkpoint:
                break
            remaining = remaining[len(fragment) :]
        return checkpoint or ""


def _compaction_messages(fragment: str, checkpoint: str | None) -> list[dict[str, Any]]:
    prompt = (
        "把旧会话片段压缩成供同一个 coding task 继续执行的 checkpoint。\n"
        "只总结输入中存在的信息，不推断源码事实，不改写当前运行状态。\n"
        "这是 anchored iterative summary：Previous checkpoint 是上一片段的结果，"
        "必须合并而不是丢弃。\n"
        "必须按以下标题输出；没有内容写“无”：\n"
        "## Goal\n## User constraints\n## Finding and patch state\n"
        "## Verified facts\n## Decisions\n## Open questions\n"
        "## Next actions\n## Artifact references\n\n"
        f"Previous checkpoint:\n{checkpoint or '无'}\n\n"
        "Older discourse fragment (serialized JSON):\n"
        f"{fragment}"
    )
    return [
        {
            "role": "system",
            "content": (
                "你是 context compactor。保留任务连续性，明确区分用户要求、"
                "已验证事实和未解决问题；不得提出新补丁或新决策。"
            ),
        },
        {"role": "user", "content": prompt},
    ]


def _split_compaction_text(text: str, token_budget: int) -> list[str]:
    if estimate_text_tokens(text) <= token_budget:
        return [text]
    encoded = text.encode("utf-8")
    byte_budget = token_budget * 3
    fragments: list[str] = []
    offset = 0
    while offset < len(encoded):
        end = min(len(encoded), offset + byte_budget)
        while end > offset:
            try:
                fragments.append(encoded[offset:end].decode("utf-8"))
                break
            except UnicodeDecodeError:
                end -= 1
        if end == offset:
            raise ContextCapacityError("无法按 token 预算切分 runtime compaction 输入。")
        offset = end
    return fragments
