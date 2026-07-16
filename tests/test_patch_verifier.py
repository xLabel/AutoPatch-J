from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autopatch_j.core.patching import (
    PatchApplicationResult,
    PatchQualityVerifier,
    SearchReplacePatchDraft,
    SearchReplacePatchEngine,
    SyntaxCheckResult,
    VerificationOutcome,
)
from autopatch_j.scanners import Finding, FindingIdentity, ScanResult, SourceRegion


def _region(start: int, end: int) -> SourceRegion:
    return SourceRegion(1, start + 1, 1, end + 1, start, end)


def _draft(
    *,
    match_region: SourceRegion | None = None,
    target_region: SourceRegion | None = None,
) -> SearchReplacePatchDraft:
    match_region = match_region or _region(100, 103)
    target_region = target_region or _region(100, 103)
    return SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="MD5",
        new_string="SHA256",
        diff="...",
        match_region=match_region,
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=FindingIdentity(
            fingerprint=f"apj-v1:{'a' * 64}:1",
            check_id="weak-crypto",
            path="Auth.java",
            region=target_region,
        ),
    )


def _applied(
    source_region: SourceRegion | None = None,
    changed_region: SourceRegion | None = None,
) -> PatchApplicationResult:
    source_region = source_region or _region(100, 103)
    changed_region = changed_region or _region(100, 106)
    return PatchApplicationResult(
        applied=True,
        message="applied",
        source_region=source_region,
        changed_region=changed_region,
    )


def _finding(region: SourceRegion, fingerprint_char: str = "b") -> Finding:
    return Finding(
        fingerprint=f"apj-v1:{fingerprint_char * 64}:1",
        check_id="weak-crypto",
        path="Auth.java",
        region=region,
        severity="error",
        message="weak crypto",
        snippet='MessageDigest.getInstance("md5")',
    )


def _scan_result(*findings: Finding, status: str = "ok") -> ScanResult:
    return ScanResult(
        engine="semgrep",
        scope=["Auth.java"],
        targets=["Auth.java"],
        status=status,
        message="scan failed" if status != "ok" else "ok",
        findings=list(findings),
    )


def test_verification_reports_resolved_when_target_and_same_rule_are_gone() -> None:
    scanner = MagicMock()
    scanner.scan.return_value = _scan_result()
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is VerificationOutcome.RESOLVED
    assert result.other_same_rule_findings == 0


def test_verification_detects_changed_evidence_inside_patch_region() -> None:
    scanner = MagicMock()
    scanner.scan.return_value = _scan_result(
        _finding(_region(101, 104), "b"),
        _finding(_region(200, 203), "c"),
    )
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is VerificationOutcome.STILL_PRESENT
    assert result.other_same_rule_findings == 1
    assert "目标 finding 区域仍被触发" in result.message


def test_verification_detects_violation_in_prefix_left_by_suffix_deletion(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "Auth.java"
    source_file.write_text("abcdefghi", encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("Auth.java", "defghi", "")
    draft = SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="defghi",
        new_string="",
        diff=build_result.diff,
        match_region=build_result.match_region,
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
        associated_finding_id="F1",
        source_scan_id="scan-1",
        target_finding=FindingIdentity(
            fingerprint=f"apj-v1:{'a' * 64}:1",
            check_id="weak-crypto",
            path="Auth.java",
            region=_region(0, 9),
        ),
    )
    application_result = engine.apply_patch(draft)
    scanner = MagicMock()
    scanner.scan.return_value = _scan_result(_finding(_region(0, 3)))
    verifier = PatchQualityVerifier(tmp_path, scanner)

    result = verifier.verify_finding_resolved(draft, application_result)

    assert source_file.read_text(encoding="utf-8") == "abc"
    assert result.outcome is VerificationOutcome.STILL_PRESENT
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
            VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(100, 109),
            _region(103, 106),
            _region(103, 104),
            _region(100, 107),
            VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(103, 106),
            _region(100, 109),
            _region(100, 102),
            _region(100, 102),
            VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(103, 109),
            _region(103, 109),
            _region(103, 103),
            _region(103, 105),
            VerificationOutcome.STILL_PRESENT,
            0,
        ),
        (
            _region(103, 109),
            _region(103, 109),
            _region(103, 103),
            _region(100, 103),
            VerificationOutcome.RESOLVED,
            1,
        ),
        (
            _region(100, 109),
            _region(103, 109),
            _region(103, 103),
            _region(200, 203),
            VerificationOutcome.RESOLVED,
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
    target_region: SourceRegion,
    source_region: SourceRegion,
    changed_region: SourceRegion,
    candidate_region: SourceRegion,
    expected_outcome: VerificationOutcome,
    expected_other: int,
) -> None:
    scanner = MagicMock()
    scanner.scan.return_value = _scan_result(_finding(candidate_region))
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)
    draft = _draft(match_region=source_region, target_region=target_region)

    result = verifier.verify_finding_resolved(
        draft,
        _applied(source_region, changed_region),
    )

    assert result.outcome is expected_outcome
    assert result.other_same_rule_findings == expected_other


def test_verification_ignores_same_rule_at_other_location_for_target_outcome() -> None:
    scanner = MagicMock()
    scanner.scan.return_value = _scan_result(_finding(_region(200, 203), "c"))
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is VerificationOutcome.RESOLVED
    assert result.other_same_rule_findings == 1


def test_verification_is_unverified_when_rescan_fails() -> None:
    scanner = MagicMock()
    scanner.scan.return_value = _scan_result(status="error")
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)

    result = verifier.verify_finding_resolved(_draft(), _applied())

    assert result.outcome is VerificationOutcome.UNVERIFIED
    assert "无法确认" in result.message


def test_verification_is_unverified_without_target_identity() -> None:
    scanner = MagicMock()
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)
    draft = SearchReplacePatchDraft(
        file_path="Auth.java",
        old_string="MD5",
        new_string="SHA256",
        diff="...",
        match_region=_region(100, 103),
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
    )

    result = verifier.verify_finding_resolved(draft, _applied())

    assert result.outcome is VerificationOutcome.UNVERIFIED
    scanner.scan.assert_not_called()


@pytest.mark.parametrize(
    "application_result",
    [
        PatchApplicationResult(
            applied=False,
            message="failed",
            error_code="SOURCE_CHANGED",
        ),
        _applied(_region(101, 103), _region(101, 104)),
        _applied(_region(100, 103), _region(101, 104)),
        _applied(
            _region(100, 103),
            SourceRegion(2, 1, 2, 4, 100, 103),
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
    application_result: PatchApplicationResult,
) -> None:
    scanner = MagicMock()
    verifier = PatchQualityVerifier(Path("/tmp/mock-repo"), scanner)

    result = verifier.verify_finding_resolved(_draft(), application_result)

    assert result.outcome is VerificationOutcome.UNVERIFIED
    assert "无法确认" in result.message
    scanner.scan.assert_not_called()
