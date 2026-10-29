"""官方 SQLite 笔记的项目管理入口；不维护后台提取管线。"""

import json
import pathlib
import sqlite3
import uuid

from pydantic_ai_harness import memory

STORAGE_ERRORS = (sqlite3.DatabaseError, OSError)
PREFIX = "project/main/"


class ProjectMemory:
    """项目独立的笔记存储与用户显式管理操作。"""

    def __init__(self, repo_root: pathlib.Path) -> None:
        self.state_dir = repo_root / ".autopatch-j"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.store = memory.SqliteMemoryStore(
            database=self.state_dir / "memory.sqlite3"
        )

    async def paths(self) -> list[str]:
        """返回本项目所有笔记路径；列表不静默截断。"""
        limit = 128
        while True:
            paths = await self.store.list_paths(PREFIX, limit=limit)
            if len(paths) < limit:
                return [path.removeprefix(PREFIX) for path in paths]
            limit *= 2

    def _path(self, path: str) -> str:
        """将合法相对笔记路径限制在固定 namespace，拒绝越界和反斜杠。"""
        parts = pathlib.PurePosixPath(path)
        if (
            parts.is_absolute()
            or ".." in parts.parts
            or not path
            or parts.as_posix() == "."
            or "\\" in path
        ):
            raise ValueError("笔记必须使用 notebook 内的相对路径")
        return PREFIX + parts.as_posix()

    async def show(self, path: str = "MEMORY.md") -> memory.MemoryFile | None:
        """读取单个笔记；大小边界与官方能力保持一致。"""
        return await self.store.read(self._path(path), max_chars=65_536)

    async def forget(self, path: str) -> None:
        """显式删除文件；主笔记通过修订或 clear 处理。"""
        if self._path(path) == PREFIX + "MEMORY.md":
            raise ValueError(
                "MEMORY.md 受保护，请明确要求 Agent 修订内容，或使用 "
                "/memory clear --confirm"
            )
        item = await self.show(path)
        if item is None:
            raise LookupError(path)
        await self.store.delete(self._path(path), expected_version=item.version)

    async def clear(self) -> None:
        """删除当前笔记，不删除普通会话；冲突时不覆盖并发修改。"""
        for path in await self.paths():
            item = await self.show(path)
            if item is not None:
                await self.store.delete(
                    self._path(path), expected_version=item.version
                )

    async def export(self) -> pathlib.Path:
        """导出当前笔记，独占创建文件，不覆盖既有导出。"""
        content = {}
        for path in await self.paths():
            item = await self.show(path)
            if item is not None:
                content[path] = item.content
        destination = self.state_dir / f"memory-export-{uuid.uuid4().hex}.json"
        with destination.open("x", encoding="utf-8") as output:
            json.dump(content, output, ensure_ascii=False, indent=2)
        return destination
