"""使用固定扫描候选检验编辑后目标足迹，不以语法成功替代修复验收。"""

from __future__ import annotations

import pathlib
from unittest import mock

import pytest

from autopatch_j.core import finding
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import types
from autopatch_j.core.patching import verification
from autopatch_j.scanners import models


def _region(start: int, end: int) -> finding.SourceRegion:
    return finding.SourceRegion(1, start + 1, 1, end + 1, start, end)


def _draft(
    *,
    match_region: finding.SourceRegion | None = None,
    target_region: finding.SourceRegion | None = None,
) -> types.SearchReplacePatchDraft:
    match_region = match_region or _region(100, 103)
    target_region = target_region or _region(100, 103)
    return types.SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="MD5",
        new_string="SHA256",
        diff="...",
        match_region=match_region,
        validation=types.SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=finding.FindingIdentity(
            fingerprint=f"apj-v1:{"a" * 64}:1",
            check_id="weak-crypto",
            path="Auth.java",
            region=target_region,
        ),
    )


def _applied(
    source_region: finding.SourceRegion | None = None,
    changed_region: finding.SourceRegion | None = None,
) -> types.PatchApplicationResult:
    source_region = source_region or _region(100, 103)
    changed_region = changed_region or _region(100, 106)
    return types.PatchApplicationResult(
        applied=True,
        message="applied",
        source_region=source_region,
        changed_region=changed_region,
    )


def _finding(
    region: finding.SourceRegion, fingerprint_char: str = "b"
) -> models.Finding:
    return models.Finding(
        fingerprint=f"apj-v1:{fingerprint_char * 64}:1",
        check_id="weak-crypto",
        path="Auth.java",
        region=region,
        severity="error",
        message="weak crypto",
        snippet='MessageDigest.getInstance("md5")',
    )


def _scan_result(
    *findings: models.Finding, status: str = "ok"
) -> models.ScanResult:
    return models.ScanResult(
        engine="semgrep",
        scope=["Auth.java"],
        targets=["Auth.java"],
        status=status,
        message="scan failed" if status != "ok" else "ok",
        findings=list(findings),
    )


def test_verification_reports_resolved_when_target_and_same_rule_are_gone() -> (
    None
):
    scanner = mock.MagicMock()
    scanner.scan.return_value = _scan_result()
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is types.VerificationOutcome.RESOLVED
    assert result.other_same_rule_findings == 0


def test_verification_detects_changed_evidence_inside_patch_region() -> None:
    scanner = mock.MagicMock()
    scanner.scan.return_value = _scan_result(
        _finding(_region(101, 104), "b"),
        _finding(_region(200, 203), "c"),
    )
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is types.VerificationOutcome.STILL_PRESENT
    assert result.other_same_rule_findings == 1
    assert "目标 finding 区域仍被触发" in result.message


def test_verification_detects_violation_in_prefix_left_by_suffix_deletion(
    tmp_path: pathlib.Path,
) -> None:
    source_file = tmp_path / "Auth.java"
    source_file.write_text("abcdefghi", encoding="utf-8")
    engine = search_replace.SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("Auth.java", "defghi", "")
    draft = types.SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="defghi",
        new_string="",
        diff=build_result.diff,
        match_region=build_result.match_region,
        validation=types.SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=finding.FindingIdentity(
            fingerprint=f"apj-v1:{"a" * 64}:1",
            check_id="weak-crypto",
            path="Auth.java",
            region=_region(0, 9),
        ),
    )
    application_result = engine.apply_patch(draft)
    scanner = mock.MagicMock()
    scanner.scan.return_value = _scan_result(_finding(_region(0, 3)))
    verifier = verification.PatchQualityVerifier(tmp_path, scanner)

    result = verifier.verify_finding_resolved(draft, application_result)

    assert source_file.read_text(encoding="utf-8") == "abc"
    assert result.outcome is types.VerificationOutcome.STILL_PRESENT
    assert result.other_same_rule_findings == 0


@pytest.mark.parametrize(
    (
        "target_region",
        "source_region",
        "changed_region",
        "candidate_region",
        "expected_outcome",
        "expected_other",
    ),
    [
        (
            _region(100, 109),
            _region(100, 106),
            _region(100, 100),
            _region(100, 103),
            types.VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(100, 109),
            _region(103, 106),
            _region(103, 104),
            _region(100, 107),
            types.VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(103, 106),
            _region(100, 109),
            _region(100, 102),
            _region(100, 102),
            types.VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(103, 109),
            _region(103, 109),
            _region(103, 103),
            _region(103, 105),
            types.VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(103, 109),
            _region(103, 109),
            _region(103, 103),
            _region(100, 103),
            types.VerificationOutcome.RESOLVED,
            1,
        ),
        (
            _region(100, 109),
            _region(103, 109),
            _region(103, 103),
            _region(200, 203),
            types.VerificationOutcome.RESOLVED,
            1,
        ),
    ],
    ids=[
        "deleted-prefix",
        "shortened-middle",
        "source-covers-target",
        "candidate-starts-at-deletion-point",
        "candidate-ends-at-deletion-point",
        "other-location-only",
    ],
)
def test_verification_maps_target_footprint_across_edit_topologies(
    target_region: finding.SourceRegion,
    source_region: finding.SourceRegion,
    changed_region: finding.SourceRegion,
    candidate_region: finding.SourceRegion,
    expected_outcome: types.VerificationOutcome,
    expected_other: int,
) -> None:
    scanner = mock.MagicMock()
    scanner.scan.return_value = _scan_result(_finding(candidate_region))
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )
    draft = _draft(match_region=source_region, target_region=target_region)

    result = verifier.verify_finding_resolved(
        draft,
        _applied(source_region, changed_region),
    )

    assert result.outcome is expected_outcome
    assert result.other_same_rule_findings == expected_other


def test_verification_ignores_same_rule_outside_target() -> None:
    scanner = mock.MagicMock()
    scanner.scan.return_value = _scan_result(_finding(_region(200, 203), "c"))
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is types.VerificationOutcome.RESOLVED
    assert result.other_same_rule_findings == 1


def test_verification_is_unverified_when_rescan_fails() -> None:
    scanner = mock.MagicMock()
    scanner.scan.return_value = _scan_result(status="error")
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is types.VerificationOutcome.UNVERIFIED
    assert "无法确认" in result.message


def test_verification_is_unverified_without_target_identity() -> None:
    scanner = mock.MagicMock()
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )
    draft = types.SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="MD5",
        new_string="SHA256",
        diff="...",
        match_region=_region(100, 103),
        validation=types.SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
    )

    result = verifier.verify_finding_resolved(draft, _applied())

    assert result.outcome is types.VerificationOutcome.UNVERIFIED
    scanner.scan.assert_not_called()


@pytest.mark.parametrize(
    "application_result",
    [
        types.PatchApplicationResult(
            applied=False,
            message="failed",
            error_code="SOURCE_CHANGED",
        ),
        _applied(_region(101, 103), _region(101, 104)),
        _applied(_region(100, 103), _region(101, 104)),
        _applied(
            _region(100, 103),
            finding.SourceRegion(2, 1, 2, 4, 100, 103),
        ),
    ],
    ids=[
        "failed-application",
        "source-does-not-match-draft",
        "changed-offset-anchor-mismatch",
        "changed-position-anchor-mismatch",
    ],
)
def test_verification_is_unverified_for_inconsistent_apply_evidence(
    application_result: types.PatchApplicationResult,
) -> None:
    scanner = mock.MagicMock()
    verifier = verification.PatchQualityVerifier(
        pathlib.Path("/tmp/mock-repo"), scanner
    )

    result = verifier.verify_finding_resolved(_draft(), application_result)

    assert result.outcome is types.VerificationOutcome.UNVERIFIED
    assert "无法确认" in result.message
    scanner.scan.assert_not_called()
