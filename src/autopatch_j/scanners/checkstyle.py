from __future__ import annotations

from autopatch_j.scanners.base import ScannerCatalogEntry


class CheckstyleScanner:
    name = "Checkstyle"

    def catalog_entry(self) -> ScannerCatalogEntry:
        return ScannerCatalogEntry(
            name=self.name,
            selected=False,
            status="接入中，敬请期待",
            message="接入中，敬请期待",
        )
