## 1. Provider 与配置边界

- [x] 1.1 增加 DeepSeek V4 / OpenAI baseline provider profile 与按 purpose 的 typed 请求策略。
- [x] 1.2 删除 reasoning、arbitrary extra body、stream dialect 配置及 DSML/Aliyun/Bailian 实现。
- [x] 1.3 将 `openai` 依赖限定为 `>=2.32.0,<3`，同步 CLI 配置状态。

## 2. Token、transport 与响应协议

- [x] 2.1 拆分 provider context/output 能力与 AutoPatch-J input/output 运行预算，应用 DeepSeek 840K/128K profile。
- [x] 2.2 切换为标准 Chat Completions transport、SDK `max_retries=2` 和成功结果 `retries_taken` 诊断。
- [x] 2.3 用直接 parser 严格处理 stream/nonstream、finish reason、reasoning 字段存在性和完整 tool calls。
- [x] 2.4 更新 assistant message adapter 与 ReAct 轨迹，保留真实 reasoning/空 content 并在工具执行前校验协议。

## 3. 分类与 Memory

- [x] 3.1 将 intent/route classifier 收敛为单次 `CLASSIFIER` 操作和确定性安全 fallback。
- [x] 3.2 将 Memory `llm_call_count` 明确为顶层逻辑操作，接入统一 SDK retry 和截断 repair 语义。
- [x] 3.3 在 Memory provider 调用期间每 30 秒续租 job/open turns，并阻止 fenced late result repair/commit。

## 4. 测试与文档

- [x] 4.1 更新 provider request matrix、实际 SDK MockTransport、parser、retry、classifier 和 token profile 聚焦测试。
- [x] 4.2 更新 Memory 调用计数、heartbeat、clear/generation fencing 聚焦测试。
- [x] 4.3 更新 quick start、配置/status、Memory 设计和 provider cleanup handoff。
- [x] 4.4 运行聚焦测试、`pytest -q`、工作区 diff 审查和 `openspec validate align-llm-runtime-with-deepseek-v4 --strict`；明确真实 LLM 集成测试未运行。

## 5. 验收后的规格重对齐

- [x] 5.1 更新 `proposal.md`，明确非 Memory 前台 RAW debug 与 Memory 后台 typed diagnostic 的边界。
- [x] 5.2 更新 `design.md`，记录持久 Memory 诊断禁止保存 provider RAW 错误的决策与重对齐原因。
- [x] 5.3 更新 `llm-call-diagnostics` delta spec，消除其与 `typed-memory-runtime` 的 Memory 错误诊断冲突。

## 6. 验收后的验证补强

- [x] 6.1 增加 provider 调用期间 schema 损坏的 heartbeat 回归测试，确认摘要转为 `stale`、不 repair 且不提交 late result。
- [x] 6.2 运行聚焦测试、`pytest -q`、`git diff --check` 和 OpenSpec strict validation；明确真实 LLM 集成测试未运行。
- [x] 6.3 重新验收 change，确认没有 CRITICAL 或 WARNING 后再评估归档。
