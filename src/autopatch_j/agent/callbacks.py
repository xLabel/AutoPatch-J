"""模型文本、推理和工具观察的展示回调契约。"""

from __future__ import annotations

import dataclasses
from typing import Callable
from typing import TypedDict

TextCallback = Callable[[str], None]
ObservationCallback = Callable[[str, str | None], None]


@dataclasses.dataclass(frozen=True, slots=True)
class AgentCallbacks:
    """ReAct 执行过程的输出回调集合。

    Agent 内部明确区分可见文本、思考链、工具观察和工具启动事件，避免把
    不同签名的回调用同一个类型别名硬凑在一起。
    """

    on_token: TextCallback | None = None
    on_reasoning: TextCallback | None = None
    on_observation: ObservationCallback | None = None
    on_tool_start: TextCallback | None = None


class OutputCallbacks(TypedDict, total=False):
    """公开调用可传入的展示回调，与 dataclass 共享准确签名。"""

    on_token: TextCallback | None
    on_reasoning: TextCallback | None
    on_observation: ObservationCallback | None
    on_tool_start: TextCallback | None
