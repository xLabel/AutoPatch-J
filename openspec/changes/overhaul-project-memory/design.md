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

当前 user message 不仅保留在 checkpoint 外，也必须从交给 checkpoint builder 的旧消息切片中排除；即使 compaction 游标已经越过当前输入，checkpoint 也不能再次有损归纳它。compaction 必须至少回收 `max(min(32K, max(1, floor(input_capacity*10%))), floor(compact 前 token*10%))`，或者在上次 checkpoint 后出现了新的有效进展；否则停止重复 compaction。

普通本地 preflight 因完整 Memory Map 或可重读 Memory result 超容时，在调用 provider 前执行一次 hard rebuild：启用 aggressive pruning 并把 Map target 减半；hard rebuild 仍无法装入才返回 `ContextCapacityError`。这次本地重建不消耗 provider overflow 的单次重试额度。provider 明确报告 context overflow 时仍只强制执行一次 hard rebuild 并重试一次，第二次失败原样返回。所有 purpose 的实际 `max_tokens` 还受 context profile 的 output reserve 上限约束，确保 preflight 预留与真实请求一致。

compaction 自身也是受同一 input capacity 约束的 LLM 调用。每个 fragment 在发送前都从实际 capacity 中扣除 system、固定 prompt、消息 overhead 与当前 previous checkpoint；片段不设置 4K 下限，中间 checkpoint 按既有 checkpoint budget 截断后再进入下一次调用。包装内容已无可用片段空间时，在调用 provider 前返回 `ContextCapacityError`。

`LLMClient` 在 transport 前执行所有 purpose 共享的最终 input preflight，防止 classifier、Memory pipeline 或未来直接调用绕过容量边界。Classifier 对超长 user message 使用带省略标记的首尾投影，并让 CLASSIFIER 与 REACT fallback 复用同一投影。Memory database 继续保存完整 RAW turn；只有 extraction LLM payload 在超容时依次卸载旧 repair evidence、adjacent turn、previous compaction，并对 primary assistant/user 文本做首尾投影。固定 contract 与必需 primary 内容仍无法装入时，job 以带 capacity 原因的 `succeeded_no_output` 结束，不进入确定性 retry loop。

checkpoint 被截断时，截断标记本身计入 checkpoint budget；预算小到无法容纳标记时只保留预算内正文前缀，任何返回值都不得超过调用方账本。consolidation 不允许通过删除 related active registry 来适配容量，因为 LLM 可见 registry 必须与 Store 提交时的 identity 校验边界一致；完整 candidates 与 registry 无法装入时不调用 provider，job 以 `succeeded_no_output` 结束，并在同一事务中把关联 pending candidates 标记为 `suppressed`。

Compaction provider overflow 与主 ReAct overflow 分别计数：单个 compaction fragment 首次 overflow 后将 fragment budget 减半并重建消息，最多重试一次；第二次 overflow 原样返回，不进入第三次调用。

### 3. Schema v3 保存可执行语义，而不是标题索引

`memory_items`/candidate typed model 增加 `subject`、`statement`、`content`、`strength(hard|soft)`、`origin(explicit|adopted_proposal|inferred_repetition)`、`recall_mode(always|on_match)` 与 `applies_to_paths`。`user_preference`、`project_decision` 是项目范围且 `thread_id=NULL`；`discussion_context` 绑定 thread。runtime patch constraint 单独保存在当前 review/request 状态，不写成 durable item。

Runtime patch constraint 以 `ReviewPatchItem.item_id` 为生命周期边界，而不是以 file path 为边界。同一 item 的 revise/explain 可以继续读取既有约束；apply/discard 成功推进后清除该 item，失败 apply 保留，其他同文件 item 不继承。

`logical_id` 是 Store 生成的 opaque chain ID。LLM extraction 不生成 ID，只输出 typed semantics 与来源；consolidation 只能从程序给出的 related active registry 中选择 target item。create 由 Store 生成 logical ID，revision 继承 logical ID 并递增 revision。当前 revision 只关联促成本次 revision 的 candidates/sources，旧 revision provenance 不再挤占 `memory_read` 的当前证据。

identity resolver 在 `user_preference` 与 `project_decision` 之间共同查找同 subject/applicability 候选，允许 revision 改变 kind；discussion 只在本 thread 内匹配。不同路径条件可以共存，具体路径优先。缺失的 duplicate 不启动周期性全库 Agent，只在后续相关 candidate 到来时修复。

### 4. 写入信号必须来自用户行为

单纯“LLM 提出补丁，用户 apply”不生成长期 candidate。明确用户陈述可以产生 hard item；用户用短确认采纳上一轮 LLM 决策时必须同时绑定 assistant 决策与 user confirmation。当前/本次局部纠正只形成 runtime constraint；明确项目长期规则才能写 durable `always`。局部性按来源 quote 对应的完整 RAW user clause 判断，repair intent 中的“这里”“当前补丁”等限制不能通过截短 quote 或改变 candidate kind 绕过。`discussion_context` 的代码事实过滤覆盖 `subject`、`statement` 和 `content` 的完整可注入语义。

当当前 user turn 明确提出尚未决定、需要后续延续的项目方案取舍时，extraction 必须以该 user exact quote 生成 non-factual `discussion_context`。同一 turn 中 assistant 单方面宣称已决定不能生成 preference/decision，也不能进入 discussion 的可注入语义；这与没有用户支持的 assistant-only candidate 拒绝边界分开处理。

重复纠正可以形成 soft `inferred_repetition`，但至少需要三次语义一致、跨两个文件且绑定三个不同 finding `evidence_key` 的信号；三个不同 user source turn 必须能一对一绑定互不相同的 key，且这些支持来源自身覆盖至少两个 path，不能用单个来源携带的 key 并集替代来源独立性。repair review turn 使用 `<source_scan_id>:<finding_id>` 作为 evidence identity，避免不同 scan 都从 `F1` 开始时发生碰撞。它永远是 `on_match`，不能覆盖 explicit item。extraction 额外读取同 thread 最近最多 20 个、合计最多 32K tokens 的 repair evidence，使已经处理过的早期纠正仍可参与后续归纳；所有非 inferred candidate 都必须先引用当前 extraction batch 的 user turn，不能因 `discussion_context` 的分支提前返回而绕过。最新同 subject/scope explicit revision 替代旧 explicit，explicit 高于 inferred，反向重复信号只能撤回 inferred。

extraction 继续允许合法 no-candidate；thread checkpoint 与 candidate admission 分开。RAW user turn 在主调用前写入，用户实际看到的 final assistant text 在完成边界保存，failed/interrupted turn 继续由 durable job处理。所有新 turn 的 `scope_paths` 在 Store 持久化边界按输入顺序保留最多 `MAX_SCOPE_PATHS=10` 条，避免大型 project scope 无界进入 SQLite 与 extraction payload；实际扫描、focus 和 patch 行为不受影响，既有记录不迁移或读取时回退截断。

candidate provenance 同时受持久化 RAW turn 与本次 extraction 实际投影约束。pipeline 将裁剪后的 primary、仍保留的 adjacent turn 和未卸载 repair evidence 作为 typed source envelope 交给 Store；quote 必须同时是 envelope 可见文本和对应 RAW role 文本的精确子串。Store 仍使用完整 RAW clause 判断局部性、scope 与 evidence 语义，但不得重新扩张已卸载来源或 primary 被省略的中段作为本次可引用输入。

中文 `project_decision` 的 extraction 还必须生成稳定的英文语义 alias：alias 既要覆盖决定对象，也要覆盖动作、约束或取舍，不能把原文中的 `memory schema` 等技术名词直接复制为英文后就视为完成跨语言召回。例如“旧 memory schema 不兼容，直接删除”至少应生成 `backward compatibility`，并可补充 `delete legacy schema`；keywords 继续保存原子术语，不能替代这种多词语义 alias。consolidation 对 create/revise/supersede 必须保留仍适用的 candidate/active recall metadata，并补足缺失的稳定跨语言表达；`discussion_context` 不承担该硬要求。

这一语义质量由既有 extraction/consolidation prompt、确定性 corpus 与显式授权的真实 LLM 集成测试共同作为阻断门槛，不新增生产二次 LLM 校验、翻译词典、embedding 或 reranker。任意模型输出仍可能违反语义契约，因此真实集成失败时保持 change 未完成并修复 prompt，而不是放宽查询断言或改变确定性检索门槛。

`code_audit` 将稳定 workflow summary 作为用户可见 final assistant text 保存：零 finding 时记录二次复核与待审补丁结果，普通审计记录 finding、待审补丁、失败与剩余数量。Scanner 失败必须进入 failed turn；失败收尾中的 Memory contract error 只能作为 cause 保留，不能替换原始业务异常。

`patch_revise` 同样由 workflow 而不是 presenter 生成 final assistant text。Agent 执行只负责产生候选草案；workflow 必须先取出修订草案、完成 finding binding 校验与当前补丁替换，再以“已更新”“未生成”或“binding 校验失败”的稳定 summary 完成 turn 并向用户显示。不得把 LLM raw answer，或 pending review 下为空的 `display_answer`，持久化为修订完成结果。

### 5. 双通道 RecallQuery 与渐进读取

程序从 intent、当前用户原文、thread、focus paths，以及按 intent 可用的 finding `path/check_id/message`、patch file/bound finding 或 code scope 构造 `RecallQuery`；不把源码全文、snippet、diff、assistant rationale、旧回答或 tool output作为 query。

检索先执行 kind/thread/path eligibility，再构建两条 lane：

- `standing`：`recall_mode=always` 且路径适用，不要求 query 词项命中。
- `relevant`：`on_match` 必须命中 subject/alias/check_id/keyword，或至少两个不同 content terms；路径本身不能成为唯一命中。

词项执行 NFKC、case-fold、repo path normalization，并拆分 camelCase、snake_case、包名和路径段；连续汉字段在保留完整 term 后追加去重的滑动双字词项，不生成噪声更高的单字 term。完整 term 和既有 Java/path term 优先进入上限，每次最多 32 个 query terms。排序为 path specificity、subject/check-id exactness、alias/keyword、distinct content coverage、origin/strength、updated_at、stable ID；相关性不足时 abstain，不以 recency 补位。检索仍使用 SQLite 确定性 term tables，不使用 LLM reranker。

durable recall 总上限为 `clamp(input_capacity * 12%, 4K, 24K)`，Memory Map target 为实际 recall budget 的一半且最多 8K。Map 直接提供 bounded statement；search 最多四个不同 query、每次八个 hit，read 最多八个不同 ID，并共享剩余 recall token pool。

显式 `memory_search(query=...)` 通过 typed `RecallQuery.search_text` 保留独立边界，并在 32 个 query terms 上限内先于 base finding/user terms 展开。没有显式 search 的自动 Map 保持既有 `check_id`、finding message、bound finding ID、当前 user text 顺序，避免修复 search 漏词时改变自动召回权重。

`memory_read` 的工具错误按 typed exception 分类投影：`MemoryNotFoundError` 只表示该 ID 不存在或当前 policy 不可读取；`MemoryStorageError` 及其 schema/corruption 子类产生明确 degraded notice 并让主 ReAct 继续；`MemoryContractError` 明确报告请求额度或 token pool 约束，不伪装为缺失，也不标记为 storage degraded。其他异常保留为工具执行异常，不映射成“Memory 不存在”。

### 6. Request-local RecallPolicy 是安全边界

每个请求绑定 typed `RecallPolicy`、thread、intent、path、调用预算和 readable-ID allowlist。Map 暴露的 ID 以及本轮 search hit 才能被 read；LLM 参数不能改变 policy。

request-local binding 只由创建它的请求在 `finally` 中清除。`/new` 可以清理 review/runtime 状态并切换 active thread，但不能通过全局 history reset 提前清除已 admission 请求的 policy、search/read 预算或 readable-ID allowlist。

ordinary intent 可以读取项目 preference/decision 与当前-thread discussion。repair intent 可以获得项目 preference/decision 的 Map 和 `memory_search/read`，但 repository 层强制排除 discussion、archived thread 和任意 RAW history。semantic item 的 bounded provenance quote 只证明用户表达来源，不能作为源码或 finding 证据。

Memory Map 不再进入 system prompt，而作为 request-local advisory user context 放在 recent history 之后、当前用户消息之前；thread checkpoint 使用独立的 session-continuity user context 放在 recent history 之前，因此它不占 durable recall pool。两者都不写入 turn、history 或 extraction。system contract 明确当前用户指令优先且 Memory 非源码事实。每个 ReAct step 都在同一 policy 下刷新 Map；hard rebuild 使用减半 Map budget。

### 7. Thread boundary、degraded 与后台任务

一个项目可以累积多个 thread，但只有一个 active thread。`/new` 先捕获旧 thread 当前 job watermark，并最多同步等待 5 秒；完成或超时后都归档旧 thread、清理 review runtime 并创建新 thread。超时时显示警告，watermark 内 extraction 及其派生 consolidation 在 daemon/启动恢复中继续处理。

Memory bootstrap/schema/storage degraded 时，Memory 自身停止 admission、projection、search、read 和后台写入；status/show/export/clear 保持诊断恢复边界。主扫描、源码读取和 patch review 继续运行，并只提示本轮未应用项目 Memory，因为 Memory 不是 patch evidence。ordinary request 在 admission 已显示 degraded notice 后把 thread history 视为空投影，不能再次触发同一 storage failure 而中止主调用。若 Memory Map admission 成功但 active thread checkpoint 读取发生 storage error，本请求不得绑定半完成的 request state，并以空 checkpoint/Map 继续主 Agent。主业务完成后的 turn 收尾若遇到 storage、lease、not-found 或 thread-conflict 等 operational Memory error，同样只进入可见降级并保留主业务结果；`MemoryContractError` 与普通编程错误继续显式失败。

已初始化 Store 在每次 operational/status 入口检查 `user_version`、SQLite schema cookie 与 bootstrap singleton；cookie 变化时执行完整 catalog、quick check 与 foreign-key check。显式 clear 若发现运行时 schema/corruption damage，先停止并等待 manager 管理的后台线程，再删除明确的 database/WAL/SHM 工件并创建干净 v3，不尝试迁移或局部修补损坏 catalog。

summary snapshot 属于可信 projection，不是诊断恢复路径；它必须在同一个 read transaction 中执行 runtime guard。运行期间的 user version 或 catalog 损坏应使 summary refresh 标记 stale，不能继续把旧数据库投影为 `current`。

watermark flush 与普通 worker 共享 `_process_lock`。当 `/new` 等待超时后 flush 仍在后台处理旧 thread job 时，由持锁的 flush 循环在每个 pipeline step 前续租同 manager owner 的 open turns；不新增 heartbeat thread，也不改变 5 秒等待或 120 秒 turn lease。

Watermark 的 pending/result 只统计 captured job ID 及其派生 consolidation ID。若 job 被其他 manager lease 或处于尚未到期的 retry wait，flush 在 deadline 前有界等待并继续检查；到期后保留 job 给普通 worker。现有 watermark daemon thread 由 manager 登记，`close()` 必须停止等待并 join，不能在 close 返回后继续写 SQLite 或 summary。

同一进程中的 RAW export 使用一个模块级互斥锁串行化文件发布阶段：时间戳 suffix 选择、exclusive lock-file 预留、唯一临时文件写入、原子发布与清理在同一临界区内完成；SQLite snapshot 读取和 JSON 序列化保留在临界区之外。现有 `.lock` 继续承担跨进程文件名预留，不改变顺序文件名或公共 API。Windows 若在预留阶段返回 `PermissionError`，只有对应 lock 或最终 snapshot 确实存在时才按并发占用尝试下一 suffix；两者均不存在时仍作为真实文件系统错误返回，不增加后台重试、per-directory lock registry 或新配置。

`access_count/last_accessed_at` 仅作诊断，不参与 ranking；不增加 citation popularity、自动 expiry 或 apply feedback loop。

## Risks / Trade-offs

- **[启发式 token estimator 与 provider tokenizer 不完全一致]** → 保守高估、1%/16K margin、provider overflow hard rebuild + single retry。
- **[LLM compaction 遗漏 discourse 细节]** → 当前 user/task/finding/patch binding 不交给 checkpoint 替代，保留 recent tail，并在每次 rebuild 重注入 standing Memory。
- **[standing 过多仍可能超过 bounded Map]** → 路径具体性优先、显示 truncation/omitted count，并允许 policy-filtered search/read；不建立第三套 summary service。
- **[repair Memory 影响补丁判断]** → kind/path policy 在 repository 层执行，prompt 标记 advisory，patch-safety 仍要求当前源码/finding/function tools 证据。
- **[无 embedding 导致同义词召回受限]** → 写入阶段为中文项目决定保存覆盖关键动作/约束的稳定英文 aliases/keywords，拆分 identifier/path，并用确定性与真实 LLM 检索质量 corpus 阻断回归。
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
- 2026-07-21：根据第四轮 CR 补齐所有 purpose 的最终 preflight、超长 RAW 有界投影、review item constraint、code-audit 完成语义、watermark subset/lifecycle 与 runtime schema clear，原因是局部入口防护、path key、全库计数和初始化时单次校验无法覆盖当前并发与恢复契约。
- 2026-07-21：根据第五轮 CR 对齐 consolidation registry、capacity candidate 终态、实际 extraction provenance、checkpoint 标记预算、显式 search 词项和 summary runtime guard，原因是投影边界与提交边界分叉会产生确定性重试、孤立记录和不可信 Memory 结果。
- 2026-07-23：根据第六轮 CR 将 `patch_revise` 的 turn 完成点移到草案校验与替换之后，并为 `memory_read` 区分 not-found、storage degraded 与 contract error；watermark 测试改为验证后台任务最终启动，不把异步调度扩张为 provider 必须在 timeout 返回前启动的同步契约。
- 2026-07-23：根据第七轮 CR 将 watermark thread 的停止检查、登记和启动置于同一状态锁内，并在每个 pipeline step 前检查 `_stop`；这样 `close()` 要么能登记并等待已启动线程，要么能阻止尚未启动的线程。
- 2026-07-23：根据第七轮 CR 将 consolidation 的 target allowlist 与可变 active identity registry 分离；revise/supersede 继承 logical ID 前排除当前 target 检查同 subject/applicability 冲突，冲突即事务回滚，不替换已有 identity。
- 2026-07-23：根据第七轮 CR 将 classifier 固定 prompt 超容量视为路由诊断失败并直接使用既有 `REVIEW_CONTINUE` fallback；不调用 CLASSIFIER 或 REACT provider，也不扩大通用容量 fitter 的异常范围。
- 2026-07-23：根据本轮 CR 将 compaction 回收门槛按实际 `input_capacity` 缩放；固定的 32K 下限对小窗口模型并不成立，采用 `min(32K, max(1, floor(input_capacity*10%)))` 与原请求 10% 取较大值，保留进度保护但不改变大窗口行为。
- 2026-07-23：根据本轮 CR 将 consolidation 的 bounded target allowlist 与完整 active identity registry 分离；候选上下文仍限制为相关的最多 20 条，create/revise 的重复 identity 校验则直接查询同 scope/path 的全部 active item，并在 revise 时排除当前 target。
- 2026-07-23：根据本轮 CR 让 runtime worker 发现 schema/corruption 后立即使审阅摘要失效；瞬时锁竞争仍由下一轮重试处理，不把损坏数据库继续显示为可信 `current`。
- 2026-07-23：根据本轮 CR 让 Memory Map 和 `memory_search` 使用最终注入文本/完整 tool-message wire 的估算值；search 只接纳能放入完整响应的 hit，并对该响应一次性扣除共享 durable recall 预算，不新增 function-call 字段。
- 2026-07-23：根据本轮 CR 将 watermark 的终态 capacity skip 与真正 pending/failed 分开提示；已完成且不会重试的 job 不再宣称“后台继续”。
- 2026-07-31：真实 LLM 集成测试发现中文项目决定只生成了 `memory schema` 等技术词，没有覆盖“不兼容、直接删除”的英文语义 alias；因此将 `backward compatibility` 类跨语言召回提升为 prompt 契约与阻断性质量门槛，不增加生产二次调用或新检索组件。
- 2026-07-31：真实 LLM 集成测试的确定性先行阶段在 Windows 暴露 RAW export 文件名预留竞态；因此用单一模块级锁保护同进程文件发布，并保留 `.lock` 的跨进程预留语义，避免改变文件名、SQLite snapshot 并发和公共接口。
- 2026-07-31：修复 Windows export 后的真实 LLM 集成测试发现 extraction 将“assistant 单方主张不得形成长期决定”扩大为不生成任何 candidate；因此明确保留由当前 user 原文支持的未决项目讨论，同时继续拒绝 assistant-only preference/decision。
- 2026-07-31：澄清 prompt 后同一 live case 仍以 `succeeded_no_output` 完成；审核发现 fixture 的 user 只提出一次性意见询问，并未满足“暂时不决定、后续继续评估”的 discussion 前置条件。将 fixture 改为显式持续讨论，保留 `discussion_context` 正向期望并同时禁止 preference/decision，不再通过强化生产 prompt 适配含糊测试输入。
- 2026-07-31：20.4 授权运行的前七个 live case 通过，第八个 `discussion-does-not-cross-new-thread` 因 user 只说“先讨论”而没有满足现行“暂时不决定、后续延续”契约，真实模型合理返回 no-candidate。完整审核 16-case corpus 后，只校准该 fixture 并增加独立 contract 断言，不修改生产 prompt、Store、线程边界或其他 corpus。
- 2026-07-31：校准 fixture 后的授权运行中，第 1 个 live case 通过，第 2 个 `implicit-style-is-not-preference` 因 provider 结果未满足 parser 对非空 `thread_compaction` 的契约而失败，后续 14 个 case 按首错 gate 跳过。该结果表明 prompt、parser、pipeline、Store、诊断和重试边界需要系统性重审；本 change 不通过放宽 parser 或继续强化局部 fixture 收口，问题转交后续独立 change。
- 2026-08-01：最终审计复现 watermark deadline 临界竞态：worker 可在截止点返回 `pending>0` 并先置位 completed，使主线程绕过超时分支且遗漏“后台继续”提示。返回点统一按 pending 状态补充同一安全提示；不改变 5 秒上限、job 状态、重试或线程生命周期。
