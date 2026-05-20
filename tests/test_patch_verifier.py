from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock
from autopatch_j.core.patching import PatchQualityVerifier, SyntaxCheckResult
from autopatch_j.core.patching import SearchReplacePatchDraft
from autopatch_j.scanners.models import Finding, ScanResult


def test_verify_fix_logic():
    """验证指纹比对算法的准确性"""
    repo_root = Path("/tmp/mock-repo")
    mock_scanner = MagicMock()
    validator = PatchQualityVerifier(repo_root, mock_scanner)
    
    # 准备补丁草案快照 (修复目标代码: old_snippet)
    draft = SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="MD5",
        new_string="SHA256",
        diff="...",
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
        target_check_id="weak-crypto",
        target_snippet="MessageDigest.getInstance(\"MD5\")"
    )

    # 1. 测试成功场景：重扫发现漏洞消失
    mock_scanner.scan.return_value = ScanResult(
        engine="semgrep", scope=[], targets=[], status="ok", message="",
        findings=[] 
    )
    result = validator.verify_finding_resolved(draft)
    assert result.is_resolved is True

    # 2. 测试失败场景：漏洞特征依然存在
    mock_scanner.scan.return_value = ScanResult(
        engine="semgrep", scope=[], targets=[], status="ok", message="",
        findings=[
            Finding(check_id="weak-crypto", path="Auth.java", start_line=10, end_line=10, 
                    severity="error", message="...", rule="...", 
                    snippet="MessageDigest.getInstance(\"MD5\")")
        ]
    )
    result = validator.verify_finding_resolved(draft)
    assert result.is_resolved is False
    assert "语义校验失败" in result.message


def test_project_validation_describes_build_entry_without_running_build(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
    validator = PatchQualityVerifier(tmp_path, MagicMock())

    result = validator.describe_project_validation()

    assert result.status == "not_run"
    assert "Maven" in result.message
    assert "默认未执行" in result.message


def test_project_validation_is_not_applicable_without_build_entry(tmp_path: Path) -> None:
    validator = PatchQualityVerifier(tmp_path, MagicMock())

    result = validator.describe_project_validation()

    assert result.status == "not_applicable"
    assert "未检测到 Maven/Gradle" in result.message
