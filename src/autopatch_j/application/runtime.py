"""装配本地业务服务与 SDK 资源，不持有意图分类器或补丁队列。"""

from collections.abc import Callable
import dataclasses
import pathlib
import sqlite3

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.agent import agent as project_agent
from autopatch_j.agent import run_context
from autopatch_j.core import conversations as core_conversations
from autopatch_j.core import errors
from autopatch_j.core import memory as core_memory
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.llm import factory
from autopatch_j.llm import runtime
from autopatch_j.scanners import catalog
from autopatch_j.scanners.semgrep import runtime as scanner_runtime


@dataclasses.dataclass(slots=True)
class ProjectRuntime:
    """当前项目服务及模型资源；调用方负责 close，关闭不删除持久数据。"""

    repair: service.RepairService
    evidence: evidence.EvidenceService
    symbol_index: symbol_index.SymbolIndex
    source_reader: source_reader.SourceReader
    memory: core_memory.ProjectMemory
    conversations: core_conversations.ProjectConversations
    model_runtime: runtime.ModelRuntime
    agent: project_agent.Agent
    settings: config.AppConfig
    scanners: catalog.ScannerCatalog

    def initialize(self) -> InitializationResult:
        """顺序准备扫描器与索引；故障立即停止后续步骤。"""
        scanner = scanner_runtime.install_managed_semgrep_runtime(
            self.settings.semgrep_version,
            lock_timeout=self.settings.semgrep_install_lock_timeout,
        )
        if scanner.status != "ready":
            raise errors.OperationError(
                error_messages.failed("扫描器初始化", scanner.message)
            ) from RuntimeError(scanner.message)
        stats = self.symbol_index.rebuild_index()
        return InitializationResult(scanner, stats)

    def reset(self, expected: models.RepairState) -> None:
        """同锁核对任务并清索引，不撤销已经写入的源码。

        Args:
            expected: 用户作出决定时已展示的状态版本。

        Raises:
            models.StateConflict: 决定已经过期，任务与索引均不清理，资源保留。
            errors.OperationError: 任务已结束但索引清理失败，资源关闭，不回滚任务。
        """
        # 清理失败属于次生错误；任意清理异常都不能覆盖主业务异常。
        # pylint: disable=broad-exception-caught
        ended = False
        try:
            with self.repair.store.lock:
                self.repair.end(expected)
                ended = True
                try:
                    self.symbol_index.clear()
                except (OSError, ValueError, sqlite3.DatabaseError) as exc:
                    raise errors.OperationError(
                        error_messages.incomplete_reset(str(exc))
                    ) from exc
        except BaseException as exc:
            if ended:
                try:
                    self.close()
                except (
                    Exception
                ) as close_error:  # pylint: disable=broad-exception-caught
                    exc.add_note(
                        error_messages.failed("项目服务关闭", str(close_error))
                    )
            raise
        else:
            self.close()

    def close(self) -> None:
        """关闭唯一模型运行资源，重复调用由其生命周期入口保证无副作用。"""
        self.model_runtime.close()

    def new_conversation(self, expected: models.RepairState) -> str:
        """同锁结束已展示任务并切换会话，阻止旧运行晚到保存。"""
        with self.repair.store.lock:
            self.repair.end(expected)
            try:
                return self.model_runtime.run(self.conversations.new())
            except (OSError, sqlite3.DatabaseError, LookupError) as exc:
                raise errors.OperationError(
                    error_messages.incomplete_new(str(exc))
                ) from exc
            except Exception as exc:
                exc.add_note(
                    error_messages.incomplete_new(
                        error_messages.INTERNAL_FAILURE
                    )
                )
                raise


@dataclasses.dataclass(frozen=True, slots=True)
class InitializationResult:
    """成功完成的扫描器与索引结果。"""

    scanner: scanner_runtime.RuntimeStatus
    index_stats: dict[str, int]


def build_project_runtime(
    repo_root: pathlib.Path,
    settings: config.AppConfig,
    model_factory: Callable[[], runtime.ModelRuntime] | None = None,
    *,
    scanners: catalog.ScannerCatalog | None = None,
) -> ProjectRuntime:
    """装配本地项目服务，失败时关闭本次取得的模型资源。

    Args:
        repo_root: 已确定的项目根目录，会在其中创建本地状态存储。
        settings: 本次启动显式读取的不可变配置。
        model_factory: 可注入不联网的模型工厂；返回资源的所有权移交给项目。
        scanners: 可选的实例级扫描器清单；缺省按 settings 创建。

    Returns:
        调用方负责关闭的项目运行时。模型配置无效时项目服务装配失败，CLI 可继续诊断。
    """
    # 装配失败后的清理边界，保留原始异常并附加任意次生关闭故障。
    # pylint: disable=broad-exception-caught
    model_runtime = (
        model_factory()
        if model_factory
        else factory.build_model_runtime(settings)
    )
    try:
        root = repo_root.resolve()
        scanners = scanners or catalog.ScannerCatalog.default(
            version=settings.semgrep_version, timeout=settings.scanner_timeout
        )
        scanner = scanners.get(catalog.DEFAULT_SCANNER_NAME)
        repair = service.RepairService(root, scanner)
        scanner_evidence = evidence.EvidenceService(
            root, scanner, ignored_dirs=settings.ignored_dirs
        )
        index = symbol_index.SymbolIndex(
            root, ignored_dirs=settings.ignored_dirs
        )
        reader = source_reader.SourceReader(root)
        memory = core_memory.ProjectMemory(root)
        conversations = core_conversations.ProjectConversations(root)
        dependencies = run_context.AgentRunContext(
            root,
            repair,
            scanner_evidence,
            reader,
            index,
            repair.store.load(),
            "",
            ignored_dirs=settings.ignored_dirs,
        )
        agent = project_agent.Agent(
            dependencies,
            model_runtime,
            conversations,
            memory,
            request_limit=settings.llm_request_limit,
        )
        return ProjectRuntime(
            repair,
            scanner_evidence,
            index,
            reader,
            memory,
            conversations,
            model_runtime,
            agent,
            settings,
            scanners,
        )
    except BaseException as exc:
        try:
            model_runtime.close()
        except (
            Exception
        ) as close_error:  # pylint: disable=broad-exception-caught
            exc.add_note(
                error_messages.failed("项目服务关闭", str(close_error))
            )
        raise
