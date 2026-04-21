from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopatch_j.paths import get_project_state_dir
from autopatch_j.scanners.base import ScanResult
from autopatch_j.core.patch_engine import PatchDraft


@dataclass(slots=True)
class ArtifactManager:
    """
    状态持久化管家 (Core Service)
    职责：管理项目本地状态 (.autopatch-j/) 下的文件存储与读取。
    主要处理：扫描结果 (findings) 和 待确认补丁 (pending_patch)。
    """
    repo_root: Path

    def __post_init__(self) -> None:
        self.state_dir = get_project_state_dir(self.repo_root)
        self.findings_dir = self.state_dir / "findings"
        self.patches_dir = self.state_dir / "patches"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """确保必要的存储目录存在"""
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        self.patches_dir.mkdir(parents=True, exist_ok=True)

    # --- 扫描结果 (Findings) 管理 ---

    def save_scan_result(self, result: ScanResult) -> str:
        """保存扫描结果，并返回一个唯一的 artifact_id"""
        artifact_id = self._generate_id("scan")
        target_path = self.findings_dir / f"{artifact_id}.json"
        
        data = result.to_dict()
        target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return artifact_id

    def load_scan_result(self, artifact_id: str) -> ScanResult | None:
        """根据 ID 加载扫描结果"""
        target_path = self.findings_dir / f"{artifact_id}.json"
        if not target_path.exists():
            return None
        
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            return ScanResult.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def get_finding_by_index(self, artifact_id: str, index: int) -> Finding | None:
        """
        从特定的扫描快照中按索引提取单个 Finding。
        """
        result = self.load_scan_result(artifact_id)
        if result and 0 <= index < len(result.findings):
            return result.findings[index]
        return None

    # --- 补丁草案 (Pending Patch) 管理 ---

    def save_pending_patch(self, draft: PatchDraft) -> None:
        """
        保存当前的待确认补丁。
        """
        target_path = self.patches_dir / "current_pending.json"
        
        data = {
            "file_path": draft.file_path,
            "old_string": draft.old_string,
            "new_string": draft.new_string,
            "diff": draft.diff,
            "status": draft.status,
            "message": draft.message,
            "target_check_id": draft.target_check_id,
            "target_snippet": draft.target_snippet,
            "validation": {
                "status": draft.validation.status,
                "message": draft.validation.message,
                "errors": draft.validation.errors
            }
        }
        target_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_pending_patch(self) -> PatchDraft | None:
        """加载当前活跃的 Pending Patch"""
        target_path = self.patches_dir / "current_pending.json"
        if not target_path.exists():
            return None

        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            from autopatch_j.validators.java_syntax import SyntaxValidationResult
            
            val_data = data.get("validation", {})
            validation = SyntaxValidationResult(
                status=val_data.get("status", "unknown"),
                message=val_data.get("message", ""),
                errors=val_data.get("errors", [])
            )
            
            return PatchDraft(
                file_path=data["file_path"],
                old_string=data["old_string"],
                new_string=data["new_string"],
                diff=data["diff"],
                validation=validation,
                status=data["status"],
                message=data["message"],
                target_check_id=data.get("target_check_id"),
                target_snippet=data.get("target_snippet")
            )
        except (json.JSONDecodeError, KeyError):
            return None

    def clear_pending_patch(self) -> None:
        """清空当前的 Pending Patch (例如在 Apply 或 Discard 之后)"""
        target_path = self.patches_dir / "current_pending.json"
        if target_path.exists():
            target_path.unlink()

    # --- 辅助方法 ---

    def _generate_id(self, prefix: str) -> str:
        """生成带时间戳的唯一 ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{prefix}-{timestamp}"
