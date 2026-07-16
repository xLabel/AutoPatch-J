from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from autopatch_j.core.domain.audit import (
    AuditAttemptOutcome,
    AuditFindingStatus,
    FindingAttemptDecision,
    FindingTask,
)
from autopatch_j.scanners.models import ScanResult


@dataclass(slots=True)
class FindingBacklog:
    """
    本轮审计 finding 队列的状态机。

    职责边界：
    1. 将 ScanResult 展开为 F1/F2 这类可逐个推进的 FindingTask。
    2. 根据 propose_patch 的工具消息推断补丁已就绪、可重试或失败。
    3. 不生成补丁、不调用 Agent，也不写 workspace；这些由 CLI workflow 协调。
    """

    def build_from_scan_result(self, scan_result: ScanResult) -> list[FindingTask]:
        backlog: list[FindingTask] = []
        for index, finding in enumerate(scan_result.findings, start=1):
            backlog.append(
                FindingTask(
                    finding_id=f"F{index}",
                    file_path=finding.path,
                    check_id=finding.check_id,
                    start_line=finding.region.start_line,
                    end_line=finding.region.inclusive_end_line,
                    message=finding.message,
                    snippet=finding.snippet,
                )
            )
        return backlog

    def current(self, backlog: list[FindingTask]) -> FindingTask | None:
        for item in backlog:
            if item.is_pending():
                return item
        return None

    def mark_patch_ready(self, backlog: list[FindingTask], finding_id: str) -> None:
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.status = AuditFindingStatus.PATCH_READY
        item.last_error_code = None
        item.last_error_message = None

    def record_retry(
        self,
        backlog: list[FindingTask],
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
        backlog: list[FindingTask],
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
        current_item: FindingTask,
        messages: list[dict[str, Any]],
    ) -> FindingAttemptDecision:
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
            normalized_finding_id = self._normalize_finding_id(payload_finding_id)
            if normalized_finding_id and normalized_finding_id != current_item.finding_id:
                continue
            if payload_file_path and payload_file_path != current_item.file_path:
                continue

            status = str(message.get("tool_status", ""))
            if status in {"ok", "invalid"}:
                return FindingAttemptDecision(outcome=AuditAttemptOutcome.PATCH_READY)

            error_code = self._fetch_error_code(payload=payload, message=message)
            error_message = self._fetch_error_message(payload=payload, message=message)
            if error_code == "OLD_STRING_NOT_FOUND":
                return FindingAttemptDecision(
                    outcome=AuditAttemptOutcome.RETRYABLE_ERROR,
                    error_code=error_code,
                    error_message=error_message,
                )
            return FindingAttemptDecision(
                outcome=AuditAttemptOutcome.NO_PATCH,
                error_code=error_code,
                error_message=error_message,
            )

        return FindingAttemptDecision(outcome=AuditAttemptOutcome.NO_PATCH)

    def _fetch_item(self, backlog: list[FindingTask], finding_id: str) -> FindingTask | None:
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

    def _normalize_finding_id(self, finding_id: Any) -> str | None:
        if finding_id is None:
            return None
        match = re.fullmatch(r"[Ff]([1-9]\d*)", str(finding_id).strip())
        if match is None:
            return str(finding_id)
        return f"F{int(match.group(1))}"

    def _fetch_error_message(self, payload: dict[str, Any], message: dict[str, Any]) -> str | None:
        if payload.get("error_message") is not None:
            return str(payload["error_message"])
        content = message.get("content")
        return str(content) if content is not None else None
