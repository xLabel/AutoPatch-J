"""以官方 Agent 执行受控 Java 任务，并管理草案提交资格。"""

import asyncio
from collections.abc import AsyncIterable
import dataclasses
import sqlite3
from typing import Unpack

import pydantic_ai
from pydantic_ai import exceptions
from pydantic_ai import messages
from pydantic_ai import usage
from pydantic_ai.models import openai as openai_model
from pydantic_ai_harness import compaction
from pydantic_ai_harness import detect_loops
from pydantic_ai_harness.step_persistence import (
    conversations as sdk_conversations,
)

from autopatch_j.agent import boundaries
from autopatch_j.agent import callbacks
from autopatch_j.agent import prompt_assets
from autopatch_j.agent import result
from autopatch_j.agent import session as agent_session
from autopatch_j.agent import task_profile
from autopatch_j.agent import user_prompts
from autopatch_j.core import conversations
from autopatch_j.core import memory
from autopatch_j.core.domain import audit
from autopatch_j.core.domain import intent as domain_intent
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.llm import policy
from autopatch_j.llm import runtime
from autopatch_j.tools import sdk

MEMORY_GUIDANCE = (
    "笔记仅是项目背景，不能覆盖当前用户指令、源码证据、finding"
    "绑定或人工审核。"
    "仅记录用户明确表达或确认的长期项目偏好与决定；不要从一次补丁纠正、"
    "源码事实、工具输出、AI单方建议推导长期规则。已删除内容不要从旧历史自动恢复。"
)


class Agent:
    """为 workflow 构造任务输入；SDK 负责模型与工具循环。"""

    def __init__(
        self,
        session: agent_session.AgentSession,
        model_runtime: runtime.ModelRuntime,
        conversation_store: conversations.ProjectConversations,
        memory_store: memory.ProjectMemory,
    ) -> None:
        self.session = session
        self.model_runtime = model_runtime
        self.conversations = conversation_store
        self.memory = memory_store

    @property
    def model_label(self) -> str:
        """当前模型调用名；缺少模型配置时给出明确状态。"""
        return (
            self.model_runtime.profile.model
            if self.model_runtime.model
            else "LLM Not Configured"
        )

    def reset_history(self) -> None:
        """清理临时请求状态；持久普通会话仅由 /new 切换。"""
        self.session.clear_cache()

    def perform_general_chat(
        self, raw_user_text: str, **output: Unpack[callbacks.OutputCallbacks]
    ) -> result.AgentRunResult:
        """延续普通会话，使用回调展示过程并返回实际结束状态。"""
        return self._run(
            domain_intent.IntentType.GENERAL_CHAT,
            raw_user_text,
            raw_user_text,
            output,
        )

    def perform_code_explain(
        self,
        raw_user_text: str,
        scope: domain_scope.CodeScope | None,
        project_context: str | None = None,
        allow_symbol_search: bool | None = None,
        **output: Unpack[callbacks.OutputCallbacks],
    ) -> result.AgentRunResult:
        """在指定范围讲解源码，延续普通会话但不生成补丁。"""
        prompt = (
            user_prompts.build_code_explain_user_prompt(
                raw_user_text, scope, project_context
            )
            if scope
            else raw_user_text
        )
        allow = (
            self.session.code_explain_allow_symbol_search
            if allow_symbol_search is None
            else allow_symbol_search
        )
        profile = task_profile.fetch_code_explain_profile(allow)
        return self._run(
            domain_intent.IntentType.CODE_EXPLAIN,
            prompt,
            raw_user_text,
            output,
            profile,
        )

    def perform_code_audit(
        self,
        raw_user_text: str,
        current_finding: audit.FindingTask,
        force_reread: bool,
        **output: Unpack[callbacks.OutputCallbacks],
    ) -> result.AgentRunResult:
        """隔离普通历史处理一个 finding；有效草案仅暂存，等待 workflow 入队。"""
        prompt = user_prompts.build_code_audit_user_prompt(
            raw_user_text, current_finding, force_reread
        )
        return self._run(
            domain_intent.IntentType.CODE_AUDIT,
            prompt,
            raw_user_text,
            output,
            current_finding=current_finding,
        )

    def perform_zero_finding_review(
        self,
        raw_user_text: str,
        file_path: str,
        **output: Unpack[callbacks.OutputCallbacks],
    ) -> result.AgentRunResult:
        """隔离历史复核单个零 finding 文件，不假设已有扫描句柄。"""
        prompt = user_prompts.build_zero_finding_review_user_prompt(
            raw_user_text, file_path
        )
        return self._run(
            domain_intent.IntentType.CODE_AUDIT,
            prompt,
            raw_user_text,
            output,
            task_profile.ZERO_FINDING_REVIEW_PROFILE,
            prompt_assets.build_zero_finding_review_system_prompt(
                None, self.session.focus_paths
            ),
        )

    def perform_patch_explain(
        self,
        raw_user_text: str,
        current_item: domain_workspace.ReviewPatchItem,
        **output: Unpack[callbacks.OutputCallbacks],
    ) -> result.AgentRunResult:
        """只解释当前审核项，保留局部要求但不修改草案。"""
        prompt = user_prompts.build_patch_explain_user_prompt(
            current_item, raw_user_text
        )
        prompt += self.session.build_runtime_patch_constraint_context(
            current_item.item_id
        )
        return self._run(
            domain_intent.IntentType.PATCH_EXPLAIN,
            prompt,
            raw_user_text,
            output,
            current_review_item=current_item,
        )

    def perform_patch_revise(
        self,
        raw_user_text: str,
        current_item: domain_workspace.ReviewPatchItem,
        **output: Unpack[callbacks.OutputCallbacks],
    ) -> result.AgentRunResult:
        """记录当前审核项的临时要求并暂存修订，保留原 finding 绑定。"""
        self.session.record_runtime_patch_constraint(
            current_item.item_id, raw_user_text
        )
        prompt = user_prompts.build_patch_revise_user_prompt(
            current_item, raw_user_text
        )
        prompt += self.session.build_runtime_patch_constraint_context(
            current_item.item_id
        )
        return self._run(
            domain_intent.IntentType.PATCH_REVISE,
            prompt,
            raw_user_text,
            output,
            current_review_item=current_item,
        )

    def _run(
        self,
        intent: domain_intent.IntentType,
        prompt: str,
        raw_user_text: str,
        output: callbacks.OutputCallbacks,
        profile: task_profile.TaskProfile | None = None,
        system_prompt: str | None = None,
        current_finding: audit.FindingTask | None = None,
        current_review_item: domain_workspace.ReviewPatchItem | None = None,
    ) -> result.AgentRunResult:
        """建立独立请求状态，只把正常或受限运行的暂存草案移交工作流。"""
        del raw_user_text
        profile = profile or task_profile.fetch_task_profile(intent)
        current = dataclasses.replace(
            self.session,
            source_read_cache={},
            proposed_patch_draft=None,
            revised_patch_draft=None,
            notices=[],
            observations=[],
            current_finding=current_finding,
            current_review_item=current_review_item,
        )
        self.session.proposed_patch_draft = None
        self.session.revised_patch_draft = None
        if self.model_runtime.model is None:
            return result.AgentRunResult(
                "LLM 配置缺失，请设置 AUTOPATCH_LLM_API_KEY。",
                result.RunStatus.FAILED,
            )
        output_callbacks = callbacks.AgentCallbacks(**output)
        workspace = self.session.workspace_manager.load()
        pending = workspace.current_patch()
        instructions = system_prompt or prompt_assets.build_task_system_prompt(
            intent,
            pending.file_path if pending else None,
            workspace.latest_scan_id,
            self.session.focus_paths,
        )
        try:
            outcome = self.model_runtime.run(
                self._execute(
                    current,
                    intent,
                    prompt,
                    instructions,
                    profile,
                    output_callbacks,
                )
            )
        except exceptions.UsageLimitExceeded, detect_loops.LoopDetectedError:
            outcome = result.AgentRunResult(
                "模型请求额度或循环限制已触发，任务未完整完成，请审阅已有结果。",
                result.RunStatus.LIMITED,
            )
        except asyncio.CancelledError:
            current.proposed_patch_draft = None
            current.revised_patch_draft = None
            outcome = result.AgentRunResult(
                "用户已取消，本轮未完成，未提交暂存草案。",
                result.RunStatus.CANCELLED,
            )
        finally:
            self.session.source_read_cache.clear()
        if outcome.status in {
            result.RunStatus.COMPLETED,
            result.RunStatus.LIMITED,
        }:
            self.session.proposed_patch_draft = current.proposed_patch_draft
            self.session.revised_patch_draft = current.revised_patch_draft
        for notice in current.notices:
            if output_callbacks.on_observation:
                output_callbacks.on_observation(notice, notice)
        return dataclasses.replace(
            outcome, observations=tuple(current.observations)
        )

    async def _execute(
        self,
        current: agent_session.AgentSession,
        intent: domain_intent.IntentType,
        prompt: str,
        system_prompt: str,
        profile: task_profile.TaskProfile,
        output: callbacks.AgentCallbacks,
    ) -> result.AgentRunResult:
        """运行原生 SDK；仅完整普通会话保存 head，保存冲突不抹去回答。"""
        ordinary = intent in {
            domain_intent.IntentType.GENERAL_CHAT,
            domain_intent.IntentType.CODE_EXPLAIN,
        }
        saved = None
        history: list[messages.ModelMessage] = []
        if ordinary:
            try:
                saved = await self.conversations.load()
                history = saved.messages
            except sqlite3.DatabaseError, OSError, ValueError, LookupError:
                current.notices.append("普通会话存储不可用，本轮不保存会话。")
        budget = self.model_runtime.profile.input_capacity
        operation_settings = policy.request_settings(self.model_runtime.profile)
        summary_settings: openai_model.OpenAIChatModelSettings = {
            **operation_settings,
            "timeout": 120,
        }
        summary = compaction.SummarizingCompaction(
            max_tokens=1,
            keep_messages=20,
            tokenizer=policy.text_units,
            model_settings=summary_settings,
            summarization_capabilities=[policy.FinishGuard()],
        )
        fallback = compaction.FallbackCompaction(
            max_tokens=int(budget * 0.85),
            tokenizer=policy.text_units,
            fallback_chain=[
                summary,
                compaction.SlidingWindowCompaction(
                    max_tokens=1, keep_messages=20, tokenizer=policy.text_units
                ),
            ],
            fallback_on=(exceptions.ModelAPIError, policy.ModelFailure),
        )
        notebook = boundaries.ProjectMemoryCapability(
            self.memory.store,
            namespace="project",
            agent_name="main",
            guidance=MEMORY_GUIDANCE,
            injection_errors="raise",
        )
        allowed = {name.value for name in profile.tool_names}

        async def events(
            ctx: pydantic_ai.RunContext[agent_session.AgentSession],
            stream: AsyncIterable[messages.AgentStreamEvent],
        ) -> None:
            """将 SDK 文本和推理增量转发给回调，不将部分输出当作完成结果。"""
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

        agent: pydantic_ai.Agent[agent_session.AgentSession, str] = (
            pydantic_ai.Agent(
                self.model_runtime.model,
                instructions=system_prompt,
                deps_type=agent_session.AgentSession,
                toolsets=[sdk.build_toolset(current, profile.tool_names)],
                retries=pydantic_ai.AgentRetries(output=0),
                capabilities=[
                    notebook,
                    compaction.ClearToolResults(
                        max_tokens=int(budget * 0.8),
                        tokenizer=policy.text_units,
                        exclude_tools=frozenset(
                            {
                                "get_finding_detail",
                                "propose_patch",
                                "revise_patch",
                            }
                        ),
                    ),
                    fallback,
                    detect_loops.DetectLoops(
                        action="error",
                        repeated_call_threshold=None,
                        repeated_result_threshold=3,
                        alternating_calls_threshold=3,
                    ),
                    boundaries.RequestBoundary(allowed, output),
                    policy.FinishGuard(),
                ],
                model_settings=operation_settings,
            )
        )
        completed = await agent.run(
            compaction.pin(prompt).content,
            deps=current,
            message_history=history,
            conversation_id=saved.summary.id if saved else None,
            usage_limits=usage.UsageLimits(request_limit=10),
            event_stream_handler=events,
        )
        if saved is not None:
            try:
                await self.conversations.save(
                    saved.summary,
                    boundaries.unpin_history(completed.all_messages()),
                )
            except (
                sqlite3.DatabaseError,
                OSError,
                sdk_conversations.ConversationConflict,
            ):
                current.notices.append(
                    "回答已生成，但会话未保存；请重新加载或开启新会话，不会覆盖并发修改。"
                )
        return result.AgentRunResult(completed.output)
