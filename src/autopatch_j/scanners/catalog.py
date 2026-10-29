"""仅登记实际可执行的扫描器并提供默认实现。"""

from __future__ import annotations

from collections.abc import Iterable

from autopatch_j.scanners import contracts
from autopatch_j.scanners import models
from autopatch_j.scanners.semgrep import scanner as semgrep_scanner

DEFAULT_SCANNER_NAME = models.ScannerName.SEMGREP


class ScannerCatalog:
    """Java 静态扫描器注册表。

    只登记可执行的扫描器；未来接入方向在公开项目介绍中维护。
    """

    def __init__(self, scanners: Iterable[contracts.StaticScanner]) -> None:
        self._scanners: dict[str, contracts.StaticScanner] = {
            scanner.name.value: scanner for scanner in scanners
        }

    @classmethod
    def default(cls) -> ScannerCatalog:
        """默认扫描器标识。"""
        return cls([semgrep_scanner.SemgrepScanner()])

    def all(self) -> tuple[contracts.StaticScanner, ...]:
        """返回当前注册的扫描器实现，不包含未来占位项。"""
        return tuple(self._scanners.values())

    def implemented(self) -> tuple[contracts.StaticScanner, ...]:
        """返回目录中真实实现的扫描器。"""
        return tuple(
            scanner
            for scanner in self._scanners.values()
            if scanner.get_meta().is_implemented
        )

    def get(
        self, name: models.ScannerName | str
    ) -> contracts.StaticScanner | None:
        """按标识返回扫描器，未注册时为 None。"""
        scanner_name = (
            name.value if isinstance(name, models.ScannerName) else name.lower()
        )
        return self._scanners.get(scanner_name)


DEFAULT_SCANNER_CATALOG = ScannerCatalog.default()
