from __future__ import annotations

from pathlib import Path
from autopatch_j.scanners.base import JavaScanner, ScannerMeta, ScannerName, ScanResult


class PMDScanner(JavaScanner):
    """PMD 预留适配器，仅暴露规划状态，不参与当前扫描流程。"""
    name = ScannerName.PMD

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        return ScannerMeta(
            name=self.name,
            is_implemented=False,
            status="规划中 (Coming Soon)",
            description="基于静态规则的 Java 代码质量分析工具。"
        )

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        return ScanResult(self.name.value, scope, [], "error", "PMD 尚未实现适配。")
