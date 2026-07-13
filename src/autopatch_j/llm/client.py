from __future__ import annotations

from typing import Any, Callable

from .diagnostics import MAX_RAW_LLM_ERROR_CHARS, format_raw_llm_exception
from .dialects import MessageDialect, StandardDialect, DeepSeekAliyunDialect
from .models import LLMResponse
from .options import LLMCallDiagnostic, LLMCallPurpose, resolve_request_options
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
        self.diagnostics: list[LLMCallDiagnostic] = []

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
        options = resolve_request_options(purpose)
        try:
            kwargs = self.request_builder.build_request_kwargs(
                messages=messages,
                tools=tools,
                options=options,
            )
            response = self.transport.create_completion(kwargs=kwargs, options=options)
            if not options.stream:
                parsed = self.response_parser.parse_non_stream_response(
                    response,
                    on_content_delta=on_content_delta,
                )
            else:
                parsed = self.response_parser.parse_stream_response(
                    response,
                    on_content_delta=on_content_delta,
                    on_reasoning_delta=on_reasoning_delta,
                )
        except Exception as exc:
            self._record_diagnostic(
                purpose,
                "error",
                format_raw_llm_exception(exc),
            )
            raise
        self._record_diagnostic(purpose, "ok")
        return parsed

    def _record_diagnostic(self, purpose: LLMCallPurpose, status: str, error: str = "") -> None:
        options = resolve_request_options(purpose)
        self.diagnostics.append(
            LLMCallDiagnostic(
                purpose=purpose,
                stream=options.stream,
                reasoning=options.reasoning,
                max_tokens=options.max_tokens,
                temperature=options.temperature,
                timeout_seconds=options.timeout_seconds,
                status=status,
                error=error[:MAX_RAW_LLM_ERROR_CHARS],
            )
        )
        self.diagnostics = self.diagnostics[-20:]
