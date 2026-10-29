"""用独立环境变量检查配置解析，不依赖本机 Key 或真实服务。"""

from __future__ import annotations

from autopatch_j import config as autopatch_j_config


def test_app_config_reads_llm_environment(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_API_KEY", "key")
    monkeypatch.setenv("AUTOPATCH_LLM_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "custom-model")
    monkeypatch.setenv("AUTOPATCH_DEBUG", "true")

    config = autopatch_j_config.AppConfig.from_env()

    assert config.llm_api_key == "key"
    assert config.llm_base_url == "https://example.com/v1"
    assert config.llm_model == "custom-model"
    assert config.llm_context_window is None
    assert config.debug_mode is True
    assert config.is_llm_ready() is True


def test_app_config_uses_defaults_and_isolates_ignored_dirs(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AUTOPATCH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AUTOPATCH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AUTOPATCH_LLM_MODEL", raising=False)
    monkeypatch.delenv("AUTOPATCH_DEBUG", raising=False)

    first = autopatch_j_config.AppConfig.from_env()
    second = autopatch_j_config.AppConfig.from_env()
    first.ignored_dirs.add("custom")

    assert first.llm_api_key == ""
    assert first.llm_base_url == "https://api.deepseek.com"
    assert first.llm_model == "deepseek-flash"
    profile = first.resolve_llm_context_profile()
    assert profile.context_window == 1_048_576
    assert profile.provider_max_output_tokens == 393_216
    assert profile.input_capacity == 887_808
    assert profile.max_output_tokens == 128_000
    assert first.debug_mode is False
    assert first.is_llm_ready() is False
    assert "custom" not in second.ignored_dirs


def test_app_config_ignores_removed_provider_environment(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_EXTRA_BODY", "{bad json")
    monkeypatch.setenv("AUTOPATCH_LLM_REASONING_EFFORT", "low")
    monkeypatch.setenv("AUTOPATCH_LLM_STREAM_DIALECT", "bailian-dsml")

    config = autopatch_j_config.AppConfig.from_env()

    assert not hasattr(config, "llm_extra_body")
    assert not hasattr(config, "llm_reasoning_effort")
    assert not hasattr(config, "llm_stream_dialect")


def test_app_config_reads_context_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "enterprise-deepseek")
    monkeypatch.setenv("AUTOPATCH_LLM_CONTEXT_WINDOW", "900000")
    monkeypatch.setenv("AUTOPATCH_LLM_MAX_OUTPUT_TOKENS", "16000")

    profile = (
        autopatch_j_config.AppConfig.from_env().resolve_llm_context_profile()
    )

    assert profile.context_window == 900_000
    assert profile.provider_max_output_tokens == 16_000
    assert profile.input_capacity == 851_232
    assert profile.max_output_tokens == 16_000


def test_unknown_model_requires_context_window(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "unknown-model")
    monkeypatch.delenv("AUTOPATCH_LLM_CONTEXT_WINDOW", raising=False)

    config = autopatch_j_config.AppConfig.from_env()

    try:
        config.resolve_llm_context_profile()
    except ValueError as exc:
        assert "AUTOPATCH_LLM_CONTEXT_WINDOW" in str(exc)
    else:
        raise AssertionError("unknown model must require context configuration")


def test_invalid_context_override_fails_configuration(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_CONTEXT_WINDOW", "0")

    try:
        autopatch_j_config.AppConfig.from_env()
    except ValueError as exc:
        assert "AUTOPATCH_LLM_CONTEXT_WINDOW" in str(exc)
    else:
        raise AssertionError("invalid context window must fail")
