"""扫描证据身份及精确源码半开区域的校验与序列化。"""

from __future__ import annotations

import pathlib
import re

import pydantic
from pydantic import dataclasses


@dataclasses.dataclass(
    config=pydantic.ConfigDict(extra="forbid"), frozen=True, slots=True
)
class SourceRegion:
    """源码中的半开区间；行列从 1 开始，字节偏移从 0 开始。"""

    start_line: pydantic.StrictInt
    start_column: pydantic.StrictInt
    end_line: pydantic.StrictInt
    end_column: pydantic.StrictInt
    start_offset: pydantic.StrictInt
    end_offset: pydantic.StrictInt

    def __post_init__(self) -> None:
        if (
            min(
                self.start_line,
                self.start_column,
                self.end_line,
                self.end_column,
            )
            < 1
        ):
            raise ValueError("source region 的行列必须是正整数。")
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("source region 的字节偏移无效。")
        if (self.end_line, self.end_column) < (
            self.start_line,
            self.start_column,
        ):
            raise ValueError("source region 的结束位置不能早于开始位置。")

    @property
    def inclusive_end_line(self) -> int:
        """适合源码展示的含尾行号，半开终点位于行首时减一。"""
        if self.end_line > self.start_line and self.end_column == 1:
            return self.end_line - 1
        return self.end_line

    def intersects(self, other: SourceRegion) -> bool:
        """按字节半开区间及零长度点语义判断区域相交。"""
        if self.start_offset == self.end_offset:
            return other.start_offset <= self.start_offset < other.end_offset
        if other.start_offset == other.end_offset:
            return self.start_offset <= other.start_offset < self.end_offset
        return (
            self.start_offset < other.end_offset
            and other.start_offset < self.end_offset
        )


@dataclasses.dataclass(
    config=pydantic.ConfigDict(extra="forbid"), frozen=True, slots=True
)
class FindingIdentity:
    """可持久化的 finding 身份。"""

    fingerprint: pydantic.StrictStr
    check_id: pydantic.StrictStr
    path: pydantic.StrictStr
    region: SourceRegion

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"apj-v1:[0-9a-f]{64}:[1-9]\d*", self.fingerprint)
            is None
        ):
            raise ValueError("finding fingerprint 格式无效。")
        if not self.check_id.strip():
            raise ValueError("finding check_id 不能为空。")
        path = self.path.strip()
        parts = pathlib.PurePosixPath(path).parts
        if (
            not path
            or path != self.path
            or self.check_id != self.check_id.strip()
            or "\\" in path
            or "\x00" in path
            or pathlib.PurePosixPath(path).is_absolute()
            or pathlib.PurePosixPath(path).as_posix() != path
            or path == "."
            or ".." in parts
        ):
            raise ValueError(
                "finding path 必须是安全的 repo-relative POSIX 文件路径。"
            )
