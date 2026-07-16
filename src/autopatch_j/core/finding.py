from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceRegion:
    """源码中的半开区间；行列从 1 开始，字节偏移从 0 开始。"""

    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if min(self.start_line, self.start_column, self.end_line, self.end_column) < 1:
            raise ValueError("source region 的行列必须是正整数。")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("source region 的字节偏移无效。")
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("source region 的结束位置不能早于开始位置。")

    @property
    def inclusive_end_line(self) -> int:
        if self.end_line > self.start_line and self.end_column == 1:
            return self.end_line - 1
        return self.end_line

    def intersects(self, other: SourceRegion) -> bool:
        if self.start_offset == self.end_offset:
            return other.start_offset <= self.start_offset < other.end_offset
        if other.start_offset == other.end_offset:
            return self.start_offset <= other.start_offset < self.end_offset
        return self.start_offset < other.end_offset and other.start_offset < self.end_offset

    def to_dict(self) -> dict[str, int]:
        return {
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRegion:
        return cls(
            start_line=_required_int(data, "start_line"),
            start_column=_required_int(data, "start_column"),
            end_line=_required_int(data, "end_line"),
            end_column=_required_int(data, "end_column"),
            start_offset=_required_int(data, "start_offset"),
            end_offset=_required_int(data, "end_offset"),
        )


@dataclass(frozen=True, slots=True)
class FindingIdentity:
    """可持久化的 finding 身份。"""

    fingerprint: str
    check_id: str
    path: str
    region: SourceRegion

    def __post_init__(self) -> None:
        if re.fullmatch(r"apj-v1:[0-9a-f]{64}:[1-9]\d*", self.fingerprint) is None:
            raise ValueError("finding fingerprint 格式无效。")
        if not self.check_id.strip():
            raise ValueError("finding check_id 不能为空。")
        path = self.path.strip()
        parts = PurePosixPath(path).parts
        if (
            not path
            or path != self.path
            or self.check_id != self.check_id.strip()
            or "\\" in path
            or "\x00" in path
            or PurePosixPath(path).is_absolute()
            or PurePosixPath(path).as_posix() != path
            or path == "."
            or ".." in parts
        ):
            raise ValueError("finding path 必须是安全的 repo-relative POSIX 文件路径。")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "check_id": self.check_id,
            "path": self.path,
            "region": self.region.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FindingIdentity:
        return cls(
            fingerprint=_required_string(data, "fingerprint"),
            check_id=_required_string(data, "check_id"),
            path=_required_string(data, "path"),
            region=SourceRegion.from_dict(dict(data["region"])),
        )


def _required_int(data: dict[str, Any], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} 必须是整数。")
    return value


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} 必须是字符串。")
    return value
