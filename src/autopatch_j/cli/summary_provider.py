"""从工作台、扫描工件和索引构造轻量项目摘要。"""

from __future__ import annotations

import dataclasses
import pathlib

from autopatch_j.agent import agent as agent_agent
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_text
from autopatch_j.core.review import artifacts
from autopatch_j.core.review import workspace as review_workspace


@dataclasses.dataclass(slots=True)
class CliSummaryProvider:
    """CLI 展示摘要提供者。

    职责边界：
    1. 从 workspace、scan artifact 和 agent session 中提取适合展示的路径与摘要文案。
    2. 服务于 AgentStreamPresenter、空结果面板和当前审核上下文提示。
    3. 不修改 workspace，不触发扫描，也不参与意图判断。
    """

    repo_root: pathlib.Path
    artifact_manager: artifacts.ProjectArtifactStore
    workspace_manager: review_workspace.ReviewWorkspaceManager
    agent: agent_agent.Agent

    def fetch_review_scope_paths(
        self, current_item: domain_workspace.ReviewPatchItem
    ) -> list[str]:
        """返回当前审核范围，缺少范围时使用审核项目标文件。"""
        workspace = self.workspace_manager.load()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        return [current_item.file_path]

    def describe_scope_paths(self, scope: domain_scope.CodeScope) -> list[str]:
        """返回范围的文件、原始入口或明确的默认标签。"""
        if scope.focus_files:
            return list(scope.focus_files)
        if scope.source_roots:
            return list(scope.source_roots)
        return ["当前范围"]

    def describe_current_scope_paths(self) -> list[str]:
        """优先使用审核范围，其次扫描路径和请求焦点生成展示范围。"""
        workspace = self.workspace_manager.load()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        scan_paths = self._collect_latest_scan_paths()
        if scan_paths:
            return scan_paths
        if self.agent.session.focus_paths:
            return list(self.agent.session.focus_paths)
        return ["当前范围"]

    def build_local_no_issue_summary(self) -> str:
        """供完整结束且无草案的复核面板使用的限定结论。"""
        return "模型复核未发现需要修复的问题。"

    def build_static_scan_summary(self) -> str:
        """供成功零 finding 扫描的结果面板使用的范围结论。"""
        return "当前范围未发现安全或正确性问题。"

    def build_project_explain_context(
        self, scope: domain_scope.CodeScope
    ) -> str:
        """从索引和通过准入的项目说明构造有界讲解上下文。"""
        stats = self.agent.session.symbol_indexer.get_stats()
        root_dirs = sorted(
            {path.split("/", 1)[0] for path in scope.focus_files if "/" in path}
        )
        lines = [
            "项目轻量上下文:",
            f"- 项目根目录名: {self.repo_root.name}",
            f"- 索引统计: {self._format_symbol_stats(stats)}",
            f"- 主要顶层目录: {", ".join(root_dirs[:8]) if root_dirs else "无"}",
        ]
        metadata = self._read_project_metadata()
        if metadata:
            lines.append(metadata)
        return "\n".join(lines)

    def _collect_latest_scan_paths(self) -> list[str]:
        """从最近有效扫描快照展开 Java 路径，仅用于范围展示。"""
        scan_files = sorted(
            self.artifact_manager.findings_dir.glob("scan-*.json"), reverse=True
        )
        if not scan_files:
            return []
        latest = self.artifact_manager.load_scan_result(scan_files[0].stem)
        if latest is None:
            return []

        resolved: list[str] = []
        repo_root = self.repo_root.resolve()
        for target in latest.targets:
            normalized = target.replace("\\", "/")
            try:
                candidate = repo_path.resolve_source_path(repo_root, normalized)
            except repo_path.UnsafeRepoPathError:
                continue
            if candidate.is_dir():
                for java_file in sorted(candidate.rglob("*.java")):
                    rel_path = java_file.relative_to(repo_root).as_posix()
                    try:
                        repo_path.resolve_source_path(repo_root, rel_path)
                    except repo_path.UnsafeRepoPathError:
                        continue
                    if rel_path not in resolved:
                        resolved.append(rel_path)
                continue
            if normalized not in resolved:
                resolved.append(normalized)
        return resolved

    def _read_project_metadata(self) -> str:
        """摘录安全的仓库内说明与构建文件，跳过越界、状态目录及读取失败。"""
        candidates = (
            "README_CN.md",
            "README.md",
            "pom.xml",
            "build.gradle",
            "settings.gradle",
        )
        snippets: list[str] = []
        for name in candidates:
            try:
                path = repo_path.resolve_source_path(self.repo_root, name)
                if not path.is_file():
                    continue
                content = source_text.read(path).content
            except repo_path.UnsafeRepoPathError, OSError:
                continue
            compact = " ".join(content.split())
            if compact:
                snippets.append(f"- {name}: {compact[:500]}")
            if len(snippets) >= 2:
                break
        if not snippets:
            return ""
        return "项目说明/构建文件摘录:\n" + "\n".join(snippets)

    def _format_symbol_stats(self, stats: dict[str, int]) -> str:
        """格式化索引中的各符号种类及总数。"""
        labels = (
            ("file", "文件"),
            ("class", "类"),
            ("interface", "接口"),
            ("enum", "枚举"),
            ("record", "record"),
            ("constructor", "构造器"),
            ("method", "方法"),
        )
        parts = [
            f"{label} {stats.get(kind, 0)}"
            for kind, label in labels
            if stats.get(kind, 0)
        ]
        if not parts:
            parts = ["文件 0"]
        parts.append(f"总计 {stats.get("total", 0)}")
        return "，".join(parts)
