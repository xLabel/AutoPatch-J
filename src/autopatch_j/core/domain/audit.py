"""finding 处理队列、有限重读和草案生成的业务状态。"""

from __future__ import annotations

import dataclasses
import enum


class AuditFindingStatus(str, enum.Enum):
    """审计 finding 在自动修复流程中的推进状态。"""

    PENDING = "pending"
    PATCH_READY = "patch_ready"
    FAILED = "failed"


class AuditAttemptOutcome(str, enum.Enum):
    """单次 finding 修复尝试的归因结果。"""

    PATCH_READY = "patch_ready"
    RETRYABLE_ERROR = "retryable_error"
    NO_PATCH = "no_patch"


@dataclasses.dataclass(slots=True)
class FindingTask:
    """扫描 finding 在本轮审计 workflow 中的待办项。

    它保存 F1/F2 这类逻辑句柄、重试次数和最后失败原因，不直接持久化到 workspace。
    """

    finding_id: str
    file_path: str
    check_id: str
    start_line: int
    end_line: int
    message: str
    snippet: str
    status: AuditFindingStatus = AuditFindingStatus.PENDING
    retry_count: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None

    def is_pending(self) -> bool:
        """该 finding 是否仍等待本轮处理。"""
        return self.status is AuditFindingStatus.PENDING


@dataclasses.dataclass(slots=True)
class FindingAttemptDecision:
    """一次 Agent 修复尝试后的流程决策。

    FindingBacklog 根据工具消息推断该 finding 是否已产生补丁、是否可重试或应标记失败。
    """

    outcome: AuditAttemptOutcome
    error_code: str | None = None
    error_message: str | None = None
