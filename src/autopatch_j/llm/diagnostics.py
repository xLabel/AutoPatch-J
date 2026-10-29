"""有界异常展示，不主动附加请求或认证上下文。"""

from __future__ import annotations

import json

MAX_RAW_LLM_ERROR_CHARS = 20_000
_TRUNCATION_MARKER = f"\n...[truncated to {MAX_RAW_LLM_ERROR_CHARS} characters]"


def format_raw_llm_exception(exc: Exception) -> str:
    """展示服务端 RAW 失败信息，不检查请求或认证上下文。"""

    exception_type = type(exc).__name__
    message = _safe_exception_message(exc)
    parts = [f"{exception_type}: {message}" if message else exception_type]

    for name in ("status_code", "code"):
        value = _safe_getattr(exc, name)
        if value is not None:
            parts.append(f"{name}: {_safe_value_text(value)}")

    body = _safe_getattr(exc, "body")
    body_text = _safe_value_text(body) if body is not None else ""
    if body_text:
        parts.append(f"body: {body_text}")

    response = _safe_getattr(exc, "response")
    response_label, response_text = _response_body(response)
    if response_text and response_text != body_text:
        parts.append(f"{response_label}: {response_text}")

    return _truncate("\n".join(parts))


def _safe_getattr(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    # 展示对象可有任意属性或序列化实现；次生错误不能掩盖原始失败。
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _safe_exception_message(exc: Exception) -> str:
    try:
        return str(exc)
    # 展示对象可有任意属性或序列化实现；次生错误不能掩盖原始失败。
    except Exception:  # pylint: disable=broad-exception-caught
        return "<unprintable exception message>"


def _safe_value_text(value: object) -> str:
    """尽量展示异常附带值，格式化次生错误不能覆盖原始失败。"""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    # 展示对象可有任意属性或序列化实现；次生错误不能掩盖原始失败。
    except Exception:  # pylint: disable=broad-exception-caught
        try:
            return str(value)
        # 自定义 __str__ 仍可能失败，最后仅展示类型。
        except Exception:  # pylint: disable=broad-exception-caught
            return f"<unprintable {type(value).__name__}>"


def _response_body(response: object | None) -> tuple[str, str]:
    """只读取异常响应正文，不扩展到请求、headers 或认证上下文。"""
    if response is None:
        return "", ""
    text = _safe_getattr(response, "text")
    if _has_body_value(text):
        return "response.text", _safe_value_text(text)
    content = _safe_getattr(response, "content")
    if _has_body_value(content):
        return "response.content", _safe_value_text(content)
    return "", ""


def _has_body_value(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value)
    return True


def _truncate(value: str) -> str:
    if len(value) <= MAX_RAW_LLM_ERROR_CHARS:
        return value
    prefix_length = MAX_RAW_LLM_ERROR_CHARS - len(_TRUNCATION_MARKER)
    return value[:prefix_length] + _TRUNCATION_MARKER
