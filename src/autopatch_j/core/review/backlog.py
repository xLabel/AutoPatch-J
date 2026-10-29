"""按 finding 顺序推进草案处理，最多一次强制重读。"""

from __future__ import annotations

import dataclasses
import re

from autopatch_j.core.domain import audit
from autopatch_j.scanners import models
from autopatch_j.tools import contract


@dataclasses.dataclass(slots=True)
class FindingBacklog:
    """本轮审计 finding 队列的状态机。

    职责边界：
    1. 将 ScanResult 展开为 F1/F2 这类可逐个推进的 FindingTask。
    2. 根据 propose_patch 的工具消息推断补丁已就绪、可重试或失败。
    3. 不生成补丁、不调用 Agent，也不写 workspace；这些由 CLI workflow 协调。
    """

    def build_from_scan_result(
        self, scan_result: models.ScanResult
    ) -> list[audit.FindingTask]:
        """将完整成功扫描的 finding 按顺序分配本轮 F 句柄。"""
        backlog: list[audit.FindingTask] = []
        for index, finding in enumerate(scan_result.findings, start=1):
            backlog.append(
                audit.FindingTask(
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

    def current(
        self, backlog: list[audit.FindingTask]
    ) -> audit.FindingTask | None:
        """返回下一个待处理 finding，没有待办时返回 None。"""
        for item in backlog:
            if item.is_pending():
                return item
        return None

    def mark_patch_ready(
        self, backlog: list[audit.FindingTask], finding_id: str
    ) -> None:
        """将指定 finding 标记为已有待审草案。"""
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.status = audit.AuditFindingStatus.PATCH_READY
        item.last_error_code = None
        item.last_error_message = None

    def record_retry(
        self,
        backlog: list[audit.FindingTask],
        finding_id: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        """记录本次强制重读次数及上次错误，不自动执行模型。"""
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.retry_count += 1
        item.last_error_code = error_code
        item.last_error_message = error_message

    def mark_failed(
        self,
        backlog: list[audit.FindingTask],
        finding_id: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        """保存指定 finding 的处理失败原因并结束该项。"""
        item = self._fetch_item(backlog, finding_id)
        if item is None:
            return
        item.status = audit.AuditFindingStatus.FAILED
        item.last_error_code = error_code
        item.last_error_message = error_message

    def infer_attempt_decision(
        self,
        current_item: audit.FindingTask,
        observations: tuple[contract.ToolExecutionResult, ...],
    ) -> audit.FindingAttemptDecision:
        """只根据当前目标的真实 propose 观察判断草案或一次重读资格。"""
        for observation in reversed(observations):
            if observation.name != "propose_patch":
                continue
            payload = (
                observation.payload
                if isinstance(observation.payload, dict)
                else {}
            )
            finding_id = self._normalize_finding_id(
                payload.get("associated_finding_id")
            )
            if finding_id and finding_id != current_item.finding_id:
                continue
            if (
                payload.get("file_path")
                and payload["file_path"] != current_item.file_path
            ):
                continue
            if observation.status in {"ok", "invalid"}:
                return audit.FindingAttemptDecision(
                    outcome=audit.AuditAttemptOutcome.PATCH_READY
                )
            error_code = str(payload.get("error_code") or "UNKNOWN")
            return audit.FindingAttemptDecision(
                outcome=(
                    audit.AuditAttemptOutcome.RETRYABLE_ERROR
                    if error_code == "OLD_STRING_NOT_FOUND"
                    else audit.AuditAttemptOutcome.NO_PATCH
                ),
                error_code=error_code,
                error_message=str(
                    payload.get("error_message") or observation.message
                ),
            )
        return audit.FindingAttemptDecision(
            outcome=audit.AuditAttemptOutcome.NO_PATCH
        )

    def _fetch_item(
        self, backlog: list[audit.FindingTask], finding_id: str
    ) -> audit.FindingTask | None:
        for item in backlog:
            if item.finding_id == finding_id:
                return item
        return None

    def _normalize_finding_id(self, finding_id: object) -> str | None:
        if finding_id is None:
            return None
        match = re.fullmatch(r"[Ff]([1-9]\d*)", str(finding_id).strip())
        if match is None:
            return str(finding_id)
        return f"F{int(match.group(1))}"
