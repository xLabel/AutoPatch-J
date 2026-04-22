from __future__ import annotations

from pathlib import Path
from autopatch_j.scanners.base import JavaScanner, ScannerMeta, ScannerName, ScanResult


class SpotBugsScanner(JavaScanner):
    """SpotBugs 扫描器适配器 (规划中)"""
    name = ScannerName.SPOTBUGS

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        return ScannerMeta(
            name=self.name,
            is_implemented=False,
            status="规划中 (Coming Soon)",
            description="基于字节码分析的 Java 潜在 Bug 探测工具。"
        )

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        return ScanResult(self.name.value, scope, [], "error", "SpotBugs 尚未实现适配。")
