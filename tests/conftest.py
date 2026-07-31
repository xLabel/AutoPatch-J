from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest

from autopatch_j.config import AppConfig, GlobalConfig
from autopatch_j.llm import transport as llm_transport
from autopatch_j.llm.factory import build_default_llm_client


LIVE_LLM_MARKER = "live_llm"


class StrictFakeLLM:
    """普通测试的默认 LLM：允许组装对象，但拒绝任何未声明的模型调用。"""

    model = "LLM Not Configured"

    def chat(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("普通测试发生了未声明的 LLM 调用；请为该测试注入明确的 fake 响应")


def _partition_live_items(items: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    regular: list[Any] = []
    live: list[Any] = []
    for item in items:
        target = live if item.get_closest_marker(LIVE_LLM_MARKER) is not None else regular
        target.append(item)
    return regular, live


def _live_llm_block_reason(*, enabled: bool, tests_failed: int) -> str | None:
    if not enabled:
        return "真实 LLM 集成测试需要明确同意并使用 --run-live-llm"
    if tests_failed:
        return "先行测试或前一个真实 LLM 集成测试未通过"
    return None


def _live_llm_config_error(config: AppConfig) -> str | None:
    if not config.is_llm_ready():
        return config.get_missing_llm_message()
    if config.llm_extra_body_error:
        return config.llm_extra_body_error
    try:
        config.resolve_llm_context_profile()
    except ValueError as exc:
        return str(exc)
    return None


def _safe_endpoint_label(base_url: str) -> str:
    candidate = base_url if "://" in base_url else f"//{base_url}"
    parsed = urlsplit(candidate)
    if parsed.netloc:
        authority = parsed.netloc.rsplit("@", 1)[-1]
        return f"{parsed.scheme}://{authority}" if parsed.scheme else authority
    return "<configured endpoint>"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("autopatch-j")
    group.addoption(
        "--run-live-llm",
        action="store_true",
        default=False,
        dest="run_live_llm",
        help="在普通测试全部通过后运行真实 LLM 集成测试",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    regular, live = _partition_live_items(items)
    items[:] = [*regular, *live]
    setattr(config, "_autopatch_live_llm_count", len(live))
    setattr(config, "_autopatch_live_llm_started", False)

    reason = _live_llm_block_reason(
        enabled=config.getoption("run_live_llm"),
        tests_failed=0,
    )
    if reason is None:
        return
    marker = pytest.mark.skip(reason=reason)
    for item in live:
        item.add_marker(marker)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker(LIVE_LLM_MARKER) is None:
        return
    enabled = item.config.getoption("run_live_llm")
    reason = _live_llm_block_reason(
        enabled=enabled,
        tests_failed=item.session.testsfailed,
    )
    if reason is not None:
        pytest.skip(reason)
    setattr(item.config, "_autopatch_live_llm_started", True)


def pytest_terminal_summary(
    terminalreporter: Any,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    del exitstatus
    count = getattr(config, "_autopatch_live_llm_count", 0)
    if not count:
        return

    enabled = config.getoption("run_live_llm")
    started = getattr(config, "_autopatch_live_llm_started", False)
    if not enabled:
        status = (
            f"未运行（{count} 个 case；需要明确同意并使用 --run-live-llm）"
        )
    elif not started:
        status = f"未运行（{count} 个 case；先行测试未通过）"
    elif terminalreporter.stats.get("failed") or terminalreporter.stats.get("error"):
        status = f"运行未完成（{count} 个 case；请查看首个失败）"
    else:
        status = f"已完成（{count} 个 case）"
    terminalreporter.write_sep("-", f"真实 LLM 集成测试：{status}")


@pytest.fixture(autouse=True)
def _block_real_openai_client(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker(LIVE_LLM_MARKER) is not None:
        return

    def blocked_client(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "普通测试禁止创建真实 OpenAI client；请注入 fake/mock。"
            "真实 LLM 集成测试必须标记 live_llm 并使用 --run-live-llm。"
        )

    monkeypatch.setattr(llm_transport.openai, "OpenAI", blocked_client)


@pytest.fixture
def mock_openai_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr(llm_transport.openai, "OpenAI", lambda **_kwargs: client)
    return client


@pytest.fixture
def fake_llm_factory() -> Callable[[], StrictFakeLLM]:
    return StrictFakeLLM


@pytest.fixture(scope="session")
def live_llm_client(request: pytest.FixtureRequest) -> Iterator[Any]:
    reason = _live_llm_block_reason(
        enabled=request.config.getoption("run_live_llm"),
        tests_failed=request.session.testsfailed,
    )
    if reason is not None:
        pytest.skip(reason)

    error = _live_llm_config_error(GlobalConfig)
    if error is not None:
        pytest.fail(f"真实 LLM 集成测试配置检查失败：{error}", pytrace=False)

    terminal = request.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(
            "真实 LLM 集成测试配置："
            f"model={GlobalConfig.llm_model}, "
            f"endpoint={_safe_endpoint_label(GlobalConfig.llm_base_url)}"
        )

    llm = build_default_llm_client()
    if llm is None:
        pytest.fail("真实 LLM 集成测试配置检查失败：LLM client 未创建", pytrace=False)

    try:
        yield llm
    finally:
        close = getattr(llm.client, "close", None)
        if callable(close):
            close()
