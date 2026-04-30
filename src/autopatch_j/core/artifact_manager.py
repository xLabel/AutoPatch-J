from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from autopatch_j.config import get_project_state_dir
from autopatch_j.core.models import ActiveWorkspace
from autopatch_j.scanners.base import Finding, ScanResult


@dataclass(slots=True)
class ArtifactManager:
    """
    .autopatch-j 目录下的 JSON 工件存储驱动。

    职责边界：
    1. 保存和读取扫描快照、workspace 快照，并生成 scan artifact id。
    2. 只处理物理读写和 JSON 反序列化容错。
    3. 不理解补丁队列、审核游标或 finding 状态机；这些属于 core 领域服务。
    """

    repo_root: Path
    state_dir: Path = field(init=False)
    findings_dir: Path = field(init=False)
    workspace_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = get_project_state_dir(self.repo_root)
        self.findings_dir = self.state_dir / "findings"
        self.workspace_file = self.state_dir / "workspace.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.findings_dir.mkdir(parents=True, exist_ok=True)

    def save_scan_result(self, result: ScanResult) -> str:
        """保存扫描结果快照并返回产生的 ID"""
        artifact_id = self._generate_id("scan")
        target_path = self.findings_dir / f"{artifact_id}.json"
        target_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return artifact_id

    def load_scan_result(self, artifact_id: str) -> ScanResult | None:
        """从磁盘加载指定 ID 的扫描结果"""
        target_path = self.findings_dir / f"{artifact_id}.json"
        if not target_path.exists():
            return None
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            return ScanResult.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def get_finding_by_index(self, artifact_id: str, index: int) -> Finding | None:
        """快捷定位扫描结果中的特定 finding"""
        result = self.load_scan_result(artifact_id)
        if result is None or index < 0 or index >= len(result.findings):
            return None
        return result.findings[index]

    def save_workspace(self, workspace: ActiveWorkspace) -> None:
        """保存工作台全量状态"""
        self.workspace_file.write_text(
            json.dumps(workspace.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_workspace(self) -> ActiveWorkspace | None:
        """加载工作台全量状态"""
        if not self.workspace_file.exists():
            return None
        try:
            data = json.loads(self.workspace_file.read_text(encoding="utf-8"))
            return ActiveWorkspace.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def clear_workspace(self) -> None:
        """彻底清除工作台文件"""
        if self.workspace_file.exists():
            self.workspace_file.unlink()

    def _generate_id(self, prefix: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{prefix}-{timestamp}"
