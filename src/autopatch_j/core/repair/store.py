"""进程间互斥及修复状态的原子保存，不把损坏数据当作空任务。"""

from collections.abc import Generator
import contextlib
import pathlib

import filelock
import pydantic

from autopatch_j import config
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models


class RepairStore:
    """所有业务写操作共用同一个锁文件；锁文件从不随重置删除。"""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root.resolve()
        self.directory = config.get_project_state_dir(self.root)
        if self.directory.resolve() != self.root / ".autopatch-j":
            raise models.RepairError("项目状态目录不能重定向到其他位置。")
        self.path = self.directory / "repair.json"
        self.lock = filelock.FileLock(
            self.directory / "repair.lock", timeout=5, preserve_lock_file=True
        )

    @contextlib.contextmanager
    def locked(self) -> Generator[models.RepairState]:
        """锁内重新读取当前状态；不跨模型调用或人工等待持锁。"""
        with self.lock:
            yield self.load()

    def load(self) -> models.RepairState:
        """仅文件不存在表示初始状态；损坏或旧 schema 明确拒绝。"""
        try:
            payload = self.path.read_bytes()
        except FileNotFoundError:
            return models.RepairState()
        try:
            return models.RepairState.model_validate_json(payload)
        except pydantic.ValidationError as exc:
            raise models.RepairError(
                "修复状态损坏或 schema 无效，请保留文件并人工核查，未自动清空。"
            ) from exc

    def save(self, state: models.RepairState) -> None:
        """由业务入口持锁调用；保存同一领域对象，不创建第二套快照。"""
        files.atomic_write(
            self.path,
            state.model_dump_json(indent=2).encode("utf-8"),
            expected=None,
        )


def changed(state: models.RepairState, **updates: object) -> models.RepairState:
    """合并后再次验证不变量，版本号只由业务提交入口推进。"""
    return models.RepairState.model_validate({**state.model_dump(), **updates})


def require_current(
    current: models.RepairState, expected: models.RepairState
) -> None:
    """核对锁内当前版本与调用者已查看的任务及提案，失配拒绝提交。"""
    if (current.version, current.task_id, current.issue_id, current.status) != (
        expected.version,
        expected.task_id,
        expected.issue_id,
        expected.status,
    ) or current.proposal != expected.proposal:
        raise models.StateConflict(
            "任务或提案版本已变化，请重新查看；未覆盖其他运行的结果。"
        )
