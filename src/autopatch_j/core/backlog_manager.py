from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autopatch_j.core.models import (
    AuditAttemptDecision,
    AuditAttemptOutcome,
    AuditFindingItem,
    AuditFindingStatus,
)
from autopatch_j.scanners.base import ScanResult


@dataclass(slots=True)
class BacklogManager:
    """
    本轮审计 finding 队列的状态机。

    职责边界：
    1. 将 ScanResult 展开为 F1/F2 这类可逐个推进的 AuditFindingItem。
    2. 根据 propose_patch 的工具消息推断补丁已就绪、可重试或失败。
    3. 不生成补丁、不调用 Agent，也不写 workspace；这些由 WorkflowController 协调。
    """

    def fetch_backlog(self, scan_result: ScanResult) -> list[AuditFindingItem]:
        backlog: list[AuditFindingItem] = []
        for index, finding in enumerate(scan_result.findings, start=1):
            backlog.append(
                AuditFindingItem(
                    finding_id=f"F{index}",
                    file_path=finding.path,
                    check_id=finding.check_id,
                    start_line=finding.start_line,
                    end_line=finding.end_line,
                    message=finding.message,
                    snippet=finding.snippet,
                )
            )
        return backlog

    def fetch_current_finding(self, backlog: list[AuditFindingItem]) -> AuditFindingItem | None:
        for item in backlog:
            if item.is_pending():
                return item
        return None

    def mark_patch_ready(self, backlog: list[AuditFindingItem], finding_id: str) -> None:
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.status = AuditFindingStatus.PATCH_READY
        item.last_error_code = None
        item.last_error_message = None

    def record_retry(
        self,
        backlog: list[AuditFindingItem],
        finding_id: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.retry_count += 1
        item.last_error_code = error_code
        item.last_error_message = error_message

    def mark_failed(
        self,
        backlog: list[AuditFindingItem],
        finding_id: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.status = AuditFindingStatus.FAILED
        item.last_error_code = error_code
        item.last_error_message = error_message

    def infer_attempt_decision(
        self,
        current_item: AuditFindingItem,
        messages: list[dict[str, Any]],
    ) -> AuditAttemptDecision:
        propose_messages = [
            message
            for message in messages
            if message.get("role") == "tool" and message.get("name") == "propose_patch"
        ]

        for message in reversed(propose_messages):
            payload = message.get("tool_payload")
            if not isinstance(payload, dict):
                payload = {}
            payload_finding_id = payload.get("associated_finding_id")
            payload_file_path = payload.get("file_path")
            if payload_finding_id and payload_finding_id != current_item.finding_id:
                continue
            if payload_file_path and payload_file_path != current_item.file_path:
                continue

            status = str(message.get("tool_status", ""))
            if status in {"ok", "invalid"}:
                return AuditAttemptDecision(outcome=AuditAttemptOutcome.PATCH_READY)

            error_code = self._fetch_error_code(payload=payload, message=message)
            error_message = self._fetch_error_message(payload=payload, message=message)
            if error_code == "OLD_STRING_NOT_FOUND":
                return AuditAttemptDecision(
                    outcome=AuditAttemptOutcome.RETRYABLE_ERROR,
                    error_code=error_code,
                    error_message=error_message,
                )
            return AuditAttemptDecision(
                outcome=AuditAttemptOutcome.NO_PATCH,
                error_code=error_code,
                error_message=error_message,
            )

        return AuditAttemptDecision(outcome=AuditAttemptOutcome.NO_PATCH)

    def _fetch_item(self, backlog: list[AuditFindingItem], finding_id: str) -> AuditFindingItem | None:
        for item in backlog:
            if item.finding_id == finding_id:
                return item
        return None

    def _fetch_error_code(self, payload: dict[str, Any], message: dict[str, Any]) -> str | None:
        if payload.get("error_code") is not None:
            return str(payload["error_code"])
        if message.get("tool_status") == "error":
            return "UNKNOWN"
        return None

    def _fetch_error_message(self, payload: dict[str, Any], message: dict[str, Any]) -> str | None:
        if payload.get("error_message") is not None:
            return str(payload["error_message"])
        content = message.get("content")
        return str(content) if content is not None else None
