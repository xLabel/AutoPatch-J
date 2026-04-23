from __future__ import annotations

import os
import re
from pathlib import Path

from autopatch_j.core.index_service import IndexEntry, IndexService
from autopatch_j.core.models import CodeScope, CodeScopeKind


class ScopeService:
    """
    范围解析服务 (Core Service)
    职责：解析 @mention，展开目录，统一产出文件级范围。
    """

    def __init__(self, repo_root: Path, indexer: IndexService, ignored_dirs: set[str] | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.indexer = indexer
        self.ignored_dirs = ignored_dirs or set()

    def fetch_scope(self, user_text: str, default_to_project: bool = False) -> CodeScope | None:
        mentions = re.findall(r"@([^\s@]+)", user_text)
        if not mentions:
            if not default_to_project:
                return None
            project_files = self._fetch_project_java_files()
            return CodeScope(
                kind=CodeScopeKind.PROJECT,
                source_roots=["."],
                focus_files=project_files,
                is_locked=False,
            )

        source_roots: list[str] = []
        focus_files: list[str] = []
        saw_directory = False
        for mention in mentions:
            entry = self._fetch_best_entry(mention)
            if entry is None:
                continue
            normalized_path = self._normalize_repo_path(entry.path)
            if normalized_path not in source_roots:
                source_roots.append(normalized_path)
            if entry.kind == "dir":
                saw_directory = True
                for file_path in self._expand_directory_java_files(normalized_path):
                    if file_path not in focus_files:
                        focus_files.append(file_path)
            elif normalized_path.endswith(".java") and normalized_path not in focus_files:
                focus_files.append(normalized_path)

        if not focus_files:
            return None

        if len(focus_files) == 1 and not saw_directory:
            kind = CodeScopeKind.SINGLE_FILE
        else:
            kind = CodeScopeKind.MULTI_FILE

        return CodeScope(
            kind=kind,
            source_roots=source_roots,
            focus_files=focus_files,
            is_locked=True,
        )

    def _fetch_best_entry(self, mention: str) -> IndexEntry | None:
        normalized = self._normalize_repo_path(mention)
        candidate = (self.repo_root / normalized).resolve()
        if candidate.exists():
            rel_path = candidate.relative_to(self.repo_root).as_posix()
            if candidate.is_dir():
                return IndexEntry(path=rel_path, name=candidate.name, kind="dir")
            return IndexEntry(path=rel_path, name=candidate.name, kind="file")

        normalized_name = Path(normalized).name
        results = [
            entry
            for entry in self.indexer.search(normalized_name, limit=20)
            if entry.kind in {"file", "dir"}
        ]
        for entry in results:
            entry_path = self._normalize_repo_path(entry.path)
            if entry_path == normalized:
                return entry
        for entry in results:
            if entry.name == normalized_name:
                return entry
        return None

    def _expand_directory_java_files(self, rel_dir: str) -> list[str]:
        target_dir = (self.repo_root / rel_dir).resolve()
        if not target_dir.exists() or not target_dir.is_dir():
            return []
        java_files: list[str] = []
        for full_path in sorted(target_dir.rglob("*.java")):
            rel_path = full_path.relative_to(self.repo_root).as_posix()
            if rel_path not in java_files:
                java_files.append(rel_path)
        return java_files

    def _fetch_project_java_files(self) -> list[str]:
        java_files: list[str] = []
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in self.ignored_dirs]
            for file_name in sorted(files):
                if not file_name.endswith(".java"):
                    continue
                full_path = Path(root) / file_name
                java_files.append(full_path.relative_to(self.repo_root).as_posix())
        return java_files

    def _normalize_repo_path(self, path: str) -> str:
        clean = path.replace("\\", "/").strip()
        if clean.startswith("./"):
            clean = clean[2:]
        return clean
