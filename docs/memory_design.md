# AutoPatch-J Agent Memory 设计说明

> 面向 `code_explain` / `general_chat` 的项目级普通问答记忆。它不是全局 Agent 记忆，也不是补丁修复记忆。

## 1. 为什么需要 Memory

AutoPatch-J 的主要工作不是聊天，而是围绕 Java 仓库完成代码解释、代码审计、补丁生成和人工确认。

普通问答仍然需要连续性：用户可能先问项目结构，再追问某个模块，
再切到 Java 语法或工程实践。如果每一轮都完全失忆，体验会很割裂。

但把完整历史直接塞进 prompt 也会带来问题：

- 上下文变长，成本和延迟上升。
- 无关历史会干扰当前问题。
- 历史解释、旧状态或算法题讨论可能污染补丁修复判断。

因此 memory 的目标不是保存所有对话，而是在清晰边界内沉淀少量有用上下文，让普通问答更连续，同时保证代码修复链路仍然只相信当前证据。

## 2. IntentType 边界

AutoPatch-J 的自然语言输入最终会进入五类 `IntentType`。Memory 的第一条设计原则是按意图划清边界。

| IntentType | 场景 | 读 Memory | 写 Memory | 原因 |
|---|---|---:|---:|---|
| `code_audit` | 检查代码并生成补丁 | 否 | 否 | 必须以当前 scope、finding 和源码证据为准 |
| `code_explain` | 解释项目、目录、文件或代码 | 是 | 是 | 需要继承用户对项目的关注点 |
| `general_chat` | Java、算法、调试、架构和工程常识问答 | 是 | 是 | 需要继承用户偏好和近期话题 |
| `patch_explain` | 解释当前待确认补丁 | 否 | 否 | 只应围绕当前补丁，不被普通聊天污染 |
| `patch_revise` | 重写当前待确认补丁 | 否 | 否 | 修订范围必须锁定当前补丁 |

这个边界比“让 Agent 更懂用户”更重要。审计和修复必须围绕当前代码证据、扫描 finding、补丁队列和用户反馈推进；普通问答 memory 可以帮助回答更连贯，但不能参与决定补丁是否正确。

## 3. 设计原则

```text
raw 是材料，不是记忆。
summary 是上下文，不是事实。
long-term memory 是沉淀资产，必须经过治理。
LLM 负责理解，程序负责约束。
普通问答有记忆，补丁修复保持隔离。
```

### 边界优先

Memory 只服务 `code_explain` 和 `general_chat`。补丁相关流程不读取、不写入普通问答 memory。

这会牺牲一点全局个性化，但换来更关键的工程收益：审计和修复链路不会被历史聊天、旧偏好、算法题讨论或无关项目解释污染。

### 摘要优先

可注入 prompt 的内容应该是摘要、近期话题、用户偏好和项目事实，而不是完整历史。

完整历史太长、太杂，而且混有示例、推测、临时问题和旧状态。`assistant_text` 可以作为后续摘要材料保存，但不会直接注入下一轮 prompt。

### 程序治理

短 LLM 只负责把近期问答压缩成 memory delta。它不能输出完整 `memory.json`，也不能随意决定最终写入内容。

程序负责 JSON 解析、字段校验、id 校验、来源白名单、长度裁剪、容量裁剪和原子写文件。LLM 的语义能力可以被使用，但最终状态必须由程序约束。

### 失败降级

Memory 失败不能影响主流程。

摘要失败、delta 非法、写入失败都应安静降级。
坏 JSON 会备份为 `memory.corrupt*.json` 并回退为空文档。
`/reset` 会清理 memory，并丢弃尚未写回的后台摘要结果。

## 4. 分层模型

Memory 分为 `working_memory` 和 `long_term_memory`。

`working_memory` 解决近期上下文连续：

- `recent_turns`：近期问答材料。
- `active_topics`：由多轮问答压缩出的近期话题。

`long_term_memory` 保存更稳定的资产：

- `durable_preferences`：用户明确表达的长期偏好或协作规则。
- `project_facts`：有仓库证据支撑的项目事实。

这四层避免把一次性问题、近期话题、稳定偏好和项目事实混在同一个历史列表里。

### recent_turns：近期材料层

`recent_turns` 保存最近的普通问答材料。它不是长期记忆，而是摘要器的输入和短期兜底上下文。

典型结构：

```json
{
  "id": "turn_20260501_123000_001",
  "intent": "general_chat",
  "user_text": "Optional 怎么用？",
  "assistant_text": "Optional 是 Java 8 引入的容器类型...",
  "summary": "用户关注 Java Optional 的空值建模、常用 API，以及避免直接 get 的安全用法。",
  "summary_status": "ready",
  "scope_paths": [],
  "created_at": "2026-05-01T12:30:00+08:00"
}
```

约束：

- `intent` 只允许 `code_explain` 或 `general_chat`。
- `user_text` 最多 1000 字符。
- `assistant_text` 最多 2000 字符，只供摘要，不直接注入 prompt。
- `summary` 最多 300 字符。
- `scope_paths` 最多 10 个路径。
- 总数最多 12 条。

### active_topics：短期工作记忆

`active_topics` 是多个 recent turn summary 合并后的近期话题。它解决几轮内的话题连续，不承诺永久保存。

典型结构：

```json
{
  "id": "topic_20260501_001",
  "label": "Java Optional",
  "summary": "用户近期关注 Optional 的正确用法，以及项目中是否存在 optional-get-without-check 类问题。",
  "related_turn_ids": ["turn_20260501_123000_001"],
  "last_touched_at": "2026-05-01T12:30:00+08:00"
}
```

`active_topics` 最多 8 条。旧话题会被新话题淘汰，避免工作记忆变成主题仓库。

### durable_preferences：稳定用户偏好

`durable_preferences` 保存用户明确表达的稳定规则和协作偏好。

典型结构：

```json
{
  "id": "mem_20260501_001",
  "type": "durable_preference",
  "label": "commit message format",
  "summary": "提交信息必须使用 <type>: <lowercase english phrase> 格式。",
  "status": "active",
  "source": "user_explicit",
  "created_at": "2026-05-01T12:30:00+08:00",
  "updated_at": "2026-05-01T12:30:00+08:00"
}
```

可以进入长期偏好的内容：

- 用户明确说“以后、每次、必须、不要、我希望、我不喜欢、优先、规则、守则、记住”等。
- 稳定交互偏好。
- 稳定输出格式偏好。
- 稳定协作规则。

不能进入长期偏好的内容：

- 单次算法题。
- 临时问题。
- LLM 推测出的用户意图。
- 补丁细节。
- 源码片段。
- 日志内容。

### project_facts：有证据的项目事实

`project_facts` 保存当前项目的稳定事实，必须有仓库证据支撑。

典型结构：

```json
{
  "id": "mem_20260501_002",
  "type": "project_fact",
  "label": "project identity",
  "summary": "AutoPatch-J 是面向 Java 仓库的 AI 代码修复 CLI，核心流程包括意图识别、静态扫描、补丁生成和人工确认。",
  "status": "active",
  "source": "repo_verified",
  "created_at": "2026-05-01T12:31:00+08:00",
  "updated_at": "2026-05-01T12:31:00+08:00"
}
```

当前程序侧项目证据来自仓库文件片段，候选文件包括：

- `README_CN.md`
- `README.md`
- `pom.xml`
- `build.gradle`
- `settings.gradle`

最多收集 4 份证据，每份最多 700 字符。

短 LLM 创建或更新 `project_fact` 时，必须带 `source=repo_verified` 和合法 `evidence_id`。
没有证据的项目推测只能进入 `active_topics`，不能沉淀为长期项目事实。

## 5. 阈值设计

V1 的阈值不是理论最优值，而是一组保守、可解释、可测试的工程默认值。它们服务三个目标：

- 控制短 LLM 调用成本。
- 减少无关历史对 prompt 的污染。
- 避免过早把临时问题沉淀为长期记忆。

### 摘要触发

`pending_turns >= 2` 触发摘要。

每轮都摘要会增加短 LLM 成本；完全不摘要又会让下一轮缺少稳定上下文。2 条 pending turn 把短期失忆窗口控制在 1 到 2 轮内，是成本和连续性的折中。

`recent_turns >= 6` 触发摘要。

一两轮对话往往只是临时问题，不足以判断稳定话题。6 轮左右通常已经能看出用户是否围绕同一主题持续追问。

项目级 `code_explain` 触发摘要。

项目级解释往往包含项目身份、模块结构、启动方式等高价值事实。它值得尽早尝试摘要，但写入 `project_facts` 仍必须通过程序侧证据约束。

`LONG_TERM_SIGNAL` 触发摘要。

当用户输入中出现明显长期偏好信号时，系统会尝试尽快摘要，避免“以后都这样”这类规则只停留在短期上下文里。

### 保存容量

| 项 | 阈值 | 设计理由 |
|---|---:|---|
| `recent_turns` | 12 | 覆盖一段自然 CLI 对话，超过后旧 turn 更适合压缩 |
| `active_topics` | 8 | 覆盖几个并行关注点，避免变成主题仓库 |
| `long_term_memory` | 50 | 保持 JSON 可读、可审查、可人工 diff |
| `scope_paths` | 10 | 保留代码解释范围线索，避免路径列表膨胀 |
| `user_text` | 1000 | 保留问题主体，不把长日志完整写入 memory |
| `assistant_text` | 2000 | 供摘要器理解回答，不直接注入 prompt |
| `label` | 60 | 保持列表可读 |
| `summary` | 300 | 控制 prompt 注入噪声 |

### Prompt 注入

进入 `code_explain` / `general_chat` 前，系统会构造 memory context，最多注入：

- 相关 `durable_preferences`：5 条。
- 相关 `project_facts`：5 条。
- 相关 `active_topics`：3 条。
- ready 的 recent turn summaries：3 条。
- pending recent turn 的 `user_text`：2 条。

注入顺序体现优先级：稳定偏好和项目事实优先，近期话题和问答摘要次之，未摘要用户原文只作为兜底线索。

严格禁止注入：

- `assistant_text`
- 源码全文
- 补丁 diff
- 工具 observation
- reasoning

### 文件大小

`24KB` 是软限制，超过后触发整理。`32KB` 是硬限制，超过后必须裁剪 `working_memory` 或拒绝本轮写入。

优先保留 `long_term_memory`，因为它是治理后的沉淀资产；优先裁剪 `working_memory`，因为它本来就是近期材料。

## 6. 运行链路

普通问答 memory 的主链路如下：

```text
code_explain/general_chat 完成
-> 写入 recent_turn，summary_status=pending
-> 判断是否触发摘要
-> 单线程后台调度 MemorySummarizer
-> 短 LLM 生成 memory delta
-> MemoryDeltaParser 解析 JSON
-> MemoryDeltaApplier 做程序侧硬校验
-> MemoryStore 原子写回 memory.json
-> 下一轮普通问答构造 prompt 时只注入相关摘要
```

摘要是被动触发的。AutoPatch-J 启动时不会自动后台总结，也不会监听文件变化。

后台调度器使用单线程执行，避免多个摘要任务并发写同一个 memory 文件。

如果上一轮摘要任务尚未完成，本轮不会重复提交。
`/reset` 会提升 generation，旧任务即使完成也会被丢弃，避免状态清空后又写回旧结果。

## 7. 短 LLM 与 Delta

短 LLM 不输出完整 memory 文件，只输出 delta。

示例：

```json
{
  "turn_summaries": [
    {
      "turn_id": "turn_20260501_123000_001",
      "summary": "用户关注 Java Optional 的空值建模和安全用法。"
    }
  ],
  "topic_operations": [
    {
      "operation": "create_new",
      "label": "Java Optional",
      "summary": "用户近期关注 Optional 的正确用法。",
      "related_turn_ids": ["turn_20260501_123000_001"]
    }
  ],
  "long_term_operations": [
    {
      "operation": "create_new",
      "type": "durable_preference",
      "label": "answer style",
      "summary": "用户偏好中文、工程化、直接的回答。",
      "source": "user_explicit"
    }
  ]
}
```

更新已有长期记忆时，LLM 必须使用程序提供的 `target_id`：

```json
{
  "long_term_operations": [
    {
      "operation": "update_existing",
      "target_id": "mem_20260501_001",
      "summary": "提交信息必须使用 <type>: <lowercase english phrase> 格式。"
    }
  ]
}
```

如果 LLM 输出不存在的 id、非法类型、非法来源、过长字段或无证据的项目事实，程序会拒绝对应 operation。这套机制的价值是：

- LLM 不能随意改坏整个 memory 文件。
- 长期记忆不依赖 LLM 自由生成 key。
- 非法 delta 可以丢弃，不影响已有记忆。
- 项目事实必须有仓库证据，不能由 LLM 猜测沉淀。

## 8. 存储与恢复

Memory 使用项目级 JSON 文件存储，位置在当前仓库的 `.autopatch-j/memory.json`。

选择 JSON 的原因：

- 数据量小，没必要一开始引入数据库。
- 文件可读、可 diff，方便人工审查。
- 写坏时可以备份和回退。
- `/reset` 可以把它作为可重建状态一起清理。

如果读取失败或 JSON 损坏，系统会尝试把坏文件移动为 `memory.corrupt.json` 或带时间戳的 `memory.corrupt.*.json`，然后使用空 memory 继续运行。

## 9. 当前明确不做

V1 明确不做：

- 跨项目记忆。
- 补丁记忆。
- 源码全文记忆。
- 工具输出记忆。
- reasoning 记忆。
- 数据库、embedding、RAG。
- 用户手动编辑 memory 的复杂 UI。
- 自动把 LLM 猜测沉淀为项目事实。

这些限制不是能力缺失，而是架构边界。Memory 先服务普通问答连续性，同时保持代码修复链路稳定可控。

## 10. 后续演进

如果真实使用中出现 memory 条目增长、简单相关性检索不够准确、多项目偏好需要共享、项目事实需要更强证据索引等问题，可以逐步演进：

- 将存储从 JSON 迁移到 SQLite。
- 给长期记忆增加 embedding 检索。
- 增加 `/memory` 命令族。
- 将 `project_facts` 和代码索引、README、构建文件建立更明确的证据链接。

这些增强不应改变当前最重要的边界：普通问答 memory 不进入 `code_audit` / `patch_explain` / `patch_revise` 修复链路。
