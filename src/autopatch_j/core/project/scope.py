"""用户路径指向的 Java 文件、目录和项目范围解析。"""

from __future__ import annotations

import os
import pathlib
import re

from autopatch_j.core.domain import scope
from autopatch_j.core.project import repo_path as project_repo_path
from autopatch_j.core.project import symbol_index as project_symbol_index
from autopatch_j.core.project import symbols


class ScopeResolver:
    """用户 @mention 到 CodeScope 的解析服务。

    职责边界：
    1. 结合 SymbolIndex 将文件、目录、类或方法 mention 解析为文件级 focus_files。
    2. 决定当前任务是否锁定焦点范围，作为 Agent/Tool 的路径约束来源。
    3. 不判断用户意图，也不触发扫描；它只产出可执行的代码范围。
    """

    def __init__(
        self,
        repo_root: pathlib.Path,
        symbol_index: project_symbol_index.SymbolIndex,
        ignored_dirs: set[str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.symbol_index = symbol_index
        self.ignored_dirs = ignored_dirs or set()

    def resolve(
        self, user_text: str, default_to_project: bool = False
    ) -> scope.CodeScope | None:
        """从用户入口确定文件范围；不合法或不支持的符号入口返回 None。"""
        mentions = re.findall(r"@([^\s@]+)", user_text)
        if not mentions:
            if not default_to_project:
                return None
            project_files = self._fetch_project_java_files()
            return scope.CodeScope(
                kind=scope.CodeScopeKind.PROJECT,
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
            normalized_path = project_repo_path.normalize_repo_path(entry.path)
            if normalized_path not in source_roots:
                source_roots.append(normalized_path)
            if entry.kind == "dir":
                saw_directory = True
                for file_path in self._expand_directory_java_files(
                    normalized_path
                ):
                    if file_path not in focus_files:
                        focus_files.append(file_path)
            elif (
                normalized_path.endswith(".java")
                and normalized_path not in focus_files
            ):
                focus_files.append(normalized_path)

        if not focus_files:
            return None

        kind = (
            scope.CodeScopeKind.SINGLE_FILE
            if len(focus_files) == 1 and not saw_directory
            else scope.CodeScopeKind.MULTI_FILE
        )
        return scope.CodeScope(
            kind=kind,
            source_roots=source_roots,
            focus_files=focus_files,
            is_locked=True,
        )

    def _fetch_best_entry(
        self, mention: str
    ) -> symbols.SymbolIndexEntry | None:
        """选择文件或目录入口，排除不能作为范围的类和方法。"""
        normalized = project_repo_path.normalize_repo_path(mention)
        try:
            candidate = project_repo_path.resolve_repo_path(
                self.repo_root, normalized
            )
        except project_repo_path.UnsafeRepoPathError:
            return None
        if candidate.exists():
            rel_path = project_repo_path.to_repo_relative_path(
                self.repo_root, candidate
            )
            if project_repo_path.is_project_state_path(rel_path):
                return None
            if candidate.is_dir():
                return symbols.SymbolIndexEntry(
                    path=rel_path, name=candidate.name, kind="dir"
                )
            return symbols.SymbolIndexEntry(
                path=rel_path, name=candidate.name, kind="file"
            )

        normalized_name = pathlib.Path(normalized).name
        results = [
            entry
            for entry in self.symbol_index.search(normalized_name, limit=20)
            if entry.kind in {"file", "dir"}
            and self._is_source_entry(entry.path)
        ]
        for entry in results:
            if project_repo_path.normalize_repo_path(entry.path) == normalized:
                return entry
        for entry in results:
            if entry.name == normalized_name:
                return entry
        return None

    def _is_source_entry(self, path: str) -> bool:
        try:
            resolved = project_repo_path.resolve_repo_path(self.repo_root, path)
            repo_path = project_repo_path.to_repo_relative_path(
                self.repo_root, resolved
            )
        except project_repo_path.UnsafeRepoPathError:
            return False
        return not project_repo_path.is_project_state_path(repo_path)

    def _expand_directory_java_files(self, rel_dir: str) -> list[str]:
        """展开安全目录内的 Java 文件，过滤忽略目录和项目状态。"""
        try:
            target_dir = project_repo_path.resolve_repo_path(
                self.repo_root, rel_dir
            )
        except project_repo_path.UnsafeRepoPathError:
            return []
        if not target_dir.exists() or not target_dir.is_dir():
            return []
        if project_repo_path.is_project_state_path(
            project_repo_path.to_repo_relative_path(self.repo_root, target_dir)
        ):
            return []
        java_files: list[str] = []
        for full_path in sorted(target_dir.rglob("*.java")):
            try:
                rel_path = project_repo_path.to_repo_relative_path(
                    self.repo_root, full_path
                )
            except project_repo_path.UnsafeRepoPathError:
                continue
            if project_repo_path.is_project_state_path(rel_path):
                continue
            if any(
                part in self.ignored_dirs
                for part in pathlib.Path(rel_path).parts
            ):
                continue
            if rel_path not in java_files:
                java_files.append(rel_path)
        return java_files

    def _fetch_project_java_files(self) -> list[str]:
        """收集当前项目允许扫描的 Java 文件，保留安全内部软链接。"""
        java_files: list[str] = []
        for root, dirs, files in os.walk(self.repo_root):
            root_path = pathlib.Path(root)
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in self.ignored_dirs
                and not project_repo_path.is_project_state_path(
                    (root_path / directory)
                    .relative_to(self.repo_root)
                    .as_posix()
                )
            ]
            for file_name in sorted(files):
                if not file_name.endswith(".java"):
                    continue
                full_path = pathlib.Path(root) / file_name
                rel_path = full_path.relative_to(self.repo_root).as_posix()
                if not self._is_source_entry(rel_path):
                    continue
                java_files.append(rel_path)
        return java_files
