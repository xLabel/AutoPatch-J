"""模型文本、推理和工具观察的展示回调契约。"""

from collections.abc import AsyncIterable
from collections.abc import Callable
import dataclasses

from pydantic_ai import messages
from pydantic_ai import tools

TextCallback = Callable[[str], None]
ObservationCallback = Callable[[str, str | None], None]


@dataclasses.dataclass(frozen=True, slots=True)
class AgentCallbacks:
    """Agent 运行过程的输出回调和原生流事件展示。

    Agent 内部明确区分可见文本、思考链、工具观察和工具启动事件，避免把
    不同签名的回调用同一个类型别名硬凑在一起。
    """

    on_token: TextCallback | None = None
    on_reasoning: TextCallback | None = None
    on_observation: ObservationCallback | None = None
    on_tool_start: TextCallback | None = None

    async def handle_stream_events[DepsT](
        self,
        ctx: tools.RunContext[DepsT],
        stream: AsyncIterable[messages.AgentStreamEvent],
    ) -> None:
        """仅展示 SDK 文本和 reasoning 事件，不读取或修改运行依赖。"""
        del ctx
        async for event in stream:
            if isinstance(event, messages.PartStartEvent):
                if isinstance(event.part, messages.TextPart) and self.on_token:
                    self.on_token(event.part.content)
                elif (
                    isinstance(event.part, messages.ThinkingPart)
                    and self.on_reasoning
                ):
                    self.on_reasoning(event.part.content)
            elif isinstance(event, messages.PartDeltaEvent):
                if (
                    isinstance(event.delta, messages.TextPartDelta)
                    and self.on_token
                ):
                    self.on_token(event.delta.content_delta)
                elif (
                    isinstance(event.delta, messages.ThinkingPartDelta)
                    and self.on_reasoning
                ):
                    self.on_reasoning(event.delta.content_delta or "")
