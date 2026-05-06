from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from autopatch_j.agent.agent import Agent
from autopatch_j.agent.session import AgentSession
from autopatch_j.cli.summary_provider import CliSummaryProvider
from autopatch_j.config import GlobalConfig
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.backlog_manager import BacklogManager
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.input_classifier import ConversationRouter, IntentDetector, build_llm_intent_classifier
from autopatch_j.core.memory import MemoryManager
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.patch_verifier import PatchVerifier
from autopatch_j.core.scanner_runner import ScannerRunner
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.llm.client import LLMClient, build_default_llm_client
from autopatch_j.scanners import DEFAULT_SCANNER_NAME, get_scanner


@dataclass(slots=True)
class CliRuntime:
    """
    当前项目初始化后的 CLI 运行时依赖集合。

    它只表达已构造好的 core service、Agent 和展示摘要提供者；不处理用户输入、
    不执行命令，也不承载 CLI 主循环。
    """

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
    memory_manager: MemoryManager
    agent: Agent
    summary_provider: CliSummaryProvider


def build_cli_runtime(
    repo_root: Path,
    llm_factory: Callable[[], LLMClient | None] = build_default_llm_client,
) -> CliRuntime:
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
    memory_manager = MemoryManager(artifact_manager.state_dir / "memory.json")

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

