## Context

当前 Memory v2 已具备项目级 SQLite、RAW turn、durable job、两阶段 extraction/consolidation、provenance、thread、lease/retry/generation fencing 和 `memory_search/read`，但读取路径仍是“每类最近五条 + 固定字符上限”，repair intent 完全看不到 Memory。ReAct 请求也只有按消息位置压缩旧 tool output 的局部逻辑，没有模型窗口、输出预留、token preflight、结构化 compaction 或 overflow recovery。

本 change 面向只使用 DeepSeek 的企业客户，默认模型 `deepseek-v4-flash` 按 1,000,000 token context window 设计。实现借鉴 Codex 的 bounded summary + dedicated tools、Claude Code 的 compaction 后重注入、OpenCode 的旧 tool output pruning，以及 pi 的 summary + recent tail 和 tool-call/result 配对，但不复制跨项目画像、远端 Memory 或多 Agent 能力。

## Goals / Non-Goals

**Goals:**

- 在 1M context 中尽量保留有价值的 recent history 和工具证据，只在真实压力下分层压缩。
- 让长期 Memory 以明确写入信号、可追溯 revision 和 query-aware recall 真正影响普通问答及补丁约束。
- 以程序侧 `RecallPolicy` 保证 repair intent 只能使用项目 preference/decision，且不改变 patch-safety 证据链。
- 保留现有 SQLite 单一事实源、后台 durable jobs、thread 与恢复边界，清理未发布版本不需要的兼容逻辑。
- 让最终 `docs/memory_design.md` 完整解释真实实现，而不是描述规划能力。

**Non-Goals:**

- 不做跨项目用户画像、团队共享、多 Agent Memory、远端 Memory service、embedding、向量数据库或 FTS。
- 不从补丁 apply、规则消失或未经可靠验证的结果学习 repair procedure。
- 不新增 Memory 人工审批队列、编辑器、CLI 命令或 schema v2 迁移。
- 不把 RAW turn、普通 discussion 或 assistant/tool 内容开放给 repair intent 任意搜索。

## Decisions

### 1. DeepSeek 1M profile 与保守 token estimator

新增 context profile：默认 `deepseek-v4-flash` 使用 `context_window=1_000_000`、`max_output_tokens=32_768`。`AUTOPATCH_LLM_CONTEXT_WINDOW` 与 `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS` 可以显式覆盖；未知模型且未提供 window 时配置失败，不用小窗口 fallback 猜测。

不增加 tokenizer 依赖。统一 estimator 使用 UTF-8 byte 数的保守上界并计入 message/tool schema 固定 overhead；中文近似一字符一 token，英文会适度高估。provider safety margin 取 `max(16_384, context_window * 1%)`，请求 input capacity 为 window 减去 output reserve 与 margin。

### 2. Context 按压力重建，不按固定 turn 截断

请求装配由新的 typed context assembler 负责。1M profile 的 component ceilings 是：recent completed history 最多 384K tokens、structured thread checkpoint 最多 16K、durable recall 最多 24K、单条 Memory statement 最多 320 tokens；这些是上限而非预留，未使用空间归还当前请求。

每次 LLM 调用前执行 preflight：

1. `<=80% input capacity`：保留当前完整投影。
2. `>80%`：先把较旧、可由既有 domain tool 重读的 observation 替换为 bounded summary，保留 assistant tool call 与对应 tool result，不拆 pair；最近 tool results 和 scanner/patch binding 继续保留。
3. pruning 后仍 `>85%`：将较老对话和 ReAct discourse 压缩为带固定标题的 structured checkpoint，保留最新最多 128K token 的完整 tail；当前 user message、task/system contract、finding 和 patch binding 留在 checkpoint 外按原文投影，LLM 只归纳被淘汰的旧 discourse。多次 compaction 只推进已压缩前缀，当前 user message 每次都单独重新投影。
4. rebuild 后仍超 input capacity：on-demand Memory detail 随可重读 tool result 卸载，Memory Map target 减半并按 standing-first 顺序自然淘汰尾部 relevant item；当前用户输入、task/system contract、finding、patch binding 和最新 tail 最后保留。仍无法装入时返回明确 context capacity error。

当前 user message 不仅保留在 checkpoint 外，也必须从交给 checkpoint builder 的旧消息切片中排除；即使 compaction 游标已经越过当前输入，checkpoint 也不能再次有损归纳它。compaction 必须至少回收 `max(32K, compact 前 token 的 10%)`，或者在上次 checkpoint 后出现了新的有效进展；否则停止重复 compaction。

普通本地 preflight 因完整 Memory Map 或可重读 Memory result 超容时，在调用 provider 前执行一次 hard rebuild：启用 aggressive pruning 并把 Map target 减半；hard rebuild 仍无法装入才返回 `ContextCapacityError`。这次本地重建不消耗 provider overflow 的单次重试额度。provider 明确报告 context overflow 时仍只强制执行一次 hard rebuild 并重试一次，第二次失败原样返回。所有 purpose 的实际 `max_tokens` 还受 context profile 的 output reserve 上限约束，确保 preflight 预留与真实请求一致。

compaction 自身也是受同一 input capacity 约束的 LLM 调用。每个 fragment 在发送前都从实际 capacity 中扣除 system、固定 prompt、消息 overhead 与当前 previous checkpoint；片段不设置 4K 下限，中间 checkpoint 按既有 checkpoint budget 截断后再进入下一次调用。包装内容已无可用片段空间时，在调用 provider 前返回 `ContextCapacityError`。

### 3. Schema v3 保存可执行语义，而不是标题索引

`memory_items`/candidate typed model 增加 `subject`、`statement`、`content`、`strength(hard|soft)`、`origin(explicit|adopted_proposal|inferred_repetition)`、`recall_mode(always|on_match)` 与 `applies_to_paths`。`user_preference`、`project_decision` 是项目范围且 `thread_id=NULL`；`discussion_context` 绑定 thread。runtime patch constraint 单独保存在当前 review/request 状态，不写成 durable item。

`logical_id` 是 Store 生成的 opaque chain ID。LLM extraction 不生成 ID，只输出 typed semantics 与来源；consolidation 只能从程序给出的 related active registry 中选择 target item。create 由 Store 生成 logical ID，revision 继承 logical ID 并递增 revision。当前 revision 只关联促成本次 revision 的 candidates/sources，旧 revision provenance 不再挤占 `memory_read` 的当前证据。

identity resolver 在 `user_preference` 与 `project_decision` 之间共同查找同 subject/applicability 候选，允许 revision 改变 kind；discussion 只在本 thread 内匹配。不同路径条件可以共存，具体路径优先。缺失的 duplicate 不启动周期性全库 Agent，只在后续相关 candidate 到来时修复。

### 4. 写入信号必须来自用户行为

单纯“LLM 提出补丁，用户 apply”不生成长期 candidate。明确用户陈述可以产生 hard item；用户用短确认采纳上一轮 LLM 决策时必须同时绑定 assistant 决策与 user confirmation。当前/本次局部纠正只形成 runtime constraint；明确项目长期规则才能写 durable `always`。局部性按来源 quote 对应的完整 RAW user clause 判断，repair intent 中的“这里”“当前补丁”等限制不能通过截短 quote 或改变 candidate kind 绕过。`discussion_context` 的代码事实过滤覆盖 `subject`、`statement` 和 `content` 的完整可注入语义。

重复纠正可以形成 soft `inferred_repetition`，但至少需要三次语义一致、跨两个文件且绑定三个不同 finding `evidence_key` 的信号；三个不同 user source turn 必须能一对一绑定互不相同的 key，且这些支持来源自身覆盖至少两个 path，不能用单个来源携带的 key 并集替代来源独立性。repair review turn 使用 `<source_scan_id>:<finding_id>` 作为 evidence identity，避免不同 scan 都从 `F1` 开始时发生碰撞。它永远是 `on_match`，不能覆盖 explicit item。extraction 额外读取同 thread 最近最多 20 个、合计最多 32K tokens 的 repair evidence，使已经处理过的早期纠正仍可参与后续归纳；所有非 inferred candidate 都必须先引用当前 extraction batch 的 user turn，不能因 `discussion_context` 的分支提前返回而绕过。最新同 subject/scope explicit revision 替代旧 explicit，explicit 高于 inferred，反向重复信号只能撤回 inferred。

extraction 继续允许合法 no-candidate；thread checkpoint 与 candidate admission 分开。RAW user turn 在主调用前写入，用户实际看到的 final assistant text 在完成边界保存，failed/interrupted turn 继续由 durable job处理。所有新 turn 的 `scope_paths` 在 Store 持久化边界按输入顺序保留最多 `MAX_SCOPE_PATHS=10` 条，避免大型 project scope 无界进入 SQLite 与 extraction payload；实际扫描、focus 和 patch 行为不受影响，既有记录不迁移或读取时回退截断。

### 5. 双通道 RecallQuery 与渐进读取

程序从 intent、当前用户原文、thread、focus paths，以及按 intent 可用的 finding `path/check_id/message`、patch file/bound finding 或 code scope 构造 `RecallQuery`；不把源码全文、snippet、diff、assistant rationale、旧回答或 tool output作为 query。

检索先执行 kind/thread/path eligibility，再构建两条 lane：

- `standing`：`recall_mode=always` 且路径适用，不要求 query 词项命中。
- `relevant`：`on_match` 必须命中 subject/alias/check_id/keyword，或至少两个不同 content terms；路径本身不能成为唯一命中。

词项执行 NFKC、case-fold、repo path normalization，并拆分 camelCase、snake_case、包名和路径段；连续汉字段在保留完整 term 后追加去重的滑动双字词项，不生成噪声更高的单字 term。完整 term 和既有 Java/path term 优先进入上限，每次最多 32 个 query terms。排序为 path specificity、subject/check-id exactness、alias/keyword、distinct content coverage、origin/strength、updated_at、stable ID；相关性不足时 abstain，不以 recency 补位。检索仍使用 SQLite 确定性 term tables，不使用 LLM reranker。

durable recall 总上限为 `clamp(input_capacity * 12%, 4K, 24K)`，Memory Map target 为实际 recall budget 的一半且最多 8K。Map 直接提供 bounded statement；search 最多四个不同 query、每次八个 hit，read 最多八个不同 ID，并共享剩余 recall token pool。

### 6. Request-local RecallPolicy 是安全边界

每个请求绑定 typed `RecallPolicy`、thread、intent、path、调用预算和 readable-ID allowlist。Map 暴露的 ID 以及本轮 search hit 才能被 read；LLM 参数不能改变 policy。

request-local binding 只由创建它的请求在 `finally` 中清除。`/new` 可以清理 review/runtime 状态并切换 active thread，但不能通过全局 history reset 提前清除已 admission 请求的 policy、search/read 预算或 readable-ID allowlist。

ordinary intent 可以读取项目 preference/decision 与当前-thread discussion。repair intent 可以获得项目 preference/decision 的 Map 和 `memory_search/read`，但 repository 层强制排除 discussion、archived thread 和任意 RAW history。semantic item 的 bounded provenance quote 只证明用户表达来源，不能作为源码或 finding 证据。

Memory Map 不再进入 system prompt，而作为 request-local advisory user context 放在 recent history 之后、当前用户消息之前；thread checkpoint 使用独立的 session-continuity user context 放在 recent history 之前，因此它不占 durable recall pool。两者都不写入 turn、history 或 extraction。system contract 明确当前用户指令优先且 Memory 非源码事实。每个 ReAct step 都在同一 policy 下刷新 Map；hard rebuild 使用减半 Map budget。

### 7. Thread boundary、degraded 与后台任务

一个项目可以累积多个 thread，但只有一个 active thread。`/new` 先捕获旧 thread 当前 job watermark，并最多同步等待 5 秒；完成或超时后都归档旧 thread、清理 review runtime 并创建新 thread。超时时显示警告，watermark 内 extraction 及其派生 consolidation 在 daemon/启动恢复中继续处理。

Memory bootstrap/schema/storage degraded 时，Memory 自身停止 admission、projection、search、read 和后台写入；status/show/export/clear 保持诊断恢复边界。主扫描、源码读取和 patch review 继续运行，并只提示本轮未应用项目 Memory，因为 Memory 不是 patch evidence。ordinary request 在 admission 已显示 degraded notice 后把 thread history 视为空投影，不能再次触发同一 storage failure 而中止主调用。若 Memory Map admission 成功但 active thread checkpoint 读取发生 storage error，本请求不得绑定半完成的 request state，并以空 checkpoint/Map 继续主 Agent。主业务完成后的 turn 收尾若遇到 storage、lease、not-found 或 thread-conflict 等 operational Memory error，同样只进入可见降级并保留主业务结果；`MemoryContractError` 与普通编程错误继续显式失败。

watermark flush 与普通 worker 共享 `_process_lock`。当 `/new` 等待超时后 flush 仍在后台处理旧 thread job 时，由持锁的 flush 循环在每个 pipeline step 前续租同 manager owner 的 open turns；不新增 heartbeat thread，也不改变 5 秒等待或 120 秒 turn lease。

`access_count/last_accessed_at` 仅作诊断，不参与 ranking；不增加 citation popularity、自动 expiry 或 apply feedback loop。

## Risks / Trade-offs

- **[启发式 token estimator 与 provider tokenizer 不完全一致]** → 保守高估、1%/16K margin、provider overflow hard rebuild + single retry。
- **[LLM compaction 遗漏 discourse 细节]** → 当前 user/task/finding/patch binding 不交给 checkpoint 替代，保留 recent tail，并在每次 rebuild 重注入 standing Memory。
- **[standing 过多仍可能超过 bounded Map]** → 路径具体性优先、显示 truncation/omitted count，并允许 policy-filtered search/read；不建立第三套 summary service。
- **[repair Memory 影响补丁判断]** → kind/path policy 在 repository 层执行，prompt 标记 advisory，patch-safety 仍要求当前源码/finding/function tools 证据。
- **[无 embedding 导致同义词召回受限]** → 写入阶段保存 aliases/keywords，拆分 identifier/path，并用检索质量 corpus 覆盖中英文和 Java 术语。
- **[schema v3 丢弃本地 v2 Memory]** → 项目未发布且用户明确选择干净实现；旧数据库只进入 degraded，显式 clear 创建 v3，不做隐式迁移或删除。

## Migration Plan

这是未发布项目的 breaking local schema change。不存在部署迁移：新 repo 直接创建 v3；检测到 v2、未知 schema 或旧 `memory.json` 时不读取、不迁移、不自动删除，Memory 进入 degraded并提示用户执行 `/memory clear --confirm`。代码回滚后 v3 同样不会被旧运行时接管。

## Open Questions

无。此前讨论中的 scope、写入信号、recall、1M profile、compatibility 和 repair boundary 均已确定。

## 决策记录

- 2026-07-18：将 thread checkpoint 与 durable Memory Map 拆成两个 synthetic user context，原因是 recent history/checkpoint 属于 session continuity，不得消耗或伪装成 durable recall 额度。
- 2026-07-18：为 repair turn 增加 finding `evidence_key` 和 bounded recent repair evidence，原因是两条 turn 即触发的后台 extraction 会让“三次独立 finding”在真实运行中无法只靠单批输入成立。
- 2026-07-18：`/new` 改为捕获 watermark 后最多等待 5 秒，超时后台继续，原因是同步循环虽有 job snapshot，却不能给交互切换提供确定上限。
- 2026-07-18：让 repeated runtime compaction 越过当前 turn 后仍单独保留原始 user message，原因是 append-only 轨迹中的压缩游标不能等价于当前输入的可见性。
- 2026-07-18：所有 LLM purpose 的 `max_tokens` 受 context profile output reserve 封顶，原因是输入预算必须与实际输出上限使用同一本账。
- 2026-07-19：根据 CR 将局部 repair、request-local binding、Memory operational error 与 compaction fragment 纳入程序侧完整边界校验，原因是 prompt 约束、全局 reset、窄异常捕获和固定片段下限都无法覆盖已支持的并发、lease 与小 context 场景。
- 2026-07-19：根据第二轮 CR 补齐 degraded history、连续汉字召回、scan-scoped finding evidence、当前 batch provenance 与 watermark heartbeat，原因是既有单元测试没有覆盖这些真实运行组合。
- 2026-07-19：根据第三轮 CR 将当前输入排除、local hard rebuild、checkpoint 降级、来源级 evidence admission 与 turn scope 上限落到程序边界，原因是全局并集、provider-only retry 和无界 scope 无法保证长上下文与 Memory 的正确性。
