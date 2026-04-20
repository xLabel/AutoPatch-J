from __future__ import annotations

from pathlib import Path

from autopatch_j.scanners.base import ScannerMeta


class PMDScanner:
    name = "PMD"

    def get_scanner(self, repo_root: Path | None = None) -> ScannerMeta:
        del repo_root
        return ScannerMeta(
            name=self.name,
            selected=False,
            status="接入中，敬请期待",
            message="接入中，敬请期待",
        )
