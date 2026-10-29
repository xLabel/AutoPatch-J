"""代码范围的锁定状态、文件集合及持久表示。"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import enum

import pydantic

from autopatch_j.core import json_data


class CodeScopeKind(str, enum.Enum):
    """代码范围的粗粒度类型，用于展示和流程分支。"""

    SINGLE_FILE = "single_file"
    MULTI_FILE = "multi_file"
    PROJECT = "project"


@dataclasses.dataclass(slots=True)
class CodeScope:
    """一次用户请求解析出的代码范围。

    source_roots 保留用户选择的原始入口，focus_files 是展开后的文件级范围。
    is_locked 为 True 时，Agent 和 Tool 只能在 focus_files 内读取或修改代码。
    """

    kind: CodeScopeKind
    source_roots: list[str]
    focus_files: list[str]
    is_locked: bool

    def to_dict(self) -> dict[str, pydantic.JsonValue]:
        """返回范围的 JSON 表示，复制文件列表以隔离后续修改。"""
        return {
            "kind": self.kind.value,
            "source_roots": list(self.source_roots),
            "focus_files": list(self.focus_files),
            "is_locked": self.is_locked,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CodeScope:
        """恢复范围锁定及路径列表，拒绝将非数组输入当作文件集合。"""
        return cls(
            kind=CodeScopeKind(str(data["kind"])),
            source_roots=[
                str(item)
                for item in json_data.array_items(data.get("source_roots", []))
            ],
            focus_files=[
                str(item)
                for item in json_data.array_items(data.get("focus_files", []))
            ],
            is_locked=bool(data.get("is_locked", False)),
        )
