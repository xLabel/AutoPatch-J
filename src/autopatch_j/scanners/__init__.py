from __future__ import annotations

from autopatch_j.scanners.base import Finding, JavaScanner, ScanResult, ScannerMeta
from autopatch_j.scanners.checkstyle import CheckstyleScanner
from autopatch_j.scanners.pmd import PMDScanner
from autopatch_j.scanners.semgrep import SemgrepScanner
from autopatch_j.scanners.spotbugs import SpotBugsScanner

ALL_SCANNERS = [
    SemgrepScanner(),
    PMDScanner(),
    SpotBugsScanner(),
    CheckstyleScanner(),
]


def build_java_scanner() -> SemgrepScanner:
    return SemgrepScanner()


def get_scanner(name: str) -> object | None:
    for scanner in ALL_SCANNERS:
        if scanner.name == name:
            return scanner
    return None
