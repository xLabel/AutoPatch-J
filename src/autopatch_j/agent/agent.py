"""统一项目 Agent，SDK 拥有模型循环、工具纠错、流处理和上下文摘要。"""

import asyncio
import dataclasses

import pydantic_ai
from pydantic_ai import exceptions
from pydantic_ai import messages
from pydantic_ai import usage
from pydantic_ai_harness import compaction
from pydantic_ai_harness import detect_loops
from pydantic_ai_harness import memory as sdk_memory
from pydantic_ai_harness.step_persistence import (
    conversations as sdk_conversations,
)

from autopatch_j import error_messages
from autopatch_j.agent import callbacks
from autopatch_j.agent import capabilities
from autopatch_j.agent import instructions
from autopatch_j.agent import result
from autopatch_j.agent import run_context
from autopatch_j.core import conversations
from autopatch_j.core import memory
from autopatch_j.core.repair import models
from autopatch_j.core.repair import store
from autopatch_j.llm import policy
from autopatch_j.llm import runtime
from autopatch_j.tools import sdk


@dataclasses.dataclass(frozen=True)
class _ExecutedRun:
    """正常或触限的原生消息，待业务版本核对后才可持久化。"""

    outcome: result.AgentRunResult
    history: list[messages.ModelMessage]


class Agent:
    """一次运行准备业务候选，正常或受限结束后才通过业务入口提交。"""

    def __init__(
        self,
        dependencies: run_context.AgentRunContext,
        model_runtime: runtime.ModelRuntime,
        conversation_store: conversations.ProjectConversations,
        memory_store: memory.ProjectMemory,
        *,
        request_limit: int | None,
    ) -> None:
        self.run_context_template = dependencies
        self.model_runtime = model_runtime
        self.conversations = conversation_store
        self.memory = memory_store
        self.request_limit = request_limit

    @property
    def model_label(self) -> str:
        """当前有效配置的模型名。"""
        return self.model_runtime.profile.model

    def run(
        self, user_text: str, *, output: callbacks.AgentCallbacks | None = None
    ) -> result.AgentRunResult:
        """模型失败或取消不提交本轮提案；过期结果不能覆盖人工决定。"""
        current = dataclasses.replace(
            self.run_context_template, user_text=user_text, notices=[]
        )
        output_callbacks = output or callbacks.AgentCallbacks()
        try:
            outcome = self.model_runtime.run(
                self._run(current, output_callbacks)
            )
        except asyncio.CancelledError:
            outcome = result.AgentRunResult(
                "用户已取消，本轮未提交暂存提案，原任务和提案保留。",
                result.RunStatus.CANCELLED,
            )
        for notice in current.notices:
            if output_callbacks.on_observation:
                output_callbacks.on_observation(notice, notice)
        return outcome

    async def _run(
        self,
        current: run_context.AgentRunContext,
        output: callbacks.AgentCallbacks,
    ) -> result.AgentRunResult:
        """短存储操作共享业务锁，模型运行和取证留在锁外。"""
        with current.repair.store.locked() as initial:
            current.state = initial
            current.base_state = initial
            saved = await self.conversations.load()
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        executed = await self._execute(current, output, saved)
        committed = False
        try:
            with current.repair.store.locked() as latest:
                store.require_current(latest, initial)
                if current.state != initial:
                    current.repair.commit(initial, current.state)
                    committed = True
                await self._save_history(saved, executed)
        except Exception as exc:
            exc.add_note(executed.outcome.final_answer)
            exc.add_note(error_messages.history_not_saved(committed=committed))
            raise
        if executed.outcome.status is result.RunStatus.LIMITED:
            return dataclasses.replace(
                executed.outcome,
                final_answer=executed.outcome.final_answer
                + "会话进展已保存，可输入继续。源码仍须人工 apply。",
            )
        return executed.outcome

    async def _save_history(
        self,
        saved: sdk_conversations.SavedConversation,
        executed: _ExecutedRun,
    ) -> None:
        """在业务锁内保存原生历史；失败交上层报告实际提交事实。"""
        summary = dataclasses.replace(
            saved.summary,
            outcome=(
                "completed"
                if executed.outcome.status is result.RunStatus.COMPLETED
                else "ready"
            ),
        )
        try:
            await self.conversations.save(summary, executed.history)
        except sdk_conversations.ConversationConflict as exc:
            raise models.StateConflict(
                error_messages.CONVERSATION_CONFLICT
            ) from exc
        except Exception as exc:
            exc.add_note(error_messages.answer_not_saved("存储操作未完成"))
            raise

    async def _execute(
        self,
        current: run_context.AgentRunContext,
        output: callbacks.AgentCallbacks,
        saved: sdk_conversations.SavedConversation,
    ) -> _ExecutedRun:
        """执行一次原生运行，返回候选与消息；业务和会话由外层依次提交。"""
        profile = self.model_runtime.profile
        history = saved.messages
        settings = policy.request_settings(profile)
        summary = compaction.SummarizingCompaction[run_context.AgentRunContext](
            max_tokens=int(profile.input_capacity * 0.85),
            model_settings=settings,
            summarization_capabilities=[policy.FinishGuard()],
            tool_return_max_chars=None,
        )
        notebook = sdk_memory.Memory[run_context.AgentRunContext](
            self.memory.store,
            namespace="project",
            agent_name="main",
            injection_errors="raise",
        )
        guides = current.source_reader.read_guidance()
        project_guidance = "\n\n".join(
            (
                f"## 项目规范来源 {guide.path}（范围 {guide.start_line}-"
                f"{guide.end_line}，截断={guide.truncated}）\n{guide.code}"
            )
            for guide in guides
        )

        native: pydantic_ai.Agent[run_context.AgentRunContext, str] = (
            pydantic_ai.Agent(
                self.model_runtime.model,
                instructions=instructions.SYSTEM + "\n" + project_guidance,
                deps_type=run_context.AgentRunContext,
                toolsets=[sdk.build_toolset(current)],
                capabilities=[
                    capabilities.AgentRunPolicy(output),
                    summary,
                    notebook,
                    detect_loops.DetectLoops(),
                    policy.FinishGuard(),
                ],
                model_settings=settings,
            )
        )
        # 必须在运行协程内捕获；复用的 Runner 不会继承同步调用方的新 ContextVar。
        with pydantic_ai.capture_run_messages() as captured:
            try:
                completed = await native.run(
                    compaction.pin(current.user_text).content,
                    deps=current,
                    message_history=history,
                    conversation_id=saved.summary.id,
                    usage_limits=usage.UsageLimits(
                        request_limit=self.request_limit
                    ),
                    event_stream_handler=output.handle_stream_events,
                )
                outcome = result.AgentRunResult(completed.output)
            except (
                exceptions.UsageLimitExceeded,
                detect_loops.LoopDetectedError,
            ):
                outcome = result.AgentRunResult(
                    "本轮请求额度或循环保护已触发，任务未完整完成。",
                    result.RunStatus.LIMITED,
                )
        return _ExecutedRun(
            outcome,
            _unpin_history(captured),
        )


def _unpin_history(
    history: list[messages.ModelMessage],
) -> list[messages.ModelMessage]:
    """仅当前请求受 pin 保护；旧用户轮次仍可在将来压缩。"""
    unpinned_history = []
    for message in history:
        if isinstance(message, messages.ModelRequest):
            parts = []
            for part in message.parts:
                if compaction.is_pinned(part):
                    assert isinstance(
                        part, messages.UserPromptPart
                    ) and not isinstance(part.content, str)
                    part = dataclasses.replace(
                        part,
                        content=[
                            (
                                dataclasses.replace(item, metadata=None)
                                if isinstance(item, messages.TextContent)
                                else item
                            )
                            for item in part.content
                        ],
                    )
                parts.append(part)
            message = dataclasses.replace(message, parts=parts)
        unpinned_history.append(message)
    return unpinned_history
