from __future__ import annotations

from autopatch_j.config import AppConfig, DEFAULT_LLM_MODEL


def test_app_config_reads_llm_environment(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("AUTOPATCH_DEBUG", "true")

    config = AppConfig()

    assert config.llm_api_key == "key"
    assert config.llm_base_url == "https://example.com/v1"
    assert config.llm_model == "custom-model"
    assert config.debug_mode is True
    assert config.is_llm_ready() is True


def test_app_config_uses_defaults_and_isolates_ignored_dirs(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("AUTOPATCH_DEBUG", raising=False)

    first = AppConfig()
    second = AppConfig()
    first.ignored_dirs.add("custom")

    assert first.llm_model == DEFAULT_LLM_MODEL
    assert first.debug_mode is False
    assert first.is_llm_ready() is False
    assert "custom" not in second.ignored_dirs
