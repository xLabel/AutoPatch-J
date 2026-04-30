from __future__ import annotations

from pathlib import Path

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.models import CodeScope
from autopatch_j.scanners import DEFAULT_SCANNER_NAME, get_scanner
from autopatch_j.scanners.base import JavaScanner, ScanResult


class ScannerRunner:
    """
    静态扫描入口服务。

    职责边界：
    1. 选择默认 Java scanner，对 CodeScope 中的文件范围执行扫描。
    2. 将成功扫描结果保存为 artifact，供后续 finding 详情和补丁流程引用。
    3. 不解析用户输入，也不解释扫描结果；范围解析和 backlog 推进分别由其他服务负责。
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
