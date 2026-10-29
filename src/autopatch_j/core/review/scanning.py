"""选择扫描器并仅保存完整成功的扫描工件。"""

from __future__ import annotations

import pathlib

from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.review import artifacts
from autopatch_j.scanners import catalog
from autopatch_j.scanners import models


class StaticScanRunner:
    """静态扫描入口服务。

    职责边界：
    1. 选择默认 Java scanner，对 CodeScope 中的文件范围执行扫描。
    2. 将成功扫描结果保存为 artifact，供后续 finding 详情和补丁流程引用。
    3. 不解析用户输入，也不解释扫描结果；范围解析和 backlog 推进分别由其他服务负责。
    """

    def __init__(
        self,
        repo_root: pathlib.Path,
        artifact_store: artifacts.ProjectArtifactStore,
        scanner_catalog: catalog.ScannerCatalog = (
            catalog.DEFAULT_SCANNER_CATALOG
        ),
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.artifact_store = artifact_store
        self.scanner_catalog = scanner_catalog

    def run_scan_and_save(
        self, scope: domain_scope.CodeScope
    ) -> tuple[str, models.ScanResult]:
        """运行已解析范围，只持久化完整成功结果，失败抛出 RuntimeError。"""
        scanner = self.scanner_catalog.get(catalog.DEFAULT_SCANNER_NAME)
        if scanner is None:
            raise RuntimeError(
                f"未找到默认扫描器：{catalog.DEFAULT_SCANNER_NAME}"
            )

        result = scanner.scan(self.repo_root, list(scope.focus_files))
        if result.status != "ok":
            raise RuntimeError(result.message)

        artifact_id = self.artifact_store.save_scan_result(result)
        return artifact_id, result
