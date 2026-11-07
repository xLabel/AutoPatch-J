"""默认隔离真实网络；真实模型用例须显式开关，并排在确定性测试之后。"""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Sequence
from collections.abc import Callable
from typing import Protocol
from unittest import mock
from urllib import parse

from _pytest import terminal as pytest_terminal
import openai
from pydantic_ai import models
from pydantic_ai.models import test as test_model
import pytest

from autopatch_j import config as autopatch_j_config
from autopatch_j.llm import context_window
from autopatch_j.llm import factory
from autopatch_j.llm import runtime
from tests import sdk_fakes

LIVE_LLM_MARKER = "live_llm"


class MarkedItem(Protocol):
    """分层排序只依赖 marker 查询，不依赖 pytest item 的其他能力。"""

    def get_closest_marker(self, name: str) -> object | None: ...


def _partition_live_items[ItemT: MarkedItem](
    items: Sequence[ItemT],
) -> tuple[list[ItemT], list[ItemT]]:
    regular: list[ItemT] = []
    live: list[ItemT] = []
    for item in items:
        target = (
            live
            if item.get_closest_marker(LIVE_LLM_MARKER) is not None
            else regular
        )
        target.append(item)
    return regular, live


def _live_llm_block_reason(*, enabled: bool, tests_failed: int) -> str | None:
    if not enabled:
        return "真实 LLM 集成测试需要明确同意并使用 --run-live-llm"
    if tests_failed:
        return "先行测试或前一个真实 LLM 集成测试未通过"
    return None


def _live_llm_config_error(config: autopatch_j_config.AppConfig) -> str | None:
    if not config.is_llm_ready():
        return config.get_missing_llm_message()
    try:
        config.resolve_llm_context_profile()
    except ValueError as exc:
        return str(exc)
    return None


def _safe_endpoint_label(base_url: str) -> str:
    candidate = base_url if "://" in base_url else f"//{base_url}"
    parsed = parse.urlsplit(candidate)
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


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
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
    terminalreporter: pytest_terminal.TerminalReporter,
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
        status = f"未运行（{count} 个 case；需要明确同意并使用 --run-live-llm）"
    elif not started:
        status = f"未运行（{count} 个 case；先行测试未通过）"
    elif terminalreporter.stats.get("failed") or terminalreporter.stats.get(
        "error"
    ):
        status = f"运行未完成（{count} 个 case；请查看首个失败）"
    else:
        status = f"已完成（{count} 个 case）"
    terminalreporter.write_sep("-", f"真实 LLM 集成测试：{status}")


@pytest.fixture(autouse=True, name="_block_real_openai_client")
def make__block_real_openai_client(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker(LIVE_LLM_MARKER) is not None:
        return

    def blocked_client(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "普通测试禁止创建真实 OpenAI client；请注入 fake/mock。"
            "真实 LLM 集成测试必须标记 live_llm 并使用 --r"
            "un-live-llm。"
        )

    monkeypatch.setattr(openai, "AsyncOpenAI", blocked_client)
    monkeypatch.setattr(openai, "OpenAI", blocked_client)
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(name="mock_openai_sdk")
def make_mock_openai_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> mock.MagicMock:
    client = mock.MagicMock()
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: client)
    return client


@pytest.fixture(name="fake_llm_factory")
def make_fake_llm_factory() -> (
    Callable[[autopatch_j_config.AppConfig], runtime.ModelRuntime]
):
    def build(settings: autopatch_j_config.AppConfig) -> runtime.ModelRuntime:
        # 保持正式模型工厂签名，离线资源不读取真实模型配置。
        del settings
        return runtime.ModelRuntime(
            test_model.TestModel(),
            context_window.resolve_context_profile(model="deepseek-flash"),
        )

    return build


@pytest.fixture(name="sdk_scenario")
def make_sdk_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> type[sdk_fakes.ScriptedModel]:

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    return sdk_fakes.ScriptedModel


@pytest.fixture(name="live_llm_client")
def make_live_llm_client(
    request: pytest.FixtureRequest,
) -> Iterator[runtime.ModelRuntime]:
    reason = _live_llm_block_reason(
        enabled=request.config.getoption("run_live_llm"),
        tests_failed=request.session.testsfailed,
    )
    if reason is not None:
        pytest.skip(reason)

    settings = autopatch_j_config.AppConfig.from_env()
    error = _live_llm_config_error(settings)
    if error is not None:
        pytest.fail(f"真实 LLM 集成测试配置检查失败：{error}", pytrace=False)

    endpoint = _safe_endpoint_label(settings.llm_base_url)
    terminal = request.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(
            "真实 LLM 集成测试配置："
            f"model={settings.llm_model}"
            f", "
            f"endpoint={endpoint}"
        )

    llm = factory.build_model_runtime(settings)
    if llm is None:
        pytest.fail(
            "真实 LLM 集成测试配置检查失败：LLM client 未创建", pytrace=False
        )

    try:
        yield llm
    finally:
        llm.close()
