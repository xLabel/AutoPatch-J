# AutoPatch-J Agent Memory v2

> 前半说明 Memory 的用途和用户操作，后半记录实现与排障细节。

## 一、先看整体

### 1. Memory 做什么

Memory 让同一项目里的普通对话跨 CLI 重启继续。它整理用户明确表达的偏好、已确认的项目决定，以及当前讨论需要延续的上下文。

例如：

- “以后先给结论”可以整理为用户偏好。
- “项目统一使用 Python 3.10+”可以整理为项目决定。
- 正在比较两个方案时，Memory 可以保留当前讨论脉络。

长期记忆只有三类：

| 类型 | 范围 | 内容 |
|---|---|---|
| `user_preference` | 当前项目 | 用户明确表达的回答、协作或工程偏好 |
| `project_decision` | 当前项目 | 用户明确确认的项目选择和约束 |
| `discussion_context` | 当前 thread | 延续讨论所需的非代码事实上下文 |

Memory 只服务 `code_explain` 和 `general_chat`。`code_audit`（包括 zero-finding review）、`patch_explain`、`patch_revise` 等修复流程使用独立空历史，不读取普通对话 Memory。

Memory 不是源码证据。源码、构建配置和错误现状必须实时读取，当前用户指令也始终优先于旧记忆。

系统不会单独保存 reasoning、tool call、observation、system prompt、工具读取到的源码或 patch diff。如果这些文本本来就在用户输入或最终回答中，它们仍属于该轮 RAW 原文。

### 2. Memory 如何整理历史

```text
用户输入与最终回答的原文
  → Stage 1：更新 thread 摘要并提取候选
  → Stage 2：创建、修订、替代或拒绝记忆
  → Agent 按需搜索和读取
```

没有候选是正常结果，不代表处理失败。偏好和决定必须能追溯到用户原话；用户用“同意”确认上一轮提案时，提案和确认必须一起作为来源。

### 3. thread 何时创建

每个项目只有一个 active thread（当前对话线程）。在项目目录内启动 CLI 时，系统会自动初始化 Memory；如果还没有 `.autopatch-j/memory.db`，同时创建数据库和 active thread。

active thread 只有三个创建入口：

1. 项目首次初始化 Memory。
2. 用户执行 `/new`。
3. 用户执行 `/memory clear --confirm`。

之后启动 CLI 只会验证数据库并复用已有 active thread。如果数据库已经存在，却找不到 active thread，系统会报错并停止普通 Memory 读写，不会自动补建；该状态在技术附录中称为 degraded。

健康但没有历史、摘要或记忆条目的空 Memory 是正常状态。此时历史和搜索返回空结果。

### 4. 命令边界

| 命令 | 作用 | 保留内容 |
|---|---|---|
| `/new` | 让旧 thread 的待处理任务获得一次处理机会；终止待确认补丁；清理临时 Agent 状态；归档旧 thread 并创建新 thread | 旧对话原文、用户偏好、项目决定、导出文件和 CLI history；旧 discussion 不再可见 |
| `/reset` | 清理 review workspace、scan、index 和请求缓存，并提示 Memory 仍被保留及其清理命令 | 全部 Memory、导出文件和 CLI history |
| `/memory status` | 查看健康状态、后台任务和错误 | 不修改数据 |
| `/memory list` | 列出当前可用的派生记忆 | 不修改数据 |
| `/memory show <memory-id>` | 查看记忆及其来源 | 不修改数据；bootstrap 损坏时仍可用于诊断 |
| `/memory forget <memory-id>` | 让指定 item 退出 routing 和检索，并抑制旧 candidates | 原始 turn 保留；以后新的明确表达仍可形成新记忆 |
| `/memory clear --confirm` | 阻止旧后台任务回写，删除全部 Memory 业务数据，再创建空 active thread | 已有导出文件和 CLI history |
| `/memory export` | 创建一次性 RAW JSON 快照，不覆盖旧文件，也不建立持续镜像 | 数据库内容和旧快照不变 |

### 5. 隐私与恢复

Memory 保存 RAW（未经脱敏或改写的原文）user turn 和用户实际看到的 final assistant turn，不提供敏感信息检测或脱敏。LLM 整理输入、`show` 和 export 都可能包含这些原文。

provider 的异常或响应正文也可能进入有界 RAW 诊断。系统不会主动附加 request messages、prompt、headers 或认证配置，但不会清洗 provider 返回内容本身。

数据库损坏或结构不符合契约时，普通 Memory 读写会停止并报告 degraded。`status`、`show`、RAW `export` 和显式 clear 仍可用于诊断或恢复，损坏状态不会被当成健康空 Memory。

## 二、技术附录

### 6. 数据模型与 bootstrap

`.autopatch-j/memory.db` 是唯一运行时事实源，使用 SQLite 保存：

| 表组 | 职责 |
|---|---|
| `threads` | active/archived ordinary thread 与 rolling compaction |
| `turns` | RAW user/final assistant text、intent、状态和前台 owner lease |
| `memory_jobs` | durable extraction/consolidation job、lease、retry 和错误 |
| `memory_candidates` / `candidate_sources` | Stage 1 candidate 与原文 quote |
| `memory_items` / `memory_item_candidates` / `memory_terms` | Stage 2 item revision、provenance 和检索词 |

Memory manager 使用 typed records 表示 thread、turn、job、candidate、item 和 provenance。裸 `dict` 只允许出现在严格 LLM JSON contract 和显式 export 边界；业务失败通过 typed exception 或 typed result 暴露，不返回含义不明的布尔值。

bootstrap invariant 要求以下条件同时成立：

- `user_version=2`。
- table、index、view 和 trigger 精确匹配 Memory v2 catalog。
- 存在唯一 `memory_meta(id=1)`。
- 存在唯一 active thread。

初始化根据 `_SCHEMA_SQL` 生成并缓存精确 catalog manifest，并检查 `quick_check`、`foreign_key_check`、meta singleton 和 active thread 数量。

初始化边界如下：

- 没有 `memory.db` 时创建 schema、meta 和唯一 active thread。
- `user_version=0` 只有在不存在任何非 `sqlite_*` schema object 时才作为空白库初始化。
- 既有 `user_version=2` 只验证，不执行修补性 DDL、seed 或 active thread 补建。
- 未知对象、catalog 偏差、缺表、缺约束、缺 index、缺 meta、缺 active thread 或物理损坏都进入 degraded。
- 验证失败时保留原 schema、数据和 journal mode；验证成功后才启用 WAL。
- 只有显式 `/memory clear --confirm` 可以在损坏状态下重建 meta 和 active thread。

operational guard 发现 meta 或 active thread 缺失时，会阻止 ordinary admission、history/routing projection、事务型读写、后台 recover/claim/commit、`/new`、list 和 forget，并抛出 `MemorySchemaError`。`status`、`show`、RAW export 和显式 clear 绕过该 guard，用于诊断或恢复。

项目尚未上线，因此不迁移旧版 `.autopatch-j/memory.json`。首次初始化发现该文件时会直接删除，不读取、不备份；删除失败会报告存储错误。

### 7. turn 持久化与 intent 隔离

ordinary admission 在主 LLM 调用前保存 RAW user turn，并把请求绑定到当时的 `thread_id`。主调用成功后保存用户实际看到的 final assistant text；失败时仍保留 user 原文，并将 turn 标为 `failed`，或在下次启动恢复为 `interrupted`。

数据库中的 RAW 原文不受 prompt 预算裁剪。每个 ordinary turn 最终都会进入 durable extraction queue。

请求完成 `begin_turn()` 后，即使另一个控制流执行 `/new`，该请求的 history、routing context、`memory_search` 和 `memory_read` 仍绑定旧 `thread_id`。请求结束后清除绑定。

| `IntentType` | 持久 thread | Memory Context | Memory tools |
|---|---:|---:|---:|
| `code_explain` | 是 | 是 | 是 |
| `general_chat` | 是 | 是 | 是 |
| `code_audit` | 否 | 否 | 否 |
| `patch_explain` | 否 | 否 | 否 |
| `patch_revise` | 否 | 否 | 否 |

### 8. 两阶段处理

#### Stage 1：extraction

每个 ordinary turn 最终都由 durable extraction job 处理。pending 达到 2 条时立即调度，或在最老 pending 达到 30 秒时调度；每批按 thread sequence 最多领取 4 条。

每批输出更新后的 thread compaction 和零到多条 candidate。候选校验包括：

- `user_preference` 和 `project_decision` 至少引用一段输入 user turn 的精确 quote，程序验证 quote 是 RAW 原文的子串。
- 显式中英文 speech act 和内容锚点必须足以支持偏好或决定。
- 当前代码事实、工具结果、assistant-only 主张和推测不能单独形成长期偏好或决定。
- 短确认必须同时引用相邻 assistant proposal 和当前 user confirmation，并校验 proposal signal 和内容锚点。
- `discussion_context` 必须标记为 non-factual 并绑定当前 thread。

非法 candidate 会被过滤，不阻塞合法 compaction。没有 candidate 时，job 以 `succeeded_no_output` 完成，不重复处理 turn，也不创建 consolidation job。

#### Stage 2：consolidation

存在 candidate 时才创建 serialized consolidation job。输出只接受 `create`、`revise`、`supersede` 和 `reject`。

程序验证 candidate/item ID、状态和来源，并在单一事务中应用全部 operation。任一 operation 非法都会整体回滚，原 active item view 不变。

item 使用 append-only revision。新决定可以显式 supersede 旧决定，但不能仅按时间戳覆盖冲突；forgotten item 及其旧 candidates 不再进入 routing 或检索，未来新的明确 user turn 仍可建立新记忆。

### 9. 渐进检索与上下文预算

Memory v2 不使用 embedding、向量数据库或 FTS5。整理阶段生成 `title`、`aliases` 和 `keywords`，读取路径如下：

```text
bounded Memory Context
  → memory_search(query)
  → 最多 5 条 ID/title/synopsis
  → memory_read(memory_id)
  → item detail + bounded provenance excerpts
```

检索将完整 metadata term（title、alias、keyword）与有界 content term 分开存储。前者用于 exact、prefix 和 substring，后者只在结果不足时补充。

搜索只覆盖 active repo preferences、active repo decisions 和 active-thread discussions。archived discussion、forgotten item 和 superseded item 不可用。

空 query 或规范化后超过 256 字符的 query 直接返回空。其他 query 最多执行三段 `SELECT/WITH` SQL：

1. 选择 exact 和 prefix 命中。
2. 结果不足时排除已选 item，再选择 substring 命中。
3. 结果仍不足时排除已选 item，再选择 content-term 命中。

三段查询直接返回有限的 ID、kind、title、synopsis 和 match type。搜索复用 admission 已绑定的 `thread_id`，不追加 bootstrap 查询，也不执行逐 item 的 N+1 查询。

零文本命中返回空数组，不用 confidence、importance 或 recency 补足结果。长 query 也不会仅因包含短公共词而反向命中。

ordinary request 有两个独立的有界输入：

- initial history：最多 8 个 recent completed turns，总计最多 12,000 字符；它作为请求历史传入，不属于 system prompt。
- Memory routing context：总计最多 4,000 字符，包含 bounded compaction、active preferences/decisions、active-thread discussion index，以及“Memory 不是源码证据、当前 user 指令优先”的边界提示。

compaction 自身候选上限为 4,000 字符，但注入时与其他 routing 内容共享 4,000 字符总预算。`memory_read` 只在 Agent 按需调用时返回有界正文、non-factual 标记和 provenance excerpt，不会预先注入 system prompt。

### 10. 并发、lease 与 clear fencing

SQLite 写操作使用短连接、WAL、foreign keys、`BEGIN IMMEDIATE` 和 `busy_timeout`。LLM 调用期间不持有数据库事务。

前台 open turn 和后台 job 都有 owner lease。运行中的 manager 周期 heartbeat；第二个 CLI 只恢复 lease 已过期的 open turn，将其标为 `interrupted` 并补建 extraction job。

worker 先 claim job，LLM 调用结束后再提交。提交必须同时通过 lease owner 和 generation fencing 校验，因此 clear 前已领取任务的晚到结果不能写回。

瞬时 claim 或 SQLite 异常不会永久终止后台 daemon。执行中新增的 turn 会在当前批结束后继续 drain，不会因已有 inflight job 丢失调度。

bootstrap 损坏时，显式 clear 使用以下公式创建新 generation：

```text
max(现存 meta generation, 现存 job generation, 0) + 1
```

有效旧 meta 的 `created_at` 会保留，缺失时使用当前时间；`last_error` 和 `last_succeeded_at` 会清空。clear 删除所有 thread、turn、candidate、item 和 job，再创建唯一 meta 和唯一空 active thread。

RAW export 包含 turn、持久化 last error 和完整审计关系。并发 export 即使使用同一时间戳，也会各自创建路径唯一、内容完整的 snapshot；成功或失败后都不遗留该次 export 的临时文件或 lock file。

### 11. 失败、诊断与重试

失败 job 保存 attempt、last error 和 retry time，退避节奏为 `5s → 30s → 2m → 10m → 1h capped`。lease 过期后，其他 worker 或下次启动可以继续处理。

RAW 诊断最多保留 20,000 字符，包括异常类型、message、结构化 status/code、exception body 和 provider response body。系统不主动附加 request messages、prompt、headers 或认证配置，也不对 provider 返回内容脱敏。

只要仍有带错误的 pending、leased 或 retry-wait job 未解决，无关 job 成功就不能清空全局 last error。多个未解决错误按 `updated_at DESC, id DESC` 选择全局 last error，最后一个错误解决或 clear 后才清空。

普通模式下，LLM 失败只显示简洁状态，不渲染 RAW exception 或 provider body；`/memory status` 会提示 debug 开关。`AUTOPATCH_DEBUG=true` 时显示持久化 RAW 错误，RAW export 也保留该审计内容。

classifier、memory extraction 和 memory consolidation 属于短调用，reasoning 和 streaming 都关闭，诊断记录调用 purpose 和请求策略。classifier 失败并采用安全 fallback 时，debug 显示失败原因和最终路由；fallback 到 REACT 后即使调用成功，也保留 fallback 原因。

provider 错误中的 Rich markup-like text 按普通文字显示，避免渲染错误掩盖原故障。

本诊断边界不调整 extraction/consolidation output token、routing history、Memory Context 或 `memory_read` 预算。截断或上下文过量问题应通过独立质量评测处理，不能只按 token 成本收紧预算。

### 12. 启动与退出

启动只做三类恢复：

- 验证 bootstrap invariant，并复用已有 active thread。
- 只将 owner lease 已过期的 open turn 恢复为 `interrupted` 并补建 job。
- 回收过期 job lease，唤醒 eligible job。

启动不会在既有数据库缺少 active thread 时创建新 thread。该情况报告 degraded 或抛出 `MemorySchemaError`，直到用户显式 clear。

退出时先快照当时 pending 的 extraction/consolidation job，每个 ID 最多尝试一次。快照内 extraction 成功后直接产生的 consolidation child 也可在同轮完成。

claim SQL 在排序和 `LIMIT` 前应用 snapshot allowlist 与 thread 条件，避免并发产生的非快照 consolidation job 阻塞当前 child。并发新增的无关 job 留给后台或下次启动，不会被退出流程持续追赶。

单次处理失败或达到 60 秒 request timeout 时，任务进入 retry 状态并显示退出警告。CLI 随后继续退出。

### 13. 质量验证

默认 pytest 使用 canned LLM 验证 storage、turn/job lease、retry、provenance、consolidation、三段 SQL retrieval、CLI 和 intent 隔离。版本化中英文 quality corpus 覆盖明确偏好、临时表达、短确认、决定反转、代码事实错标、assistant-only 主张、跨 thread、遗忘和无关 query。

强不变量包括：非法 provenance 为 0、repair Memory 泄漏为 0、跨 repo 泄漏为 0、forgotten item 被旧证据重建为 0、active preference/decision provenance 完整率为 100%。可选 live eval 只在显式开关下实际调用 LLM，不影响默认 CI。

## 正式行为契约

本文只解释现有设计，不修改代码、数据库 schema、OpenSpec 契约或公开 API。若有冲突，以以下主规格为准：

- [persistent-conversation-memory](../openspec/specs/persistent-conversation-memory/spec.md)
- [typed-memory-runtime](../openspec/specs/typed-memory-runtime/spec.md)
- [llm-call-diagnostics](../openspec/specs/llm-call-diagnostics/spec.md)
