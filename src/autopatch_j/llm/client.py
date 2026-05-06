from __future__ import annotations

from typing import Any, Callable

from .dialect import MessageDialect, StandardDialect, DeepSeekAliyunDialect
from .models import LLMResponse
from .options import LLMCallPurpose, LLMRequestOptions, resolve_request_options
from .parser import LLMResponseParser
from .request import LLMRequestBuilder
from .transport import OpenAIChatTransport


class LLMClient:
    """
    OpenAI 兼容 LLM 网关。

    职责边界：
    1. 封装聊天补全的流式调用，统一收集可见文本、reasoning 和 Tool Call。
    2. 通过 MessageDialect 兼容不同供应商的流式标签和工具调用格式。
    3. 不理解 AutoPatch-J 的业务意图；它只提供协议层输入输出。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str | None = None,
        stream_dialect: str = "standard",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.stream_dialect = stream_dialect
        self.transport = OpenAIChatTransport(api_key=api_key, base_url=base_url)
        self.request_builder = LLMRequestBuilder(model=model, reasoning_effort=reasoning_effort)
        self.response_parser = LLMResponseParser(self._create_dialect)

    @property
    def client(self) -> Any:
        return self.transport.client

    @client.setter
    def client(self, value: Any) -> None:
        self.transport.client = value

    def _create_dialect(self) -> MessageDialect:
        if self.stream_dialect == "bailian-dsml":
            return DeepSeekAliyunDialect()
        return StandardDialect()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        options = self._resolve_options(purpose)
        kwargs = self._build_request_kwargs(
            messages=messages,
            tools=tools,
            options=options,
        )
        response = self._create_completion(kwargs=kwargs, options=options)
        if not options.stream:
            return self._parse_non_stream_response(response, on_content_delta=on_content_delta)

        return self._parse_stream_response(
            response,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
        )

    def _resolve_options(self, purpose: LLMCallPurpose) -> LLMRequestOptions:
        return resolve_request_options(purpose)

    def _build_request_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: LLMRequestOptions,
    ) -> dict[str, Any]:
        return self.request_builder.build_request_kwargs(messages=messages, tools=tools, options=options)

    def _build_extra_body(self, options: LLMRequestOptions) -> dict[str, Any] | None:
        return self.request_builder._build_extra_body(options)

    def _load_global_extra_body(self) -> dict[str, Any]:
        return self.request_builder._load_global_extra_body()

    def _create_completion(self, kwargs: dict[str, Any], options: LLMRequestOptions) -> Any:
        return self.transport.create_completion(kwargs=kwargs, options=options)

    def _should_retry_without_disabled_reasoning(
        self,
        exc: Exception,
        kwargs: dict[str, Any],
        options: LLMRequestOptions,
    ) -> bool:
        return self.transport.should_retry_without_disabled_reasoning(exc=exc, kwargs=kwargs, options=options)

    def _parse_stream_response(
        self,
        response: Any,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        return self.response_parser.parse_stream_response(
            response,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
        )

    def _parse_non_stream_response(
        self,
        response: Any,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        return self.response_parser.parse_non_stream_response(response, on_content_delta=on_content_delta)


def build_default_llm_client() -> LLMClient | None:
    from autopatch_j.config import GlobalConfig
    if not GlobalConfig.llm_api_key:
        return None
    return LLMClient(
        api_key=GlobalConfig.llm_api_key,
        base_url=GlobalConfig.llm_base_url,
        model=GlobalConfig.llm_model,
        reasoning_effort=GlobalConfig.llm_reasoning_effort,
        stream_dialect=GlobalConfig.llm_stream_dialect,
    )
