from __future__ import annotations

from autopatch_j.core.backlog_manager import BacklogManager
from autopatch_j.core.models import AuditAttemptOutcome, AuditFindingStatus
from autopatch_j.scanners.base import Finding, ScanResult


def _scan_result() -> ScanResult:
    return ScanResult(
        engine="semgrep",
        scope=["src/main/java/demo"],
        targets=["src/main/java/demo"],
        status="ok",
        message="ok",
        findings=[
            Finding(
                check_id="rule-a",
                path="src/main/java/demo/AppConfig.java",
                start_line=6,
                end_line=6,
                severity="warning",
                message="missing null check",
                snippet="this.mode = mode;",
            ),
            Finding(
                check_id="rule-b",
                path="src/main/java/demo/UserService.java",
                start_line=5,
                end_line=5,
                severity="warning",
                message="unsafe equals order",
                snippet='return user.getName().equals("admin");',
            ),
        ],
    )


def test_backlog_manager_builds_logical_finding_queue() -> None:
    service = BacklogManager()

    backlog = service.fetch_backlog(_scan_result())

    assert [item.finding_id for item in backlog] == ["F1", "F2"]
    assert service.fetch_current_finding(backlog) is not None
    assert service.fetch_current_finding(backlog).file_path == "src/main/java/demo/AppConfig.java"


def test_backlog_manager_detects_retryable_old_string_error() -> None:
    service = BacklogManager()
    current_item = service.fetch_backlog(_scan_result())[0]

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


def test_backlog_manager_marks_patch_ready_and_failure() -> None:
    service = BacklogManager()
    backlog = service.fetch_backlog(_scan_result())

    service.mark_patch_ready(backlog, "F1")
    service.record_retry(backlog, "F2", "OLD_STRING_NOT_FOUND", "retry")
    service.mark_failed(backlog, "F2", "UNKNOWN", "give up")

    assert backlog[0].status is AuditFindingStatus.PATCH_READY
    assert backlog[1].retry_count == 1
    assert backlog[1].status is AuditFindingStatus.FAILED
    assert service.fetch_current_finding(backlog) is None
