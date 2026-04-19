from __future__ import annotations

from autopatch_j.scanners.base import Finding, JavaScanner, ScanResult
from autopatch_j.scanners.catalog import ScannerCatalogEntry, build_scanner_catalog
from autopatch_j.scanners.semgrep import SemgrepScanner


def build_java_scanner() -> SemgrepScanner:
    return SemgrepScanner()
