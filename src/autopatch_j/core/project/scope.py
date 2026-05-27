from __future__ import annotations

import os
import re
from pathlib import Path

from autopatch_j.core.domain.scope import CodeScope, CodeScopeKind
from autopatch_j.core.project.repo_path import (
    UnsafeRepoPathError,
    normalize_repo_path,
    resolve_repo_path,
    to_repo_relative_path,
)
from autopatch_j.core.project.symbol_index import SymbolIndex, SymbolIndexEntry


class ScopeResolver:
    """
    用户 @mention 到 CodeScope 的解析服务。

    职责边界：
    1. 结合 SymbolIndex 将文件、目录、类或方法 mention 解析为文件级 focus_files。
    2. 决定当前任务是否锁定焦点范围，作为 Agent/Tool 的路径约束来源。
    3. 不判断用户意图，也不触发扫描；它只产出可执行的代码范围。
    """

    def __init__(self, repo_root: Path, symbol_index: SymbolIndex, ignored_dirs: set[str] | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.symbol_index = symbol_index
        self.ignored_dirs = ignored_dirs or set()

    def resolve(self, user_text: str, default_to_project: bool = False) -> CodeScope | None:
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
            normalized_path = normalize_repo_path(entry.path)
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

        kind = CodeScopeKind.SINGLE_FILE if len(focus_files) == 1 and not saw_directory else CodeScopeKind.MULTI_FILE
        return CodeScope(
            kind=kind,
            source_roots=source_roots,
            focus_files=focus_files,
            is_locked=True,
        )

    def _fetch_best_entry(self, mention: str) -> SymbolIndexEntry | None:
        normalized = normalize_repo_path(mention)
        try:
            candidate = resolve_repo_path(self.repo_root, normalized)
        except UnsafeRepoPathError:
            return None
        if candidate.exists():
            rel_path = to_repo_relative_path(self.repo_root, candidate)
            if candidate.is_dir():
                return SymbolIndexEntry(path=rel_path, name=candidate.name, kind="dir")
            return SymbolIndexEntry(path=rel_path, name=candidate.name, kind="file")

        normalized_name = Path(normalized).name
        results = [
            entry
            for entry in self.symbol_index.search(normalized_name, limit=20)
            if entry.kind in {"file", "dir"}
        ]
        for entry in results:
            if normalize_repo_path(entry.path) == normalized:
                return entry
        for entry in results:
            if entry.name == normalized_name:
                return entry
        return None

    def _expand_directory_java_files(self, rel_dir: str) -> list[str]:
        try:
            target_dir = resolve_repo_path(self.repo_root, rel_dir)
        except UnsafeRepoPathError:
            return []
        if not target_dir.exists() or not target_dir.is_dir():
            return []
        java_files: list[str] = []
        for full_path in sorted(target_dir.rglob("*.java")):
            try:
                rel_path = to_repo_relative_path(self.repo_root, full_path)
            except UnsafeRepoPathError:
                continue
            if any(part in self.ignored_dirs for part in Path(rel_path).parts):
                continue
            if rel_path not in java_files:
                java_files.append(rel_path)
        return java_files

    def _fetch_project_java_files(self) -> list[str]:
        java_files: list[str] = []
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [directory for directory in dirs if directory not in self.ignored_dirs]
            for file_name in sorted(files):
                if not file_name.endswith(".java"):
                    continue
                full_path = Path(root) / file_name
                java_files.append(full_path.relative_to(self.repo_root).as_posix())
        return java_files
