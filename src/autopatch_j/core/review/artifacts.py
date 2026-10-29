"""扫描和审核 JSON 工件的原子持久化及工作台重置。"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
import shutil
import uuid

from autopatch_j import config
from autopatch_j.core import json_data
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.scanners import models


@dataclasses.dataclass(slots=True)
class ProjectArtifactStore:
    """.autopatch-j 目录下的 JSON 工件存储驱动。

    职责边界：
    1. 保存和读取扫描快照、workspace 快照，并生成 scan artifact id。
    2. 只处理物理读写和 JSON 反序列化容错。
    3. 不理解补丁队列、审核游标或 finding 状态机；这些属于 core 领域服务。
    """

    repo_root: pathlib.Path
    state_dir: pathlib.Path = dataclasses.field(init=False)
    findings_dir: pathlib.Path = dataclasses.field(init=False)
    workspace_file: pathlib.Path = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = config.get_project_state_dir(self.repo_root)
        self.findings_dir = self.state_dir / "findings"
        self.workspace_file = self.state_dir / "workspace.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.findings_dir.mkdir(parents=True, exist_ok=True)

    def save_scan_result(self, result: models.ScanResult) -> str:
        """原子保存成功扫描工件并返回本地 scan ID。"""
        artifact_id = self._generate_id("scan")
        target_path = self.findings_dir / f"{artifact_id}.json"
        self._write_json_atomic(target_path, result.to_dict())
        return artifact_id

    def load_scan_result(self, artifact_id: str) -> models.ScanResult | None:
        """读取当前扫描 schema；缺失或无效工件返回 None。"""
        target_path = self.findings_dir / f"{artifact_id}.json"
        if not target_path.exists():
            return None
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            return models.ScanResult.from_dict(json_data.object_fields(data))
        except json.JSONDecodeError, KeyError, TypeError, ValueError:
            return None

    def get_finding_by_index(
        self, artifact_id: str, index: int
    ) -> models.Finding | None:
        """按扫描快照内序号取 finding，拒绝不存在或越界的条目。"""
        result = self.load_scan_result(artifact_id)
        if result is None or index < 0 or index >= len(result.findings):
            return None
        return result.findings[index]

    def save_review_workspace(
        self, workspace: domain_workspace.ReviewWorkspace
    ) -> None:
        """原子保存审核工作台快照。"""
        self._write_json_atomic(self.workspace_file, workspace.to_dict())

    def load_review_workspace(self) -> domain_workspace.ReviewWorkspace | None:
        """恢复有效审核快照；缺失或不合法时返回 None。"""
        if not self.workspace_file.exists():
            return None
        try:
            data = json.loads(self.workspace_file.read_text(encoding="utf-8"))
            return domain_workspace.ReviewWorkspace.from_dict(
                json_data.object_fields(data)
            )
        except json.JSONDecodeError, KeyError, TypeError, ValueError:
            return None

    def clear_review_workspace(self) -> None:
        """删除审核快照，保留其他项目数据。"""
        if self.workspace_file.exists():
            self.workspace_file.unlink()

    def clear_project_state(self) -> None:
        """清空当前项目的工作台状态，保留独立的 Memory 与 CLI history。

        `/reset` 只负责 review、scan、index 和运行时缓存；Memory 数据库、
        一次性导出与终端输入历史有各自的显式管理命令。
        """

        if not self._is_expected_state_dir():
            raise ValueError(f"拒绝清理非项目状态目录: {self.state_dir}")
        if self.state_dir.exists():
            for child in self.state_dir.iterdir():
                if self._should_preserve_on_reset(child):
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _should_preserve_on_reset(self, path: pathlib.Path) -> bool:
        name = path.name
        return (
            name == "history.txt"
            or name.startswith("memory.sqlite3")
            or name.startswith("conversations.sqlite3")
            or name.startswith("memory.db")
            or name == "memory_summary.md"
            or name.startswith("memory-export")
        )

    def _is_expected_state_dir(self) -> bool:
        return (
            self.state_dir.name == ".autopatch-j"
            and self.state_dir.parent == self.repo_root.resolve()
        )

    def _generate_id(self, prefix: str) -> str:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%S%f"
        )
        return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"

    def _write_json_atomic(self, target_path: pathlib.Path, data: dict) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        tmp_path = target_path.with_name(
            f"{target_path.name}.{uuid.uuid4().hex}.tmp"
        )
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(target_path)
