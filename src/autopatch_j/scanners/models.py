"""扫描结果、finding 和可用性元信息的边界模型。"""

from __future__ import annotations

import dataclasses
import enum
from typing import Annotated, Literal

import pydantic
from pydantic import dataclasses as pydantic_dataclasses

from autopatch_j.core import finding


class ScannerName(str, enum.Enum):
    """AutoPatch-J 已知的 Java 扫描器标识。"""

    SEMGREP = "semgrep"


@pydantic_dataclasses.dataclass(
    config=pydantic.ConfigDict(extra="forbid"), slots=True
)
class Finding:
    """扫描器输出的单个 Java 安全或正确性问题。"""

    fingerprint: pydantic.StrictStr
    check_id: pydantic.StrictStr
    path: pydantic.StrictStr
    region: finding.SourceRegion
    severity: pydantic.StrictStr
    message: pydantic.StrictStr
    rule: pydantic.StrictStr = ""
    snippet: pydantic.StrictStr = ""

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


@pydantic_dataclasses.dataclass(
    config=pydantic.ConfigDict(extra="forbid"), slots=True
)
class ScanResult:
    """一次扫描的标准结果，供 artifact 和 CLI 层持久化、展示。"""

    engine: pydantic.StrictStr
    scope: Annotated[list[pydantic.StrictStr], pydantic.Field(strict=True)]
    targets: Annotated[list[pydantic.StrictStr], pydantic.Field(strict=True)]
    status: Literal["ok", "error", "skipped"]
    message: pydantic.StrictStr
    findings: Annotated[list[Finding], pydantic.Field(strict=True)] = (
        dataclasses.field(default_factory=list)
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
