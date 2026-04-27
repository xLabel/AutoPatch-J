from __future__ import annotations

from autopatch_j.scanners.base import (
    ScannerName, 
    ScanResult, 
    Finding, 
    JavaScanner
)
from autopatch_j.scanners.semgrep import SemgrepScanner
from autopatch_j.scanners.spotbugs import SpotBugsScanner
from autopatch_j.scanners.pmd import PMDScanner
from autopatch_j.scanners.checkstyle import CheckstyleScanner

# 默认全局配置
DEFAULT_SCANNER_NAME = ScannerName.SEMGREP

# 全量注册
ALL_SCANNERS: list[JavaScanner] = [
    SemgrepScanner(),
    SpotBugsScanner(),
    PMDScanner(),
    CheckstyleScanner()
]


def get_scanner(name: ScannerName | str) -> JavaScanner | None:
    """根据名称获取扫描器实例"""
    target = name.value if isinstance(name, ScannerName) else str(name).lower()
    for s in ALL_SCANNERS:
        if s.name == target:
            return s
    return None
