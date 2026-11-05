"""装配本地业务服务与 SDK 资源，不持有意图分类器或补丁队列。"""

from collections.abc import Callable
import contextlib
import dataclasses
import pathlib

from autopatch_j.agent import agent as project_agent
from autopatch_j.agent import session
from autopatch_j import config
from autopatch_j.core import conversations as core_conversations
from autopatch_j.core import memory as core_memory
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import service
from autopatch_j.llm import factory
from autopatch_j.llm import runtime
from autopatch_j.scanners import catalog


@dataclasses.dataclass(slots=True)
class CliRuntime:
    """当前项目服务集合，拥有模型运行资源；会话和笔记由官方存储管理。"""

    repair: service.RepairService
    evidence: evidence.EvidenceService
    symbol_indexer: symbol_index.SymbolIndex
    code_fetcher: source_reader.SourceReader
    memory: core_memory.ProjectMemory
    conversations: core_conversations.ProjectConversations
    model_runtime: runtime.ModelRuntime
    agent: project_agent.Agent

    def close(self) -> None:
        """关闭唯一模型运行资源，重复调用由其生命周期入口保证无副作用。"""
        self.model_runtime.close()


def build_cli_runtime(
    repo_root: pathlib.Path,
    model_factory: Callable[
        [], runtime.ModelRuntime
    ] = factory.build_model_runtime,
) -> CliRuntime:
    """装配失败也交还模型资源；启动不发送模型请求。"""
    model_runtime = model_factory()
    try:
        root = repo_root.resolve()
        scanner = catalog.DEFAULT_SCANNER_CATALOG.get(
            catalog.DEFAULT_SCANNER_NAME
        )
        repair = service.RepairService(root, scanner)
        scanner_evidence = evidence.EvidenceService(root, scanner)
        index = symbol_index.SymbolIndex(
            root, ignored_dirs=config.GLOBAL_CONFIG.ignored_dirs
        )
        reader = source_reader.SourceReader(root)
        memory = core_memory.ProjectMemory(root)
        conversations = core_conversations.ProjectConversations(root)
        dependencies = session.AgentSession(
            root,
            repair,
            scanner_evidence,
            reader,
            index,
            repair.store.load(),
            "",
        )
        agent = project_agent.Agent(
            dependencies, model_runtime, conversations, memory
        )
        return CliRuntime(
            repair,
            scanner_evidence,
            index,
            reader,
            memory,
            conversations,
            model_runtime,
            agent,
        )
    except BaseException:
        with contextlib.suppress(Exception):
            model_runtime.close()
        raise
