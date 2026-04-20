from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch_j.scanners.semgrep import SemgrepScanner


@dataclass(slots=True)
class ScannerCatalogEntry:
    name: str
    selected: bool
    status: str
    message: str


COMING_SOON_SCANNERS = ("PMD", "SpotBugs", "Checkstyle")


def build_scanner_catalog(repo_root: Path | None) -> list[ScannerCatalogEntry]:
    entries = [build_semgrep_entry(repo_root)]
    entries.extend(
        ScannerCatalogEntry(
            name=name,
            selected=False,
            status="接入中，敬请期待",
            message="接入中，敬请期待",
        )
        for name in COMING_SOON_SCANNERS
    )
    return entries


def build_semgrep_entry(repo_root: Path | None) -> ScannerCatalogEntry:
    scanner = SemgrepScanner()
    resolved = scanner.resolve_binary_with_source(repo_root)
    if resolved is None:
        return ScannerCatalogEntry(
            name=scanner.name,
            selected=True,
            status="selected, runtime missing",
            message=(
                "已默认选中；runtime/semgrep/bin/<platform>/semgrep 缺失或不可执行。"
                "可运行 python3 scripts/install_semgrep_runtime.py --source /path/to/semgrep 安装。"
            ),
        )

    semgrep_path, _source = resolved
    return ScannerCatalogEntry(
        name=scanner.name,
        selected=True,
        status="selected, ready",
        message=f"已默认选中；使用 {semgrep_path}",
    )
