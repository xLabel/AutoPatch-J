"""业务需要的 Agent 结果，不保存另一套模型消息协议。"""

import dataclasses
import enum

from autopatch_j.tools import contract


class RunStatus(enum.StrEnum):
    """受限结果可以审核；异常和取消不能提交暂存草案。"""

    COMPLETED = "completed"
    LIMITED = "limited"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclasses.dataclass(frozen=True, slots=True)
class AgentRunResult:
    """呈现给 workflow 的最终回答与完成状态。"""

    final_answer: str
    status: RunStatus = RunStatus.COMPLETED
    observations: tuple[contract.ToolExecutionResult, ...] = ()
