from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch_j.core.project.java_blocks import JavaBlockExtractor
from autopatch_j.core.project.repo_path import UnsafeRepoPathError, normalize_repo_path, resolve_repo_path
from autopatch_j.core.project.symbol_index import SymbolIndexEntry


@dataclass(frozen=True, slots=True)
class SourceRange:
    code: str
    start_line: int
    end_line: int
    total_lines: int


class SourceReader:
    """
    源码读取和片段回源服务。

    负责从磁盘读取文件、物理行范围和 Java 语法块；不负责扫描、补丁生成或语法校验。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.last_extract_mode: str = "full"
        self.last_extract_error: str | None = None

    def fetch_entry_source(self, entry: SymbolIndexEntry) -> str:
        try:
            full_path = resolve_repo_path(self.repo_root, entry.path)
        except UnsafeRepoPathError as exc:
            return f"错误：{exc}"
        if not full_path.exists():
            return f"错误：找不到文件或目录：{entry.path}"

        if entry.kind == "dir" or full_path.is_dir():
            return (
                f"[系统防线] 这是一个目录：{entry.path}。为防止上下文爆炸，已拦截代码全量注入。"
                "请直接对该目录发起检查，或先缩小到文件级范围。"
            )

        if not entry.path.endswith(".java"):
            content = self._read_file(full_path)
            lines = content.splitlines()
            if len(lines) > 200:
                return "\n".join(lines[:200]) + f"\n\n... [系统防线] 非 Java 文件，截断显示 200 行 (共 {len(lines)} 行) ..."
            return content

        guard_message = self._build_full_java_guard(full_path, entry.path)
        if guard_message is not None:
            return guard_message

        content = self._read_file(full_path)
        lines = content.splitlines()
        if len(lines) > 3000:
            return (
                f"[系统防线] 警告：该文件内容过多 (约 {len(lines)} 行)，为防止上下文爆炸，已拒绝全量代码注入。"
                "请优先使用 search_symbols 查找特定特征，或使用 read_source_block/read_source_context 缩小范围。"
                "严禁使用 read_source_file 读取全量内容。"
            )

        if entry.kind == "file":
            return content

        if entry.kind in ("class", "method"):
            return self._extract_java_block(content, entry.line)

        return ""

    def fetch_block_source(self, file_path: str, line: int) -> str:
        try:
            full_path = resolve_repo_path(self.repo_root, file_path)
        except UnsafeRepoPathError as exc:
            return f"错误：{exc}"
        if not full_path.exists():
            return f"错误：找不到文件或目录：{file_path}"
        if full_path.is_dir():
            return f"[系统防线] 这是一个目录：{file_path}。请先缩小到文件级范围。"
        if not file_path.endswith(".java"):
            return self.fetch_context_source(file_path, line).code

        content = self._read_file(full_path)
        return self._extract_java_block(content, line)

    def fetch_lines(self, file_path: str, start_line: int, end_line: int) -> str:
        try:
            full_path = resolve_repo_path(self.repo_root, file_path)
        except UnsafeRepoPathError:
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
        try:
            full_path = resolve_repo_path(self.repo_root, file_path)
        except UnsafeRepoPathError:
            return SourceRange(code="", start_line=line, end_line=line, total_lines=0)
        if not full_path.exists() or full_path.is_dir():
            return SourceRange(code="", start_line=line, end_line=line, total_lines=0)

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
        normalized_path = normalize_repo_path(file_path)
        safe_start_line = max(1, start_line)
        safe_end_line = max(safe_start_line, end_line)
        snippet = self.fetch_lines(normalized_path, safe_start_line, safe_end_line).strip()
        if snippet:
            return snippet
        return (fallback_snippet or "").strip()

    def _build_full_java_guard(self, full_path: Path, repo_path: str) -> str | None:
        try:
            size_kb = full_path.stat().st_size / 1024
        except OSError:
            return None
        if size_kb <= 100:
            return None
        return (
            f"[系统防线] 警告：该文件体积过大 ({size_kb:.1f} KB)，为防止上下文爆炸，已拒绝全量代码注入。"
            "请优先使用 search_symbols 查找特定特征，或使用 read_source_block/read_source_context 缩小范围。"
            "严禁使用 read_source_file 读取全量内容。"
        )

    def _extract_java_block(self, content: str, line: int) -> str:
        extractor = JavaBlockExtractor()
        source = extractor.extract(content, line)
        self.last_extract_mode = extractor.last_mode
        self.last_extract_error = extractor.last_error
        return source

    def _read_file(self, path: Path) -> str:
        raw_bytes = path.read_bytes()
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw_bytes.decode("gbk")
            except UnicodeDecodeError:
                content = raw_bytes.decode("utf-8", errors="replace")
        return content.replace("\r\n", "\n")
