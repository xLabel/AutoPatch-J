from __future__ import annotations

from pathlib import Path
from autopatch_j.scanners.base import JavaScanner, ScannerMeta, ScannerName, ScanResult


class CheckstyleScanner(JavaScanner):
    """Checkstyle 扫描器适配器 (规划中)"""
    name = ScannerName.CHECKSTYLE

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        return ScannerMeta(
            name=self.name,
            is_implemented=False,
            status="规划中 (Coming Soon)",
            description="Java 代码风格与规范强制检查工具。"
        )

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        return ScanResult(self.name.value, scope, [], "error", "Checkstyle 尚未实现适配。")
