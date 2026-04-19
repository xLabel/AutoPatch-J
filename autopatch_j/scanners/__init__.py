from __future__ import annotations

import os
from pathlib import Path

from autopatch_j.scanners.model import Finding, JavaScanner, ScanResult
from autopatch_j.scanners.semgrep import SemgrepScanner


class UnsupportedJavaScanner:
    def __init__(self, configured_name: str) -> None:
        self.configured_name = configured_name

    @property
    def label(self) -> str:
        return f"unsupported:{self.configured_name}"

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        del repo_root
        return ScanResult(
            engine=self.configured_name,
            scope=list(scope),
            targets=[],
            status="error",
            message=(
                "Unsupported Java scanner. "
                f"Configured AUTOPATCH_SCANNER={self.configured_name!r}. "
                "Currently supported: semgrep."
            ),
            summary={"total": 0},
            findings=[],
        )


def build_default_java_scanner() -> JavaScanner:
    configured_name = os.getenv("AUTOPATCH_SCANNER", "semgrep").strip().lower() or "semgrep"
    if configured_name == "semgrep":
        config = os.getenv("AUTOPATCH_SEMGREP_CONFIG", "p/java")
        return SemgrepScanner(config=config)
    return UnsupportedJavaScanner(configured_name)


__all__ = [
    "Finding",
    "JavaScanner",
    "ScanResult",
    "SemgrepScanner",
    "UnsupportedJavaScanner",
    "build_default_java_scanner",
]
