"""源码索引条目的值类型，不依赖提取器或持久化服务。"""

import dataclasses


@dataclasses.dataclass(slots=True)
class SymbolIndexEntry:
    """本地代码索引的一条记录。

    kind 表示目录、文件、类或方法；line 用于 SourceReader 定位符号块。
    """

    path: str
    name: str
    kind: str
    line: int = 0
    column: int = 0
    end_line: int = 0
    end_column: int = 0
    owner: str = ""
    signature: str = ""
    mtime: float = 0.0
    source_sha256: str = ""
    source_has_errors: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class SymbolSearchResult:
    """稳定索引页只是定位提示，旧快照不能证明当前源码事实。"""

    entries: list[SymbolIndexEntry]
    total: int
    next_offset: int | None
    stale_paths: tuple[str, ...] = ()
    notice: str = "索引仅用于定位，请读取当前源码；空结果不证明没有引用。"
