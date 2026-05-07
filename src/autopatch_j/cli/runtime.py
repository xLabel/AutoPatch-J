from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.session import AgentSession
from autopatch_j.cli.summary_provider import CliSummaryProvider
from autopatch_j.config import GlobalConfig
from autopatch_j.core.review import ProjectArtifactStore
from autopatch_j.core.review import FindingBacklog
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.project import SourceReader
from autopatch_j.core.user_input import ReviewRouteClassifier, UserIntentClassifier, build_llm_user_intent_classifier
from autopatch_j.core.memory import MemoryManager
from autopatch_j.core.patching import SearchReplacePatchEngine
from autopatch_j.core.patching import PatchQualityVerifier
from autopatch_j.core.review import StaticScanRunner
from autopatch_j.core.project import ScopeResolver
from autopatch_j.core.project import SymbolIndex
from autopatch_j.core.review import ReviewWorkspaceManager
from autopatch_j.llm.client import LLMClient, build_default_llm_client
from autopatch_j.scanners import DEFAULT_SCANNER_CATALOG, DEFAULT_SCANNER_NAME


@dataclass(slots=True)
class CliRuntime:
    """
    当前项目初始化后的 CLI 运行时依赖集合。

    它只表达已构造好的 core service、Agent 和展示摘要提供者；不处理用户输入、
    不执行命令，也不承载 CLI 主循环。
    """

    artifact_manager: ProjectArtifactStore
    symbol_indexer: SymbolIndex
    patch_engine: SearchReplacePatchEngine
    code_fetcher: SourceReader
    patch_verifier: PatchQualityVerifier | None
    intent_detector: UserIntentClassifier
    conversation_router: ReviewRouteClassifier
    backlog_manager: FindingBacklog
    chat_filter: ChatFilter
    scope_service: ScopeResolver
    scanner_runner: StaticScanRunner
    workspace_manager: ReviewWorkspaceManager
    memory_manager: MemoryManager
    agent: Agent
    summary_provider: CliSummaryProvider


def build_cli_runtime(
    repo_root: Path,
    llm_factory: Callable[[], LLMClient | None] = build_default_llm_client,
) -> CliRuntime:
    shared_llm = llm_factory()
    artifact_manager = ProjectArtifactStore(repo_root)
    symbol_indexer = SymbolIndex(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
    patch_engine = SearchReplacePatchEngine(repo_root)
    code_fetcher = SourceReader(repo_root)
    intent_detector = UserIntentClassifier(classify_with_llm=build_llm_user_intent_classifier(shared_llm))
    backlog_manager = FindingBacklog()
    chat_filter = ChatFilter()
    conversation_router = ReviewRouteClassifier(llm=shared_llm)
    scope_service = ScopeResolver(repo_root, symbol_indexer, ignored_dirs=GlobalConfig.ignored_dirs)
    scanner_runner = StaticScanRunner(repo_root, artifact_manager)
    workspace_manager = ReviewWorkspaceManager(artifact_manager)
    memory_manager = MemoryManager(artifact_manager.state_dir / "memory.json")

    scanner = DEFAULT_SCANNER_CATALOG.get(DEFAULT_SCANNER_NAME)
    patch_verifier = PatchQualityVerifier(repo_root, scanner) if scanner else None

    agent_session = AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=patch_verifier,
        memory_manager=memory_manager,
    )
    agent = Agent(session=agent_session, llm=shared_llm)
    summary_provider = CliSummaryProvider(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        agent=agent,
    )

    return CliRuntime(
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
        memory_manager=memory_manager,
        agent=agent,
        summary_provider=summary_provider,
    )
