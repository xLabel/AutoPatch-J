"""本项目统一会话的选择与保存，消息存储由官方组件负责。"""

import asyncio
from collections.abc import Coroutine
import pathlib

from pydantic_ai import messages
from pydantic_ai_harness.step_persistence import conversations

from autopatch_j import error_messages


async def _settle_storage[ValueT](
    operation: Coroutine[None, None, ValueT], *, propagate_cancellation: bool
) -> ValueT:
    """SDK 后台存储结束后才释放调用方业务锁，不能遗留晚到写入。"""
    task = asyncio.create_task(operation)
    cancelled = False
    while True:
        try:
            value = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        else:
            if cancelled and propagate_cancellation:
                raise asyncio.CancelledError
            return value


class ProjectConversations:
    """选择本项目会话，问答、取证与修复共用原生 SDK 消息。

    调用方在项目业务锁内执行 load/save/new，协调会话选择和任务提交。
    读取收到取消时先完成后台存储再传播取消；最终保存和切换完成后返回事实。
    """

    def __init__(self, repo_root: pathlib.Path) -> None:
        self.workspace = str(repo_root.resolve())
        self.store = conversations.SqliteConversationStore(
            database=repo_root / ".autopatch-j" / "conversations.sqlite3"
        )

    async def load(self) -> conversations.SavedConversation:
        """恢复最近未归档会话；首次运行持久化空会话。"""
        return await _settle_storage(self._load(), propagate_cancellation=True)

    async def _load(self) -> conversations.SavedConversation:
        current = await self._active()
        if current is None:
            current = await self.store.save(
                summary=conversations.ConversationSummary(
                    workspace=self.workspace
                ),
                messages=[],
            )
        return await self.store.get(conversation_id=current.id)

    async def _active(self) -> conversations.ConversationSummary | None:
        candidates = await self.store.listing(archived=False)
        return next(
            (item for item in candidates if item.workspace == self.workspace),
            None,
        )

    async def save(
        self,
        summary: conversations.ConversationSummary,
        history: list[messages.ModelMessage],
    ) -> None:
        """在调用方业务锁内核对活动身份，再 CAS 保存原生 head。"""
        await _settle_storage(
            self._save(summary, history), propagate_cancellation=False
        )

    async def _save(
        self,
        summary: conversations.ConversationSummary,
        history: list[messages.ModelMessage],
    ) -> None:
        target = await self.store.get(conversation_id=summary.id)
        active = await self._active()
        if (
            target.summary.archived_at is not None
            or active is None
            or active.id != summary.id
        ):
            raise conversations.ConversationConflict(
                error_messages.CONVERSATION_SWITCHED
            )
        await self.store.save(summary=summary, messages=history)

    async def new(self) -> str:
        """归档旧会话并创建空会话，长期笔记不受影响。"""
        return await _settle_storage(self._new(), propagate_cancellation=False)

    async def _new(self) -> str:
        current = await self._load()
        await self.store.set_archived(
            conversation_id=current.summary.id, archived=True
        )
        created = await self.store.save(
            summary=conversations.ConversationSummary(workspace=self.workspace),
            messages=[],
        )
        return created.id
