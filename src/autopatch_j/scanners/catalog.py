from __future__ import annotations

from pathlib import Path

from autopatch_j.scanners.base import ScannerCatalogEntry
from autopatch_j.scanners.checkstyle import CheckstyleScanner
from autopatch_j.scanners.pmd import PMDScanner
from autopatch_j.scanners.semgrep import SEMGREP_INSTALL_COMMAND, SemgrepScanner
from autopatch_j.scanners.spotbugs import SpotBugsScanner


def build_scanner_catalog(repo_root: Path | None) -> list[ScannerCatalogEntry]:
    return [
        build_semgrep_entry(repo_root),
        PMDScanner().catalog_entry(),
        SpotBugsScanner().catalog_entry(),
        CheckstyleScanner().catalog_entry(),
    ]


def build_semgrep_entry(repo_root: Path | None) -> ScannerCatalogEntry:
    scanner = SemgrepScanner()
    resolved = scanner.resolve_binary_with_source(repo_root)
    if resolved is None:
        return ScannerCatalogEntry(
            name=scanner.name,
            selected=True,
            status="selected, runtime missing",
            message=(
                "已默认选中；AutoPatch-J 管理的 Semgrep 环境缺失或不可执行。"
                f"可运行 {SEMGREP_INSTALL_COMMAND} 安装到 ~/.autopatch-j。"
            ),
        )

    semgrep_path, _source = resolved
    return ScannerCatalogEntry(
        name=scanner.name,
        selected=True,
        status="selected, ready",
        message=f"已默认选中；使用 {semgrep_path}",
    )
