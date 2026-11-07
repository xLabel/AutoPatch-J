"""内存与磁盘共用的修复领域类型，不从展示状态重建业务事实。"""

from typing import Literal

import pydantic

from autopatch_j.core import finding


class Record(pydantic.BaseModel):
    """不可变的领域记录；源码 bytes 使用明确的 Base64 JSON 表示。"""

    model_config = pydantic.ConfigDict(
        extra="forbid",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class Replacement(Record):
    """非空匹配原文与允许为空的替换文本。"""

    old_text: pydantic.StrictStr = pydantic.Field(min_length=1)
    new_text: pydantic.StrictStr


class FileEdit(Record):
    """一个已有文件的有序替换，前项输出是后项的匹配输入。"""

    path: pydantic.StrictStr = pydantic.Field(min_length=1)
    replacements: tuple[Replacement, ...] = pydantic.Field(min_length=1)


class Check(Record):
    """独立验证证据；未执行不能转换为通过，运行故障另行抛出。"""

    kind: str
    status: Literal["passed", "failed", "unverified", "skipped"]
    message: str


class EditSpan(Record):
    """一次替换在当时候选 bytes 中的位置及替换长度。"""

    start: int
    end: int
    replacement_length: int


class PreparedFile(Record):
    """可审查的单文件准备结果，保存完整基线与确定的候选 bytes。

    Attributes:
        path: 仓库相对真实路径，resolved_path 保存准备时的绝对目标供预检。
        original: 准备时的完整原始 bytes；candidate 是全部替换后的 bytes。
        original_sha256: 原始指纹；candidate_sha256 是候选指纹。
        encoding: 无损解码采用的编码，不对区域外 bytes 重新编码。
        permission_mode: 准备时可观察的文件权限模式。
        diff: 完整统一差异，明确无尾换行。
        spans: 各次替换在当时候选 bytes 中的位置，用于目标足迹映射。
        syntax: 候选的语法证据，独立于写入状态。
    """

    path: str
    resolved_path: str
    original: bytes
    candidate: bytes
    original_sha256: str
    candidate_sha256: str
    encoding: str
    permission_mode: int
    diff: str
    spans: tuple[EditSpan, ...]
    syntax: Check


class FindingTarget(Record):
    """稳定的扫描句柄、原始身份及该扫描文件的完整指纹。"""

    handle: str
    identity: finding.FindingIdentity
    source_sha256: str


class Proposal(Record):
    """同一任务和问题的一项完整修复；id 每次修订均更新。

    Attributes:
        id: 供审核绑定的提案身份，不复用旧修订身份。
        task_id: 任务身份；issue_id 是本项修复的问题身份。
        rationale: 业务依据、修改理由与限制。
        files: 必需的全部文件准备结果，不能部分入审。
        target: 可选扫描目标；用户提出的缺陷可以没有扫描绑定。
    """

    id: str
    task_id: str
    issue_id: str
    rationale: str
    files: tuple[PreparedFile, ...] = pydantic.Field(min_length=1)
    target: FindingTarget | None = None


class Assessment(Record):
    """绑定当前问题的不修改、证据不足或执行失败结论及具体依据。"""

    issue_id: str
    outcome: Literal["no_change", "insufficient_evidence", "execution_failure"]
    reason: str
    finding_handle: str | None = None


class FileProgress(Record):
    """逐文件已确认的写入事实；当前 bytes 对照通过 message 说明。"""

    path: str
    status: Literal["not_written", "written", "uncertain"]
    message: str = ""


class RepairState(Record):
    """一项活动任务至多一项待审提案；写入事实与审核状态分别保存。

    Attributes:
        version: 每次持久化业务提交递增，拒绝过期运行或人工决定。
        task_id: 当前任务身份；issue_id 绑定本项修复，不因同文件而复用。
        goal: 已明确的当前修复目标。
        write_paths: 已由用户批准的确切文件。
        requested_paths: 等待用户 allow 的文件，不构成写权限。
        constraints: 仅当前任务有效的用户要求。
        status: 调查、判断、审核与写入生命周期。
        proposal: 当前完整提案，准备失败不会替换它。
        progress: 逐文件写入事实，中断后不能据此自动重放。
        assessment: 当前明确的问题判断，不从缺少提案推断失败。
        checks: 应用后的独立验证结论。
        last_apply_error: 当前提案最近一次预检或写入中断的主因。
    """

    version: pydantic.StrictInt = pydantic.Field(default=0, ge=0)
    task_id: str | None = None
    issue_id: str | None = None
    goal: str = ""
    write_paths: tuple[str, ...] = ()
    requested_paths: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    status: Literal[
        "idle",
        "investigating",
        "assessed",
        "pending",
        "applying",
        "interrupted",
        "applied",
    ] = "idle"
    proposal: Proposal | None = None
    progress: tuple[FileProgress, ...] = ()
    assessment: Assessment | None = None
    checks: tuple[Check, ...] = ()
    last_apply_error: str | None = None

    @pydantic.model_validator(mode="after")
    def validate_binding(self) -> RepairState:
        """拒绝失去任务身份、提案身份或写入记录的持久状态。"""
        if self.last_apply_error is not None and (
            not self.last_apply_error.strip() or self.proposal is None
        ):
            raise ValueError("应用失败原因必须非空且绑定当前提案。")
        if self.status == "idle" and (
            self.task_id or self.proposal or self.write_paths
        ):
            raise ValueError("空闲状态不能保留活动任务、提案或写权限。")
        if self.status != "idle" and (not self.task_id or not self.issue_id):
            raise ValueError("活动任务缺少身份。")
        if self.status in {"investigating", "assessed"} and (
            self.proposal or self.progress
        ):
            raise ValueError("调查或不修改结论不能夹带待审提案或写入进度。")
        if self.status == "pending" and self.progress:
            raise ValueError("待审提案不能已经包含写入进度。")
        if (
            self.assessment is not None
            and self.assessment.issue_id != self.issue_id
        ):
            raise ValueError("问题判断与当前问题身份不一致。")
        if self.status == "assessed" and (
            self.assessment is None or self.assessment.outcome != "no_change"
        ):
            raise ValueError("不修改完成状态缺少对应结论。")
        if (
            self.status in {"pending", "applying", "interrupted", "applied"}
            and self.proposal is None
        ):
            raise ValueError("审核或写入状态缺少完整提案。")
        if self.proposal is not None and (
            self.proposal.task_id != self.task_id
            or self.proposal.issue_id != self.issue_id
        ):
            raise ValueError("提案与任务或问题身份不一致。")
        if (
            self.proposal is not None
            and self.status in {"applying", "interrupted", "applied"}
            and (
                tuple(item.path for item in self.progress)
                != tuple(item.path for item in self.proposal.files)
            )
        ):
            raise ValueError("写入进度与提案文件不一致。")
        if self.status == "applied" and any(
            item.status != "written" for item in self.progress
        ):
            raise ValueError("整体 applied 必须有全部文件的成功写入事实。")
        return self


class ApplyResult(Record):
    """当前应用事实与验证证据，不把部分写入描述为整体成功。"""

    status: Literal["applied", "rejected"]
    message: str
    progress: tuple[FileProgress, ...] = ()
    checks: tuple[Check, ...] = ()


class RepairError(ValueError):
    """用户、模型或外部源码导致的可解释业务拒绝。"""


class StateConflict(RepairError):
    """审核选择或运行结果已落后于磁盘中的任务版本。"""
