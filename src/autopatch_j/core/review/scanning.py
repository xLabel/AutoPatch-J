from __future__ import annotations

from pathlib import Path

from autopatch_j.core.domain.scope import CodeScope
from autopatch_j.core.review.artifacts import ProjectArtifactStore
from autopatch_j.scanners import DEFAULT_SCANNER_CATALOG, DEFAULT_SCANNER_NAME
from autopatch_j.scanners.catalog import ScannerCatalog
from autopatch_j.scanners.models import ScanResult


class StaticScanRunner:
    """
    静态扫描入口服务。

    职责边界：
    1. 选择默认 Java scanner，对 CodeScope 中的文件范围执行扫描。
    2. 将成功扫描结果保存为 artifact，供后续 finding 详情和补丁流程引用。
    3. 不解析用户输入，也不解释扫描结果；范围解析和 backlog 推进分别由其他服务负责。
    """

    def __init__(
        self,
        repo_root: Path,
        artifact_store: ProjectArtifactStore,
        scanner_catalog: ScannerCatalog = DEFAULT_SCANNER_CATALOG,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.artifact_store = artifact_store
        self.scanner_catalog = scanner_catalog

    def run_scan_and_save(self, scope: CodeScope) -> tuple[str, ScanResult]:
        scanner = self.scanner_catalog.get(DEFAULT_SCANNER_NAME)
        if scanner is None:
            raise RuntimeError(f"未找到默认扫描器：{DEFAULT_SCANNER_NAME}")

        result = scanner.scan(self.repo_root, list(scope.focus_files))
        if result.status != "ok":
            raise RuntimeError(result.message)

        artifact_id = self.artifact_store.save_scan_result(result)
        return artifact_id, result
