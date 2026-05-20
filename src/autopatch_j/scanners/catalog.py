from __future__ import annotations

from collections.abc import Iterable

from autopatch_j.scanners.contracts import StaticScanner
from autopatch_j.scanners.models import ScannerName
from autopatch_j.scanners.planned import build_planned_scanners
from autopatch_j.scanners.semgrep import SemgrepScanner


DEFAULT_SCANNER_NAME = ScannerName.SEMGREP


class ScannerCatalog:
    """
    Java 静态扫描器注册表。

    catalog 同时收纳已实现扫描器和计划中的占位扫描器，让 CLI 可以稳定展示
    插件边界；真正执行扫描时仍由调用方选择明确的 scanner name。
    """

    def __init__(self, scanners: Iterable[StaticScanner]) -> None:
        self._scanners: dict[str, StaticScanner] = {scanner.name.value: scanner for scanner in scanners}

    @classmethod
    def default(cls) -> ScannerCatalog:
        return cls([SemgrepScanner(), *build_planned_scanners()])

    def all(self) -> tuple[StaticScanner, ...]:
        return tuple(self._scanners.values())

    def implemented(self) -> tuple[StaticScanner, ...]:
        return tuple(scanner for scanner in self._scanners.values() if scanner.get_meta().is_implemented)

    def planned(self) -> tuple[StaticScanner, ...]:
        return tuple(scanner for scanner in self._scanners.values() if not scanner.get_meta().is_implemented)

    def get(self, name: ScannerName | str) -> StaticScanner | None:
        scanner_name = name.value if isinstance(name, ScannerName) else str(name).lower()
        return self._scanners.get(scanner_name)


DEFAULT_SCANNER_CATALOG = ScannerCatalog.default()
