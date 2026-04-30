from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ScannerName(str, Enum):
    """AutoPatch-J 已知的 Java 扫描器标识。"""

    SEMGREP = "semgrep"
    SPOTBUGS = "spotbugs"
    PMD = "pmd"
    CHECKSTYLE = "checkstyle"


@dataclass(slots=True)
class Finding:
    """扫描器输出的单个 Java 安全或正确性问题。"""
    check_id: str
    path: str
    start_line: int
    end_line: int
    severity: str
    message: str
    rule: str = ""
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "severity": self.severity,
            "message": self.message,
            "rule": self.rule,
            "snippet": self.snippet
        }


@dataclass(slots=True)
class ScanResult:
    """一次扫描的标准结果，供 artifact 和 CLI 层持久化、展示。"""
    engine: str
    scope: list[str]
    targets: list[str]
    status: str
    message: str
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "scope": self.scope,
            "targets": self.targets,
            "status": self.status,
            "message": self.message,
            "findings": [f.to_dict() for f in self.findings]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanResult:
        findings_data = data.get("findings", [])
        findings = [Finding(**f) for f in findings_data]
        return cls(
            engine=data["engine"],
            scope=data["scope"],
            targets=data["targets"],
            status=data["status"],
            message=data["message"],
            findings=findings
        )


@dataclass(slots=True)
class ScannerMeta:
    """扫描器能力与就绪状态元数据，用于 `/scanner` 等展示入口。"""
    name: ScannerName
    is_implemented: bool
    status: str
    version: str = "N/A"
    description: str = ""


class JavaScanner(ABC):
    """Java 扫描器适配器契约，统一扫描执行和状态展示接口。"""
    name: ScannerName

    @abstractmethod
    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        """获取状态元数据"""

    @abstractmethod
    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        """执行扫描逻辑"""
