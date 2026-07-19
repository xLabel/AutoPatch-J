## ADDED Requirements

### Requirement: DeepSeek 1M context profile
系统 SHALL 以 DeepSeek 1,000,000 token context window 作为默认请求容量，并 SHALL 在每次 LLM 调用前预留 configured output tokens 与 provider safety margin。

#### Scenario: 使用默认 DeepSeek profile
- **WHEN** 模型为默认 `deepseek-v4-flash` 且用户未覆盖 context 配置
- **THEN** context window 为 1,000,000 tokens
- **AND** output reserve 为 32,768 tokens
- **AND** provider safety margin 不小于 16,384 tokens 或 window 的 1%

#### Scenario: 显式覆盖模型容量
- **WHEN** 用户提供合法正整数 `AUTOPATCH_LLM_CONTEXT_WINDOW` 或 `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS`
- **THEN** 请求预算使用显式值覆盖内置 profile
- **AND** 所有 REACT、Memory 与 compaction 请求通过同一 profile resolver 获取容量
- **AND** 任一 purpose 的实际 `max_tokens` 不得超过该 profile 的 output reserve

#### Scenario: 未知模型缺少容量
- **WHEN** 模型没有内置 profile 且用户未配置 context window
- **THEN** CLI 返回明确配置错误
- **AND** 系统不得猜测 128K 或在 provider overflow 后才学习窗口大小

### Requirement: 每次 LLM 调用执行 token preflight
系统 SHALL 使用统一的保守 token estimator 估算 messages 与 tool schemas，并 SHALL 在输入超过当前 profile 容量前重建 context。

#### Scenario: 请求低于压力阈值
- **WHEN** 完整请求不超过 input capacity 的 80%
- **THEN** 系统保持 recent history、Memory Map 和当前 ReAct 轨迹的完整投影
- **AND** 不为制造固定长度而提前 compaction

#### Scenario: 本地普通投影超容但可 hard rebuild
- **WHEN** 完整 Memory Map 或可重读 Memory result 使普通 preflight 超过 input capacity，但 aggressive pruning 与减半 Map 可以装入
- **THEN** 系统在调用 provider 前执行一次 hard rebuild 并继续相同业务请求
- **AND** 这次本地 rebuild 不消耗 provider overflow 的单次重试额度

#### Scenario: 单个必需输入无法装入
- **WHEN** system/task contract、当前用户输入、finding 或 patch binding 等不可丢组件本身超过 input capacity
- **THEN** 系统返回明确 context capacity error
- **AND** 不通过静默截断当前用户指令或 patch binding 继续调用 LLM

#### Scenario: compaction 调用受实际容量约束
- **WHEN** 系统把旧 discourse 拆分为一个或多个 compaction fragment
- **THEN** 每次调用都从实际 input capacity 中扣除 system、固定 prompt、消息 overhead 与当前 previous checkpoint
- **AND** 完整 compaction messages 不得超过 input capacity
- **AND** 没有可用 fragment 空间时在调用 provider 前返回明确 context capacity error

### Requirement: 可重读 tool result 优先卸载
当请求超过 80% input capacity 时，系统 SHALL 优先压缩旧的可重读 tool observation，并 MUST 保留 assistant tool call 与对应 tool result 的协议配对。

#### Scenario: 旧源码读取结果可重放
- **WHEN** 较旧 tool result 来自带完整参数的源码读取或 Memory 读取工具
- **THEN** wire message 将正文替换为 bounded summary 与可重读提示
- **AND** 本地请求轨迹保留原 tool name、arguments、status、summary 和必要 artifact identity

#### Scenario: 最近或不可重建证据
- **WHEN** tool result 是最近观察、当前 finding、patch binding 或无法从既有 domain tool 重建的证据
- **THEN** pruning 不得优先移除该结果
- **AND** 系统不得为了通用 offload 引入无证据需求的持久 artifact reader

### Requirement: 结构化 checkpoint 与 recent tail
pruning 后 context 仍超过 input capacity 的 85% 时，系统 SHALL 把较老 discourse 压缩为 structured checkpoint，并 SHALL 保留最新最多 128K tokens 的完整消息 tail。

#### Scenario: 生成 checkpoint
- **WHEN** 长 history 或 ReAct discourse 触发 compaction
- **THEN** checkpoint 使用 goal、user constraints、finding/patch state、verified facts、decisions、open questions、next actions 与 artifact references 的固定结构
- **AND** LLM 只总结被淘汰的旧 discourse，不得替代 checkpoint 外保留的当前 user、task contract、finding 或 patch binding
- **AND** 当前 user message 不得进入 checkpoint builder 的旧消息输入

#### Scenario: 连续多次 compaction
- **WHEN** 同一 ReAct request 在第一次 checkpoint 后再次超过压力阈值
- **THEN** 系统从上次压缩游标继续归纳新增旧 discourse
- **AND** 当前 user message 仍以原文恰好投影一次，不得因压缩游标越过该消息而丢失

#### Scenario: compaction 后重建稳定上下文
- **WHEN** structured checkpoint 已生成
- **THEN** 系统从 system/task contract、checkpoint、recent tail、当前用户输入、finding/patch binding 和新的 Memory Map 重建请求
- **AND** Memory Map 与工具策略不得只依赖 compaction summary 保留

#### Scenario: compaction 无有效回收
- **WHEN** 一次 compaction 没有回收至少 32K tokens 或原请求的 10%，且 checkpoint 后没有新增有效进展
- **THEN** 系统停止重复 compaction
- **AND** 返回明确的无可回收 context error，而不是进入 summary loop

### Requirement: Provider overflow 只强制重试一次
系统 SHALL 识别 provider context overflow，执行一次 hard rebuild，并 SHALL 对同一 LLM 调用最多重试一次。

#### Scenario: 首次 provider overflow
- **WHEN** provider 明确返回 context length/maximum token overflow
- **THEN** 系统执行 tool pruning、structured checkpoint，并将 Memory Map target 减半以优先淘汰尾部 relevant item
- **AND** 使用相同业务请求重试一次

#### Scenario: 重试仍 overflow
- **WHEN** hard rebuild 后的唯一重试仍返回 overflow
- **THEN** 系统保留第二次 provider error 并终止该请求
- **AND** 不执行第三次调用或无限降低上下文

### Requirement: Durable recall 使用可借用 token pool
系统 SHALL 将 Memory Map 与 `memory_search/read` 结果计入同一 durable recall pool；recent history 与 thread checkpoint SHALL 单独计入 session continuity，但最终都受全局 preflight 限制。

#### Scenario: 1M profile 分配 recall
- **WHEN** 使用默认 1M profile
- **THEN** durable recall 上限为 input capacity 的 12%，且限制在 4K 到 24K tokens
- **AND** 首次 Memory Map target 为实际 recall budget 的一半且不超过 8K tokens

#### Scenario: Memory 不相关
- **WHEN** 当前请求没有相关 standing 或 relevant item
- **THEN** 未使用的 recall pool 不生成填充内容
- **AND** token 可由 recent history、源码或当前 ReAct 轨迹使用
