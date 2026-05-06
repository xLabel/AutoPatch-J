from __future__ import annotations

from pathlib import Path

from autopatch_j.core.project.java_blocks import JavaBlockExtractor
from autopatch_j.core.project.repo_path import UnsafeRepoPathError, normalize_repo_path, resolve_repo_path
from autopatch_j.core.project.symbol_index import SymbolIndexEntry


class SourceReader:
    """
    源码读取和片段回源服务。

    职责边界：
    1. 根据索引项、物理行号或 finding 坐标从磁盘读取代码。
    2. 对大文件、目录和非 Java 文件做上下文防爆保护。
    3. 可用 Tree-sitter 时提取类/方法块；不负责扫描、补丁生成或语法校验。
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
                f"[系统防线] 这是一个目录：{entry.path}。为了防止上下文爆炸，已拦截代码全量注入。"
                "请直接对该目录发起检查，或先缩小到文件级范围。"
            )

        if not entry.path.endswith(".java"):
            content = self._read_file(full_path)
            lines = content.splitlines()
            if len(lines) > 200:
                return "\n".join(lines[:200]) + f"\n\n... [系统防线] 非 Java 文件，截断显示 200 行 (共 {len(lines)} 行) ..."
            return content

        try:
            size_kb = full_path.stat().st_size / 1024
            if size_kb > 100:
                return (
                    f"[系统防线] 警告：该文件体积过大 ({size_kb:.1f} KB)，为防止上下文爆炸，已拒绝全量代码注入。"
                    "请优先对该文件发起检查，或使用 search_symbols 查找特定特征。"
                    "严禁使用 read_source_code 读取全量内容。"
                )
        except OSError:
            pass

        content = self._read_file(full_path)
        lines = content.splitlines()
        if len(lines) > 3000:
            return (
                f"[系统防线] 警告：该文件内容过多 (约 {len(lines)} 行)，为防止上下文爆炸，已拒绝全量代码注入。"
                "请优先对该文件发起检查，或使用 search_symbols 查找特定特征。"
                "严禁使用 read_source_code 读取全量内容。"
            )

        if entry.kind == "file":
            return content

        if entry.kind in ("class", "method"):
            extractor = JavaBlockExtractor()
            source = extractor.extract(content, entry.line)
            self.last_extract_mode = extractor.last_mode
            self.last_extract_error = extractor.last_error
            return source

        return ""

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
