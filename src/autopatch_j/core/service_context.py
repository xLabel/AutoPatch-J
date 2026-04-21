from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopatch_j.core.artifact_manager import ArtifactManager
    from autopatch_j.core.index_service import IndexService
    from autopatch_j.core.patch_engine import PatchEngine
    from autopatch_j.core.code_fetcher import CodeFetcher


@dataclass(slots=True)
class ServiceContext:
    """
    服务上下文 (Service Context / Bean Container)
    职责：统一持有核心 Service 实例，支持依赖注入。
    """
    repo_root: Path
    artifacts: ArtifactManager
    indexer: IndexService
    patch_engine: PatchEngine
    fetcher: CodeFetcher
