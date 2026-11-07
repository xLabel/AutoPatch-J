"""任务与提案业务的唯一提交入口，协调审核、预检、逐文件写入和恢复证据。"""

import pathlib
import uuid

from autopatch_j import error_messages
from autopatch_j.core import errors
from autopatch_j.core.project import repo_path
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models
from autopatch_j.core.repair import store
from autopatch_j.core.repair import verification
from autopatch_j.scanners import contracts


class RepairService:
    """模型运行只准备候选状态；人工动作和最终提交在锁内核对原版本。"""

    def __init__(
        self, root: pathlib.Path, scanner: contracts.StaticScanner | None = None
    ) -> None:
        self.root = root.resolve()
        self.store = store.RepairStore(self.root)
        self.scanner = scanner

    def normalize_paths(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        """写权限绑定确切的真实文件，不因目录、别名或 @ 引用自动扩大。"""
        result = []
        for name in paths:
            path = repo_path.resolve_source_path(self.root, name)
            if not path.is_file():
                raise models.RepairError(
                    error_messages.scope_file_required(name)
                )
            relative = path.relative_to(self.root).as_posix()
            if relative in result:
                raise models.RepairError(error_messages.duplicate_file(name))
            result.append(relative)
        return tuple(result)

    def start(
        self, state: models.RepairState, goal: str, paths: tuple[str, ...]
    ) -> models.RepairState:
        """准备新任务及待用户确认的写范围，不应用源码修改。"""
        if state.status not in {"idle", "applied", "assessed"}:
            raise models.RepairError(error_messages.TASK_ACTIVE)
        if not goal.strip():
            raise models.RepairError(
                error_messages.empty_requirement("修复目标")
            )
        return models.RepairState(
            version=state.version,
            task_id=uuid.uuid4().hex,
            issue_id=uuid.uuid4().hex,
            goal=goal.strip(),
            requested_paths=self.normalize_paths(paths),
            status="investigating",
        )

    def request_scope(
        self, state: models.RepairState, paths: tuple[str, ...]
    ) -> models.RepairState:
        """返回追加写权限申请的候选状态，已有授权不变。"""
        if state.status not in {"investigating", "pending"}:
            raise models.RepairError(error_messages.TASK_NOT_EDITABLE)
        if not paths:
            raise models.RepairError(error_messages.EMPTY_SCOPE)
        normalized = self.normalize_paths(paths)
        additions = tuple(
            path for path in normalized if path not in state.write_paths
        )
        requested = tuple(dict.fromkeys((*state.requested_paths, *additions)))
        if requested == state.requested_paths:
            return state
        return store.changed(state, requested_paths=requested)

    def approve_scope(self, expected: models.RepairState) -> models.RepairState:
        """仅人工入口批准已展示的确切范围；不修改已有提案。"""
        with self.store.locked() as current:
            store.require_current(current, expected)
            if not current.requested_paths or current.status not in {
                "investigating",
                "pending",
            }:
                raise models.RepairError(error_messages.NO_SCOPE)
            updated = store.changed(
                current,
                version=current.version + 1,
                write_paths=tuple(
                    dict.fromkeys(
                        (*current.write_paths, *current.requested_paths)
                    )
                ),
                requested_paths=(),
            )
            self.store.save(updated)
            return updated

    def prepare(
        self,
        state: models.RepairState,
        edits: tuple[models.FileEdit, ...],
        rationale: str,
        target: models.FindingTarget | None = None,
    ) -> models.RepairState:
        """完整准备后一次替换提案，任何失败均保留原候选和原待审提案。"""
        if state.status not in {"investigating", "pending"}:
            raise models.RepairError(error_messages.TASK_NOT_EDITABLE)
        if not edits or not rationale.strip():
            raise models.RepairError(error_messages.INCOMPLETE_PROPOSAL)
        paths = self.normalize_paths(tuple(item.path for item in edits))
        unauthorized = [path for path in paths if path not in state.write_paths]
        if unauthorized:
            raise models.RepairError(
                error_messages.unauthorized_file("、".join(unauthorized))
            )
        if state.proposal is not None and state.proposal.target != target:
            raise models.RepairError(error_messages.TARGET_CHANGED)
        prepared = tuple(files.prepare_file(self.root, edit) for edit in edits)
        if target is not None:
            path = repo_path.resolve_source_path(
                self.root, target.identity.path
            )
            if files.digest(path.read_bytes()) != target.source_sha256:
                raise models.RepairError(error_messages.STALE_SCAN)
        proposal = models.Proposal(
            id=uuid.uuid4().hex,
            task_id=state.task_id or "",
            issue_id=state.issue_id or "",
            rationale=rationale,
            files=prepared,
            target=target,
        )
        return store.changed(
            state,
            proposal=proposal,
            status="pending",
            progress=(),
            checks=(),
            assessment=None,
            last_apply_error=None,
        )

    def assess(
        self, state: models.RepairState, assessment: models.Assessment
    ) -> models.RepairState:
        """返回带明确问题结论的状态；只有不修改结论结束该问题。"""
        if (
            state.status != "investigating"
            or state.issue_id != assessment.issue_id
        ):
            raise models.RepairError(error_messages.INVALID_ASSESSMENT)
        return store.changed(
            state,
            assessment=assessment,
            status=(
                "assessed"
                if assessment.outcome == "no_change"
                else "investigating"
            ),
            constraints=(
                () if assessment.outcome == "no_change" else state.constraints
            ),
            requested_paths=(
                ()
                if assessment.outcome == "no_change"
                else state.requested_paths
            ),
        )

    def commit(
        self, expected: models.RepairState, candidate: models.RepairState
    ) -> models.RepairState:
        """模型完成或受限结束后提交，拒绝落后运行覆盖并发人工操作。"""
        with self.store.locked() as current:
            store.require_current(current, expected)
            updated = store.changed(candidate, version=current.version + 1)
            self.store.save(updated)
            return updated

    def end(self, expected: models.RepairState) -> None:
        """显式结束任务；不回滚或删除已经写入的源码。"""
        with self.store.locked() as current:
            store.require_current(current, expected)
            self.store.save(models.RepairState(version=current.version + 1))

    def update_task_requirements(
        self,
        state: models.RepairState,
        additions: tuple[str, ...],
        removals: tuple[str, ...],
        user_text: str,
    ) -> models.RepairState:
        """原子修订当前有效要求；新增摘自用户原文，撤回不得伪造已有项。"""
        if state.status not in {"investigating", "pending"}:
            raise models.RepairError(error_messages.NO_ACTIVE_TASK)
        added = tuple(dict.fromkeys(item.strip() for item in additions))
        removed = frozenset(item.strip() for item in removals)
        if "" in added or "" in removed:
            raise models.RepairError(
                error_messages.empty_requirement("任务要求")
            )
        unquoted = tuple(item for item in added if item not in user_text)
        if unquoted:
            raise models.RepairError(
                error_messages.unquoted_requirement("、".join(unquoted))
            )
        if removed.difference(state.constraints):
            raise models.RepairError(error_messages.UNKNOWN_REQUIREMENT)
        if removed.intersection(added):
            raise models.RepairError(error_messages.OVERLAPPING_REQUIREMENTS)
        requirements = tuple(
            dict.fromkeys(
                (
                    *[
                        item
                        for item in state.constraints
                        if item not in removed
                    ],
                    *added,
                )
            )
        )
        if requirements == state.constraints:
            return state
        return store.changed(state, constraints=requirements)

    def apply(self, expected: models.RepairState) -> models.ApplyResult:
        """完整预检通过才记 applying；逐文件保存进度，首次故障即停止。"""
        with self.store.locked() as current:
            store.require_current(current, expected)
            if current.status != "pending" or current.proposal is None:
                raise models.RepairError(
                    error_messages.INTERRUPTED_APPLY
                    if current.status in {"applying", "interrupted"}
                    else error_messages.NO_PROPOSAL
                )
            proposal = current.proposal
            try:
                for item in proposal.files:
                    self._preflight(item)
                if proposal.target is not None:
                    path = repo_path.resolve_source_path(
                        self.root, proposal.target.identity.path
                    )
                    if (
                        files.digest(path.read_bytes())
                        != proposal.target.source_sha256
                    ):
                        raise models.RepairError("扫描目标已变化。")
            except (
                OSError,
                models.RepairError,
                repo_path.UnsafeRepoPathError,
            ) as exc:
                message = f"完整预检失败，未写任何文件：{exc}"
                rejected = store.changed(
                    current,
                    version=current.version + 1,
                    last_apply_error=message,
                )
                try:
                    self.store.save(rejected)
                except OSError as save_error:
                    raise errors.OperationError(
                        message + f"；拒绝原因未保存：{save_error}"
                    ) from exc
                if isinstance(exc, OSError):
                    raise errors.OperationError(message) from exc
                return models.ApplyResult(
                    status="rejected",
                    message=message,
                )
            progress = tuple(
                models.FileProgress(path=item.path, status="not_written")
                for item in proposal.files
            )
            current = store.changed(
                current,
                version=current.version + 1,
                status="applying",
                progress=progress,
                last_apply_error=None,
            )
            self.store.save(current)  # 失败则不得写源码。
            for index, item in enumerate(proposal.files):
                write_started = False
                try:
                    self._preflight(item)
                    path = repo_path.resolve_source_path(self.root, item.path)
                    write_started = True
                    files.atomic_write(
                        path,
                        item.candidate,
                        expected=item.original,
                        mode=item.permission_mode,
                    )
                except (
                    OSError,
                    models.RepairError,
                    repo_path.UnsafeRepoPathError,
                ) as exc:
                    if write_started:
                        progress = list(current.progress)
                        progress[index] = models.FileProgress(
                            path=item.path, status="uncertain"
                        )
                        current = store.changed(
                            current, progress=tuple(progress)
                        )
                    self._interrupted(
                        current,
                        error_messages.apply_interrupted(item.path, str(exc)),
                        exc,
                    )
                updated_progress = list(current.progress)
                updated_progress[index] = models.FileProgress(
                    path=item.path, status="written"
                )
                current = store.changed(
                    current, progress=tuple(updated_progress)
                )
                try:
                    self.store.save(current)
                except OSError as exc:
                    raise errors.OperationError(
                        error_messages.progress_not_saved(item.path)
                        + "\n"
                        + self._progress_message(current)
                    ) from exc
            current = store.changed(
                current,
                status="applied",
                version=current.version + 1,
                constraints=(),
                requested_paths=(),
            )
            try:
                self.store.save(current)
            except OSError as exc:
                raise errors.OperationError(
                    error_messages.APPLIED_STATE_NOT_SAVED
                    + "\n"
                    + self._progress_message(current)
                ) from exc
        # 源码及 applied 事实已提交；复扫在锁外执行，避免扫描期间占用业务写锁。
        try:
            checks = verification.verify(self.root, proposal, self.scanner)
        except (OSError, RuntimeError, ValueError) as exc:
            message = (
                str(exc)
                if isinstance(exc, errors.OperationError)
                else error_messages.failed("源码已应用，但复扫", "程序内部错误")
            )
            try:
                with self.store.locked() as latest:
                    store.require_current(latest, current)
                    self.store.save(
                        store.changed(
                            current,
                            last_apply_error=message,
                            version=current.version + 1,
                        )
                    )
            except OSError, models.StateConflict, errors.OperationError:
                message += "\n" + error_messages.FAILURE_NOT_SAVED
            raise errors.OperationError(message) from exc
        try:
            with self.store.locked() as latest:
                store.require_current(latest, current)
                self.store.save(
                    store.changed(
                        current, checks=checks, version=current.version + 1
                    )
                )
        except models.StateConflict as exc:
            raise models.StateConflict(
                error_messages.APPLIED_CHECKS_CONFLICT
            ) from exc
        except (OSError, errors.OperationError) as exc:
            raise errors.OperationError(
                error_messages.failed("源码已应用，但验证结果保存", str(exc))
            ) from exc
        return models.ApplyResult(
            status="applied",
            message="完整提案已应用。",
            progress=current.progress,
            checks=checks,
        )

    def inspect(
        self, state: models.RepairState
    ) -> tuple[models.FileProgress, ...]:
        """重启或中断后按落盘证据显示事实，不恢复执行，不把相等 bytes 当作执行证明。"""
        if state.proposal is None:
            return ()
        observations = []
        for item in state.proposal.files:
            raw = None
            try:
                path = repo_path.resolve_source_path(self.root, item.path)
                raw = path.read_bytes()
                if raw == item.candidate:
                    message = "当前 bytes 等于提案结果；需结合持久进度核查。"
                elif raw == item.original:
                    message = "当前 bytes 等于原始基线。"
                else:
                    message = "当前 bytes 既非基线也非结果，可能有外部修改。"
            except (
                OSError,
                models.RepairError,
                repo_path.UnsafeRepoPathError,
            ) as exc:
                raise errors.OperationError(
                    error_messages.source_unavailable(item.path, str(exc))
                    + "\n"
                    + self._progress_message(state)
                ) from exc
            recorded = next(
                (row for row in state.progress if row.path == item.path),
                models.FileProgress(path=item.path, status="uncertain"),
            )
            if recorded.status != "written" and raw != item.original:
                recorded = models.FileProgress(
                    path=item.path, status="uncertain"
                )
            observations.append(
                recorded.model_copy(update={"message": message})
            )
        return tuple(observations)

    def _preflight(self, item: models.PreparedFile) -> None:
        path = repo_path.resolve_source_path(self.root, item.path)
        if (
            str(path) != item.resolved_path
            or path.read_bytes() != item.original
        ):
            raise models.RepairError(error_messages.preflight_failed(item.path))
        if (
            files.digest(item.original) != item.original_sha256
            or files.digest(item.candidate) != item.candidate_sha256
        ):
            raise models.RepairError(error_messages.proposal_invalid(item.path))

    def _interrupted(
        self, state: models.RepairState, message: str, cause: Exception
    ) -> None:
        interrupted = store.changed(
            state, status="interrupted", last_apply_error=message
        )
        try:
            self.store.save(interrupted)
        except OSError:
            message += "\n" + error_messages.FAILURE_NOT_SAVED
        raise errors.OperationError(
            message
            + "\n"
            + self._progress_message(state)
            + "\n"
            + error_messages.NOT_ROLLED_BACK
        ) from cause

    @staticmethod
    def _progress_message(state: models.RepairState) -> str:
        return error_messages.write_progress(
            [row.path for row in state.progress if row.status == "written"],
            [row.path for row in state.progress if row.status == "not_written"],
            [row.path for row in state.progress if row.status == "uncertain"],
        )
