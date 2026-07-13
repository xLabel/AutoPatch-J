from __future__ import annotations


class MemoryError(RuntimeError):
    """Memory 子系统的显式基础错误。"""


class MemoryStorageError(MemoryError):
    """SQLite 或本地文件操作失败。"""


class MemoryCorruptError(MemoryStorageError):
    """SQLite 文件无法被可靠读取。"""


class MemorySchemaError(MemoryStorageError):
    """SQLite schema 版本不受支持或结构不完整。"""


class MemoryContractError(MemoryError, ValueError):
    """Memory LLM 输出违反机器契约。"""


class MemoryLeaseError(MemoryError):
    """worker 的 lease 或 generation 已失效。"""


class MemoryNotFoundError(MemoryError, LookupError):
    """指定 Memory 不存在或当前不可读取。"""


class MemoryThreadConflictError(MemoryError):
    """active thread 已被其他 manager 切换。"""
