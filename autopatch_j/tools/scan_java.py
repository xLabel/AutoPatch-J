from __future__ import annotations

from pathlib import Path

from autopatch_j.scanners import Finding, JavaScanner, ScanResult, build_default_java_scanner
from autopatch_j.scanners.semgrep import normalize_semgrep_payload, select_targets


def scan_java(
    repo_root: Path,
    scope: list[str],
    scanner: JavaScanner | None = None,
) -> ScanResult:
    active_scanner = scanner or build_default_java_scanner()
    return active_scanner.scan(repo_root, scope)


__all__ = [
    "Finding",
    "ScanResult",
    "normalize_semgrep_payload",
    "scan_java",
    "select_targets",
]
