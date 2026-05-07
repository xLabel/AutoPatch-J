from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from autopatch_j.scanners.models import ScannerMeta, ScannerName, ScanResult


class StaticScanner(ABC):
    """Static Java scanner contract used by audit and patch verification flows."""

    name: ScannerName

    @abstractmethod
    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        """Return scanner capability and readiness metadata."""

    @abstractmethod
    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        """Scan the given repository scope and return a normalized result."""
