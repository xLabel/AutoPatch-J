from __future__ import annotations

from typing import Any, Callable

from .context_window import ContextCapacityError, ModelContextProfile, estimate_request_tokens
from .diagnostics import MAX_RAW_LLM_ERROR_CHARS, format_raw_llm_exception
from .errors import (
    LLMContentFilterError,
    LLMProtocolError,
    LLMTerminalError,
    LLMTransientError,
    LLMTruncationError,
)
from .models import LLMResponse
from .options import LLMCallDiagnostic, LLMCallPurpose, resolve_request_options
from .parser import LLMResponseParser
from .providers import ProviderProfile, resolve_provider_profile
from .request import LLMRequestBuilder
from .transport import OpenAIChatTransport


_MEMORY_PURPOSES = {
    LLMCallPurpose.MEMORY_EXTRACTION,
    LLMCallPurpose.MEMORY_EXTRACTION_REPAIR,
    LLMCallPurpose.MEMORY_CONSOLIDATION,
    LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR,
}


class LLMClient:
    """Typed Chat Completions gateway for one logical LLM operation."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        context_profile: ModelContextProfile | None = None,
    ) -> None:
        self.model = model
        self.context_profile = context_profile
        self.provider: ProviderProfile = resolve_provider_profile(model)
        self.transport = OpenAIChatTransport(api_key=api_key, base_url=base_url)
        self.request_builder = LLMRequestBuilder(
            model=model,
            provider=self.provider,
            max_output_tokens=(
                context_profile.max_output_tokens if context_profile is not None else None
            ),
        )
        self.response_parser = LLMResponseParser()
        self.diagnostics: list[LLMCallDiagnostic] = []

    @property
    def client(self) -> Any:
        return self.transport.client

    @client.setter
    def client(self, value: Any) -> None:
        self.transport.client = value

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
        on_content_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        options = resolve_request_options(purpose)
        sdk_retries_taken: int | None = None
        request_max_tokens: int | None = None
        try:
            if (
                self.context_profile is not None
                and estimate_request_tokens(messages, tools)
                > self.context_profile.input_capacity
            ):
                raise ContextCapacityError(
                    "LLM 请求超过当前 context profile 的 input capacity。"
                )
            kwargs = self.request_builder.build_request_kwargs(
                messages=messages,
                tools=tools,
                options=options,
            )
            request_max_tokens = kwargs.get("max_tokens") or kwargs.get(
                "max_completion_tokens"
            )
            completion = self.transport.create_completion(kwargs=kwargs, options=options)
            sdk_retries_taken = completion.sdk_retries_taken
            if options.stream:
                parsed = self.response_parser.parse_stream_response(
                    completion.response,
                    on_content_delta=on_content_delta,
                    on_reasoning_delta=on_reasoning_delta,
                )
            else:
                parsed = self.response_parser.parse_non_stream_response(
                    completion.response,
                    on_content_delta=on_content_delta,
                )
            self.provider.validate_response(parsed, options)
            self._validate_finish_reason(parsed)
        except Exception as exc:
            self._record_diagnostic(
                purpose,
                "error",
                (
                    type(exc).__name__
                    if purpose in _MEMORY_PURPOSES
                    else format_raw_llm_exception(exc)
                ),
                max_tokens=request_max_tokens,
                sdk_retries_taken=sdk_retries_taken,
                response_id=getattr(exc, "response_id", None),
                finish_reason=getattr(exc, "finish_reason", None),
            )
            raise
        self._record_diagnostic(
            purpose,
            "ok",
            response_id=parsed.response_id,
            finish_reason=parsed.finish_reason,
            max_tokens=request_max_tokens,
            sdk_retries_taken=sdk_retries_taken,
        )
        return parsed

    def _validate_finish_reason(self, response: LLMResponse) -> None:
        finish_reason = response.finish_reason
        if finish_reason in {"stop", "tool_calls"}:
            return
        if finish_reason == "insufficient_system_resource":
            raise LLMTransientError(
                "Provider reported insufficient system resources.",
                response_id=response.response_id,
                finish_reason=finish_reason,
            )
        if finish_reason == "length":
            raise LLMTruncationError(
                "Provider response reached the output limit.",
                response_id=response.response_id,
                finish_reason=finish_reason,
            )
        if finish_reason == "content_filter":
            raise LLMContentFilterError(
                "Provider filtered the response content.",
                response_id=response.response_id,
                finish_reason=finish_reason,
            )
        if finish_reason is None:
            raise LLMProtocolError("Chat completion is missing finish_reason.")
        raise LLMTerminalError(
            f"Unsupported finish_reason: {finish_reason}",
            response_id=response.response_id,
            finish_reason=finish_reason,
        )

    def _record_diagnostic(
        self,
        purpose: LLMCallPurpose,
        status: str,
        error: str = "",
        *,
        response_id: str | None = None,
        finish_reason: str | None = None,
        max_tokens: int | None = None,
        sdk_retries_taken: int | None = None,
    ) -> None:
        options = resolve_request_options(purpose)
        self.diagnostics.append(
            LLMCallDiagnostic(
                purpose=purpose,
                stream=options.stream,
                reasoning=options.reasoning,
                max_tokens=max_tokens if max_tokens is not None else options.max_tokens,
                temperature=options.temperature,
                timeout_seconds=options.timeout_seconds,
                status=status,
                error=error[:MAX_RAW_LLM_ERROR_CHARS],
                response_id=response_id,
                finish_reason=finish_reason,
                sdk_max_retries=options.sdk_max_retries,
                sdk_retries_taken=sdk_retries_taken,
            )
        )
        self.diagnostics = self.diagnostics[-20:]
