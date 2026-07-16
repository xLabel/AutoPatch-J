from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autopatch_j.core.finding import FindingIdentity, SourceRegion


class ScannerName(str, Enum):
    """AutoPatch-J 已知的 Java 扫描器标识。"""

    SEMGREP = "semgrep"
    SPOTBUGS = "spotbugs"
    PMD = "pmd"
    CHECKSTYLE = "checkstyle"


@dataclass(slots=True)
class Finding:
    """扫描器输出的单个 Java 安全或正确性问题。"""

    fingerprint: str
    check_id: str
    path: str
    region: SourceRegion
    severity: str
    message: str
    rule: str = ""
    snippet: str = ""

    def __post_init__(self) -> None:
        self.identity

    @property
    def identity(self) -> FindingIdentity:
        return FindingIdentity(
            fingerprint=self.fingerprint,
            check_id=self.check_id,
            path=self.path,
            region=self.region,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "check_id": self.check_id,
            "path": self.path,
            "region": self.region.to_dict(),
            "severity": self.severity,
            "message": self.message,
            "rule": self.rule,
            "snippet": self.snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(
            fingerprint=_required_string(data, "fingerprint"),
            check_id=_required_string(data, "check_id"),
            path=_required_string(data, "path"),
            region=SourceRegion.from_dict(dict(data["region"])),
            severity=_required_string(data, "severity"),
            message=_required_string(data, "message"),
            rule=_optional_string(data, "rule"),
            snippet=_optional_string(data, "snippet"),
        )


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
        findings = [Finding.from_dict(dict(f)) for f in findings_data]
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
    availability: str = "unknown"
    reason: str = ""

    @property
    def is_ready(self) -> bool:
        return self.availability == "ready"


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} 必须是字符串。")
    return value


def _optional_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise TypeError(f"{key} 必须是字符串。")
    return value
