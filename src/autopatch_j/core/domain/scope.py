from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class CodeScopeKind(str, Enum):
    """代码范围的粗粒度类型，用于展示和流程分支。"""

    SINGLE_FILE = "single_file"
    MULTI_FILE = "multi_file"
    PROJECT = "project"


@dataclass(slots=True)
class CodeScope:
    """
    一次用户请求解析出的代码范围。

    source_roots 保留用户选择的原始入口，focus_files 是展开后的文件级范围。
    is_locked 为 True 时，Agent 和 Tool 只能在 focus_files 内读取或修改代码。
    """

    kind: CodeScopeKind
    source_roots: list[str]
    focus_files: list[str]
    is_locked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source_roots": list(self.source_roots),
            "focus_files": list(self.focus_files),
            "is_locked": self.is_locked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeScope:
        return cls(
            kind=CodeScopeKind(str(data["kind"])),
            source_roots=[str(item) for item in data.get("source_roots", [])],
            focus_files=[str(item) for item in data.get("focus_files", [])],
            is_locked=bool(data.get("is_locked", False)),
        )
