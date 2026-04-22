from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopatch_j.config import get_project_state_dir
from autopatch_j.core.models import ActiveWorkspace
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.scanners.base import Finding, ScanResult


@dataclass(slots=True)
class ArtifactManager:
    """
    状态持久化管家 (Core Service)
    职责：管理项目本地状态 (.autopatch-j/) 下的文件存储与读取。
    主要处理：扫描结果 (findings) 和 待确认补丁 (pending_patch)。
    """
    repo_root: Path
    # 显式声明字段，以便 slots 能够预留空间
    state_dir: Path = field(init=False)
    findings_dir: Path = field(init=False)
    patches_dir: Path = field(init=False)
    workspace_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = get_project_state_dir(self.repo_root)
        self.findings_dir = self.state_dir / "findings"
        self.patches_dir = self.state_dir / "patches"
        self.workspace_file = self.state_dir / "workspace.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """确保必要的存储目录存在"""
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        self.patches_dir.mkdir(parents=True, exist_ok=True)

    # --- 扫描结果 (Findings) 管理 ---

    def persist_scan_result(self, result: ScanResult) -> str:
        """保存扫描结果，并返回一个唯一的 artifact_id"""
        artifact_id = self._generate_id("scan")
        target_path = self.findings_dir / f"{artifact_id}.json"
        
        data = result.to_dict()
        target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return artifact_id

    def fetch_scan_result(self, artifact_id: str) -> ScanResult | None:
        """根据 ID 加载扫描结果"""
        target_path = self.findings_dir / f"{artifact_id}.json"
        if not target_path.exists():
            return None
        
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            return ScanResult.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def persist_workspace(self, workspace: ActiveWorkspace) -> None:
        self.workspace_file.write_text(
            json.dumps(workspace.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def fetch_workspace(self) -> ActiveWorkspace | None:
        if not self.workspace_file.exists():
            return None
        try:
            data = json.loads(self.workspace_file.read_text(encoding="utf-8"))
            return ActiveWorkspace.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def clear_workspace(self) -> None:
        if self.workspace_file.exists():
            self.workspace_file.unlink()

    def fetch_finding_by_index(self, artifact_id: str, index: int) -> Finding | None:
        """
        从特定的扫描快照中按索引提取单个 Finding。
        """
        result = self.fetch_scan_result(artifact_id)
        if result and 0 <= index < len(result.findings):
            return result.findings[index]
        return None

    # --- 补丁草案 (Pending Patch) 队列管理 ---

    def persist_pending_patch(self, draft: PatchDraft) -> None:
        """
        将补丁草案插入到队列首部 (LIFO栈模式)。
        这样当用户要求修改当前补丁时，新生成的补丁能立即出现在最前面。
        """
        target_path = self.patches_dir / "pending_queue.json"
        queue = []
        if target_path.exists():
            try:
                queue = json.loads(target_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                pass
        
        data = {
            "file_path": draft.file_path,
            "old_string": draft.old_string,
            "new_string": draft.new_string,
            "diff": draft.diff,
            "status": draft.status,
            "message": draft.message,
            "rationale": draft.rationale,
            "target_check_id": draft.target_check_id,
            "target_snippet": draft.target_snippet,
            "validation": {
                "status": draft.validation.status,
                "message": draft.validation.message,
                "errors": draft.validation.errors
            }
        }
        # 🚀 栈模式：新补丁永远在最上面
        queue.insert(0, data)
        target_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")

    def fetch_pending_patches(self) -> list[PatchDraft]:
        """加载队列中所有活跃的 Pending Patch"""
        target_path = self.patches_dir / "pending_queue.json"
        if not target_path.exists():
            return []

        try:
            queue_data = json.loads(target_path.read_text(encoding="utf-8"))
            from autopatch_j.validators.java_syntax import SyntaxValidationResult
            
            patches = []
            for data in queue_data:
                val_data = data.get("validation", {})
                validation = SyntaxValidationResult(
                    status=val_data.get("status", "unknown"),
                    message=val_data.get("message", ""),
                    errors=val_data.get("errors", [])
                )
                patches.append(PatchDraft(
                    file_path=data["file_path"],
                    old_string=data["old_string"],
                    new_string=data["new_string"],
                    diff=data["diff"],
                    validation=validation,
                    status=data["status"],
                    message=data["message"],
                    rationale=data.get("rationale"),
                    target_check_id=data.get("target_check_id"),
                    target_snippet=data.get("target_snippet")
                ))
            return patches
        except (json.JSONDecodeError, KeyError):
            return []

    def fetch_pending_patch(self) -> PatchDraft | None:
        """获取队列首部的补丁（向后兼容）"""
        queue = self.fetch_pending_patches()
        return queue[0] if queue else None

    def pop_pending_patch(self) -> None:
        """移除队列首部的补丁"""
        target_path = self.patches_dir / "pending_queue.json"
        if not target_path.exists():
            return
        try:
            queue = json.loads(target_path.read_text(encoding="utf-8"))
            if queue:
                queue.pop(0)
                target_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
        except (json.JSONDecodeError, KeyError):
            pass

    def discard_followup_patches(self) -> list[PatchDraft]:
        """
        丢弃当前补丁之后的所有后续补丁。
        返回实际被丢弃的补丁列表，顺序与原队列中的后续顺序一致。
        """
        target_path = self.patches_dir / "pending_queue.json"
        if not target_path.exists():
            return []

        try:
            queue = json.loads(target_path.read_text(encoding="utf-8"))
            if len(queue) <= 1:
                return []
            from autopatch_j.validators.java_syntax import SyntaxValidationResult

            discarded: list[PatchDraft] = []
            for data in queue[1:]:
                val_data = data.get("validation", {})
                validation = SyntaxValidationResult(
                    status=val_data.get("status", "unknown"),
                    message=val_data.get("message", ""),
                    errors=val_data.get("errors", []),
                )
                discarded.append(PatchDraft(
                    file_path=data["file_path"],
                    old_string=data["old_string"],
                    new_string=data["new_string"],
                    diff=data["diff"],
                    validation=validation,
                    status=data["status"],
                    message=data["message"],
                    rationale=data.get("rationale"),
                    target_check_id=data.get("target_check_id"),
                    target_snippet=data.get("target_snippet"),
                ))
            queue = queue[:1]
            target_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
            return discarded
        except (json.JSONDecodeError, KeyError):
            return []

    def clear_pending_patch(self) -> None:
        """清空所有的 Pending Patch"""
        target_path = self.patches_dir / "pending_queue.json"
        if target_path.exists():
            target_path.unlink()
        legacy_path = self.patches_dir / "current_pending.json"
        if legacy_path.exists():
            legacy_path.unlink()

    # --- 辅助方法 ---

    def _generate_id(self, prefix: str) -> str:
        """生成带时间戳的唯一 ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{prefix}-{timestamp}"
