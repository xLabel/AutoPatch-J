"""统一解码后的源码全文、行窗和语法块读取。"""

from __future__ import annotations

import dataclasses
import pathlib

from autopatch_j.core.project import java_blocks
from autopatch_j.core.project import repo_path as project_repo_path
from autopatch_j.core.project import source_text
from autopatch_j.core.project import symbols


@dataclasses.dataclass(frozen=True, slots=True)
class SourceRange:
    code: str
    start_line: int
    end_line: int
    total_lines: int


class SourceReader:
    """源码读取和片段回源服务。

    负责从磁盘读取文件、物理行范围和 Java 语法块；不负责扫描、补丁生成或语法校验。
    """

    def __init__(self, repo_root: pathlib.Path) -> None:
        self.repo_root = repo_root.resolve()
        self.last_extract_mode: str = "full"
        self.last_extract_error: str | None = None

    def fetch_entry_source(self, entry: symbols.SymbolIndexEntry) -> str:
        """读取条目对应源码；目录和大文件按范围防线返回提示。"""
        try:
            full_path = self._resolve_source_path(entry.path)
        except project_repo_path.UnsafeRepoPathError as exc:
            return f"错误：{exc}"
        if not full_path.exists():
            return f"错误：找不到文件或目录：{entry.path}"

        if entry.kind == "dir" or full_path.is_dir():
            return (
                f"[系统防线] 这是一个目录：{entry.path}。为防止上下"
                f"文爆炸，已拦截代码全量注入。"
                "请直接对该目录发起检查，或先缩小到文件级范围。"
            )

        if not entry.path.endswith(".java"):
            content = self._read_file(full_path)
            lines = content.splitlines()
            if len(lines) > 200:
                return (
                    "\n".join(lines[:200])
                    + "\n\n... [系统防线] 非 Java 文件，截断显示 200 行 "
                    + f"(共 {len(lines)} 行) ..."
                )
            return content

        guard_message = self._build_full_java_guard(full_path, entry.path)
        if guard_message is not None:
            return guard_message

        content = self._read_file(full_path)
        lines = content.splitlines()
        if len(lines) > 3000:
            return (
                f"[系统防线] 警告：该文件内容过多 (约 {len(lines)}"
                f" 行)，为防止上下文爆炸，已拒绝全量代码注入。"
                "请优先使用 search_symbols 查找特定特征，或使用 "
                "read_source_block/read_source_co"
                "ntext 缩小范围。"
                "严禁使用 read_source_file 读取全量内容。"
            )

        if entry.kind == "file":
            return content

        if entry.kind in ("class", "method"):
            return self._extract_java_block(content, entry.line)

        return ""

    def fetch_block_source(self, file_path: str, line: int) -> str:
        """读取所在 Java 结构，解析不可用时返回明确行窗降级。"""
        try:
            full_path = self._resolve_source_path(file_path)
        except project_repo_path.UnsafeRepoPathError as exc:
            return f"错误：{exc}"
        if not full_path.exists():
            return f"错误：找不到文件或目录：{file_path}"
        if full_path.is_dir():
            return (
                f"[系统防线] 这是一个目录：{file_path}。请先缩小到文件级范围。"
            )
        if not file_path.endswith(".java"):
            return self.fetch_context_source(file_path, line).code

        content = self._read_file(full_path)
        return self._extract_java_block(content, line)

    def fetch_lines(
        self, file_path: str, start_line: int, end_line: int
    ) -> str:
        """返回文件内指定物理行范围，越界路径不读取。"""
        try:
            full_path = self._resolve_source_path(file_path)
        except project_repo_path.UnsafeRepoPathError:
            return ""
        if not full_path.exists():
            return ""

        content = self._read_file(full_path)
        lines = content.splitlines()
        start_index = max(0, start_line - 1)
        end_index = min(len(lines), end_line)
        return "\n".join(lines[start_index:end_index])

    def fetch_context_source(
        self,
        file_path: str,
        line: int,
        before_lines: int = 20,
        after_lines: int = 80,
    ) -> SourceRange:
        """返回目标行前后窗口及实际起止行，不给源码插入行号。"""
        try:
            full_path = self._resolve_source_path(file_path)
        except project_repo_path.UnsafeRepoPathError:
            return SourceRange(
                code="", start_line=line, end_line=line, total_lines=0
            )
        if not full_path.exists() or full_path.is_dir():
            return SourceRange(
                code="", start_line=line, end_line=line, total_lines=0
            )

        content = self._read_file(full_path)
        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return SourceRange(code="", start_line=1, end_line=0, total_lines=0)

        target_line = min(max(1, line), total_lines)
        start_line = max(1, target_line - before_lines)
        end_line = min(total_lines, target_line + after_lines)
        return SourceRange(
            code="\n".join(lines[start_line - 1 : end_line]),
            start_line=start_line,
            end_line=end_line,
            total_lines=total_lines,
        )

    def fetch_resolved_snippet(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        fallback_snippet: str | None = None,
    ) -> str:
        """优先从当前源码回源，无法取回时使用允许的展示片段。"""
        normalized_path = project_repo_path.normalize_repo_path(file_path)
        try:
            self._resolve_source_path(normalized_path)
        except project_repo_path.UnsafeRepoPathError:
            return ""
        safe_start_line = max(1, start_line)
        safe_end_line = max(safe_start_line, end_line)
        snippet = self.fetch_lines(
            normalized_path, safe_start_line, safe_end_line
        ).strip()
        if snippet:
            return snippet
        return (fallback_snippet or "").strip()

    def _build_full_java_guard(
        self, full_path: pathlib.Path, repo_path: str
    ) -> str | None:
        """大型 Java 文件拒绝全文注入；无法取得体积时沿用读取层错误处理。"""
        del repo_path
        try:
            size_kb = full_path.stat().st_size / 1024
        except OSError:
            return None
        if size_kb <= 100:
            return None
        return (
            f"[系统防线] 警告：该文件体积过大 ({size_kb:.1f}"
            f" KB)，为防止上下文爆炸，已拒绝全量代码注入。"
            "请优先使用 search_symbols 查找特定特征，或使用 "
            "read_source_block/read_source_co"
            "ntext 缩小范围。"
            "严禁使用 read_source_file 读取全量内容。"
        )

    def _resolve_source_path(self, file_path: str) -> pathlib.Path:
        return project_repo_path.resolve_source_path(self.repo_root, file_path)

    def _extract_java_block(self, content: str, line: int) -> str:
        extractor = java_blocks.JavaBlockExtractor()
        source = extractor.extract(content, line)
        self.last_extract_mode = extractor.last_mode
        self.last_extract_error = extractor.last_error
        return source

    def _read_file(self, path: pathlib.Path) -> str:
        decoded = source_text.read(path)
        text = decoded.content.replace("\r\n", "\n")
        return (
            text
            if decoded.safe_to_write
            else "[只读降级：源码无法无损解码，禁止据此建立可写证据]\n" + text
        )
