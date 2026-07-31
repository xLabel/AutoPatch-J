## Why

真实 LLM 集成验证发现 Memory extraction 的 `thread_compaction` 返回未满足 parser 的非空字符串契约；现有 Prompt、请求协议、parser、Store 与 retry 对同一输出没有形成闭环。当前实现还会把输出合同错误与瞬时 provider/SQLite 错误统一放入无限 `retry_wait`，并可能由 SDK 或 transport 隐式增加请求，导致成本与最终状态不可证明。

## What Changes

- 统一 Memory extraction/consolidation 的 Prompt、标准 JSON mode、严格 parser 与 payload 语义校验；`thread_compaction` 在所有合法分支均为去空白后 1–4000 字符的滚动摘要。
- 对模型输出合同错误增加一次专用 repair；repair 只接收同一业务输入和程序生成的安全结构诊断，完整重生成 JSON，不携带首次响应原文。
- 将 Memory provider 请求限定为标准 OpenAI-compatible Chat Completions：禁用 provider 扩展、Aliyun dialect、SDK 自动重试和 transport 隐式删参重发。
- 将 job 恢复改为有界状态机：每个 job 最多两次 claim、三次 provider 请求，增加 terminal `failed`、安全诊断和 CLI 状态投影。
- **BREAKING**：Memory schema 升为 v4，拒绝既有非 v4 database；不迁移、双读或自动删除，只有显式 `/memory clear --confirm` 才重建。
- 保留确定性测试先行与 live 首错 gate；增加最小 live contract smoke，但任何真实 LLM 运行仍需单独明确同意。
- 产出独立 provider cleanup 交接文档；本 change 不删除全局百炼/DSML 适配。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `context-window-management`：增加 Memory repair 容量、续租和请求次数边界。
- `llm-prompt-structure`：明确 Memory JSON 输出合同、标准 JSON mode 与 repair Prompt。
- `persistent-conversation-memory`：修改 durable job 的失败分类、有限重试、终态和 schema 版本。
- `typed-memory-runtime`：增加输出合同错误、安全失败诊断、provider 调用计数和 CLI typed 状态。

## Impact

- 影响 `src/autopatch_j/llm/` 的 purpose options、请求构造、transport 和非流式响应元数据。
- 影响 `src/autopatch_j/core/memory/` 的 Prompt、contracts、pipeline、typed models、SQLite Store、worker 恢复和 status。
- 影响 `/memory status` 渲染、Memory 聚焦测试、质量 corpus 与 OpenSpec 主规格。
- 不改变 ReAct `CONTEXT_COMPACTION`、补丁证据/review 边界、真实 LLM 测试授权规则或非 Memory provider 行为。
