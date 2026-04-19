from __future__ import annotations

from autopatch_j.scanners.model import Finding, JavaScanner, ScanResult
from autopatch_j.scanners.semgrep import SemgrepScanner


def build_java_scanner() -> SemgrepScanner:
    return SemgrepScanner()


def build_default_java_scanner() -> SemgrepScanner:
    return build_java_scanner()


__all__ = [
    "Finding",
    "JavaScanner",
    "ScanResult",
    "SemgrepScanner",
    "build_java_scanner",
    "build_default_java_scanner",
]
