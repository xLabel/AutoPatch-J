# AutoPatch-J Agent Memory v2

> 面向 `code_explain` / `general_chat` 的项目级连续对话与长期记忆系统。它帮助 ordinary conversation，不参与代码审计和补丁修复证据链。

## 1. 目标与边界

Memory v2 解决三件事：

- 一个 repo 的普通对话跨 CLI 重启连续，直到用户执行 `/new`。
- 从 RAW 对话中形成用户明确偏好、项目决定和当前 thread 的讨论脉络。
- 通过小型 routing context 和按需工具读取历史证据，不把全部记忆塞入 prompt。

长期 item 只有三类：

| 类型 | 作用域 | 含义 |
|---|---|---|
| `user_preference` | repo | 用户明确表达的回答、协作或工程偏好 |
| `project_decision` | repo | 用户明确确认的项目选择和约束 |
| `discussion_context` | thread | 为当前讨论保留的非事实上下文 |

代码结构、构建配置、源码内容、错误当前状态等事实必须实时读取仓库，不能由 Memory 替代。系统不单独持久化 reasoning、tool call、observation、system prompt、工具读取到的源码或 patch diff；如果用户或最终回答本身包含这些文本，它们仍属于 RAW turn。

| IntentType | 持久 thread | Memory Context | Memory tools |
|---|---:|---:|---:|
| `code_explain` | 是 | 是 | 是 |
| `general_chat` | 是 | 是 | 是 |
| `code_audit` | 否 | 否 | 否 |
| `patch_explain` | 否 | 否 | 否 |
| `patch_revise` | 否 | 否 | 否 |

所有 repair 请求都使用独立空历史。

## 2. 数据与生命周期

`.autopatch-j/memory.db` 是唯一运行时事实源。SQLite 保存：

- `threads`：active/archived ordinary thread 与 rolling compaction。
- `turns`：RAW user text、用户实际看到的 final assistant text、intent、状态和前台 owner lease。
- `memory_jobs`：durable extraction/consolidation queue、lease、retry 和错误。
- `memory_candidates` / `candidate_sources`：Stage 1 候选与原文 quote。
- `memory_items` / `memory_item_candidates` / `memory_terms`：Stage 2 item revision、provenance 和检索词。

每个 repo 只有一个 active thread。主 LLM 调用前先写入 user turn；成功后保存 final assistant text，失败或中断仍保留 user 原文并进入后续处理队列。数据库原文不因 prompt 预算被裁剪。

项目尚未上线，v1 `.autopatch-j/memory.json` 会在首次初始化时直接删除，不读取、不备份、不迁移。`user_version=0` 只有在不存在任何非 `sqlite_*` schema object 时才作为空白库初始化；`user_version=2` 必须精确匹配 Memory v2 catalog、`memory_meta` singleton 和唯一 active thread。已有 table、view、index 或 trigger 的未知数据库，以及缺表、缺约束、缺 index、缺 bootstrap state 或物理损坏的 `memory.db`，都不会被静默接管、补建或重建。系统显示 degraded 状态，用户可显式执行 `/memory clear --confirm` 创建干净数据库。

Memory v2 不提供敏感检测或脱敏。持久化、模型输入、`show` 和 export 都保持 RAW；后台 job 的 provider exception/body 也会以最多 20,000 字符的 RAW 形式进入 SQLite 审计状态。

## 3. 两阶段整理

```text
ordinary turn completed/failed
  -> durable extraction job
  -> Stage 1: thread compaction + candidates
  -> durable consolidation job（仅有 candidate 时）
  -> Stage 2: create/revise/supersede/reject
  -> active routing view
```

### Stage 1：Extraction

- pending 达 2 条立即处理；最老 pending 达 30 秒也会处理。
- 每批按 thread sequence 领取最多 4 条。
- 输出一个更新后的 thread compaction，以及零到多条 candidate。
- preference/decision 必须引用 user turn 的精确 quote。
- preference/decision 还必须通过中英文显式 speech-act、当前代码事实否决和 evidence clause 内容锚点校验；语义不足的 candidate 会被过滤，不阻塞合法 compaction。
- 用户说“同意，就这么做”或 `sounds good, go with that` 等短确认时，必须同时引用紧邻的 assistant proposal 与本轮 user 确认，并校验 proposal signal 和内容锚点。
- discussion context 强制是 non-factual 并绑定 thread。
- 没有 candidate 也是正常成功，turn 不会被重复处理。

### Stage 2：Consolidation

consolidation 只接受 `create`、`revise`、`supersede` 和 `reject`。程序验证 candidate/item ID、状态和来源后，在单一事务中应用全部 operation；任一 operation 非法则整体回滚。

item 采用 append-only revision。新决定可以 supersede 旧决定，但不能靠时间戳偷偷覆盖冲突。被 forget 的 item 及其旧 candidates 不再进入 routing 或检索；未来新的明确 user turn仍可建立一条新记忆。

## 4. 无向量渐进读取

Memory v2 不使用 embedding、向量数据库或 FTS5。整理阶段为 item 生成检索型 title、aliases 和 keywords；读取阶段由 Agent 逐步缩小范围：

```text
bounded Memory Context
  -> memory_search(query)
  -> 最多 5 条 ID/title/synopsis
  -> memory_read(memory_id)
  -> item detail + bounded provenance excerpts
```

搜索只覆盖 active repo preferences、active repo decisions 和 active thread discussions。metadata 整值与 bounded content token 分开存储，规范化 query 超过 256 字符直接返回空，合法 query 最多使用三段 SQL 按单向 exact、prefix、substring、content-term 排序；不会把长 query 仅因包含短公共词而反向命中，也没有逐 item 的 N+1 查询。零文本命中返回空结果，不用 confidence、importance 或 recency 凑数。archived discussion、forgotten 和 superseded item 不可用。

ordinary request context 分为两个有界部分：

- initial history：最多 8 个 recent completed turns，合计最多 12,000 字符；它作为请求历史传入，不属于 system prompt。
- Memory routing context：总计最多 4,000 字符，包含 bounded thread compaction、active preferences/decisions、active-thread discussion index，以及“Memory 不是源码证据、当前 user 指令优先”的边界说明。compaction 自身的候选上限为 4,000 字符，但注入时与其他 routing 内容共享同一个 4,000 字符总预算。

`memory_read` 是按需调用的证据工具，不会预先将 item 正文和来源注入 system prompt。

## 5. 并发、失败与退出

SQLite 写操作使用短连接、WAL、foreign keys、`BEGIN IMMEDIATE` 和 `busy_timeout`。初始化使用 `_SCHEMA_SQL` 生成并缓存精确 SQLite catalog manifest，并检查 `quick_check`、`foreign_key_check`、`memory_meta` singleton 与唯一 active thread；已有 v2 只验证，只有空白 v0 才执行 DDL 和 bootstrap，验证成功后才启用 WAL。active thread 也只允许首次 bootstrap、`/new` 和显式 clear 创建，普通状态读取或业务写入缺失时通过 typed schema error 暴露。LLM 调用从不持有数据库事务：worker 先 claim job，调用结束后再用 lease owner 与 clear generation 校验提交。clear 发生后，旧 worker 的晚到结果不能回写。

前台 open turn 也有 owner lease。运行中的 manager 周期 heartbeat；第二个 CLI 启动时不会误伤仍有有效 lease 的 turn，只会把过期 turn 恢复为 interrupted。后台 worker 的瞬时 claim/SQLite 异常不会永久杀死 daemon。

失败会记录 attempt、last error 和 retry time，退避节奏为 `5s -> 30s -> 2m -> 10m -> 1h capped`。诊断统一保留异常类型、message、结构化 status/code、exception body 和 provider response body，最多 20,000 字符；系统不主动从 request messages、prompt、headers 或认证配置追加信息，也不对 provider 返回内容做脱敏。普通模式只显示简洁状态和 debug 提示，`AUTOPATCH_DEBUG=true` 时 `/memory status` 展示持久化 RAW 错误，RAW export 同样保留该审计内容。执行中新增的 turn 会在当前批次结束后继续 drain，不会因为已有 inflight job 而丢失触发。

当前 extraction/consolidation output token、routing history、Memory Context 和 `memory_read` 预算不是本次诊断调整范围。后续应通过独立质量评测 change 判断截断造成的信息缺失和上下文过量造成的幻觉，token 成本不作为收紧目标。

启动时系统会：

- 复用或创建 active thread。
- 只将 owner lease 已过期的 open turn 恢复为 interrupted 并补建 job。
- 回收过期 lease 并唤醒 eligible job。

退出时先快照所有当前 pending extraction/consolidation job；每个 ID 最多尝试一次，成功 extraction 直接产生的 consolidation child 也可在同轮完成。claim 会在 SQL 排序与 `LIMIT` 前应用 snapshot allowlist 和 thread 条件，避免并发产生的非快照 consolidation job 阻塞当前 child。并发新增的无关 job 留给后台或下次启动，不会被退出流程持续追赶。失败或 60 秒请求 timeout 会转入 retry 状态并告警退出。

## 6. 用户命令

```text
/new
/memory status
/memory list
/memory show <memory-id>
/memory forget <memory-id>
/memory clear --confirm
/memory export
```

- `/new`：处理旧 thread 当前 pending job 一次，abort pending patch，清临时状态，归档旧 thread 并创建新 thread。偏好和项目决定保留，旧 discussion 不再检索。
- `forget`：忘记一条派生 item 并抑制旧 candidates；原始 turn 保留。
- `clear`：删除 Memory DB 中的全部 thread、turn、candidate、item 和 job，再创建空 thread；既有 export 与 CLI history 保留。
- `export`：生成一次性、不覆盖旧文件的 RAW JSON snapshot，不建立 JSON mirror。
- `/reset`：只清 review workspace、scan、index 和请求缓存，保留 `memory.db`、Memory exports 与 CLI history；界面会明确提示该边界。

## 7. 质量验证

默认 pytest 使用 canned LLM 验证 storage、turn/job lease、retry、provenance、consolidation、分层 SQL retrieval、CLI 和 intent 隔离。版本化中英文质量 corpus 覆盖明确偏好、临时表达、短确认、决定反转、代码事实错标、assistant-only 主张、跨 thread、遗忘和无关查询。可选 live eval 只有在显式环境开关下才调用真实模型，不影响默认 CI。

强不变量包括：非法 provenance 为 0、repair Memory 泄漏为 0、跨 repo 泄漏为 0、forgotten item 被旧证据重建为 0、active preference/decision provenance 完整率为 100%。
