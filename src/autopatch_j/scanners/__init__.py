from __future__ import annotations

from autopatch_j.scanners.base import Finding, JavaScanner, ScanResult, ScannerCatalogEntry
from autopatch_j.scanners.catalog import build_scanner_catalog
from autopatch_j.scanners.checkstyle import CheckstyleScanner
from autopatch_j.scanners.pmd import PMDScanner
from autopatch_j.scanners.semgrep import SemgrepScanner
from autopatch_j.scanners.spotbugs import SpotBugsScanner


def build_java_scanner() -> SemgrepScanner:
    return SemgrepScanner()
