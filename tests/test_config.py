from __future__ import annotations

from autopatch_j.config import AppConfig


def test_app_config_reads_llm_environment(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPATCH_LLM_API_KEY", "key")
    monkeypatch.setenv("AUTOPATCH_LLM_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "custom-model")
    monkeypatch.setenv("AUTOPATCH_DEBUG", "true")

    config = AppConfig.from_env()

    assert config.llm_api_key == "key"
    assert config.llm_base_url == "https://example.com/v1"
    assert config.llm_model == "custom-model"
    assert config.debug_mode is True
    assert config.is_llm_ready() is True


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
    assert first.debug_mode is False
    assert first.is_llm_ready() is False
    assert "custom" not in second.ignored_dirs
