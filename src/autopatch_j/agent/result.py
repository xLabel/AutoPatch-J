"""SDK 运行完成状态与用户可见回答，不从工具字典推断业务结论。"""

import dataclasses
import enum


class RunStatus(enum.StrEnum):
    """完整、受限、失败与取消分别表达，受限不冒充完成。"""

    COMPLETED = "completed"
    LIMITED = "limited"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclasses.dataclass(frozen=True, slots=True)
class AgentRunResult:
    """用户可见回答和完成状态，不能替代持久业务状态。"""

    final_answer: str
    status: RunStatus = RunStatus.COMPLETED
