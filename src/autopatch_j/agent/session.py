"""一次 SDK 运行的业务依赖与暂存状态，不拥有另一套会话或 Memory。"""

import dataclasses
import pathlib

from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service


@dataclasses.dataclass
class AgentSession:
    """一次 SDK 运行的暂存业务状态，成功提交前不改变持久提案。"""

    repo_root: pathlib.Path
    repair: service.RepairService
    evidence: evidence.EvidenceService
    reader: source_reader.SourceReader
    symbols: symbol_index.SymbolIndex
    state: models.RepairState
    user_text: str
    notices: list[str] = dataclasses.field(default_factory=list)
