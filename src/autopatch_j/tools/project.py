"""SDK function tools 的薄适配，业务预期与状态转换交给修复服务。"""

import asyncio
from collections.abc import Callable
import dataclasses
import functools
from typing import Annotated
from typing import Literal

import pydantic
import pydantic_ai

from autopatch_j.agent import session
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbols
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models
from autopatch_j.core.repair import store
from autopatch_j.scanners import models as scan_models

Text = Annotated[str, pydantic.Field(strict=True)]
Line = Annotated[int, pydantic.Field(strict=True, ge=1)]
Offset = Annotated[int, pydantic.Field(strict=True, ge=0)]


def correction[**Params, Return](
    function: Callable[Params, Return],
) -> Callable[Params, Return]:
    """将可纠正业务输入交给 SDK 同轮重试，不在 CLI 重启整个 Agent。"""

    @functools.wraps(function)
    def execute(*args: Params.args, **kwargs: Params.kwargs) -> Return:
        try:
            return function(*args, **kwargs)
        except (
            models.RepairError,
            repo_path.UnsafeRepoPathError,
            source_reader.SourceReadError,
            OSError,
            UnicodeError,
        ) as exc:
            raise pydantic_ai.ModelRetry(str(exc)) from exc

    return execute


@dataclasses.dataclass(frozen=True, slots=True)
class ReadEvidence:
    """源码读取结果与适用规范分别标识。"""

    source: source_reader.SourceRead
    guidelines: list[source_reader.SourceRead]


@dataclasses.dataclass(frozen=True, slots=True)
class FindingSummary:
    """分页候选定位，handle 包含扫描身份。"""

    handle: str
    path: str
    line: int
    rule: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class ScanObservation:
    """扫描完成或失败的明确结果，不把失败包装成零候选。"""

    status: Literal["ok", "failed"]
    message: str
    scan_id: str | None = None
    total: int = 0
    findings: tuple[FindingSummary, ...] = ()
    next_offset: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class FindingDetail:
    """原始扫描身份与当前源码证据；二者可能已不一致。"""

    target: models.FindingTarget
    finding: scan_models.Finding
    current_source: ReadEvidence
    evidence_current: bool


class ProjectTools:
    """绑定本轮暂存状态；所有工具均不写入目标源码。"""

    def __init__(self, context: session.AgentSession) -> None:
        self.context = context

    @correction
    def start_repair(self, goal: Text, write_paths: list[Text]) -> str:
        """仅用户要求修复时开始一项任务并请求写范围，等待用户 allow 后才能提案。

        Args:
            goal: 当前用户的具体修复目标，不能替换另一项待审任务。
            write_paths: 需要修改的确切已有文件，读取权限与此独立。
        """
        self.context.state = self.context.repair.start(
            self.context.state, goal, tuple(write_paths)
        )
        return "任务已准备，写范围尚未授权；请展示范围并等待用户输入 allow。"

    @correction
    def request_write_scope(self, paths: list[Text]) -> str:
        """请求增加确切写文件；必须等用户 allow，不能把 @ 阅读理解成授权。"""
        self.context.state = self.context.repair.request_scope(
            self.context.state, tuple(paths)
        )
        pending = self.context.state.requested_paths
        if not pending:
            return "这些文件已由用户批准，无需再次 allow。请继续用 propose_patch 准备当前目标的完整提案。"
        return "以下新增文件仍待用户 allow：" + "、".join(pending)

    @correction
    def record_constraint(self, requirement: Text) -> str:
        """记录用户明确提出的当前任务要求，不写长期笔记，不把询问转成要求。"""
        current = self.context.state
        if requirement.strip() not in self.context.user_text:
            raise models.RepairError(
                "临时要求只能摘取当前用户原文，不能自行改写或补造。"
            )
        if (
            current.status not in {"investigating", "pending"}
            or not requirement.strip()
        ):
            raise models.RepairError("没有活动任务或要求为空。")
        self.context.state = store.changed(
            current,
            constraints=tuple(
                dict.fromkeys((*current.constraints, requirement))
            ),
        )
        return "已暂存当前任务要求。"

    async def scan_java(self, paths: list[Text]) -> ScanObservation:
        """扫描明确的 Java 文件或目录；失败不是零 finding，零 finding 也不能否定用户缺陷。"""
        try:
            record = await asyncio.to_thread(
                self.context.evidence.scan, tuple(paths)
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.context.notices.append(
                f"静态扫描失败：{exc}；没有登记为零 finding。"
            )
            return ScanObservation(status="failed", message=str(exc))
        self.context.notices.append(
            f"静态扫描完成：{len(record.result.findings)} 项候选；不证明代码没有缺陷。"
        )
        return self._scan_page(record, 0)

    @correction
    def get_scan_findings(
        self, scan_id: Text, offset: Offset = 0
    ) -> ScanObservation:
        """按页读取已有扫描候选，不重新扫描。"""
        record = self.context.evidence.load(scan_id)
        return self._scan_page(record, offset)

    @correction
    def get_finding_detail(self, finding_handle: Text) -> FindingDetail:
        """读取原始 finding 绑定及当前源码；源码已变时证据明确标为过期。"""
        target, finding = self.context.evidence.finding(finding_handle)
        path = repo_path.resolve_source_path(
            self.context.repo_root, finding.path
        )
        current = files.digest(path.read_bytes()) == target.source_sha256
        return FindingDetail(
            target,
            finding,
            ReadEvidence(
                self.context.reader.read(
                    finding.path, finding.region.start_line, mode="block"
                ),
                self.context.reader.guidance(finding.path),
            ),
            current,
        )

    @correction
    def read_source_context(self, path: Text, line: Line = 1) -> ReadEvidence:
        """读取目标行前 20 后 80 行及适用 AGENTS；不扩大写权限。"""
        return ReadEvidence(
            self.context.reader.read(path, max(1, line - 20), line + 80),
            self.context.reader.guidance(path),
        )

    @correction
    def read_source_block(self, path: Text, line: Line) -> ReadEvidence:
        """读取所在 Java 方法、构造器或类型；返回实际范围和降级信息。"""
        return ReadEvidence(
            self.context.reader.read(path, line, mode="block"),
            self.context.reader.guidance(path),
        )

    @correction
    def read_source_file(self, path: Text) -> ReadEvidence:
        """读取文件，截断时按返回行号继续 read_source_context；规范按引用渐进阅读。"""
        return ReadEvidence(
            self.context.reader.read(path, mode="file"),
            self.context.reader.guidance(path),
        )

    def search_symbols(self, query: Text) -> list[symbols.SymbolIndexEntry]:
        """查 Java 符号，索引只用于定位，结论仍需读当前源码。"""
        result = []
        for entry in self.context.symbols.search(query):
            try:
                repo_path.resolve_source_path(
                    self.context.repo_root, entry.path
                )
            except repo_path.UnsafeRepoPathError:
                continue
            result.append(entry)
        return result

    def get_proposal(self) -> str:
        """读取当前完整提案与校验结果，不修订提案；bytes 不注入提示词。"""
        proposal = self.context.state.proposal
        if proposal is None:
            return "当前没有补丁提案。"
        return (
            proposal.rationale
            + "\n\n"
            + "\n".join(
                item.diff + "\n" + item.syntax.message
                for item in proposal.files
            )
        )

    @correction
    def propose_patch(
        self,
        edits: list[models.FileEdit],
        rationale: Text,
        finding_handle: Text | None = None,
    ) -> str:
        """新建或修订当前问题的完整提案；替换按文件内顺序匹配，不写源码。

        Args:
            edits: 每个文件只能出现一次，每项 old_text 非空且在前一项结果中恰好匹配一次。
            rationale: 缺陷依据、完整修复理由和未验证事项；辅助文件属于同一项修复。
            finding_handle: 扫描证据句柄；非扫描发现可省略，修订继承已有绑定。
        """
        current = self.context.state
        target = (
            self.context.evidence.finding(finding_handle)[0]
            if finding_handle
            else current.proposal.target if current.proposal else None
        )
        self.context.state = self.context.repair.prepare(
            current, tuple(edits), rationale, target
        )
        return "完整提案已暂存，等待运行结束后的人工预览和 apply；未修改源码。"

    @correction
    def assess_issue(
        self,
        outcome: Literal[
            "no_change", "insufficient_evidence", "execution_failure"
        ],
        reason: Text,
        finding_handle: Text | None = None,
    ) -> str:
        """明确登记不修改、证据不足或执行失败；不能以无补丁推导失败。"""
        if finding_handle:
            self.context.evidence.finding(finding_handle)
        current = self.context.state
        if not reason.strip():
            raise models.RepairError("判断需要具体依据。")
        assessment = models.Assessment(
            issue_id=current.issue_id or "",
            outcome=outcome,
            reason=reason,
            finding_handle=finding_handle,
        )
        self.context.state = self.context.repair.assess(current, assessment)
        return f"已暂存明确结论：{outcome}；不代表整个仓库没有问题。"

    def _scan_page(
        self, record: evidence.ScanRecord, offset: int
    ) -> ScanObservation:
        total = len(record.result.findings)
        entries = tuple(
            FindingSummary(
                f"{record.id}:F{index + 1}",
                item.path,
                item.region.start_line,
                item.check_id,
                item.message,
            )
            for index, item in enumerate(
                record.result.findings[offset : offset + 20], offset
            )
        )
        return ScanObservation(
            status="ok",
            message="扫描成功；finding 是候选，需结合源码判断。零 finding 不证明无缺陷。",
            scan_id=record.id,
            total=total,
            findings=entries,
            next_offset=offset + 20 if offset + 20 < total else None,
        )
