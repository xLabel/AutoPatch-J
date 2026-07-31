## Context

参见 `proposal.md`。当前 Memory primary 使用非流式 Chat Completions，但 Prompt 对 `thread_compaction` 的空值规则弱于 parser；所有异常统一进入无上限 `retry_wait`。`LLMRequestBuilder` 还为 reasoning-disabled 请求加入 provider 扩展，transport 可删参重发，OpenAI SDK 本身也默认自动重试，因此 job attempt 不能代表真实请求成本。

Store 在 transaction 内提交 compaction、candidate、consolidation operation 和 job 状态；parser 失败发生在 transaction 之前，当前没有部分写入。设计必须保留这一原子边界，并保留 Memory degraded 不阻断扫描、源码读取或 patch review 的既有不变量。

## Goals / Non-Goals

**Goals:**

- 用一个可测试的合同贯通 Prompt、标准 JSON request、parser、payload semantic validator 与 Store。
- 对模型输出错误提供一次不同 Prompt 的有界 repair，并证明每 job 的请求上限。
- 将 retry、terminal failure、lease fencing 和安全诊断落入持久化 typed 状态。
- 让 extraction 与 consolidation 共享同一控制流，但不混淆各自 payload/语义规则。

**Non-Goals:**

- 不修改 ReAct `CONTEXT_COMPACTION` 的 Prompt、overflow retry 或 output contract。
- 不实现 deterministic Memory fallback，不放宽 parser，不保存非法模型输出供人工恢复。
- 不在本 change 删除 `bailian-dsml`、`DeepSeekAliyunDialect` 或其他非 Memory 兼容代码。
- 不迁移 v3 database，不运行未经授权的真实 LLM 测试。

## Decisions

### 1. Memory purpose 使用 standard-only request policy

`LLMRequestOptions` 增加 `response_format`、`standard_only` 和 `sdk_max_retries`。Memory primary/repair 使用 JSON object mode、`sdk_max_retries=0`；request builder 不生成 `extra_body`，transport 不执行 compatibility retry，response parser 强制 `StandardDialect`。其他 purpose 保持现状，避免把 provider cleanup 混入本 change。

选择 JSON object mode 而不是 provider JSON schema，是因为 DeepSeek 官方与 OpenAI-compatible Chat Completions 都有共同的 `json_object` 交集；字段、长度和语义仍由程序侧校验。

### 2. 仅 typed output contract error 触发 repair

新增 `MemoryOutputContractError`，携带程序生成的 violation。JSON parser 和基于本次 fitted payload/source envelope 的纯 validator 抛出该错误；Store transaction 继续重复验证，但一般 `MemoryContractError`、storage、schema、lease 或未知异常不触发 repair。

这样避免现有 `MemoryContractError` 同时用于 Recall 预算、batch shape 和 LLM 输出时发生误 repair。

### 3. repair 完整重生成，不拼补首次响应

repair system prompt 复用完整业务合同并增加固定 addendum；user message 包含同一 job/batch 的重新 fitted 业务 payload，以及仅由 path/rule/type/length/recognized keys 组成的 violation object。首次 raw response、异常 message 和 observed value 永不进入 repair、Store、diagnostics 或 log。

Extraction repair 允许按既有卸载顺序适配更长 envelope；最终 source envelope 以 repair 实际可见 payload 为准。Consolidation 必须保留完整 candidates 和 active registry。repair fit 失败是 terminal failure。

### 4. 两个持久化计数共同约束恢复

Schema v4 为 job 增加 `llm_call_count` 和 `failed`。`attempt_count` 最多 2，表示持久 claim；`llm_call_count` 最多 3，发送前由 Store 原子预留。SDK retry 为 0，transport fallback 禁用，因此一次预留对应至多一次 HTTP request。

Extraction batching只合并预 claim `attempt_count` 与 `llm_call_count` 相同的连续 jobs，避免 retry job 消耗 fresh job 额度。第一次瞬时 primary failure 使用现有首个 5 秒 backoff；第二次失败或任一 repair 失败转 terminal。

### 5. repair 前续租，stale worker 不补写

Primary 最长 60 秒，repair 前将有效 job lease 续至新的 120 秒。续租先验证 owner、generation 与未过期 lease；验证失败不发 repair。Lease recovery 在第一次过期后进入一次 retry，第二次过期转 failed；stale worker 仍不得将结果、retry 或 terminal 状态写回。

### 6. 安全诊断是 typed projection，不是 RAW error

`MemoryFailureDiagnostic` 保存 category、phase、call stage、attempt、provider call count、受控 path/rule/type/length、白名单 key 状态、裁剪 ID 和标准化 finish reason。Provider 异常只投影类名和数字 status。序列化后的 bounded diagnostic 可写入现有 `last_error`/export，但 `AUTOPATCH_DEBUG` 也不能解锁 raw Memory 模型内容。

`MemoryStatus` 增加 `failed_jobs` 与 `last_failure`。数据库可读但存在 failed job 时同时报告 healthy 与 degraded；CLI 直接显示安全字段。

### 7. v4 是干净 schema，不提供兼容路径

`MEMORY_SCHEMA_VERSION` 直接升至 4，并更新完整 catalog manifest。任何非 v4 database 初始化失败且保持原文件不变；显式 `/memory clear --confirm` 仍是唯一重建入口。没有 migration、fallback reader 或旧状态测试矩阵。

## Risks / Trade-offs

- [JSON mode 仍可能返回空 content 或语义错误] → 保留严格 parser、一次 repair 和 terminal fail-closed。
- [关闭 SDK retry 降低单次请求容错] → pipeline 对明确瞬时 primary 错误提供一次持久 retry，使成本和状态可审计。
- [terminal failed 意味着某个 turn 不进入 durable Memory] → 保留 RAW turn 和已有 checkpoint/items，并在 `/memory status` 持续显示 degraded。
- [schema v4 拒绝开发期 v3 数据] → 项目未上线且已明确不兼容；只允许用户主动 clear。
- [全局百炼适配仍存在] → Memory 通过 standard-only policy 完全隔离，另写交接文档在独立 change 清理。

## Migration Plan

1. 先更新 OpenSpec 与 schema v4 catalog，再实现请求、parser/pipeline/Store 和 CLI。
2. 运行聚焦测试、默认完整回归、`git diff --check` 与 strict validation。
3. 不自动处理现有 v3 database；需要时由用户显式 clear。
4. 真实 smoke 和完整 corpus 仅在获得对应命令的明确授权后运行。

## Decision Log

- 2026-08-01：将 Memory extraction primary/repair 的 `max_tokens` 从 1800 调整为 4096。首次获授权的完整 live run 在 `assistant-proposal-user-confirmation` case 中证明 repair 以 `finish_reason=length` 被 1800-token 上限截断；当前模型 context profile 允许 32768 output tokens，因此 4096 在不改变输入容量、repair 次数或 provider 调用上限的前提下，为严格 JSON 合同提供必要输出空间。
- 2026-08-01：将 Memory consolidation primary/repair 的 `max_tokens` 从 2200 调整为 4096。第二次完整 live run 已通过此前 extraction 首错，并在 `decision-supersedes-old-decision` case 中证明 consolidation repair 以 `finish_reason=length` 被 2200-token 上限截断；统一为 4096 可消除同类合同截断，同时保持单 job 最多两次调用、失败终止和 provider retry 禁用等边界不变。
- 2026-08-01：补齐 consolidation 的 payload-relative semantic validator，使 discussion/project kind、candidate applicability、signal 继承、非空正文与可见 target scope 在 Store transaction 前转为 `MemoryOutputContractError`。第三次完整 live run 在 `assistant-only-claim-is-not-decision` case 证明，原 validator 仅检查 candidate/target ID，导致模型语义偏差绕过既定 repair 并在 Store 被误报为 internal failure；Store 继续重复执行最终权威校验。
- 2026-08-01：质量 corpus 的 `expected_active_content` 同时匹配 active item 的 canonical `statement` 与补充 `content`。第四次完整 live run 中 `decision-supersedes-old-decision` 已正确生成 revision 2、supersede 旧决定，预期短语也完整存在于可直接注入 prompt 的 `statement`；评估器不再要求 `content` 机械重复同一句话。
- 2026-08-01：明确 discussion extraction 的负例边界：用户仅陈述尚未决定时不得推断后续延续意图，只有 user 原文明确要求继续、保留或再次评估才创建 `discussion_context`。第五次完整 live run 的前 17 个 cases 已通过，最后一个 `english-undecided-is-not-decision` case 暴露模型把 “not decided” 自行扩写成 “may need revisiting”。
- 2026-08-01：在上述修复及单点 live 复验后，第六次完整 `pytest -q --run-live-llm` 实际完成全部 18 个 live cases，最终结果为 `498 passed`；本 change 的真实模型质量门槛已满足。
