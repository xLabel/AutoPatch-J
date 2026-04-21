from __future__ import annotations

from pathlib import Path
from autopatch_j.scanners.base import ScannerMeta, ScannerName


class PMDScanner:
    """PMD 扫描器 (规划中)"""
    name = ScannerName.PMD

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        return ScannerMeta(
            name=self.name,
            is_implemented=False,
            status="规划中 (Coming Soon)",
            description="基于静态规则的 Java 代码质量分析工具。"
        )
