from __future__ import annotations

from collections.abc import Iterable

from autopatch_j.scanners.contracts import StaticScanner
from autopatch_j.scanners.models import ScannerName
from autopatch_j.scanners.planned import build_planned_scanners
from autopatch_j.scanners.semgrep import SemgrepScanner


DEFAULT_SCANNER_NAME = ScannerName.SEMGREP


class ScannerCatalog:
    """Registry for available scanner implementations and planned scanner placeholders."""

    def __init__(self, scanners: Iterable[StaticScanner]) -> None:
        self._scanners: dict[str, StaticScanner] = {scanner.name.value: scanner for scanner in scanners}

    @classmethod
    def default(cls) -> ScannerCatalog:
        return cls([SemgrepScanner(), *build_planned_scanners()])

    def all(self) -> tuple[StaticScanner, ...]:
        return tuple(self._scanners.values())

    def get(self, name: ScannerName | str) -> StaticScanner | None:
        scanner_name = name.value if isinstance(name, ScannerName) else str(name).lower()
        return self._scanners.get(scanner_name)


DEFAULT_SCANNER_CATALOG = ScannerCatalog.default()
