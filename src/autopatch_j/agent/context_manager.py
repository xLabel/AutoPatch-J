from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from autopatch_j.llm.context_window import (
    ModelContextProfile,
    estimate_messages_tokens,
    estimate_text_tokens,
    estimate_tools_tokens,
)

if TYPE_CHECKING:
    from autopatch_j.agent.message_adapter import AgentMessageAdapter


KIBI = 1024


class ContextPressure(str, Enum):
    NORMAL = "normal"
    PRUNE_TOOLS = "prune_tools"
    COMPACT = "compact"
    OVERFLOW = "overflow"


@dataclass(frozen=True, slots=True)
class RequestContextBudget:
    input_capacity: int
    soft_pressure_tokens: int
    compaction_pressure_tokens: int
    recent_history_tokens: int
    recent_tail_tokens: int
    checkpoint_tokens: int
    durable_recall_tokens: int
    memory_map_tokens: int

    @classmethod
    def from_profile(cls, profile: ModelContextProfile) -> "RequestContextBudget":
        capacity = profile.input_capacity
        recall = min(max(int(capacity * 0.12), 4 * KIBI), 24 * KIBI)
        return cls(
            input_capacity=capacity,
            soft_pressure_tokens=int(capacity * 0.80),
            compaction_pressure_tokens=int(capacity * 0.85),
            recent_history_tokens=min(int(capacity * 0.40), 384 * KIBI),
            recent_tail_tokens=min(int(capacity * 0.20), 128 * KIBI),
            checkpoint_tokens=min(int(capacity * 0.04), 16 * KIBI),
            durable_recall_tokens=recall,
            memory_map_tokens=min(recall // 2, 8 * KIBI),
        )

    def pressure_for(self, total_tokens: int) -> ContextPressure:
        if total_tokens > self.input_capacity:
            return ContextPressure.OVERFLOW
        if total_tokens > self.compaction_pressure_tokens:
            return ContextPressure.COMPACT
        if total_tokens > self.soft_pressure_tokens:
            return ContextPressure.PRUNE_TOOLS
        return ContextPressure.NORMAL


@dataclass(frozen=True, slots=True)
class ContextUsage:
    system_tokens: int = 0
    tool_schema_tokens: int = 0
    checkpoint_tokens: int = 0
    recent_history_tokens: int = 0
    memory_map_tokens: int = 0
    memory_detail_tokens: int = 0
    current_input_tokens: int = 0
    react_trace_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return sum(
            (
                self.system_tokens,
                self.tool_schema_tokens,
                self.checkpoint_tokens,
                self.recent_history_tokens,
                self.memory_map_tokens,
                self.memory_detail_tokens,
                self.current_input_tokens,
                self.react_trace_tokens,
            )
        )

    @property
    def durable_recall_tokens(self) -> int:
        return self.memory_map_tokens + self.memory_detail_tokens

    @property
    def session_continuity_tokens(self) -> int:
        return self.checkpoint_tokens + self.recent_history_tokens


def required_compaction_reclaim(previous_tokens: int) -> int:
    return max(32 * KIBI, int(previous_tokens * 0.10))


class ContextCapacityError(RuntimeError):
    """请求中的必需组件无法在当前模型容量内安全装配。"""


@dataclass(frozen=True, slots=True)
class PreparedContext:
    messages: list[dict[str, Any]]
    usage: ContextUsage
    pressure: ContextPressure
    pruned_tool_results: int = 0
    compacted: bool = False


CheckpointBuilder = Callable[[list[dict[str, Any]], str | None], str]


class RequestContextManager:
    """一次 ReAct 请求内的 context 投影器；原始消息轨迹始终保持 append-only。"""

    def __init__(
        self,
        profile: ModelContextProfile,
        message_adapter: AgentMessageAdapter,
    ) -> None:
        self.profile = profile
        self.budget = RequestContextBudget.from_profile(profile)
        self.message_adapter = message_adapter
        self._checkpoint: str | None = None
        self._compacted_through = 0

    def prepare(
        self,
        *,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        initial_history_count: int,
        checkpoint_builder: CheckpointBuilder,
        force_hard_rebuild: bool = False,
        advisory_context: str = "",
        thread_checkpoint: str = "",
    ) -> PreparedContext:
        start = min(self._compacted_through, len(messages))
        active_messages, active_history_count = self._active_messages(
            messages,
            start=start,
            current_input_index=initial_history_count,
        )
        projected = self._project(
            active_messages,
            system_prompt,
            prune_tools=False,
            aggressive=False,
            advisory_context=advisory_context,
            advisory_index=active_history_count,
            thread_checkpoint=thread_checkpoint,
        )
        initial_tokens = self._request_tokens(projected, tools)
        initial_pressure = self.budget.pressure_for(initial_tokens)
        if initial_pressure is ContextPressure.NORMAL and not force_hard_rebuild:
            return self._prepared(
                projected,
                tools,
                initial_history_count=active_history_count,
                pressure=initial_pressure,
            )

        pruned = self._project(
            active_messages,
            system_prompt,
            prune_tools=True,
            aggressive=force_hard_rebuild,
            advisory_context=advisory_context,
            advisory_index=active_history_count,
            thread_checkpoint=thread_checkpoint,
        )
        pruned_count = self._count_pruned_results(projected, pruned)
        pruned_tokens = self._request_tokens(pruned, tools)
        pruned_pressure = self.budget.pressure_for(pruned_tokens)
        if (
            not force_hard_rebuild
            and pruned_tokens <= self.budget.compaction_pressure_tokens
        ):
            return self._prepared(
                pruned,
                tools,
                initial_history_count=active_history_count,
                pressure=pruned_pressure,
                pruned_tool_results=pruned_count,
            )

        tail_budget = self.budget.recent_tail_tokens
        if force_hard_rebuild:
            tail_budget = min(tail_budget // 2, 64 * KIBI)
        tail_start = self._recent_tail_start(messages, tail_budget, lower_bound=start)
        if tail_start <= start:
            if pruned_tokens <= self.budget.input_capacity:
                return self._prepared(
                    pruned,
                    tools,
                    initial_history_count=active_history_count,
                    pressure=pruned_pressure,
                    pruned_tool_results=pruned_count,
                )
            raise ContextCapacityError(
                "当前请求超过 LLM input capacity，且没有可继续压缩的旧消息。"
            )

        older_messages = messages[start:tail_start]
        if start <= initial_history_count < tail_start:
            current_offset = initial_history_count - start
            older_messages = [
                *older_messages[:current_offset],
                *older_messages[current_offset + 1 :],
            ]
        older_wire = self.message_adapter.dehydrate_history(
            older_messages,
            "",
            prune_replayable_tools=True,
            aggressive=True,
        )[1:]
        checkpoint = checkpoint_builder(older_wire, self._checkpoint).strip()
        if not checkpoint:
            raise ContextCapacityError("runtime context compaction 未生成有效 checkpoint。")
        checkpoint = clip_text_to_tokens(checkpoint, self.budget.checkpoint_tokens)

        tail, tail_history_count = self._active_messages(
            messages,
            start=tail_start,
            current_input_index=initial_history_count,
        )
        rebuilt = self._project(
            tail,
            system_prompt,
            prune_tools=True,
            aggressive=force_hard_rebuild,
            checkpoint=checkpoint,
            advisory_context=advisory_context,
            advisory_index=tail_history_count,
            thread_checkpoint=thread_checkpoint,
        )
        rebuilt_tokens = self._request_tokens(rebuilt, tools)
        reclaimed = pruned_tokens - rebuilt_tokens
        has_new_progress = self._checkpoint is not None and tail_start > start
        if reclaimed < required_compaction_reclaim(pruned_tokens) and not has_new_progress:
            raise ContextCapacityError(
                "runtime context compaction 未回收足够容量，已停止重复压缩。"
            )
        if rebuilt_tokens > self.budget.input_capacity:
            raise ContextCapacityError(
                "system contract、当前输入与 recent tail 超过 LLM input capacity。"
            )

        self._checkpoint = checkpoint
        self._compacted_through = tail_start
        return self._prepared(
            rebuilt,
            tools,
            initial_history_count=tail_history_count,
            pressure=self.budget.pressure_for(rebuilt_tokens),
            pruned_tool_results=pruned_count,
            compacted=True,
        )

    @staticmethod
    def _active_messages(
        messages: list[dict[str, Any]],
        *,
        start: int,
        current_input_index: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """投影未压缩消息，并在 checkpoint 越过它后仍保留当前 user 输入。"""

        if start <= current_input_index:
            return messages[start:], max(0, current_input_index - start)
        if current_input_index < len(messages):
            return [messages[current_input_index], *messages[start:]], 0
        return messages[start:], 0

    def _project(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        *,
        prune_tools: bool,
        aggressive: bool,
        checkpoint: str | None = None,
        advisory_context: str = "",
        advisory_index: int = 0,
        thread_checkpoint: str = "",
    ) -> list[dict[str, Any]]:
        projected = self.message_adapter.dehydrate_history(
            messages,
            system_prompt,
            prune_replayable_tools=prune_tools,
            aggressive=aggressive,
        )
        effective_checkpoint = checkpoint if checkpoint is not None else self._checkpoint
        if effective_checkpoint:
            projected.insert(
                1,
                {
                    "role": "user",
                    "content": (
                        "<runtime_checkpoint>\n"
                        f"{effective_checkpoint}\n"
                        "</runtime_checkpoint>\n"
                        "这是有损的会话检查点；当前用户输入和当前工具证据优先。"
                    ),
                },
            )
        if thread_checkpoint:
            projected.insert(
                1 + (1 if effective_checkpoint else 0),
                {
                    "role": "user",
                    "content": (
                        "<thread_checkpoint>\n"
                        f"{thread_checkpoint}\n"
                        "</thread_checkpoint>\n"
                        "这是较早普通对话的有损检查点；recent history 和当前输入优先。"
                    ),
                },
            )
        if advisory_context:
            insertion_index = 1 + min(max(advisory_index, 0), len(messages))
            if effective_checkpoint:
                insertion_index += 1
            if thread_checkpoint:
                insertion_index += 1
            projected.insert(
                insertion_index,
                {
                    "role": "user",
                    "content": (
                        "<memory_map>\n"
                        f"{advisory_context}\n"
                        "</memory_map>\n"
                        "这是项目 Memory 的有界召回，不是源码事实；当前用户指令优先。"
                    ),
                },
            )
        return projected

    def _prepared(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        initial_history_count: int,
        pressure: ContextPressure,
        pruned_tool_results: int = 0,
        compacted: bool = False,
    ) -> PreparedContext:
        system_tokens = estimate_messages_tokens(messages[:1]) if messages else 0
        checkpoint_tokens = sum(
            estimate_messages_tokens([message])
            for message in messages
            if str(message.get("content", "")).startswith(
                ("<runtime_checkpoint>", "<thread_checkpoint>")
            )
        )
        memory_map_tokens = sum(
            estimate_messages_tokens([message])
            for message in messages
            if str(message.get("content", "")).startswith("<memory_map>")
        )
        body = [
            message
            for message in messages[1:]
            if not str(message.get("content", "")).startswith(
                ("<runtime_checkpoint>", "<thread_checkpoint>", "<memory_map>")
            )
        ]
        history_count = min(initial_history_count, len(body))
        history_tokens = estimate_messages_tokens(body[:history_count])
        current_input = body[history_count : history_count + 1]
        trace = body[history_count + 1 :]
        memory_detail_tokens = sum(
            estimate_messages_tokens([message])
            for message in trace
            if message.get("role") == "tool"
            and message.get("name") in {"memory_search", "memory_read"}
        )
        trace_tokens = max(
            0,
            estimate_messages_tokens(trace) - memory_detail_tokens,
        )
        usage = ContextUsage(
            system_tokens=system_tokens,
            tool_schema_tokens=estimate_tools_tokens(tools),
            checkpoint_tokens=checkpoint_tokens,
            recent_history_tokens=history_tokens,
            memory_map_tokens=memory_map_tokens,
            memory_detail_tokens=memory_detail_tokens,
            current_input_tokens=estimate_messages_tokens(current_input),
            react_trace_tokens=trace_tokens,
        )
        return PreparedContext(
            messages=messages,
            usage=usage,
            pressure=pressure,
            pruned_tool_results=pruned_tool_results,
            compacted=compacted,
        )

    def _request_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> int:
        return estimate_messages_tokens(messages) + estimate_tools_tokens(tools)

    def _recent_tail_start(
        self,
        messages: list[dict[str, Any]],
        token_budget: int,
        *,
        lower_bound: int,
    ) -> int:
        units = _message_units(messages, lower_bound)
        total = 0
        start = len(messages)
        for unit_start, unit_end in reversed(units):
            unit_tokens = estimate_messages_tokens(messages[unit_start:unit_end])
            if start < len(messages) and total + unit_tokens > token_budget:
                break
            start = unit_start
            total += unit_tokens
        return start

    def _count_pruned_results(
        self,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
    ) -> int:
        return sum(
            1
            for original, projected in zip(before, after)
            if original.get("role") == "tool"
            and original.get("content") != projected.get("content")
        )


def _message_units(
    messages: list[dict[str, Any]],
    lower_bound: int,
) -> list[tuple[int, int]]:
    """把 assistant tool call 与紧随其后的 result 作为不可拆协议单元。"""

    units: list[tuple[int, int]] = []
    index = lower_bound
    while index < len(messages):
        message = messages[index]
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        call_ids = {
            str(call.get("id", ""))
            for call in calls or []
            if isinstance(call, dict) and call.get("id")
        }
        end = index + 1
        if call_ids:
            while end < len(messages):
                candidate = messages[end]
                if (
                    candidate.get("role") != "tool"
                    or str(candidate.get("tool_call_id", "")) not in call_ids
                ):
                    break
                end += 1
        units.append((index, end))
        index = end
    return units


def clip_text_to_tokens(text: str, max_tokens: int) -> str:
    if estimate_text_tokens(text) <= max_tokens:
        return text
    encoded = text.encode("utf-8")
    clipped = encoded[: max_tokens * 3]
    while clipped:
        try:
            result = clipped.decode("utf-8").rstrip()
            return f"{result}\n… [checkpoint 已按预算截断]"
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return ""


def is_context_overflow_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".casefold()
    markers = (
        "context_length_exceeded",
        "maximum context length",
        "max context length",
        "context window exceeded",
        "input is too long",
        "request too large for model",
        "too many input tokens",
    )
    return any(marker in message for marker in markers)
