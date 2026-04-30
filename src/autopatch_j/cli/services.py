from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.llm_client import LLMClient, build_default_llm_client
from autopatch_j.agent.session import AgentSession
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.backlog_manager import BacklogManager
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.input_classifier import ConversationRouter, IntentDetector, build_llm_intent_classifier
from autopatch_j.core.models import CodeScope, PatchReviewItem
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.patch_verifier import PatchVerifier
from autopatch_j.core.scanner_runner import ScannerRunner
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.config import GlobalConfig
from autopatch_j.scanners import DEFAULT_SCANNER_NAME, get_scanner


@dataclass(slots=True)
class CliContextSummary:
    repo_root: Path
    artifact_manager: ArtifactManager
    workspace_manager: WorkspaceManager
    agent: Agent

    def fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]:
        workspace = self.workspace_manager.load_workspace()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        return [current_item.file_path]

    def describe_scope_paths(self, scope: CodeScope) -> list[str]:
        if scope.focus_files:
            return list(scope.focus_files)
        if scope.source_roots:
            return list(scope.source_roots)
        return ["当前范围"]

    def describe_current_scope_paths(self) -> list[str]:
        workspace = self.workspace_manager.load_workspace()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        scan_paths = self._collect_latest_scan_paths()
        if scan_paths:
            return scan_paths
        if self.agent.session.focus_paths:
            return list(self.agent.session.focus_paths)
        return ["当前范围"]

    def build_local_no_issue_summary(self) -> str:
        return "模型复核未发现需要修复的问题。"

    def build_static_scan_summary(self) -> str:
        return "当前范围未发现安全或正确性问题。"

    def _collect_latest_scan_paths(self) -> list[str]:
        scan_files = sorted(self.artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return []
        latest = self.artifact_manager.load_scan_result(scan_files[0].stem)
        if latest is None:
            return []

        resolved: list[str] = []
        for target in latest.targets:
            normalized = str(target).replace("\\", "/")
            candidate = (self.repo_root / normalized).resolve()
            if candidate.is_dir():
                for java_file in sorted(candidate.rglob("*.java")):
                    rel_path = java_file.relative_to(self.repo_root).as_posix()
                    if rel_path not in resolved:
                        resolved.append(rel_path)
                continue
            if normalized not in resolved:
                resolved.append(normalized)
        return resolved


@dataclass(slots=True)
class CliServices:
    artifact_manager: ArtifactManager
    symbol_indexer: SymbolIndexer
    patch_engine: PatchEngine
    code_fetcher: CodeFetcher
    patch_verifier: PatchVerifier | None
    intent_detector: IntentDetector
    conversation_router: ConversationRouter
    backlog_manager: BacklogManager
    chat_filter: ChatFilter
    scope_service: ScopeService
    scanner_runner: ScannerRunner
    workspace_manager: WorkspaceManager
    agent: Agent
    summary: CliContextSummary


def build_cli_services(
    repo_root: Path,
    llm_factory: Callable[[], LLMClient | None] = build_default_llm_client,
) -> CliServices:
    shared_llm = llm_factory()
    artifact_manager = ArtifactManager(repo_root)
    symbol_indexer = SymbolIndexer(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
    patch_engine = PatchEngine(repo_root)
    code_fetcher = CodeFetcher(repo_root)
    intent_detector = IntentDetector(classify_with_llm=build_llm_intent_classifier(shared_llm))
    backlog_manager = BacklogManager()
    chat_filter = ChatFilter()
    conversation_router = ConversationRouter(llm=shared_llm)
    scope_service = ScopeService(repo_root, symbol_indexer, ignored_dirs=GlobalConfig.ignored_dirs)
    scanner_runner = ScannerRunner(repo_root, artifact_manager)
    workspace_manager = WorkspaceManager(artifact_manager)

    scanner = get_scanner(DEFAULT_SCANNER_NAME)
    patch_verifier = PatchVerifier(repo_root, scanner) if scanner else None

    agent_session = AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=patch_verifier,
    )
    agent = Agent(session=agent_session, llm=shared_llm)
    summary = CliContextSummary(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        agent=agent,
    )

    return CliServices(
        artifact_manager=artifact_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=patch_verifier,
        intent_detector=intent_detector,
        conversation_router=conversation_router,
        backlog_manager=backlog_manager,
        chat_filter=chat_filter,
        scope_service=scope_service,
        scanner_runner=scanner_runner,
        workspace_manager=workspace_manager,
        agent=agent,
        summary=summary,
    )
