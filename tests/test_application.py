"""配置、初始化和跨服务维护的真实生命周期验证。"""

import dataclasses
import os
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

from autopatch_j import config
from autopatch_j.application import runtime
from autopatch_j.cli import app
from autopatch_j.core import errors
from autopatch_j.core.project import symbol_index
from autopatch_j.core.repair import models
from autopatch_j.llm import factory
from autopatch_j.scanners.semgrep import runtime as scanner_runtime


@pytest.mark.parametrize(
    "values",
    [
        {"AUTOPATCH_LLM_API_KEY": ""},
        {"AUTOPATCH_LLM_CONTEXT_WINDOW": "private-invalid-value"},
        {"AUTOPATCH_LLM_MODEL": "unconfigured-model"},
        {"AUTOPATCH_LLM_CONTEXT_WINDOW": "1000000"},
    ],
)
def test_bad_model_configuration_rejects_startup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
) -> None:
    for name in (
        "AUTOPATCH_LLM_CONTEXT_WINDOW",
        "AUTOPATCH_LLM_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTOPATCH_LLM_MODEL", "deepseek-flash")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = config.AppConfig.from_env()
    with pytest.raises(ValueError) as caught:
        runtime.build_project_runtime(tmp_path, settings)
    assert "private-invalid-value" not in str(caught.value)
    assert not (tmp_path / ".autopatch-j").exists()


def test_import_ignores_bad_environment_and_creates_no_project_data(
    tmp_path: pathlib.Path,
) -> None:
    env = os.environ.copy()
    env["AUTOPATCH_LLM_CONTEXT_WINDOW"] = "bad"
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import autopatch_j.cli.app"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".autopatch-j").exists()


def test_configuration_is_explicit_and_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOPATCH_DEBUG", "false")
    first = config.AppConfig.from_env()
    monkeypatch.setenv("AUTOPATCH_DEBUG", "true")
    second = config.AppConfig.from_env()
    assert second.debug_mode
    assert not first.debug_mode
    assert first is not second
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(first, "debug_mode", True)
    assert isinstance(first.ignored_dirs, frozenset)


@pytest.mark.parametrize(
    "settings,field",
    [
        (
            config.AppConfig(
                llm_api_key="key", llm_base_url="https://host:bad"
            ),
            "AUTOPATCH_LLM_BASE_URL",
        ),
        (
            config.AppConfig(llm_api_key="key", llm_base_url="not-a-url"),
            "AUTOPATCH_LLM_BASE_URL",
        ),
        (
            config.AppConfig(llm_api_key="key", llm_model=""),
            "AUTOPATCH_LLM_MODEL",
        ),
    ],
)
def test_invalid_model_fields_never_create_client(
    settings: config.AppConfig, field: str
) -> None:
    with pytest.raises(ValueError, match=field):
        factory.build_model_runtime(settings)


def test_reset_rejects_stale_state_before_index_cleanup(
    tmp_path: pathlib.Path,
    fake_llm_factory,
) -> None:
    project = runtime.build_project_runtime(
        tmp_path,
        config.AppConfig(),
        lambda: fake_llm_factory(config.AppConfig()),
    )
    initial = project.repair.store.load()
    project.repair.commit(
        initial, project.repair.start(initial, "investigate", ())
    )
    try:
        with mock.patch.object(project.symbol_index, "clear") as clear:
            with pytest.raises(models.StateConflict):
                project.reset(initial)
            clear.assert_not_called()
        assert not project.model_runtime.closed
    finally:
        project.close()


def test_reset_cleanup_failure_preserves_task_end_and_closes(
    tmp_path: pathlib.Path,
    fake_llm_factory,
) -> None:
    project = runtime.build_project_runtime(
        tmp_path,
        config.AppConfig(),
        lambda: fake_llm_factory(config.AppConfig()),
    )
    project.symbol_index.rebuild_index()
    original = project.repair.store.load()
    current = project.repair.commit(
        original, project.repair.start(original, "investigate", ())
    )
    with mock.patch.object(
        project.symbol_index,
        "clear",
        side_effect=PermissionError("index locked"),
    ):
        with pytest.raises(
            errors.OperationError, match="任务已结束，但索引清理失败"
        ):
            project.reset(current)
    assert project.repair.store.load().status == "idle"
    assert project.symbol_index.db_path.exists()
    assert project.model_runtime.closed


def test_initialization_stops_before_index_when_scanner_fails(
    tmp_path: pathlib.Path,
    fake_llm_factory,
) -> None:
    (tmp_path / "Only.java").write_text("interface Only {}", encoding="utf-8")
    project = runtime.build_project_runtime(
        tmp_path,
        config.AppConfig(),
        lambda: fake_llm_factory(config.AppConfig()),
    )
    try:
        failure = scanner_runtime.RuntimeStatus(
            "broken", "1.160.0", message="install failed"
        )
        with mock.patch.object(
            scanner_runtime,
            "install_managed_semgrep_runtime",
            return_value=failure,
        ):
            with mock.patch.object(
                project.symbol_index, "rebuild_index"
            ) as rebuild:
                with pytest.raises(
                    errors.OperationError, match="install failed"
                ):
                    project.initialize()
                rebuild.assert_not_called()
    finally:
        project.close()


def test_startup_failure_is_reported_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = mock.MagicMock()
    monkeypatch.setattr(app.render, "CliRenderer", lambda: renderer)
    monkeypatch.setattr(
        factory,
        "build_model_runtime",
        mock.Mock(side_effect=OSError("resources unavailable")),
    )
    prompt = mock.MagicMock()
    prompt.prompt.side_effect = ["/help", EOFError()]
    monkeypatch.setattr(
        app.input_controller.CliInputController,
        "create_prompt_session",
        lambda unused_self: prompt,
    )
    assert app.main() == 0
    assert prompt.prompt.call_count == 2
    renderer.print_error.assert_called_once()


def test_default_options_are_not_required_configuration(monkeypatch):
    for name in (
        "AUTOPATCH_LLM_BASE_URL",
        "AUTOPATCH_LLM_MODEL",
        "AUTOPATCH_LLM_CONTEXT_WINDOW",
        "AUTOPATCH_LLM_MAX_OUTPUT_TOKENS",
        "AUTOPATCH_LLM_REQUEST_LIMIT",
        "AUTOPATCH_DEBUG",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTOPATCH_LLM_API_KEY", "test-key")
    settings = config.AppConfig.from_env()
    assert settings.llm_unavailable_reason() is None
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-flash"


def test_light_entrypoint_handles_missing_cli_dependency(tmp_path):
    script = """
import builtins
from autopatch_j import __main__
original = builtins.__import__
def broken(name, *args, **kwargs):
    if name == "autopatch_j.cli":
        raise ModuleNotFoundError("missing dependency", name="prompt_toolkit")
    return original(name, *args, **kwargs)
builtins.__import__ = broken
raise SystemExit(__main__.main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 1
    assert "必要组件加载失败，prompt_toolkit" in result.stderr
    assert "Traceback" not in result.stderr


def test_client_close_failure_remains_primary_when_runner_close_fails(
    fake_llm_factory,
):
    model = fake_llm_factory(config.AppConfig())
    client = mock.Mock()
    client.close = mock.AsyncMock(side_effect=OSError("client close failed"))
    model.client = client
    close_runner = model.runner.close

    def close_failed():
        close_runner()
        raise RuntimeError("runner close failed")

    with mock.patch.object(model.runner, "close", side_effect=close_failed):
        with pytest.raises(OSError, match="client close failed") as caught:
            model.close()
    assert model.closed
    assert "事件循环关闭失败" in " ".join(caught.value.__notes__)


def test_failed_initialization_does_not_publish_ready_index_on_restart(
    tmp_path, fake_llm_factory, monkeypatch
):
    failed = scanner_runtime.RuntimeStatus(
        "broken", "1.160.0", message="install failed"
    )
    monkeypatch.setattr(
        scanner_runtime,
        "install_managed_semgrep_runtime",
        lambda *args, **kwargs: failed,
    )
    for attempt in range(2):
        project = runtime.build_project_runtime(
            tmp_path,
            config.AppConfig(),
            lambda: fake_llm_factory(config.AppConfig()),
        )
        try:
            if attempt == 0:
                with pytest.raises(
                    errors.OperationError, match="install failed"
                ):
                    project.initialize()
            with pytest.raises(symbol_index.IndexNotReadyError, match="/init"):
                project.symbol_index.get_stats()
            assert not project.symbol_index.db_path.exists()
        finally:
            project.close()
