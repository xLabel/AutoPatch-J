from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from autopatch_j.config import GlobalConfig
from autopatch_j.llm.factory import build_default_llm_client
from conftest import (
    LIVE_LLM_MARKER,
    _live_llm_block_reason,
    _live_llm_config_error,
    _partition_live_items,
    _safe_endpoint_label,
)


class FakeItem:
    def __init__(self, *, is_live: bool) -> None:
        self.is_live = is_live

    def get_closest_marker(self, name: str) -> object | None:
        if self.is_live and name == LIVE_LLM_MARKER:
            return SimpleNamespace(name=name)
        return None


def test_live_items_are_partitioned_after_regular_items() -> None:
    first_live = FakeItem(is_live=True)
    first_regular = FakeItem(is_live=False)
    second_live = FakeItem(is_live=True)
    second_regular = FakeItem(is_live=False)

    regular, live = _partition_live_items(
        [first_live, first_regular, second_live, second_regular]
    )

    assert regular == [first_regular, second_regular]
    assert live == [first_live, second_live]


def test_live_llm_requires_flag_and_green_preceding_tests() -> None:
    assert _live_llm_block_reason(enabled=False, tests_failed=0) is not None
    assert _live_llm_block_reason(enabled=True, tests_failed=1) is not None
    assert _live_llm_block_reason(enabled=True, tests_failed=0) is None


def test_live_llm_preflight_rejects_missing_configuration() -> None:
    config = replace(GlobalConfig, llm_api_key="")

    error = _live_llm_config_error(config)

    assert error is not None
    assert "AUTOPATCH_LLM_API_KEY" in error


def test_live_llm_endpoint_summary_does_not_expose_credentials_path_or_query() -> None:
    label = _safe_endpoint_label(
        "https://user:secret@llm.example.test/private/v1"
        "?api_key=must-not-appear#private-fragment"
    )

    assert label == "https://llm.example.test"


def test_regular_tests_fail_closed_before_real_openai_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GlobalConfig, "llm_api_key", "configured-for-test")

    with pytest.raises(AssertionError, match="普通测试禁止创建真实 OpenAI client"):
        build_default_llm_client()
