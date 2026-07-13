## Why

现有 Memory 依赖单个 `memory.json`、进程内锁和易失后台摘要，存在并发覆盖、任务丢失、长期记忆随 episode 裁剪消失、错误被静默吞掉以及无关内容被注入等问题；同时，全 intent 共用的 Agent 消息列表既不能跨重启延续普通对话，也可能让 ordinary 与 repair 上下文相互污染。项目尚未上线，适合直接建立一个可审计、可恢复且保持技术栈最小的新一代记忆闭环。

## What Changes

- **BREAKING**：用项目级 `.autopatch-j/memory.db` 替换 `memory.json`；初始化时直接删除旧 JSON，不读取、不备份、不迁移。
- 持久化普通对话 thread 与 RAW user/final-assistant turn；一个 repo 只有一个 active thread，跨 CLI 重启延续，显式 `/new` 才切换。
- 将 Memory 处理拆为 durable Stage 1 extraction 与 Stage 2 consolidation，使用 SQLite job、lease、retry 和 generation fencing 保证并发与失败恢复。
- 只形成 `user_preference`、`project_decision`、`discussion_context`；代码事实继续实时读取，repair intent 不读写 Memory。
- 不使用 embedding、向量数据库或 FTS5；通过短 routing context、检索型 title/aliases、`memory_search` 和 `memory_read` 渐进读取证据。
- 将 Agent 历史改为请求级输入/输出：ordinary 从持久 thread 投影上下文，所有 repair 请求彼此独立，tool/reasoning/observation 不持久化。
- 新增 `/new` 与 `/memory status|list|show|forget|clear|export`；`/reset` 改为只清工作台并明确保留 Memory。
- 新增记忆质量 corpus、确定性 Pipeline 测试和可选 live-model eval。
- 将 LLM 与 Memory 失败诊断调整为面向开发者的有界 RAW 策略：普通模式保持简洁，`AUTOPATCH_DEBUG=true` 时展示 provider exception/body；Memory job 与全局状态最多持久化 20,000 字符。

## Capabilities

### New Capabilities

- `persistent-conversation-memory`: 覆盖持久 thread/turn、两阶段处理、无向量渐进检索、Memory 命令、删除语义、生命周期和失败可见性。

### Modified Capabilities

- `typed-memory-runtime`: 将 typed JSON document 和 JSON 持久化边界改为 typed SQLite records、事务与显式错误。
- `llm-prompt-structure`: 将直接注入 Memory 分类内容改为有界 routing context，并通过 Memory tools 按需读取详情。
- `llm-call-diagnostics`: 将单一 memory-summary 调用拆为 memory-extraction 与 memory-consolidation 调用用途，并为开发者 debug 模式提供有界 RAW provider 诊断。

## Impact

- 主要影响 `core/memory/`、Agent/ReAct 消息生命周期、ordinary workflow、function-call 工具目录、CLI 命令与 runtime 关闭流程。
- 删除 v1 JSON normalizer/delta/summary-trigger/repo-profile Memory 逻辑，更新现有 Memory、Agent 和 CLI 测试。
- 重写 `docs/memory_design.md` 及 README 的 Memory、命令和 `/reset` 说明。
- 不增加第三方依赖或新的模型配置；Memory 后台调用复用当前 LLM 配置，但使用独立 client。
- 除本 change 为新增 `MEMORY_EXTRACTION`/`MEMORY_CONSOLIDATION` purpose 确定的 1,800/2,200 output tokens 外，不再调整 routing history、Memory Context 或 `memory_read` 预算；验收修复保持这些既定值，后续质量调优使用独立 change 和评测完成。
