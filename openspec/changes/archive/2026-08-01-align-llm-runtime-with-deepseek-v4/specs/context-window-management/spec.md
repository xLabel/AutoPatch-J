## MODIFIED Requirements

### Requirement: DeepSeek 1M context profile
系统 SHALL 分别表示 provider 模型能力与 AutoPatch-J 运行预算；DeepSeek V4 的 provider context window 为 1,000,000 tokens、provider 最大输出为 384,000 tokens，AutoPatch-J 输入上限为 840,000 tokens、推理型操作输出上限为 128,000 tokens。

#### Scenario: 使用默认 DeepSeek profile
- **WHEN** 模型为 `deepseek-v4-flash` 或 `deepseek-v4-pro` 且用户未覆盖 context 配置
- **THEN** provider context window 为 1,000,000 tokens，provider 最大输出为 384,000 tokens
- **AND** AutoPatch-J input capacity 为 840,000 tokens，推理型操作 output cap 为 128,000 tokens
- **AND** 其余约 32,000 tokens 作为 provider safety margin，不代表输入上限或 provider 最大输出

#### Scenario: 显式覆盖未知模型容量
- **WHEN** 未知模型用户提供合法正整数 `AUTOPATCH_LLM_CONTEXT_WINDOW` 和 `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS`
- **THEN** 所有 REACT、Memory 与 compaction 请求通过同一 profile resolver 获取容量
- **AND** 任一 purpose 的实际输出上限不得超过 configured operational output cap

#### Scenario: 未知模型缺少容量
- **WHEN** 模型没有内置 profile 且用户未完整配置 context window 与 operational output cap
- **THEN** CLI 返回明确配置错误
- **AND** 系统不得猜测窗口大小或在 provider overflow 后才学习容量

### Requirement: 每次 LLM 调用执行 token preflight
系统 SHALL 使用统一的保守 token estimator 估算 messages 与 tool schemas，并 SHALL 在输入超过当前 profile 的应用 input capacity 前重建 context。

#### Scenario: 请求低于压力阈值
- **WHEN** 完整请求不超过 input capacity 的 80%
- **THEN** 系统保持 recent history、Memory Map 和当前 ReAct 轨迹的完整投影
- **AND** 不为制造固定长度而提前 compaction

#### Scenario: 本地普通投影超容但可 hard rebuild
- **WHEN** 完整 Memory Map 或可重读 Memory result 使普通 preflight 超过 input capacity，但 aggressive pruning 与减半 Map 可以装入
- **THEN** 系统在调用 provider 前执行一次 hard rebuild 并继续相同业务请求
- **AND** 这次本地 rebuild 不消耗 provider overflow 的单次 changed-input retry

#### Scenario: 单个必需输入无法装入
- **WHEN** system/task contract、当前用户输入、finding 或 patch binding 等不可丢组件本身超过 input capacity
- **THEN** 系统返回明确 context capacity error
- **AND** 不通过静默截断当前用户指令或 patch binding 继续调用 LLM

#### Scenario: compaction 调用受实际容量约束
- **WHEN** 系统把旧 discourse 拆分为一个或多个 compaction fragment
- **THEN** 每次调用都从实际 input capacity 中扣除 system、固定 prompt、消息 overhead 与当前 previous checkpoint
- **AND** 完整 compaction messages 不得超过 input capacity
- **AND** 没有可用 fragment 空间时在调用 provider 前返回明确 context capacity error

#### Scenario: 非 ReAct purpose 不得绕过 preflight
- **WHEN** classifier、Memory extraction/consolidation 或 compaction 直接调用共享 LLM client
- **THEN** client 在 transport 前使用同一 estimator 校验 messages 与 tool schema
- **AND** 超容请求不得发送给 provider

#### Scenario: classifier 超长输入投影
- **WHEN** classifier 的完整 user message 超过 input capacity
- **THEN** CLASSIFIER 使用带省略标记的首尾投影后尝试一次分类调用
- **AND** 正常容量内的用户输入保持原文

#### Scenario: classifier 固定 envelope 超过容量时走路由 fallback
- **WHEN** pending review 的 `ModelContextProfile.input_capacity` 小于 route classifier 固定 system prompt 与消息 envelope
- **THEN** `ContextCapacityError` 在 provider 调用前被转换为 route classifier diagnostic
- **AND** `classify_route_with_diagnostics()` 返回既有 `REVIEW_CONTINUE` fallback
- **AND** 不调用 provider

### Requirement: Memory provider 调用具有持久化硬上限
系统 SHALL 对每个 Memory extraction 或 consolidation job 持久化顶层逻辑调用计数，并 SHALL 将 primary、durable primary retry 与 output repair 限制为最多三次逻辑操作；每次逻辑操作内部可由 SDK 最多执行两次 HTTP retry。

#### Scenario: primary SDK 重试耗尽
- **WHEN** Memory primary 在同一逻辑操作内经 SDK retries 后仍遇到 timeout、connection、HTTP 408/409/429/5xx 或 SQLite busy/locked
- **THEN** job 最多进入一次 `retry_wait`
- **AND** durable retry 使用新的顶层逻辑调用额度

#### Scenario: 第二次 primary 输出仍违反合同
- **WHEN** job 已因瞬时失败执行第二次 primary，且返回可解析但违反输出合同的响应
- **THEN** 系统最多执行一次 repair，作为该 job 的第三次且最后一次逻辑调用
- **AND** repair 失败后不得再次 claim 或调用 provider

#### Scenario: repair 前 lease 续期
- **WHEN** primary 输出违反合同且 job 仍持有有效 lease
- **THEN** 系统在 repair 前把 lease 续至完整 120 秒
- **AND** lease 或 generation 已失效时不得调用 repair 或写回 stale 结果

#### Scenario: repair envelope 超过容量
- **WHEN** primary 已返回非法输出，但完整 repair contract 与同一业务 payload 无法装入 input capacity
- **THEN** job 直接进入 terminal `failed`
- **AND** 系统不得删除 consolidation active registry、再次缩短不可丢 primary 内容或发起 repair

#### Scenario: HTTP attempts 不增加逻辑调用计数
- **WHEN** 一个 Memory `LLMClient.chat()` 内部发生 SDK retry
- **THEN** `llm_call_count` 只增加一次
- **AND** 单个 job 最多三次逻辑调用对应理论最多九次 HTTP attempts
