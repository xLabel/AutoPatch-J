"""扫描结果、finding 和可用性元信息的边界模型。"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import enum

import pydantic

from autopatch_j.core import finding
from autopatch_j.core import json_data


class ScannerName(str, enum.Enum):
    """AutoPatch-J 已知的 Java 扫描器标识。"""

    SEMGREP = "semgrep"


@dataclasses.dataclass(slots=True)
class Finding:
    """扫描器输出的单个 Java 安全或正确性问题。"""

    fingerprint: str
    check_id: str
    path: str
    region: finding.SourceRegion
    severity: str
    message: str
    rule: str = ""
    snippet: str = ""

    def __post_init__(self) -> None:
        finding.FindingIdentity(
            fingerprint=self.fingerprint,
            check_id=self.check_id,
            path=self.path,
            region=self.region,
        )

    @property
    def identity(self) -> finding.FindingIdentity:
        """当前 finding 的不可变证据身份。"""
        return finding.FindingIdentity(
            fingerprint=self.fingerprint,
            check_id=self.check_id,
            path=self.path,
            region=self.region,
        )

    def to_dict(self) -> dict[str, pydantic.JsonValue]:
        """返回供扫描快照持久化的 JSON 字段。"""
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
    def from_dict(cls, data: Mapping[str, object]) -> Finding:
        """恢复标准扫描结果并校验字段和 finding 证据。"""
        return cls(
            fingerprint=_required_string(data, "fingerprint"),
            check_id=_required_string(data, "check_id"),
            path=_required_string(data, "path"),
            region=finding.SourceRegion.from_dict(
                json_data.object_fields(data["region"])
            ),
            severity=_required_string(data, "severity"),
            message=_required_string(data, "message"),
            rule=_optional_string(data, "rule"),
            snippet=_optional_string(data, "snippet"),
        )


@dataclasses.dataclass(slots=True)
class ScanResult:
    """一次扫描的标准结果，供 artifact 和 CLI 层持久化、展示。"""

    engine: str
    scope: list[str]
    targets: list[str]
    status: str
    message: str
    findings: list[Finding] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, pydantic.JsonValue]:
        """返回供扫描快照持久化的 JSON 字段。"""
        return {
            "engine": self.engine,
            "scope": list(self.scope),
            "targets": list(self.targets),
            "status": self.status,
            "message": self.message,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ScanResult:
        """恢复标准扫描结果并校验字段和 finding 证据。"""
        findings_data = json_data.array_items(data.get("findings", []))
        findings = [
            Finding.from_dict(json_data.object_fields(f)) for f in findings_data
        ]
        return cls(
            engine=_required_string(data, "engine"),
            scope=[str(item) for item in json_data.array_items(data["scope"])],
            targets=[
                str(item) for item in json_data.array_items(data["targets"])
            ],
            status=_required_string(data, "status"),
            message=_required_string(data, "message"),
            findings=findings,
        )


@dataclasses.dataclass(slots=True)
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
        """扫描器是否实际就绪，而非仅有注册信息。"""
        return self.availability == "ready"


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} 必须是字符串。")
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise TypeError(f"{key} 必须是字符串。")
    return value
