from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch_j.scanners.contracts import StaticScanner
from autopatch_j.scanners.models import ScannerMeta, ScannerName, ScanResult


@dataclass(slots=True)
class PlannedScanner(StaticScanner):
    """Placeholder for scanner integrations that are visible in the UI but not executable yet."""

    name: ScannerName
    description: str

    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        return ScannerMeta(
            name=self.name,
            is_implemented=False,
            status="规划中 (Coming Soon)",
            description=self.description,
        )

    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        return ScanResult(self.name.value, scope, [], "error", f"{self.name.value} 尚未实现适配。")


def build_planned_scanners() -> list[PlannedScanner]:
    return [
        PlannedScanner(
            name=ScannerName.SPOTBUGS,
            description="基于字节码分析的 Java 潜在 Bug 探测工具。",
        ),
        PlannedScanner(
            name=ScannerName.PMD,
            description="基于静态规则的 Java 代码质量分析工具。",
        ),
        PlannedScanner(
            name=ScannerName.CHECKSTYLE,
            description="Java 代码风格与规范强制检查工具。",
        ),
    ]
