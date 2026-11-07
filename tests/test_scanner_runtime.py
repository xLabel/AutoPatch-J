"""版本事实、失败初始化和锁内复查，不安装真实受管程序。"""

import pathlib
import subprocess
from typing import Literal
from unittest import mock

import pytest

from autopatch_j.scanners.semgrep import runtime
from autopatch_j.scanners.semgrep import scanner


@pytest.mark.parametrize(
    "stdout,code,status",
    [
        ("1.160.0\n", 0, "ready"),
        ("1.159.0\n", 0, "version_mismatch"),
        ("", 0, "broken"),
        ("diagnostic text", 0, "broken"),
        ("", 1, "broken"),
    ],
)
def test_probe_reports_observed_version(
    stdout: str, code: int, status: str
) -> None:
    completed = subprocess.CompletedProcess(
        ["semgrep", "--version"], code, stdout, "failure detail" if code else ""
    )
    with (
        mock.patch.object(
            runtime,
            "resolve_managed_semgrep_binary",
            return_value="managed-semgrep",
        ),
        mock.patch.object(
            runtime.subprocess, "run", return_value=completed
        ) as run,
    ):
        result = runtime.probe_managed_semgrep("1.160.0")
    assert result.status == status
    assert result.actual_version == (
        stdout.strip() if status in {"ready", "version_mismatch"} else None
    )
    assert run.call_args.args[0] == ["managed-semgrep", "--version"]
    assert run.call_args.kwargs["timeout"] == 10
    assert run.call_args.kwargs["env"]["SEMGREP_ENABLE_VERSION_CHECK"] == "0"


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired("semgrep", 10),
        OSError("not executable"),
        UnicodeError("invalid version bytes"),
    ],
)
def test_probe_failure_is_not_ready(error: Exception) -> None:
    with (
        mock.patch.object(
            runtime,
            "resolve_managed_semgrep_binary",
            return_value="managed-semgrep",
        ),
        mock.patch.object(runtime.subprocess, "run", side_effect=error),
    ):
        result = runtime.probe_managed_semgrep("1.160.0")
    assert result.status == "broken"
    assert result.actual_version is None
    assert "版本探测失败" in result.message


def test_missing_probe_does_not_launch_or_install() -> None:
    with (
        mock.patch.object(
            runtime, "resolve_managed_semgrep_binary", return_value=None
        ),
        mock.patch.object(runtime.subprocess, "run") as run,
    ):
        result = runtime.probe_managed_semgrep("1.160.0")
    assert result.status == "missing"
    run.assert_not_called()


@pytest.mark.parametrize(
    "repair_status", ["missing", "version_mismatch", "broken"]
)
@pytest.mark.parametrize("after", ["ready", "version_mismatch"])
def test_install_rechecks_under_lock_and_verifies_after(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    repair_status: Literal["missing", "version_mismatch", "broken"],
    after: Literal["ready", "version_mismatch"],
) -> None:
    monkeypatch.setattr(runtime, "semgrep_runtime_dir", lambda: tmp_path)
    replies = [
        runtime.RuntimeStatus(repair_status, "1.160.0"),
        runtime.RuntimeStatus(
            after, "1.160.0", "1.160.0" if after == "ready" else "1.159.0"
        ),
    ]
    with (
        mock.patch.object(
            runtime, "probe_managed_semgrep", side_effect=replies
        ) as probe,
        mock.patch.object(
            runtime, "run_setup_command", return_value=None
        ) as setup,
    ):
        result = runtime.install_managed_semgrep_runtime("1.160.0")
    assert result.status == after
    assert probe.call_count == 2
    assert setup.call_count == 2
    assert "semgrep==1.160.0" in setup.call_args.args[0]
    assert (tmp_path / "install.lock").is_file()


def test_install_ready_under_lock_does_not_reinstall(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "semgrep_runtime_dir", lambda: tmp_path)
    ready = runtime.RuntimeStatus("ready", "1.160.0", "1.160.0", "managed")
    with (
        mock.patch.object(runtime, "probe_managed_semgrep", return_value=ready),
        mock.patch.object(runtime, "run_setup_command") as setup,
    ):
        assert runtime.install_managed_semgrep_runtime("1.160.0") == ready
    setup.assert_not_called()


def test_install_failure_preserves_detail(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "semgrep_runtime_dir", lambda: tmp_path)
    with (
        mock.patch.object(
            runtime,
            "probe_managed_semgrep",
            return_value=runtime.RuntimeStatus("missing", "1.160.0"),
        ),
        mock.patch.object(
            runtime, "run_setup_command", return_value="pip failed: unavailable"
        ),
    ):
        result = runtime.install_managed_semgrep_runtime("1.160.0")
    assert result.status == "broken"
    assert result.message == "pip failed: unavailable"


def test_scan_rejects_wrong_version_without_scanning(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "A.java").write_text("class A {}", encoding="utf-8")
    adapter = scanner.SemgrepScanner()
    wrong = runtime.RuntimeStatus(
        "version_mismatch",
        "1.160.0",
        "old",
        "managed",
        "wrong installed version",
    )
    with (
        mock.patch.object(adapter, "probe", return_value=wrong),
        mock.patch.object(scanner.subprocess, "run") as run,
    ):
        result = adapter.scan(tmp_path, ["A.java"])
        meta = adapter.get_meta()
    run.assert_not_called()
    assert result.status == "error" and result.message == wrong.message
    assert meta.availability == "unavailable" and meta.version == "old"
