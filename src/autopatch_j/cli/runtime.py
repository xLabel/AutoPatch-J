"""构造并持有项目业务服务、SDK 运行资源和展示依赖。"""

from __future__ import annotations

import contextlib
import dataclasses
import pathlib
from typing import Callable

from autopatch_j import config
from autopatch_j.agent import agent as agent_agent
from autopatch_j.agent import classification
from autopatch_j.agent import session
from autopatch_j.cli import summary_provider as cli_summary_provider
from autopatch_j.core import chat_filter as core_chat_filter
from autopatch_j.core import conversations as core_conversations
from autopatch_j.core import memory as core_memory
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import verification
from autopatch_j.core.project import scope
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.review import application
from autopatch_j.core.review import artifacts
from autopatch_j.core.review import backlog
from autopatch_j.core.review import scanning
from autopatch_j.core.review import workspace
from autopatch_j.core.user_input import intent
from autopatch_j.core.user_input import router
from autopatch_j.llm import factory
from autopatch_j.llm import runtime
from autopatch_j.scanners import catalog


@dataclasses.dataclass(slots=True)
class CliRuntime:
    """当前项目初始化后的 CLI 运行时依赖集合。

    它只表达已构造好的 core service、Agent 和展示摘要提供者；不处理用户输入、
    不执行命令，也不承载 CLI 主循环。
    """

    artifact_manager: artifacts.ProjectArtifactStore
    symbol_indexer: symbol_index.SymbolIndex
    patch_engine: search_replace.SearchReplacePatchEngine
    code_fetcher: source_reader.SourceReader
    patch_verifier: verification.PatchQualityVerifier | None
    intent_detector: intent.UserIntentClassifier
    conversation_router: router.ReviewRouteClassifier
    backlog_manager: backlog.FindingBacklog
    chat_filter: core_chat_filter.ChatFilter
    scope_service: scope.ScopeResolver
    scanner_runner: scanning.StaticScanRunner
    workspace_manager: workspace.ReviewWorkspaceManager

    @property
    def review_service(self) -> application.PatchReviewService:
        """使用当前工作台、补丁引擎及验证器的审核协调服务。"""
        return application.PatchReviewService(
            self.workspace_manager, self.patch_engine, self.patch_verifier
        )

    memory: core_memory.ProjectMemory
    conversations: core_conversations.ProjectConversations
    model_runtime: runtime.ModelRuntime
    agent: agent_agent.Agent
    summary_provider: cli_summary_provider.CliSummaryProvider
    _closed: bool = dataclasses.field(default=False, init=False, repr=False)

    def close(self) -> None:
        """关闭当前对象拥有的资源；重复调用无副作用。"""
        if self._closed:
            return
        self._closed = True
        self.model_runtime.close()


def build_cli_runtime(
    repo_root: pathlib.Path,
    model_factory: Callable[
        [], runtime.ModelRuntime
    ] = factory.build_model_runtime,
) -> CliRuntime:
    """构造项目业务依赖；装配失败时关闭已取得的模型运行资源。"""
    model_runtime = model_factory()
    try:
        return _assemble_runtime(repo_root, model_runtime)
    except BaseException:
        # 收尾故障不能覆盖装配阶段的原始失败。
        with contextlib.suppress(Exception):
            model_runtime.close()
        raise


def _assemble_runtime(
    repo_root: pathlib.Path, model_runtime: runtime.ModelRuntime
) -> CliRuntime:
    """装配当前项目业务服务，模型资源所有权仍由外层工厂保护。"""
    artifact_manager = artifacts.ProjectArtifactStore(repo_root)
    symbol_indexer = symbol_index.SymbolIndex(
        repo_root, ignored_dirs=config.GLOBAL_CONFIG.ignored_dirs
    )
    patch_engine = search_replace.SearchReplacePatchEngine(repo_root)
    code_fetcher = source_reader.SourceReader(repo_root)
    intent_detector = intent.UserIntentClassifier(
        classify_with_llm=classification.build_intent_classifier(model_runtime)
    )
    backlog_manager = backlog.FindingBacklog()
    chat_filter = core_chat_filter.ChatFilter()
    conversation_router = router.ReviewRouteClassifier(
        classify_with_llm=classification.build_route_classifier(model_runtime)
    )
    scope_service = scope.ScopeResolver(
        repo_root,
        symbol_indexer,
        ignored_dirs=config.GLOBAL_CONFIG.ignored_dirs,
    )
    scanner_runner = scanning.StaticScanRunner(repo_root, artifact_manager)
    workspace_manager = workspace.ReviewWorkspaceManager(artifact_manager)
    memory = core_memory.ProjectMemory(repo_root)
    conversations = core_conversations.ProjectConversations(repo_root)

    scanner = catalog.DEFAULT_SCANNER_CATALOG.get(catalog.DEFAULT_SCANNER_NAME)
    patch_verifier = (
        verification.PatchQualityVerifier(repo_root, scanner)
        if scanner
        else None
    )

    agent_session = session.AgentSession(
        repo_root=repo_root,
        artifact_manager=artifact_manager,
        workspace_manager=workspace_manager,
        symbol_indexer=symbol_indexer,
        patch_engine=patch_engine,
        code_fetcher=code_fetcher,
        patch_verifier=patch_verifier,
    )
    agent = agent_agent.Agent(
        agent_session, model_runtime, conversations, memory
    )
    summary_provider = cli_summary_provider.CliSummaryProvider(
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
        agent=agent,
        memory=memory,
        conversations=conversations,
        model_runtime=model_runtime,
        summary_provider=summary_provider,
    )
