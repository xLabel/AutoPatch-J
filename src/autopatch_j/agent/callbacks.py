from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

TextCallback = Callable[[str], None]
ObservationCallback = Callable[[str, str | None], None]


@dataclass(frozen=True, slots=True)
class AgentCallbacks:
    """
    ReAct 执行过程的输出回调集合。

    Agent 内部明确区分可见文本、思考链、工具观察和工具启动事件，避免把
    不同签名的回调用同一个类型别名硬凑在一起。
    """

    on_token: TextCallback | None = None
    on_reasoning: TextCallback | None = None
    on_observation: ObservationCallback | None = None
    on_tool_start: TextCallback | None = None
