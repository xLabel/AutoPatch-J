from __future__ import annotations

from pathlib import Path

from autopatch_j.core.code_fetcher import CodeFetcher


class FindingSnippetService:
    """
    发现证据纠偏服务 (Core Service)
    职责：根据 finding 的文件路径与行号回源文件提取稳定代码片段。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.fetcher = CodeFetcher(self.repo_root)

    def fetch_resolved_snippet(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        fallback_snippet: str | None = None,
    ) -> str:
        normalized_path = self._normalize_path(file_path)
        safe_start_line = max(1, start_line)
        safe_end_line = max(safe_start_line, end_line)
        snippet = self.fetcher.fetch_lines(normalized_path, safe_start_line, safe_end_line).strip()
        if snippet:
            return snippet
        return (fallback_snippet or "").strip()

    def _normalize_path(self, file_path: str) -> str:
        return file_path.replace("\\", "/").strip()
