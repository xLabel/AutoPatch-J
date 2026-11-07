"""真实扫描准入与进程边界，失败不得冒充无 finding。"""

import pathlib
import types

from autopatch_j.scanners import catalog as scanners_catalog
from autopatch_j.scanners import models
from autopatch_j.scanners.semgrep import runtime
from autopatch_j.scanners.semgrep import scanner as semgrep_scanner_module
from autopatch_j.scanners.semgrep import targets


def test_semgrep_target_selection_rejects_paths_outside_repo(
    tmp_path: pathlib.Path,
) -> None:
    java_file = tmp_path / "Demo.java"
    outside_file = tmp_path.parent / "Outside.java"
    java_file.write_text("class Demo {}", encoding="utf-8")
    outside_file.write_text("class Outside {}", encoding="utf-8")

    assert targets.select_semgrep_targets(
        tmp_path, ["Demo.java", "../Outside.java"]
    ) == ["Demo.java"]


def test_scan_targets_exclude_state_and_keep_safe_source_symlinks(
    tmp_path: pathlib.Path,
) -> None:
    state_file = tmp_path / ".autopatch-j" / "StateOnly.java"
    safe_file = tmp_path / "src" / "SafeSource.java"
    state_file.parent.mkdir(parents=True)
    safe_file.parent.mkdir(parents=True)
    state_file.write_text("class StateOnly {}", encoding="utf-8")
    safe_file.write_text("class SafeSource {}", encoding="utf-8")
    (tmp_path / "StateAlias.java").symlink_to(state_file)
    (tmp_path / "SafeAlias.java").symlink_to(safe_file)

    assert targets.select_semgrep_targets(
        tmp_path,
        [
            ".autopatch-j/StateOnly.java",
            "StateAlias.java",
            "SafeAlias.java",
        ],
    ) == ["src/SafeSource.java"]


def test_semgrep_scanner_reports_invalid_json_output(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    java_file = tmp_path / "Demo.java"
    java_file.write_text("class Demo {}", encoding="utf-8")
    scanner = semgrep_scanner_module.SemgrepScanner()
    monkeypatch.setattr(
        scanner,
        "probe",
        lambda: runtime.RuntimeStatus("ready", "1.160.0", "1.160.0", "semgrep"),
    )
    monkeypatch.setattr(
        semgrep_scanner_module.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0, stdout="not json", stderr=""
        ),
    )

    result = scanner.scan(tmp_path, ["Demo.java"])

    assert result.status == "error"
    assert "返回的数据格式无效" in result.message


def test_semgrep_scanner_rejects_empty_json_output(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    java_file = tmp_path / "Demo.java"
    java_file.write_text("class Demo {}", encoding="utf-8")
    scanner = semgrep_scanner_module.SemgrepScanner()
    monkeypatch.setattr(
        scanner,
        "probe",
        lambda: runtime.RuntimeStatus("ready", "1.160.0", "1.160.0", "semgrep"),
    )
    monkeypatch.setattr(
        semgrep_scanner_module.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )

    result = scanner.scan(tmp_path, ["Demo.java"])

    assert result.status == "error"
    assert not result.findings
    assert "缺少必需字段" in result.message


def test_semgrep_scanner_reports_missing_runtime_as_scan_result(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    java_file = tmp_path / "Demo.java"
    java_file.write_text("class Demo {}", encoding="utf-8")
    scanner = semgrep_scanner_module.SemgrepScanner()
    monkeypatch.setattr(
        scanner,
        "probe",
        lambda: runtime.RuntimeStatus(
            "missing", "1.160.0", message="Semgrep 缺失"
        ),
    )

    result = scanner.scan(tmp_path, ["Demo.java"])

    assert result.status == "error"
    assert result.engine == "semgrep"
    assert result.targets == ["Demo.java"]
    assert "Semgrep 缺失" in result.message


def test_scanner_catalog_exposes_only_executable_scanners():
    catalog = scanners_catalog.ScannerCatalog.default(
        version="1.160.0", timeout=300
    )
    assert [scanner.name for scanner in catalog.all()] == [
        models.ScannerName.SEMGREP
    ]
