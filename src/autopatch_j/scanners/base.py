from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ScannerName(StrEnum):
    SEMGREP = "semgrep"
    SPOTBUGS = "spotbugs"
    PMD = "pmd"
    CHECKSTYLE = "checkstyle"


@dataclass(slots=True)
class Finding:
    """单个安全/正确性发现"""
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
    """扫描任务的全量结果"""
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
    """扫描器元数据，用于展示"""
    name: ScannerName
    is_implemented: bool
    status: str
    version: str = "N/A"
    description: str = ""


class JavaScanner(ABC):
    """扫描器抽象基类"""
    name: ScannerName

    @abstractmethod
    def get_meta(self, repo_root: Path | None = None) -> ScannerMeta:
        """获取状态元数据"""

    @abstractmethod
    def scan(self, repo_root: Path, scope: list[str]) -> ScanResult:
        """执行扫描逻辑"""
