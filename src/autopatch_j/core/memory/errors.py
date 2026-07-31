from __future__ import annotations

from dataclasses import dataclass


class MemoryError(RuntimeError):
    """Memory 子系统的显式基础错误。"""


class MemoryStorageError(MemoryError):
    """SQLite 或本地文件操作失败。"""


class MemoryCorruptError(MemoryStorageError):
    """SQLite 文件无法被可靠读取。"""


class MemorySchemaError(MemoryStorageError):
    """SQLite schema 版本不受支持或结构不完整。"""


class MemoryContractError(MemoryError, ValueError):
    """Memory 运行时机器契约被违反。"""


@dataclass(frozen=True, slots=True)
class MemoryContractViolation:
    path: str
    rule: str
    observed_type: str = ""
    observed_length: int | None = None
    recognized_keys: tuple[str, ...] = ()
    missing_keys: tuple[str, ...] = ()
    unknown_key_count: int = 0


class MemoryOutputContractError(MemoryContractError):
    """Memory 模型输出违反可 repair 的结构或语义契约。"""

    def __init__(self, violation: MemoryContractViolation) -> None:
        self.violation = violation
        super().__init__(f"{violation.path}: {violation.rule}")


class MemoryLeaseError(MemoryError):
    """worker 的 lease 或 generation 已失效。"""


class MemoryNotFoundError(MemoryError, LookupError):
    """指定 Memory 不存在或当前不可读取。"""


class MemoryThreadConflictError(MemoryError):
    """active thread 已被其他 manager 切换。"""
