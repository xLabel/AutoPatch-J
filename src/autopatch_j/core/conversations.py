"""本项目普通会话的选择与保存，消息存储由官方组件负责。"""

import pathlib

from pydantic_ai import messages
from pydantic_ai_harness.step_persistence import conversations


class ProjectConversations:
    """只选择本项目会话，不从修复请求拼装普通历史。"""

    def __init__(self, repo_root: pathlib.Path) -> None:
        self.workspace = str(repo_root.resolve())
        self.store = conversations.SqliteConversationStore(
            database=repo_root / ".autopatch-j" / "conversations.sqlite3"
        )
        self.current: conversations.ConversationSummary | None = None

    async def load(self) -> conversations.SavedConversation:
        """恢复最近未归档会话；首次运行持久化空会话。"""
        if self.current is None:
            candidates = await self.store.listing(archived=False)
            self.current = next(
                (
                    item
                    for item in candidates
                    if item.workspace == self.workspace
                ),
                None,
            )
            if self.current is None:
                self.current = await self.store.save(
                    summary=conversations.ConversationSummary(
                        workspace=self.workspace
                    ),
                    messages=[],
                )
        return await self.store.get(conversation_id=self.current.id)

    async def save(
        self,
        summary: conversations.ConversationSummary,
        history: list[messages.ModelMessage],
    ) -> None:
        """CAS 保存 SDK head；冲突交给调用方明确呈现。"""
        self.current = await self.store.save(summary=summary, messages=history)

    async def new(self) -> str:
        """归档旧会话并创建空会话，长期笔记不受影响。"""
        current = await self.load()
        await self.store.set_archived(
            conversation_id=current.summary.id, archived=True
        )
        self.current = await self.store.save(
            summary=conversations.ConversationSummary(workspace=self.workspace),
            messages=[],
        )
        return self.current.id
