"""验证真实模型测试的授权、执行顺序和配置摘要脱敏边界。"""

from __future__ import annotations

import dataclasses
import types

import pytest

from autopatch_j import config as autopatch_j_config
from autopatch_j.llm import factory
from tests import conftest


class FakeItem:
    def __init__(self, *, is_live: bool) -> None:
        self.is_live = is_live

    def get_closest_marker(self, name: str) -> object | None:
        if self.is_live and name == conftest.LIVE_LLM_MARKER:
            return types.SimpleNamespace(name=name)
        return None


def test_live_items_are_partitioned_after_regular_items() -> None:
    first_live = FakeItem(is_live=True)
    first_regular = FakeItem(is_live=False)
    second_live = FakeItem(is_live=True)
    second_regular = FakeItem(is_live=False)

    # 直接测试排序与授权门禁，确保禁止网络请求的条件准确。
    # pylint: disable-next=protected-access
    regular, live = conftest._partition_live_items(
        [first_live, first_regular, second_live, second_regular]
    )

    assert regular == [first_regular, second_regular]
    assert live == [first_live, second_live]


def test_live_llm_requires_flag_and_green_preceding_tests() -> None:
    assert (
        # 直接测试排序与授权门禁，确保禁止网络请求的条件准确。
        # pylint: disable-next=protected-access
        conftest._live_llm_block_reason(enabled=False, tests_failed=0)
        is not None
    )
    assert (
        # 直接测试排序与授权门禁，确保禁止网络请求的条件准确。
        # pylint: disable-next=protected-access
        conftest._live_llm_block_reason(enabled=True, tests_failed=1)
        is not None
    )
    # 直接测试排序与授权门禁，确保禁止网络请求的条件准确。
    # pylint: disable-next=protected-access
    assert conftest._live_llm_block_reason(enabled=True, tests_failed=0) is None


def test_live_llm_preflight_rejects_missing_configuration() -> None:
    config = dataclasses.replace(
        autopatch_j_config.GLOBAL_CONFIG, llm_api_key=""
    )

    # 直接测试排序与授权门禁，确保禁止网络请求的条件准确。
    # pylint: disable-next=protected-access
    error = conftest._live_llm_config_error(config)

    assert error is not None
    assert "AUTOPATCH_LLM_API_KEY" in error


def test_endpoint_summary_excludes_credentials_path_and_query() -> None:
    # 直接测试排序与授权门禁，确保禁止网络请求的条件准确。
    # pylint: disable-next=protected-access
    label = conftest._safe_endpoint_label(
        "https://user:secret@llm.example."
        "test/private/v1"
        "?api_key=must-not-appear#private"
        "-fragment"
    )

    assert label == "https://llm.example.test"


def test_regular_tests_fail_closed_before_real_openai_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        autopatch_j_config.GLOBAL_CONFIG, "llm_api_key", "configured-for-test"
    )
    monkeypatch.setattr(
        autopatch_j_config.GLOBAL_CONFIG, "llm_model", "deepseek-flash"
    )

    with pytest.raises(
        AssertionError, match="普通测试禁止创建真实 OpenAI client"
    ):
        factory.build_model_runtime()
