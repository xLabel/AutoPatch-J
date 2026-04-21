from __future__ import annotations

from pathlib import Path
from autopatch_j.scanners.base import ScannerMeta, ScannerName


class SpotBugsScanner:
    """SpotBugs 扫描器 (规划中)"""
    name = ScannerName.SPOTBUGS

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        return ScannerMeta(
            name=self.name,
            is_implemented=False,
            status="规划中 (Coming Soon)",
            description="基于字节码分析的 Java 潜在 Bug 探测工具。"
        )
