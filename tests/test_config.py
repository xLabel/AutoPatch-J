from __future__ import annotations

from autopatch_j.config import AppConfig
from autopatch_j.llm.context_window import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_OUTPUT_TOKENS,
)


def test_app_config_reads_llm_environment(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_API_KEY", "key")
    monkeypatch.setenv("AUTOPATCH_LLM_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "custom-model")
    monkeypatch.setenv("AUTOPATCH_DEBUG", "true")

    config = AppConfig.from_env()

    assert config.llm_api_key == "key"
    assert config.llm_base_url == "https://example.com/v1"
    assert config.llm_model == "custom-model"
    assert config.llm_context_window is None
    assert config.debug_mode is True
    assert config.is_llm_ready() is True
    assert config.llm_extra_body_error is None


def test_app_config_uses_defaults_and_isolates_ignored_dirs(monkeypatch) -> None:
    monkeypatch.delenv("AUTOPATCH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AUTOPATCH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AUTOPATCH_LLM_MODEL", raising=False)
    monkeypatch.delenv("AUTOPATCH_DEBUG", raising=False)

    first = AppConfig.from_env()
    second = AppConfig.from_env()
    first.ignored_dirs.add("custom")

    assert first.llm_api_key == ""
    assert first.llm_base_url == "https://api.deepseek.com"
    assert first.llm_model == "deepseek-v4-flash"
    profile = first.resolve_llm_context_profile()
    assert profile.context_window == DEFAULT_CONTEXT_WINDOW
    assert profile.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert first.debug_mode is False
    assert first.is_llm_ready() is False
    assert "custom" not in second.ignored_dirs


def test_app_config_keeps_invalid_extra_body_diagnostic(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_EXTRA_BODY", "{bad json")

    config = AppConfig.from_env()

    assert "AUTOPATCH_LLM_EXTRA_BODY 不是有效 JSON" in str(config.llm_extra_body_error)


def test_app_config_reads_context_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "enterprise-deepseek")
    monkeypatch.setenv("AUTOPATCH_LLM_CONTEXT_WINDOW", "900000")
    monkeypatch.setenv("AUTOPATCH_LLM_MAX_OUTPUT_TOKENS", "16000")

    profile = AppConfig.from_env().resolve_llm_context_profile()

    assert profile.context_window == 900_000
    assert profile.max_output_tokens == 16_000


def test_unknown_model_requires_context_window(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "unknown-model")
    monkeypatch.delenv("AUTOPATCH_LLM_CONTEXT_WINDOW", raising=False)

    config = AppConfig.from_env()

    try:
        config.resolve_llm_context_profile()
    except ValueError as exc:
        assert "AUTOPATCH_LLM_CONTEXT_WINDOW" in str(exc)
    else:
        raise AssertionError("unknown model must require context configuration")


def test_invalid_context_override_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_CONTEXT_WINDOW", "0")

    try:
        AppConfig.from_env()
    except ValueError as exc:
        assert "AUTOPATCH_LLM_CONTEXT_WINDOW" in str(exc)
    else:
        raise AssertionError("invalid context window must fail")
