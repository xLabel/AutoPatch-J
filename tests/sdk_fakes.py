"""使用原生 SDK 和内存 HTTP transport 的确定性模型场景。"""

from collections.abc import Awaitable
from collections.abc import Callable
import dataclasses
import json

import httpx2 as httpx
import openai
from pydantic_ai.models import openai as sdk_model
from pydantic_ai.profiles import openai as sdk_profile
from pydantic_ai.providers import openai as sdk_provider

from autopatch_j.llm import context_window
from autopatch_j.llm import runtime

AsyncClientClass = openai.AsyncOpenAI


@dataclasses.dataclass
class Reply:
    """一轮明确的服务端响应；None 表示流缺少结束状态。"""

    text: str = ""
    tool: str | None = None
    arguments: dict[str, object] = dataclasses.field(default_factory=dict)
    finish: str | None = "stop"
    reasoning: str = ""
    fragment_arguments: bool = False


class ScriptedModel:
    """录制 SDK 请求并返回脚本响应；没有网络 transport。"""

    def __init__(self, replies: list[Reply]):
        self.replies = iter(replies)
        self.requests: list[dict] = []
        self.before_reply: Callable[[], None] | None = None
        self.before_response: Callable[[], Awaitable[None]] | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.before_reply is not None:
            self.before_reply()
        reply = next(self.replies)
        delta: dict = {"content": reply.text}
        if reply.reasoning:
            delta["reasoning_content"] = reply.reasoning
        if reply.tool:
            delta["tool_calls"] = [
                {
                    "index": 0,
                    "id": f"call-{len(self.requests)}",
                    "type": "function",
                    "function": {
                        "name": reply.tool,
                        "arguments": json.dumps(reply.arguments),
                    },
                }
            ]
        base = {
            "id": f"response-{len(self.requests)}",
            "created": 1,
            "model": "deepseek-flash",
            "object": "chat.completion",
        }
        if not body.get("stream"):
            return httpx.Response(
                200,
                json={
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", **delta},
                            "finish_reason": reply.finish,
                        }
                    ],
                },
            )
        deltas = [delta]
        if reply.tool and reply.fragment_arguments:
            encoded = json.dumps(reply.arguments, ensure_ascii=False)
            delta["tool_calls"][0]["function"]["arguments"] = ""
            deltas.extend(
                {"tool_calls": [{"index": 0, "function": {"arguments": char}}]}
                for char in encoded
            )
        chunks: list[dict] = [
            {
                **base,
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": None}
                ],
            }
            for delta in deltas
        ]
        if reply.finish is not None:
            chunks.append(
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": reply.finish}
                    ],
                }
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )

    async def async_handle(self, request: httpx.Request) -> httpx.Response:
        if self.before_response is not None:
            await self.before_response()
        return self.handle(request)

    def runtime(self) -> runtime.ModelRuntime:
        client = AsyncClientClass(
            api_key="offline-placeholder",
            base_url="https://offline.test",
            max_retries=2,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(self.async_handle)
            ),
        )
        model = sdk_model.OpenAIChatModel(
            "deepseek-flash",
            provider=sdk_provider.OpenAIProvider(openai_client=client),
            profile=sdk_profile.OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=False,
                openai_chat_streaming_requires_finish_reason=True,
            ),
        )
        return runtime.ModelRuntime(
            model,
            context_window.resolve_context_profile(model="deepseek-flash"),
            client,
        )
