"""带范围与解码证据的只读源码访问；错误通过异常表达，不伪装成源码。"""

import dataclasses
import pathlib
from typing import Literal

from autopatch_j.core.project import java_blocks
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_text


class SourceReadError(ValueError):
    """有效路径上的读取范围无效，可以调整输入后重试。"""


@dataclasses.dataclass(frozen=True, slots=True)
class SourceRead:
    """实际源码及行范围、截断、编码和无损解码事实。"""

    path: str
    code: str
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool
    encoding: str
    lossless: bool
    mode: str = "lines"
    notice: str | None = None


class SourceReader:
    """按实际文件返回证据，读取范围与写权限无关。"""

    def __init__(self, repo_root: pathlib.Path) -> None:
        self.repo_root = repo_root.resolve()

    def read(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        *,
        mode: Literal["lines", "block", "file"] = "lines",
    ) -> SourceRead:
        """按 300 行及 30000 字符预算读取；单个物理行完整保留，可继续请求后续行。"""
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise SourceReadError("行号必须从 1 开始，结束行不能早于开始行。")
        full_path = repo_path.resolve_source_path(self.repo_root, path)
        decoded = source_text.read(full_path)
        content = decoded.content
        lines = content.splitlines(keepends=True)
        total = len(lines)
        if total and start_line > total:
            raise SourceReadError(f"行号超出文件范围，共 {total} 行。")
        notice = None
        if mode == "block" and full_path.suffix.lower() == ".java":
            block = java_blocks.JavaBlockExtractor().extract(
                content, start_line
            )
            start_line, end_line = block.start_line, block.end_line
            mode_label, notice = block.mode, block.error
        else:
            mode_label = mode
            if mode == "file":
                start_line, end_line = 1, total
        requested_end = min(
            end_line if end_line is not None else start_line + 99, total
        )
        actual_end = min(requested_end, start_line + 299)
        selected = lines[start_line - 1 : actual_end]
        while len(selected) > 1 and sum(map(len, selected)) > 30_000:
            selected.pop()
        code = "".join(selected)
        actual_end = start_line + len(selected) - 1
        return SourceRead(
            path=full_path.relative_to(self.repo_root).as_posix(),
            code=code,
            start_line=start_line,
            end_line=actual_end,
            total_lines=total,
            truncated=actual_end < requested_end,
            encoding=decoded.encoding,
            lossless=decoded.safe_to_write,
            mode=mode_label,
            notice=notice,
        )

    def guidance(self, path: str = ".") -> list[SourceRead]:
        """只发现根目录和目标祖先的 AGENTS.md，其余规范按明确引用读取。"""
        target = repo_path.resolve_source_path(self.repo_root, path)
        directory = target if target.is_dir() else target.parent
        ancestors = [
            self.repo_root,
            *reversed(
                [
                    item
                    for item in directory.parents
                    if item != self.repo_root
                    and item.is_relative_to(self.repo_root)
                ]
            ),
            directory,
        ]
        result = []
        for parent in dict.fromkeys(ancestors):
            guide = parent / "AGENTS.md"
            if guide.is_file():
                result.append(
                    self.read(
                        guide.relative_to(self.repo_root).as_posix(),
                        mode="file",
                    )
                )
        return result
