## Context

当前 Memory 把 episode、长期条目和 pending 状态放在单个 JSON 文件中，通过单实例 `RLock` 做 `load-modify-save`。这种实现无法覆盖多 CLI/manager 并发，摘要任务也只存在于 `ThreadPoolExecutor` 内；进程退出、执行中新增任务、模型失败和 JSON 容量裁剪都会造成静默丢失。另一方面，`Agent._messages` 是所有 intent 共用的易失列表，普通对话既不能跨重启延续，也会与 repair trace 共用生命周期。

新系统仍是本地、单 repo、无需 daemon 的 CLI 能力。Memory 只处理 `code_explain` 与 `general_chat`，保存 RAW user/final-assistant 文本；代码事实实时读取，repair 流程必须保持无 Memory、无持久历史。项目尚未上线，因此不承担 v1 兼容或历史数据迁移。

## Goals / Non-Goals

**Goals:**

- 一个 repo 一个可跨重启延续的 active ordinary thread，显式 `/new` 才切换。
- 使用 SQLite 事务、lease、retry 和 generation fencing 保证 turn、job 与 consolidation 结果可恢复。
- 通过 extraction + consolidation 形成可审计的偏好、决定和非事实讨论上下文。
- 用小型 routing context 和 `memory_search`/`memory_read` 渐进读取，保持上下文预算和证据来源。
- 将 ordinary、audit、patch explain/revise 的请求历史彻底隔离。
- 让用户可查看、遗忘、清空和导出 Memory，并让所有失败可见。

**Non-Goals:**

- 不使用 embedding、向量数据库、FTS5 或外部 Memory 服务。
- 不单独持久化 reasoning、tool call、observation、system prompt、工具源码结果或 patch diff；RAW user/final-assistant turn 本身包含的文本不做剥离。
- 不做跨 repo 的个人全局 Memory。
- 不做敏感检测、标记或脱敏；整个链路保持 RAW。
- 不实现 v1 JSON reader、backup、migration 或兼容 wrapper。
- 验收修复不再改变本 change 已确定的 extraction/consolidation 1,800/2,200 output tokens、12,000 字符 recent history、4,000 字符 Memory routing context 或 `memory_read` 预算；后续以抑制幻觉和提高完成质量为目标建立独立评测 change，token 成本不作为收紧依据。

## Decisions

### 1. SQLite 是唯一运行时事实源

数据库固定为 `.autopatch-j/memory.db`，使用 `PRAGMA user_version=2`。表按职责分为 `memory_meta`、`threads`、`turns`、`memory_jobs`、`memory_candidates`、`candidate_sources`、`memory_items`、`memory_item_candidates` 和 `memory_terms`。每个 repo 独立数据库，不在行中重复 repo id。

初始化先使用不切换 journal mode 的连接识别数据库身份。`user_version=0` 只在 `sqlite_master` 不含任何非 `sqlite_*` table、view、index 或 trigger 时视为可初始化的空白库；否则立即以 schema error 降级，不扫描未知表内容，也不修改既有 schema、数据、`user_version` 或 journal mode。`user_version=2` 表示精确的 Memory v2 存储 ABI：实现使用同一份 `_SCHEMA_SQL` 在内存 SQLite 中生成规范 catalog manifest，并逐项校验所有非 `sqlite_*` table、index、view、trigger 的 type、name、owner table 与 SQLite catalog SQL，同时执行 `quick_check`、`foreign_key_check`、`memory_meta` singleton 和唯一 active thread 检查。缺失、多余或结构不同的 schema object，以及缺失的 bootstrap state，都会产生 schema error。

preflight 和 `BEGIN IMMEDIATE` 内的重校验都不切换 WAL。只有空白 v0 可以在锁内执行 DDL、写入 `memory_meta`、设置 `user_version=2` 并创建首个 active thread；既有 v2 只验证，绝不补表、补 index、补 meta 或补 active thread。验证或创建成功并提交后才启用 WAL，以保持并发 manager 初始化的确定性并避免在 degraded 前修改损坏数据库。active thread 的创建与读取使用不同 helper：只有首次 bootstrap、`/new` 和 `/memory clear --confirm` 可以显式创建，普通启动、turn 写入和 startup recovery 缺少 active thread 时必须暴露 typed schema error。未知或损坏数据库只能由用户显式 clear 删除并重建，不提供兼容读取、迁移或自动修复。

写操作使用短连接与 `BEGIN IMMEDIATE`，统一开启 WAL、foreign keys、`synchronous=NORMAL` 和 `busy_timeout`。模型调用不持有事务：事务内 claim，事务外调用 LLM，再在新事务中校验 lease owner 与 generation 后提交。相比继续加文件锁，这能直接解决多实例覆盖、半写入和 stale worker 回写。

`memory_meta.generation` 是 clear fence；`/memory clear --confirm` 先递增 generation，再删除业务数据并创建空 active thread。旧 worker 即使晚到也不能提交。

### 2. 原始对话与派生记忆分层

`turns` 永久保存 RAW user text 和用户实际看到的 final assistant text，直至显式 clear。`begin_turn` 在主模型调用前落库；`complete_turn`/`fail_turn` 在同一事务中完成状态变化并创建幂等 extraction job。open turn 绑定 manager owner 和 120 秒 lease，运行时周期 heartbeat；启动与后台恢复只把 lease 已过期的 turn 转为 `interrupted`，避免第二个 CLI 误伤仍在执行的前台请求。

派生层只有三类：repo 级 `user_preference`、repo 级 `project_decision`、thread 级且强制 non-factual 的 `discussion_context`。item 使用 append-only revision；revise/supersede 创建新行并保留旧来源。forget 只将派生 item 标为 forgotten，并 suppress 已关联 candidates；原始 turn 继续用于审计，但旧证据不能重新激活该 item。

### 3. Agent 历史改为请求级

删除全局 `_messages`。`ReActRunner` 接收本轮 initial history 并返回 `AgentRunResult(final_answer, trace_messages)`；CLI presenter 再返回 `PresentedAgentResult(raw_answer, display_answer, trace_messages)`。ordinary initial history 来自 thread compaction 与 recent completed turns，repair 永远传空历史。源码读取 cache 也改为请求级，避免过去依赖 reset history 才失效。

### 4. Stage 1 同时做 extraction 与 rolling compaction

每个 ordinary turn 对应一个 durable extraction job；pending 达 2 条或最老达到 30 秒时，按 sequence 领取最多 4 条。输入包含 previous compaction、本批 turns，以及识别“同意”等短确认所需的相邻上一 turn。

输出包含新的 `thread_compaction` 和 candidates。程序先验证 kind、source ID、role、quote 子串和长度；结构性 provenance 错误使整个输出失败。随后用本地中英文 speech-act、明显当前代码事实否决和 evidence clause 内容锚点过滤 preference/decision。短确认必须引用紧邻的 assistant proposal 与当前 user confirmation，并校验 proposal signal 和内容锚点。语义证据不足只过滤单个 candidate，不阻塞 compaction 或同批其他合法候选；其余复杂自然语言语义仍由 extraction/consolidation LLM 负责，并由 corpus、provenance 与可选 live eval 约束。

### 5. Stage 2 使用受限 operation 更新 append-only item

产生 candidate 的 batch 才创建 consolidation job。输入只包含新 candidates、通过检索词确定性找到的少量 active items 和合法 provenance；输出仅允许 `create`、`revise`、`supersede`、`reject`。程序验证所有引用后，在一个事务中应用全部 operation，任一非法则整体回滚。

新的明确 user decision 可以 supersede 旧决定，不能只凭时间戳覆盖。discussion context 绑定 thread；thread archived 后不可检索。

### 6. 检索依赖整理质量和 Agent 渐进读取

consolidation 为 item 生成短 title、aliases 和 keywords。metadata 只保存规范化整值，正文只保存 bounded token。查询做 Unicode NFKC、casefold 和空白/标点规范化，规范化后超过 256 字符直接返回空，避免超长输入放大 SQL 工作量或因截断产生假命中；合法查询以最多三段 SQLite SQL 执行单向 exact、prefix、substring、content-term 匹配。更新时间和 ID 只作稳定同分排序，零文本命中返回空。related items 同样通过批量 SQL 严格限制 kind 与 thread scope，不做逐 item N+1。此设计与 Codex/Claude 的“小索引 + 按需打开详情”一致，并避免引入 embedding/ANN 的部署和版本治理。

ordinary prompt 只注入 bounded routing context：thread compaction、少量 active preferences/decisions 和当前 thread discussion 标题/ID。`memory_search` 最多返回 5 条摘要；`memory_read` 一次读取一条及最多 3 条来源摘录。所有 repair profile 不包含这两个工具。

### 7. Lifecycle 与命令保持单一心智模型

- `/new`：先快照旧 thread 当前 pending job，让每个 ID 最多处理一次；成功 extraction 的直接 consolidation child 可在同轮完成，并发新增 job 不被持续追赶。claim 在 SQL 排序和 `LIMIT` 前应用快照 allowlist 与 thread 条件，确保并发产生的非快照 consolidation job 不会阻塞快照 child。随后 abort pending patch、清临时状态、archive 旧 thread 并创建新 thread；偏好和决定保留，旧 discussion 不再检索。
- `/reset`：只清 review workspace、scan、index 和请求缓存；保留 `memory.db`、Memory exports 与 CLI history，并在界面明确提示。
- `/memory clear --confirm`：清整个 Memory 数据集并创建空 thread；既有 export 与 CLI history 不删除。
- 退出：快照所有当前 pending extraction/consolidation job，每个 ID 最多尝试一次；失败/超时转 retry_wait，并发新增 job 留待后台或下次启动，不在退出阶段无限追赶。
- `/memory export`：产生一次性 RAW JSON snapshot，不维护 JSON mirror。相同时间戳下的并发 export 先以同目录 exclusive lock file 原子预留最终文件名；每次 export 使用唯一临时文件，写完后再原子发布，并在正常成功或错误路径清理临时文件与 lock，避免 TOCTOU 覆盖和共享 `.tmp` 竞态。

### 8. Memory LLM 使用独立 client 和明确 purpose

后台 worker 使用与前台相同配置但独立的 `LLMClient`。`MEMORY_EXTRACTION` 与 `MEMORY_CONSOLIDATION` 都关闭 stream/reasoning，temperature 为 0，分别限制 1,800/2,200 output tokens，并设置 60 秒请求 timeout。失败按 `5s、30s、2m、10m、1h capped` 重试。

所有 LLM 失败统一格式化异常类型、message、结构化 status/code、exception body 和 provider response body，最终最多保留 20,000 字符。formatter 通过 duck typing 容错读取 provider 字段，不主动访问或拼接 request messages、prompt、headers、API key 等请求侧信息，也不对 exception/body 自身做敏感信息扫描或脱敏。普通 CLI 模式只显示简洁失败状态；`AUTOPATCH_DEBUG=true` 时显示有界 RAW 诊断，所有 markup-like 内容按 literal text 渲染，不得由 Rich 二次解析。Memory job 保留各自的有界 RAW 错误；`memory_meta.last_error` 表示按 `updated_at DESC, id DESC` 确定的最新 unresolved job 错误，不得被无关 job 成功提前清空。普通 `/memory status` 只提示存在错误，debug status 显示 RAW，RAW export 保留该审计内容。worker 边界继续捕获瞬时 claim/SQLite 错误并在后续 poll 恢复。

所有可能改变 unresolved job 集合或其错误内容的事务路径都通过同一个查询重算 `memory_meta.last_error`，包括记录失败、成功解决、lease recovery 与 clear；不得由调用路径直接把本次错误写成全局错误，以免同一 `updated_at` 下绕过 `id DESC` tie-break。

## Risks / Trade-offs

- [无向量检索可能漏掉未生成 alias 的同义表达] → extraction/consolidation 同时生成中英文 retrieval terms，允许 Agent 多次改写查询，并用版本化质量 corpus 验证；只有真实评测不足时再提出独立 change。
- [RAW 对话会原样进入模型和 export] → 这是用户明确选择；系统不宣称提供脱敏能力，文档和 export 输出明确说明 RAW 边界。
- [provider RAW 诊断可能包含其回显的 prompt、认证内容或其他敏感文本] → 这是面向开发者的显式取舍；仅 debug 模式直接展示，SQLite/export 按审计语义保留且统一限制为 20,000 字符，系统不承诺脱敏。
- [退出需要等待多个远程调用] → 每个调用有 60 秒 timeout，且每个 job 只尝试一次；失败持久化后退出，不做无限 retry。
- [SQLite 损坏使 ordinary persistence 不可用] → 不自动重建或吞错；Memory degraded 并显示一次警告，repair 仍可运行，用户可显式 clear 重建。
- [精确 catalog manifest 会把手工修改或旧 DDL 认定为不兼容] → `memory.db` 是应用私有数据库，`_SCHEMA_SQL` 作为 v2 存储 ABI 冻结；任何 catalog 表达变化都必须升级 schema version，不通过兼容或自动修复放宽边界。
- [两阶段 LLM 输出可能不稳定] → 使用严格 JSON contract、程序侧 provenance/operation 校验、事务 rollback 和 deterministic canned-LLM 测试。
- [本地 speech-act 规则无法证明全部自然语言语义] → 规则只负责显式证据和明显当前代码事实门槛，复杂语义由两阶段 LLM 判断；版本化中英文 corpus、可选 live eval、可读 provenance 与用户 forget 提供剩余控制。
- [移除共享 history 会改变现有 repair 连续感] → 用户已选择 repair 每次请求完全独立，以换取最小心智负担和无污染证据链。

## Migration Plan

1. 先合入 OpenSpec contract 和 SQLite/typed facade。
2. 将 ordinary turn 生命周期与请求级 Agent history 接入 SQLite。
3. 接入两阶段 worker、渐进检索和 CLI 命令。
4. 删除 v1 JSON 实现和旧测试，重写文档。
5. 首次 v2 runtime 初始化直接 unlink `memory.json` 并创建 `memory.db`；失败则显式降级，不做 backup/migration。
6. 完成聚焦测试、全量 pytest、OpenSpec strict validate 和 verify-change 后交付 review。

项目未上线，不提供 rollback 到 v1 的数据转换；代码回滚后 v1 也不会读取 `memory.db`。

## Open Questions

无。RAW、`/new`、`/reset`、forget/clear、退出处理和无向量边界均已由用户确认。

## Decision Record

- 2026-07-13：将“启动时恢复全部 open turn”收紧为 owner lease + heartbeat，只恢复 lease 已过期的 turn，原因是并发 CLI 不得中断仍存活的前台请求。
- 2026-07-13：将退出阶段的持续 drain 调整为 pending job 快照 + 直接 consolidation child，原因是并发新增任务不能让退出时间无界增长。
- 2026-07-13：将 Python 全量/N+1 与双向模糊匹配调整为 term-type 分层 SQL 和单向匹配，原因是要同时保证无向量检索的精度、稳定性与规模可实践性。
- 2026-07-13：在 LLM 语义判断前增加 deterministic evidence gate，原因是 exact quote 只能证明来源存在，不能阻止明显代码事实被错标为长期偏好或决定。
- 2026-07-13：验证审查后将 flush allowlist 前移到 SQL claim、禁止任意 provider code 进入诊断，并限制规范化检索 query 为 256 字符，原因是并发快照、公私数据边界和输入放大必须由实现约束而不是调用方约定保证。
- 2026-07-13：根据开发者诊断优先的产品定位，以“debug-only 展示、SQLite 有界持久化”的 RAW provider exception/body 方案取代上一条记录中的 provider-safe 诊断边界；系统不主动附加 request prompt、headers 或认证配置，但 provider 返回内容保持 RAW，统一限制为 20,000 字符。context/window budget 不在本次重对齐中调整，后续由独立质量评测 change 决定。
- 2026-07-13：严格验收后将 `memory_meta.last_error` 明确为“最新 unresolved job 错误”，并要求动态错误按 literal text 渲染，原因是无关 job 成功不应隐藏尚在重试的错误，provider RAW 中的 Rich markup-like 片段也不应掩盖原始异常。
- 2026-07-13：再次严格验收后统一所有 unresolved error 变更路径的全局错误重算，并为 RAW export 增加 exclusive filename reservation 与唯一临时文件，原因是相同时间戳下仍需确定性执行 `id DESC` tie-break，且并发 export 不得覆盖、复用 `.tmp` 或随机失败。
- 2026-07-14：最终验收后将 `user_version=0` 的初始化条件收紧为“无用户 schema object 的空白数据库”，并在启用 WAL 前执行 identity preflight，原因是未知 SQLite 文件不得被静默接管或在 degraded 前改变持久 journal mode。
- 2026-07-14：再次严格验收后将已有 v2 初始化改为完整 catalog manifest 的 validate-only 路径，并将 active thread 的显式创建与普通读取分离，原因是缺失 table、constraint、index、meta 或 active thread 都不得在启动或运行期间被静默补回。
- 2026-07-14：WARNING 复核后拒绝在所有 `_connect()` 中执行 bootstrap 校验，改为事务默认 guard、选择性 operational connection 与 ordinary admission 后的请求级 thread 绑定；`memory_search` 直接使用绑定的 `thread_id` 完成既有三段 SQL，原因是必须同时让投影和业务操作 fail-closed、保留 status/show/RAW export/clear 的诊断恢复能力，并维持固定检索查询预算。
