from __future__ import annotations

from autopatch_j.core.review import FindingBacklog
from autopatch_j.core.domain import AuditAttemptOutcome, AuditFindingStatus
from autopatch_j.scanners.models import Finding, ScanResult, SourceRegion


def _scan_result() -> ScanResult:
    return ScanResult(
        engine="semgrep",
        scope=["src/main/java/demo"],
        targets=["src/main/java/demo"],
        status="ok",
        message="ok",
        findings=[
            Finding(
                fingerprint=f"apj-v1:{'a' * 64}:1",
                check_id="rule-a",
                path="src/main/java/demo/AppConfig.java",
                region=SourceRegion(6, 1, 6, 18, 100, 117),
                severity="warning",
                message="missing null check",
                snippet="this.mode = mode;",
            ),
            Finding(
                fingerprint=f"apj-v1:{'b' * 64}:1",
                check_id="rule-b",
                path="src/main/java/demo/UserService.java",
                region=SourceRegion(5, 1, 5, 39, 80, 118),
                severity="warning",
                message="unsafe equals order",
                snippet='return user.getName().equals("admin");',
            ),
        ],
    )


def test_backlog_manager_builds_logical_finding_queue() -> None:
    service = FindingBacklog()

    backlog = service.build_from_scan_result(_scan_result())

    assert [item.finding_id for item in backlog] == ["F1", "F2"]
    assert service.current(backlog) is not None
    assert service.current(backlog).file_path == "src/main/java/demo/AppConfig.java"


def test_backlog_manager_detects_retryable_old_string_error() -> None:
    service = FindingBacklog()
    current_item = service.build_from_scan_result(_scan_result())[0]

    decision = service.infer_attempt_decision(
        current_item=current_item,
        messages=[
            {
                "role": "tool",
                "name": "propose_patch",
                "tool_status": "error",
                "tool_payload": {
                    "file_path": "src/main/java/demo/AppConfig.java",
                    "associated_finding_id": "F1",
                    "error_code": "OLD_STRING_NOT_FOUND",
                    "error_message": "not found",
                },
                "content": "补丁提案生成失败",
            }
        ],
    )

    assert decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR
    assert decision.error_code == "OLD_STRING_NOT_FOUND"


def test_backlog_manager_normalizes_finding_id_before_matching_tool_payload() -> None:
    service = FindingBacklog()
    current_item = service.build_from_scan_result(_scan_result())[0]

    decision = service.infer_attempt_decision(
        current_item=current_item,
        messages=[
            {
                "role": "tool",
                "name": "propose_patch",
                "tool_status": "error",
                "tool_payload": {
                    "file_path": "src/main/java/demo/AppConfig.java",
                    "associated_finding_id": "f1",
                    "error_code": "OLD_STRING_NOT_FOUND",
                    "error_message": "not found",
                },
                "content": "补丁提案生成失败",
            }
        ],
    )

    assert decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR
    assert decision.error_code == "OLD_STRING_NOT_FOUND"


def test_backlog_manager_marks_patch_ready_and_failure() -> None:
    service = FindingBacklog()
    backlog = service.build_from_scan_result(_scan_result())

    service.mark_patch_ready(backlog, "F1")
    service.record_retry(backlog, "F2", "OLD_STRING_NOT_FOUND", "retry")
    service.mark_failed(backlog, "F2", "UNKNOWN", "give up")

    assert backlog[0].status is AuditFindingStatus.PATCH_READY
    assert backlog[1].retry_count == 1
    assert backlog[1].status is AuditFindingStatus.FAILED
    assert service.current(backlog) is None
