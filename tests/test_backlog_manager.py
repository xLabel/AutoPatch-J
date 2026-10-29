"""用固定 finding 和工具观察核对一次重读预算及处理状态。"""

from autopatch_j.core import finding
from autopatch_j.core.domain import audit
from autopatch_j.core.review import backlog as review_backlog
from autopatch_j.scanners import models
from autopatch_j.tools import contract


def _scan_result() -> models.ScanResult:
    return models.ScanResult(
        engine="semgrep",
        scope=["src/main/java/demo"],
        targets=["src/main/java/demo"],
        status="ok",
        message="ok",
        findings=[
            models.Finding(
                fingerprint=f"apj-v1:{"a" * 64}:1",
                check_id="rule-a",
                path="src/main/java/demo/AppConfig.java",
                region=finding.SourceRegion(6, 1, 6, 18, 100, 117),
                severity="warning",
                message="missing null check",
                snippet="this.mode = mode;",
            ),
            models.Finding(
                fingerprint=f"apj-v1:{"b" * 64}:1",
                check_id="rule-b",
                path="src/main/java/demo/UserService.java",
                region=finding.SourceRegion(5, 1, 5, 39, 80, 118),
                severity="warning",
                message="unsafe equals order",
                snippet='return user.getName().equals("admin");',
            ),
        ],
    )


def test_backlog_builds_logical_finding_queue() -> None:
    service = review_backlog.FindingBacklog()
    backlog = service.build_from_scan_result(_scan_result())
    assert [item.finding_id for item in backlog] == ["F1", "F2"]
    current = service.current(backlog)
    assert current is not None
    assert current.file_path == "src/main/java/demo/AppConfig.java"


def test_backlog_detects_retryable_old_string_error() -> None:
    service = review_backlog.FindingBacklog()
    current_item = service.build_from_scan_result(_scan_result())[0]
    decision = service.infer_attempt_decision(
        current_item=current_item,
        observations=(
            contract.ToolExecutionResult(
                name="propose_patch",
                status="error",
                payload={
                    "file_path": "src/main/java/demo/AppConfig.java",
                    "associated_finding_id": "F1",
                    "error_code": "OLD_STRING_NOT_FOUND",
                    "error_message": "not found",
                },
                message="补丁提案生成失败",
            ),
        ),
    )
    assert decision.outcome is audit.AuditAttemptOutcome.RETRYABLE_ERROR
    assert decision.error_code == "OLD_STRING_NOT_FOUND"


def test_backlog_normalizes_finding_id_before_matching_tool_payload() -> None:
    service = review_backlog.FindingBacklog()
    current_item = service.build_from_scan_result(_scan_result())[0]
    decision = service.infer_attempt_decision(
        current_item=current_item,
        observations=(
            contract.ToolExecutionResult(
                name="propose_patch",
                status="error",
                payload={
                    "file_path": "src/main/java/demo/AppConfig.java",
                    "associated_finding_id": "f1",
                    "error_code": "OLD_STRING_NOT_FOUND",
                    "error_message": "not found",
                },
                message="补丁提案生成失败",
            ),
        ),
    )
    assert decision.outcome is audit.AuditAttemptOutcome.RETRYABLE_ERROR
    assert decision.error_code == "OLD_STRING_NOT_FOUND"


def test_backlog_marks_patch_ready_and_failure() -> None:
    service = review_backlog.FindingBacklog()
    backlog = service.build_from_scan_result(_scan_result())
    service.mark_patch_ready(backlog, "F1")
    service.record_retry(backlog, "F2", "OLD_STRING_NOT_FOUND", "retry")
    service.mark_failed(backlog, "F2", "UNKNOWN", "give up")
    assert backlog[0].status is audit.AuditFindingStatus.PATCH_READY
    assert backlog[1].retry_count == 1
    assert backlog[1].status is audit.AuditFindingStatus.FAILED
    assert service.current(backlog) is None
