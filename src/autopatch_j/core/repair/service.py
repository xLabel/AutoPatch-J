"""任务与提案业务的唯一提交入口，协调审核、预检、逐文件写入和恢复证据。"""

import pathlib
import uuid

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
                raise models.RepairError(f"写范围必须明确到已有文件：{name}")
            relative = path.relative_to(self.root).as_posix()
            if relative in result:
                raise models.RepairError(f"重复文件或别名：{name}")
            result.append(relative)
        return tuple(result)

    def start(
        self, state: models.RepairState, goal: str, paths: tuple[str, ...]
    ) -> models.RepairState:
        """准备新任务及待用户确认的写范围，不应用源码修改。"""
        if state.status not in {"idle", "applied", "assessed"}:
            raise models.RepairError(
                "当前任务尚未结束。请继续处理，或由用户明确 discard/abort/new 后再开始另一项任务。"
            )
        if not goal.strip():
            raise models.RepairError("修复目标不能为空。")
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
            raise models.RepairError("当前任务不接受范围调整。")
        if not paths:
            raise models.RepairError("写范围申请必须包含至少一个确切文件。")
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
                raise models.RepairError("没有待确认的写范围。")
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
            raise models.RepairError(
                "请先明确当前修复任务；应用中或中断的任务不能修订。"
            )
        if not edits or not rationale.strip():
            raise models.RepairError("提案需要文件修改和具体理由。")
        paths = self.normalize_paths(tuple(item.path for item in edits))
        if any(path not in state.write_paths for path in paths):
            raise models.RepairError(
                "提案包含未授权的写路径。请先 request_write_scope 并等待用户 allow；读取文件不表示允许修改。"
            )
        if state.proposal is not None and state.proposal.target != target:
            raise models.RepairError(
                "修订不能更换当前问题或 finding 绑定；请先明确结束当前提案。"
            )
        prepared = tuple(files.prepare_file(self.root, edit) for edit in edits)
        if target is not None:
            path = repo_path.resolve_source_path(
                self.root, target.identity.path
            )
            if files.digest(path.read_bytes()) != target.source_sha256:
                raise models.RepairError(
                    "扫描证据对应的源码已变化，请重新扫描并核查。"
                )
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
        )

    def assess(
        self, state: models.RepairState, assessment: models.Assessment
    ) -> models.RepairState:
        """返回带明确问题结论的状态；只有不修改结论结束该问题。"""
        if (
            state.status != "investigating"
            or state.issue_id != assessment.issue_id
        ):
            raise models.RepairError(
                "判断必须绑定当前调查中的问题，不能替代待审提案。"
            )
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

    def reset(self, expected: models.RepairState) -> None:
        """显式重置任务和可重建索引，保留锁、笔记、会话、导出和未知用户文件。"""
        with self.store.locked() as current:
            store.require_current(current, expected)
            self.store.save(models.RepairState(version=current.version + 1))
            index = self.store.directory / "index.db"
            if index.is_symlink():
                raise models.RepairError("索引文件是软链接，未清理。")
            index.unlink(missing_ok=True)

    def apply(self, expected: models.RepairState) -> models.ApplyResult:
        """完整预检通过才记 applying；逐文件保存进度，首次故障即停止。"""
        with self.store.locked() as current:
            store.require_current(current, expected)
            if current.status != "pending" or current.proposal is None:
                raise models.RepairError(
                    "仅当前完整待审提案可应用；中断状态须人工核查并结束，不能重放。"
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
            except (OSError, ValueError) as exc:
                return models.ApplyResult(
                    status="rejected",
                    message=f"完整预检失败，未写任何文件：{exc}",
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
            )
            self.store.save(current)  # 失败则不得写源码。
            for index, item in enumerate(proposal.files):
                try:
                    self._preflight(item)
                    path = repo_path.resolve_source_path(self.root, item.path)
                    files.atomic_write(
                        path,
                        item.candidate,
                        expected=item.original,
                        mode=item.permission_mode,
                    )
                except (OSError, ValueError) as exc:
                    return self._interrupted(current, f"写入停止：{exc}")
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
                    return models.ApplyResult(
                        status="interrupted",
                        message=(
                            f"源码已写入 {item.path}，进度保存失败；"
                            f"磁盘任务仍阻断，须核查后结束：{exc}"
                        ),
                        progress=current.progress,
                    )
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
                return models.ApplyResult(
                    status="interrupted",
                    message=f"所有源码已写入，最终审核状态保存失败；未执行验证：{exc}",
                    progress=current.progress,
                )
        # 源码及 applied 事实已提交；复扫在锁外执行，避免扫描期间占用业务写锁。
        checks = verification.verify(self.root, proposal, self.scanner)
        try:
            with self.store.locked() as latest:
                store.require_current(latest, current)
                self.store.save(
                    store.changed(
                        current, checks=checks, version=current.version + 1
                    )
                )
        except (OSError, models.StateConflict) as exc:
            return models.ApplyResult(
                status="applied",
                message=f"源码已应用；验证结果未保存，未覆盖后续任务：{exc}",
                progress=current.progress,
                checks=checks,
            )
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
            except (OSError, ValueError) as exc:
                message = f"无法核查：{exc}"
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
            raise models.RepairError(
                f"{item.path} 路径或完整源码基线已变化，请重新准备并审核提案。"
            )
        if (
            files.digest(item.original) != item.original_sha256
            or files.digest(item.candidate) != item.candidate_sha256
        ):
            raise models.RepairError(f"{item.path} 提案 bytes 与指纹不一致。")

    def _interrupted(
        self, state: models.RepairState, message: str
    ) -> models.ApplyResult:
        interrupted = store.changed(state, status="interrupted")
        try:
            self.store.save(interrupted)
        except OSError:
            message += "；中断状态未保存，磁盘 applying 状态仍阻止重放。"
        return models.ApplyResult(
            status="interrupted",
            message=message + " 未自动回滚，请核查写入事实后 abort/new。",
            progress=state.progress,
        )
