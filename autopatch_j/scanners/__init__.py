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


def build_java_scanner(
    scanner_name: str | None = None,
    semgrep_config: str | None = None,
    semgrep_bin: str | None = None,
) -> JavaScanner:
    configured_name = (scanner_name or os.getenv("AUTOPATCH_SCANNER", "semgrep")).strip().lower() or "semgrep"
    if configured_name == "semgrep":
        config = semgrep_config or os.getenv("AUTOPATCH_SEMGREP_CONFIG", "p/java")
        binary_path = semgrep_bin or os.getenv("AUTOPATCH_SEMGREP_BIN")
        return SemgrepScanner(config=config, binary_path=binary_path)
    return UnsupportedJavaScanner(configured_name)


def build_default_java_scanner() -> JavaScanner:
    return build_java_scanner()


__all__ = [
    "Finding",
    "JavaScanner",
    "ScanResult",
    "SemgrepScanner",
    "UnsupportedJavaScanner",
    "build_java_scanner",
    "build_default_java_scanner",
]
