"""官方 SQLite 笔记的项目管理入口；不维护后台提取管线。"""

import json
import pathlib
import uuid

from pydantic_ai_harness import memory

from autopatch_j import error_messages

PREFIX = "project/main/"


class MemoryInputError(ValueError):
    """用户可以调整的笔记路径或管理请求。"""


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
            raise MemoryInputError(error_messages.INVALID_MEMORY_PATH)
        return PREFIX + parts.as_posix()

    async def show(self, path: str = "MEMORY.md") -> memory.MemoryFile | None:
        """读取单个笔记；大小边界与官方能力保持一致。"""
        return await self.store.read(self._path(path), max_chars=65_536)

    async def forget(self, path: str) -> None:
        """显式删除文件；主笔记通过修订或 clear 处理。"""
        if self._path(path) == PREFIX + "MEMORY.md":
            raise MemoryInputError(error_messages.PROTECTED_MEMORY)
        item = await self.show(path)
        if item is None:
            raise MemoryInputError(error_messages.missing_note(path))
        await self.store.delete(self._path(path), expected_version=item.version)

    async def clear(self) -> None:
        """删除当前笔记，不删除项目会话；冲突时不覆盖并发修改。"""
        try:
            for path in await self.paths():
                item = await self.show(path)
                if item is not None:
                    await self.store.delete(
                        self._path(path), expected_version=item.version
                    )
        except Exception as exc:
            exc.add_note(error_messages.clear_interrupted("操作未完成"))
            raise

    async def export(self) -> pathlib.Path:
        """导出当前笔记，独占创建文件，不覆盖既有导出。"""
        content = {}
        for path in await self.paths():
            item = await self.show(path)
            if item is not None:
                content[path] = item.content
        destination = self.state_dir / f"memory-export-{uuid.uuid4().hex}.json"
        created = False
        try:
            with destination.open("x", encoding="utf-8") as output:
                created = True
                json.dump(content, output, ensure_ascii=False, indent=2)
        except Exception as exc:
            if created:
                exc.add_note(
                    error_messages.incomplete_export(
                        destination.relative_to(
                            self.state_dir.parent
                        ).as_posix()
                    )
                )
            raise
        return destination
