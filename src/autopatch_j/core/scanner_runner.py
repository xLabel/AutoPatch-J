from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.models import CodeScope
from autopatch_j.scanners import DEFAULT_SCANNER_NAME, get_scanner
from autopatch_j.scanners.base import JavaScanner, ScanResult


class ScannerRunner:
    """
    基准扫描服务 (Core Service)
    职责：统一触发扫描器并持久化扫描快照。
    """

    def __init__(self, repo_root: Path, artifact_manager: ArtifactManager) -> None:
        self.repo_root = repo_root.resolve()
        self.artifact_manager = artifact_manager

    def run_scan_and_save(self, scope: CodeScope) -> tuple[str, ScanResult]:
        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        if scanner is None:
            raise RuntimeError(f"未找到默认扫描器：{DEFAULT_SCANNER_NAME}")

        result = scanner.scan(self.repo_root, list(scope.focus_files))
        if result.status != "ok":
            raise RuntimeError(result.message)

        artifact_id = self.artifact_manager.save_scan_result(result)
        return artifact_id, result
