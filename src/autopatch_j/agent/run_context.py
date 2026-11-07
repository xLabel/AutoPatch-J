"""一次 SDK 运行的业务依赖与暂存状态，不拥有另一套会话或 Memory。"""

import dataclasses
import pathlib

from autopatch_j import config
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.core.repair import status
from autopatch_j.core.repair import store


@dataclasses.dataclass
class AgentRunContext:
    """一次 SDK 运行的暂存业务状态，成功提交前不改变持久提案。"""

    repo_root: pathlib.Path
    repair: service.RepairService
    evidence: evidence.EvidenceService
    source_reader: source_reader.SourceReader
    symbol_index: symbol_index.SymbolIndex
    state: models.RepairState
    user_text: str
    notices: list[str] = dataclasses.field(default_factory=list)
    base_state: models.RepairState | None = None
    ignored_dirs: frozenset[str] = config.IGNORED_DIRS

    def require_current(self) -> models.RepairState:
        """只核对基线，不把另一运行的持久状态换入工作副本。"""
        if self.base_state is None:
            raise RuntimeError("Agent 运行尚未取得持久业务基线。")
        with self.repair.store.locked() as latest:
            store.require_current(latest, self.base_state)
        return self.base_state

    def repair_status(self) -> status.RepairStatus:
        """模型指令与只读工具共用相同事实及新鲜度语义。"""
        baseline = self.require_current()
        return status.describe(self.repo_root, self.state, baseline)
