"""修复事实的只读投影；历史执行证据与当前源码一致性分别表达。"""

import pathlib
from typing import Literal

from autopatch_j import error_messages
from autopatch_j.core import errors
from autopatch_j.core.project import repo_path
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models


class SourceEvidence(models.Record):
    """源码指纹核对不改变曾经写入、检查或失败的事实。"""

    path: str
    expected_sha256: str
    current_sha256: str
    status: Literal["current", "changed"]
    message: str


class PreparedFileStatus(models.Record):
    """候选语法证据不是应用后的验证结果。"""

    path: str
    syntax: models.Check


class RepairStatus(models.Record):
    """可注入模型的业务事实，不包含源码 bytes 或重复 diff。"""

    base_version: int
    pending_commit: bool
    task_id: str | None
    issue_id: str | None
    goal: str
    status: str
    approved_write_paths: tuple[str, ...]
    scope_awaiting_human_approval: tuple[str, ...]
    temporary_requirements: tuple[str, ...]
    current_assessment: models.Assessment | None
    proposal_id: str | None
    finding_handle: str | None
    progress: tuple[models.FileProgress, ...]
    prepared_files: tuple[PreparedFileStatus, ...]
    checks: tuple[models.Check, ...]
    verification_notice: str
    last_apply_error: str | None
    source_evidence: tuple[SourceEvidence, ...]


def describe(
    root: pathlib.Path,
    state: models.RepairState,
    baseline: models.RepairState,
) -> RepairStatus:
    """投影运行工作副本；调用方负责先核对持久基线。"""
    proposal = state.proposal
    expected: dict[str, str] = {}
    prepared = ()
    if proposal is not None:
        written = {
            item.path for item in state.progress if item.status == "written"
        }
        prepared = tuple(
            PreparedFileStatus(path=item.path, syntax=item.syntax)
            for item in proposal.files
        )
        expected = {
            item.path: (
                item.candidate_sha256
                if state.status == "applied" or item.path in written
                else item.original_sha256
            )
            for item in proposal.files
        }
        if proposal.target is not None:
            expected.setdefault(
                proposal.target.identity.path, proposal.target.source_sha256
            )
    try:
        evidence = tuple(
            _source_evidence(root, path, fingerprint)
            for path, fingerprint in sorted(expected.items())
        )
    except errors.OperationError as exc:
        exc.add_note(
            error_messages.saved_state(
                state.status,
                error_messages.write_progress(
                    [
                        row.path
                        for row in state.progress
                        if row.status == "written"
                    ],
                    [
                        row.path
                        for row in state.progress
                        if row.status == "not_written"
                    ],
                    [
                        row.path
                        for row in state.progress
                        if row.status == "uncertain"
                    ],
                ),
            )
        )
        raise
    notice = (
        "以下是该提案当时保存的验证证据，不证明业务正确。"
        if state.checks
        else "尚无已保存的应用后验证结果，不能推断验证通过。"
    )
    if any(item.status != "current" for item in evidence):
        notice += " 当前源码与证据不一致或无法确认，旧结论不能作为当前通过。"
    return RepairStatus(
        base_version=baseline.version,
        pending_commit=state != baseline,
        task_id=state.task_id,
        issue_id=state.issue_id,
        goal=state.goal,
        status=state.status,
        approved_write_paths=state.write_paths,
        scope_awaiting_human_approval=state.requested_paths,
        temporary_requirements=state.constraints,
        current_assessment=state.assessment,
        proposal_id=proposal.id if proposal else None,
        finding_handle=(
            proposal.target.handle if proposal and proposal.target else None
        ),
        progress=state.progress,
        prepared_files=prepared,
        checks=state.checks,
        verification_notice=notice,
        last_apply_error=state.last_apply_error,
        source_evidence=evidence,
    )


def _source_evidence(
    root: pathlib.Path, path: str, expected: str
) -> SourceEvidence:
    try:
        current = files.digest(
            repo_path.resolve_source_path(root, path).read_bytes()
        )
    except (OSError, repo_path.UnsafeRepoPathError) as exc:
        raise errors.OperationError(
            error_messages.source_unavailable(
                path,
                (
                    error_messages.FILE_MISSING
                    if isinstance(exc, FileNotFoundError)
                    else (
                        error_messages.PERMISSION_DENIED
                        if isinstance(exc, PermissionError)
                        else str(exc)
                    )
                ),
            )
        ) from exc
    return SourceEvidence(
        path=path,
        expected_sha256=expected,
        current_sha256=current,
        status="current" if current == expected else "changed",
        message=(
            "核对时源码指纹一致。" if current == expected else "源码已变化。"
        ),
    )
