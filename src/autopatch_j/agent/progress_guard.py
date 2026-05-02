from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from autopatch_j.llm.dialect import ToolCall
from autopatch_j.tools.base import ToolResult


@dataclass(frozen=True, slots=True)
class ReactStepTrace:
    """一条 ReAct 工具执行轨迹，用于判断工具调用是否仍在推进任务。"""

    tool_name: str
    normalized_args: str
    status: str
    normalized_summary: str


@dataclass(frozen=True, slots=True)
class ReactGuardResult:
    """ReAct 进展守卫的判断结果。"""

    blocked: bool
    reason: str = ""


class ReactProgressGuard:
    """
    ReAct 工具调用的程序侧无进展守卫。

    它不判断业务对错，也不依赖 LLM 再做一次裁决；只基于工具名、标准化参数、
    工具状态和工具摘要识别明显重复。这样可以在模型陷入机械重复时提前停止，
    但仍把主要任务边界交给 Workflow、工具白名单和 ReAct 轮数上限控制。
    """

    repeat_threshold: int = 3
    max_history: int = 8

    def __init__(self) -> None:
        self._history: list[ReactStepTrace] = []

    def record(self, trace: ReactStepTrace) -> ReactGuardResult:
        self._history.append(trace)
        self._history = self._history[-self.max_history :]

        if self._has_repeated_trace():
            return ReactGuardResult(True, "连续 3 次重复工具调用且结果无变化")
        if self._has_failed_short_cycle():
            return ReactGuardResult(True, "检测到失败工具调用短周期循环")
        return ReactGuardResult(False)

    def _has_repeated_trace(self) -> bool:
        if len(self._history) < self.repeat_threshold:
            return False
        recent = self._history[-self.repeat_threshold :]
        return all(trace == recent[0] for trace in recent)

    def _has_failed_short_cycle(self) -> bool:
        if len(self._history) < 4:
            return False
        a1, b1, a2, b2 = self._history[-4:]
        if any(trace.status == "ok" for trace in (a1, b1, a2, b2)):
            return False
        return a1 == a2 and b1 == b2 and a1 != b1


def build_react_step_trace(call: ToolCall, result: ToolResult) -> ReactStepTrace:
    return ReactStepTrace(
        tool_name=call.name,
        normalized_args=normalize_tool_arguments(call.arguments),
        status=str(result.status or "unknown").strip().lower(),
        normalized_summary=normalize_observation_summary(result.summary or result.message),
    )


def normalize_tool_arguments(arguments: dict[str, Any]) -> str:
    clipped = _clip_nested(arguments)
    try:
        return json.dumps(clipped, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(clipped)


def normalize_observation_summary(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    normalized = " ".join(normalized.split())
    return normalized[:240]


def _clip_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clip_nested(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_clip_nested(item) for item in value[:20]]
    if isinstance(value, str):
        return normalize_observation_summary(value)
    return value
