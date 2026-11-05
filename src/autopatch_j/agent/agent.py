"""统一项目 Agent，SDK 拥有模型循环、工具纠错、流处理和上下文摘要。"""

import asyncio
from collections.abc import AsyncIterable
import dataclasses
import sqlite3
from typing import Unpack

from pydantic_ai import exceptions
from pydantic_ai import messages
from pydantic_ai.models import openai as openai_model
import pydantic_ai
from pydantic_ai import usage
from pydantic_ai_harness import compaction
from pydantic_ai_harness import detect_loops
from pydantic_ai_harness.step_persistence import (
    conversations as sdk_conversations,
)

from autopatch_j.agent import boundaries
from autopatch_j.agent import callbacks
from autopatch_j.agent import instructions
from autopatch_j.agent import result
from autopatch_j.agent import session
from autopatch_j import config
from autopatch_j.core import conversations
from autopatch_j.core import memory
from autopatch_j.core.repair import models
from autopatch_j.llm import diagnostics
from autopatch_j.llm import policy
from autopatch_j.llm import runtime
from autopatch_j.tools import sdk


@dataclasses.dataclass(frozen=True)
class _CompletedRun:
    """成功 SDK 结果及待提交会话，业务提交失败时不能保存成功历史。"""

    answer: str
    saved: sdk_conversations.SavedConversation | None
    history: list[messages.ModelMessage]


class Agent:
    """一次运行准备业务候选，正常或受限结束后才通过业务入口提交。"""

    def __init__(
        self,
        dependencies: session.AgentSession,
        model_runtime: runtime.ModelRuntime,
        conversation_store: conversations.ProjectConversations,
        memory_store: memory.ProjectMemory,
    ) -> None:
        self.session = dependencies
        self.model_runtime = model_runtime
        self.conversations = conversation_store
        self.memory = memory_store

    @property
    def model_label(self) -> str:
        """当前配置的模型名或缺少配置状态。"""
        return (
            self.model_runtime.profile.model
            if self.model_runtime.model
            else "LLM Not Configured"
        )

    def run(
        self, user_text: str, **output: Unpack[callbacks.OutputCallbacks]
    ) -> result.AgentRunResult:
        """模型失败或取消不提交本轮提案；过期结果不能覆盖人工决定。"""
        if self.model_runtime.model is None:
            return result.AgentRunResult(
                "LLM 配置缺失，请设置 AUTOPATCH_LLM_API_KEY。",
                result.RunStatus.FAILED,
            )
        initial = self.session.repair.store.load()
        current = dataclasses.replace(
            self.session, state=initial, user_text=user_text, notices=[]
        )
        output_callbacks = callbacks.AgentCallbacks(**output)
        finished = None
        try:
            finished = self.model_runtime.run(
                self._execute(current, output_callbacks)
            )
            outcome = result.AgentRunResult(finished.answer)
        except exceptions.UsageLimitExceeded, detect_loops.LoopDetectedError:
            outcome = result.AgentRunResult(
                "本轮请求额度或循环限制已触发，任务未完整完成；请审阅已有提案。",
                result.RunStatus.LIMITED,
            )
        except asyncio.CancelledError:
            outcome = result.AgentRunResult(
                "用户已取消，本轮未提交暂存提案，原任务和提案保留。",
                result.RunStatus.CANCELLED,
            )
        except (
            exceptions.ModelAPIError,
            exceptions.UnexpectedModelBehavior,
            policy.ModelFailure,
        ) as exc:
            detail = (
                diagnostics.format_raw_llm_exception(exc)
                if config.GLOBAL_CONFIG.debug_mode
                else "可开启 AUTOPATCH_DEBUG 查看详情。"
            )
            outcome = result.AgentRunResult(
                f"模型运行失败，原任务和提案保留：{detail}",
                result.RunStatus.FAILED,
            )
        if (
            outcome.status
            in {result.RunStatus.COMPLETED, result.RunStatus.LIMITED}
            and current.state != initial
        ):
            try:
                self.session.repair.commit(initial, current.state)
            except (models.RepairError, OSError) as exc:
                outcome = result.AgentRunResult(
                    outcome.final_answer + f"\n业务结果未提交：{exc}",
                    result.RunStatus.FAILED,
                )
        if (
            outcome.status is result.RunStatus.COMPLETED
            and finished is not None
            and finished.saved is not None
        ):
            try:
                self.model_runtime.run(
                    self.conversations.save(
                        finished.saved.summary, finished.history
                    )
                )
            except (
                sqlite3.DatabaseError,
                OSError,
                sdk_conversations.ConversationConflict,
                asyncio.CancelledError,
            ):
                current.notices.append(
                    "回答已生成，但会话未保存；未覆盖并发修改。业务提交结果以当前任务为准。"
                )
        for notice in current.notices:
            if output_callbacks.on_observation:
                output_callbacks.on_observation(notice, notice)
        return outcome

    async def _execute(
        self, current: session.AgentSession, output: callbacks.AgentCallbacks
    ) -> _CompletedRun:
        """执行一次原生运行，返回候选与消息；业务和会话由外层依次提交。"""
        saved = None
        history: list[messages.ModelMessage] = []
        try:
            saved = await self.conversations.load()
            history = saved.messages
        except sqlite3.DatabaseError, OSError, ValueError, LookupError:
            current.notices.append("会话存储不可用，本轮不保存会话。")
        settings = policy.request_settings(self.model_runtime.profile)
        summary_settings: openai_model.OpenAIChatModelSettings = {
            **settings,
            "timeout": 120,
        }
        summary = boundaries.ProjectCompaction(
            max_tokens=int(self.model_runtime.profile.input_capacity * 0.85),
            keep_messages=20,
            model_settings=summary_settings,
            summarization_capabilities=[policy.FinishGuard()],
        )
        notebook = boundaries.ProjectMemoryCapability(
            self.memory.store,
            namespace="project",
            agent_name="main",
            guidance="只保存用户明确表达或确认的长期项目决定；临时约束、源码、扫描及 AI 单方结论不自动成为永久规则。",
            injection_errors="raise",
        )
        guides = current.reader.guidance()
        project_guidance = "\n\n".join(
            (
                f"## 项目规范来源 {guide.path}（范围 {guide.start_line}-"
                f"{guide.end_line}，截断={guide.truncated}）\n{guide.code}"
            )
            for guide in guides
        )

        async def events(
            ctx: pydantic_ai.RunContext[session.AgentSession],
            stream: AsyncIterable[messages.AgentStreamEvent],
        ) -> None:
            del ctx
            async for event in stream:
                if isinstance(event, messages.PartStartEvent):
                    if (
                        isinstance(event.part, messages.TextPart)
                        and output.on_token
                    ):
                        output.on_token(event.part.content)
                    elif (
                        isinstance(event.part, messages.ThinkingPart)
                        and output.on_reasoning
                    ):
                        output.on_reasoning(event.part.content)
                elif isinstance(event, messages.PartDeltaEvent):
                    if (
                        isinstance(event.delta, messages.TextPartDelta)
                        and output.on_token
                    ):
                        output.on_token(event.delta.content_delta)
                    elif (
                        isinstance(event.delta, messages.ThinkingPartDelta)
                        and output.on_reasoning
                    ):
                        output.on_reasoning(event.delta.content_delta or "")

        native: pydantic_ai.Agent[session.AgentSession, str] = (
            pydantic_ai.Agent(
                self.model_runtime.model,
                instructions=instructions.SYSTEM + "\n" + project_guidance,
                deps_type=session.AgentSession,
                toolsets=[sdk.build_toolset(current)],
                retries=pydantic_ai.AgentRetries(output=0),
                capabilities=[
                    notebook,
                    boundaries.RequestBoundary(output),
                    summary,
                    detect_loops.DetectLoops(),
                    policy.FinishGuard(),
                ],
                model_settings=settings,
            )
        )
        completed = await native.run(
            compaction.pin(current.user_text).content,
            deps=current,
            message_history=history,
            conversation_id=saved.summary.id if saved else None,
            usage_limits=usage.UsageLimits(),
            event_stream_handler=events,
        )
        return _CompletedRun(
            completed.output,
            saved,
            boundaries.unpin_history(completed.all_messages()),
        )
