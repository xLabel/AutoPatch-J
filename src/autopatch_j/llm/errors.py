from __future__ import annotations


class LLMProtocolError(RuntimeError):
    """Provider response does not satisfy the supported chat protocol."""


class LLMResponseError(RuntimeError):
    """Operation failure reported by a parsed provider response."""

    def __init__(
        self,
        message: str,
        *,
        response_id: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.response_id = response_id
        self.finish_reason = finish_reason


class LLMTransientError(LLMResponseError):
    """Provider reported a transient operation-level failure."""


class LLMTruncationError(LLMResponseError):
    """Provider stopped because the configured output limit was reached."""


class LLMTerminalError(LLMResponseError):
    """Provider reported a terminal operation-level failure."""


class LLMContentFilterError(LLMTerminalError):
    """Provider refused the response because of content filtering."""
