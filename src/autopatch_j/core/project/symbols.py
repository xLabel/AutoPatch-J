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
    container: str = ""
    mtime: float = 0.0
