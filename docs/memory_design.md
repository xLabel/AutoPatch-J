# AutoPatch-J Agent Memory 设计说明

> 面向 `code_explain` / `general_chat` 的项目级普通问答记忆。

## 1. 为什么 AutoPatch-J 需要 Memory

AutoPatch-J 不是通用聊天机器人，而是围绕 Java 项目执行代码解释、代码审计、补丁生成和人工确认的工程型 Agent。

Memory 的目标不是保存所有对话历史，而是在不污染修复链路的前提下，让普通问答和代码解释具备跨轮、跨启动的连续性。

它要同时解决三个矛盾：

- 没有记忆，Agent 会在多轮项目问答中显得失忆。
- 全量历史进入 prompt，会增加成本、降低速度，并让回答被无关历史干扰。
- 普通聊天记忆如果进入补丁链路，会让审计和修复变得不可控。

因此，AutoPatch-J 的 memory 被设计为一套有边界、有治理、有容量限制的项目级记忆系统。

## 2. IntentType 场景边界

AutoPatch-J 的用户输入最终会被归入五类核心 `IntentType`。Memory 的第一条设计原则，就是先按意图划清边界。

| IntentType | 场景 | 读 Memory | 写 Memory | 原因 |
|---|---|---:|---:|---|
| `code_audit` | 检查代码并生成补丁 | 否 | 否 | 必须以当前 scope、finding 和源码证据为准 |
| `code_explain` | 解释项目、模块、目录或代码 | 是 | 是 | 需要继承用户对项目的关注点 |
| `general_chat` | Java、算法、调试、架构和工程常识问答 | 是 | 是 | 需要继承用户偏好和近期话题 |
| `patch_explain` | 解释当前待确认补丁 | 否 | 否 | 只应围绕当前补丁，不被普通聊天污染 |
| `patch_revise` | 重写当前待确认补丁 | 否 | 否 | 修订范围必须锁定当前补丁 |

普通问答 memory 只服务 `code_explain` 和 `general_chat`。它不是全局 Agent 记忆，也不是补丁修复记忆。

这个边界很重要：代码审计和补丁修复必须围绕当前代码证据、扫描 finding、补丁队列和用户反馈推进；普通问答 memory 可以帮助 Agent 更懂用户和项目，但不能参与决定补丁是否正确。

## 3. 设计原则

```text
raw 是材料，不是记忆。
summary 是上下文，不是事实。
long-term memory 是沉淀资产，必须经过治理。
LLM 负责理解，程序负责约束。
普通问答有记忆，补丁修复保持隔离。
```

### 边界优先

Memory 只服务偏聊天的两个流程：`code_explain` 和 `general_chat`。补丁相关流程不读取、不写入普通问答 memory。

这样做牺牲了一点“全局个性化”，但换来更关键的工程收益：审计和修复链路不会被历史聊天、旧偏好、算法题讨论或无关项目解释污染。

### 摘要优先

可注入 prompt 的内容应该是摘要、近期话题、用户偏好和项目事实，而不是完整历史。

完整历史有两个问题：

- 太长，会抢占当前问题的注意力和上下文预算。
- 太杂，里面混有示例、推测、临时问题和旧状态。

因此，`assistant_text` 可以作为后续摘要材料保存，但不会直接注入下一轮 prompt。

### 程序治理

短 LLM 只负责理解近期问答并生成 memory delta。它不能直接输出完整 `memory.json`，也不能随意决定最终写入内容。

程序负责：

- JSON parse
- schema 校验
- id 校验
- 字段长度裁剪
- 数组容量裁剪
- 来源和类型白名单
- 原子写文件

这保证了 LLM 的语义能力可以被使用，但最终状态仍由程序约束。

### 容量克制

Memory 不是知识库，也不是日志系统。V1 选择 JSON，是为了简单、可审查、可调试。

因此 memory 必须能被裁剪，能失败降级，能在 `/reset` 时清理，不能无限增长。

## 4. 分层记忆模型

Memory 分为 `working_memory` 和 `long_term_memory`。

`working_memory` 解决近期上下文连续：

- `recent_turns`：近期问答材料。
- `active_topics`：由多轮问答压缩出的近期话题。

`long_term_memory` 保存更稳定的资产：

- `durable_preferences`：用户明确表达的长期偏好或协作规则。
- `project_facts`：有仓库证据支撑的项目事实。

这四层的分工避免了一个常见问题：把一次性问题、近期话题、稳定偏好和项目事实混在同一个历史列表里。

### 4.1 recent_turns：近期材料层

`recent_turns` 是近期问答材料，不是长期记忆。

它的职责是：

- 让下一轮不会完全失忆。
- 给短 LLM 提供后续摘要材料。
- 在摘要尚未完成时，用少量用户原始问题做兜底上下文。

示例：

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

字段约束：

- `intent` 只允许 `code_explain` 或 `general_chat`。
- `user_text` 是用户原始输入的安全裁剪版。
- `assistant_text` 是最终回答的安全裁剪版，只供后续总结，不直接注入 prompt。
- `summary` 是短 LLM 生成的单轮摘要。
- `summary_status` 为 `pending` 或 `ready`。
- `scope_paths` 记录本轮代码解释相关范围，最多 10 个路径。

### 4.2 active_topics：短期工作记忆

`active_topics` 是多个 recent turn summary 合并后的近期话题。

它解决的是几轮内的话题连续，而不是永久知识沉淀。

示例：

```json
{
  "id": "topic_20260501_001",
  "label": "Java Optional",
  "summary": "用户近期关注 Optional 的正确用法，以及项目中是否存在 optional-get-without-check 类问题。",
  "related_turn_ids": ["turn_20260501_123000_001"],
  "last_touched_at": "2026-05-01T12:30:00+08:00"
}
```

特点：

- 用于近期上下文连续。
- 会被新话题淘汰。
- 不承诺永久保存。

### 4.3 durable_preferences：稳定用户偏好

`durable_preferences` 保存用户明确表达的稳定规则和协作偏好。

示例：

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

### 4.4 project_facts：有证据的项目事实

`project_facts` 保存当前项目的稳定事实。

示例：

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

项目事实必须有证据来源，例如：

- README
- 配置文件
- 代码常量
- 用户明确确认
- 多轮项目解释中稳定出现且未冲突的事实

不能只靠 LLM 猜测写入 `project_facts`。如果没有仓库证据，只能进入 `active_topics`，不能进入长期项目事实。

## 5. 阈值设计依据

V1 的阈值不是理论最优值，而是工程默认值。它们服务三个目标：

- 控制短 LLM 调用成本。
- 减少无关历史对 prompt 的污染。
- 避免过早把临时问题沉淀为长期记忆。

这些数字后续可以根据真实使用数据调整，但 V1 需要先有一组保守、可解释、可测试的默认值。

### 5.1 摘要触发阈值

`pending_turns >= 2` 触发摘要。

原因：

- 每轮都总结会增加短 LLM 调用成本。
- 完全不总结又会让下一轮缺少稳定上下文。
- 2 条 pending turn 把短期失忆窗口控制在 1-2 轮内，是成本和连续性的折中。

`recent_turns >= 6` 触发摘要。

原因：

- 一两轮对话往往只是临时问题，不足以判断稳定话题。
- 6 轮左右通常已经能看出用户是否围绕同一主题持续追问。
- 这个阈值可以降低把一次性问题误判为长期关注点的概率。

本轮是项目级 `code_explain` 时触发摘要。

原因：

- 项目级解释往往包含项目身份、模块结构、启动方式等高价值事实。
- 这类信息下次启动后仍然有用。
- 但写入 `project_facts` 仍必须经过程序侧证据约束。

### 5.2 保存容量阈值

`recent_turns max 12`。

原因：

- CLI 的一段自然对话通常能被 12 条 recent turn 覆盖。
- 超过这个数量后，旧 turn 更适合被压缩为 topic 或 summary。
- 保留更多 raw turn 会让 JSON 增长，并增加后续摘要噪声。

`active_topics max 8`。

原因：

- 工作记忆应该反映近期活跃问题，不应该变成主题仓库。
- 8 个 topic 足以覆盖普通 CLI 会话中的几个并行关注点。
- 太多 topic 会让检索和 prompt 注入变得不稳定。

`long_term max 50`。

原因：

- V1 使用 JSON，优先保证可读、可审查、可维护。
- 长期记忆是沉淀资产，不应该快速膨胀。
- 50 条足够覆盖稳定偏好和项目事实，同时仍适合人工检查。

### 5.3 Prompt 注入阈值

`ready summaries max 3`。

原因：

- Memory 是辅助上下文，不是当前问题主体。
- 3 条摘要可以提供连续性，但不会明显抢占当前问题注意力。
- 如果注入更多摘要，LLM 更容易被历史牵引，回答变得发散。

`pending user_text max 2`。

原因：

- pending turn 尚未摘要，只能作为线索。
- 注入太多未治理的用户原文，本质上又退回历史重放。
- 2 条足以覆盖最近未摘要问题，避免短期断层。

`durable_preferences max 5`、`project_facts max 5`、`active_topics max 3`。

原因：

- 长期偏好和项目事实价值高，但仍需要相关性筛选。
- 注入过多长期信息会降低当前问题权重。
- V1 先用小而稳定的默认值，后续再根据真实回答质量调整。

### 5.4 文件大小阈值

`24KB` 是软限制，超过后触发整理。

`32KB` 是硬限制，超过后必须裁剪 `working_memory` 或拒绝本轮写入。

原因：

- JSON 文件应该保持可读、可 diff、可人工检查。
- 文件过大意味着 prompt 成本和摘要成本都会上升。
- `long_term_memory` 优先保留，`working_memory` 可以裁剪，因为它本来就是近期材料。

## 6. 运行链路

普通问答 memory 的主链路如下：

```text
code_explain/general_chat 完成
-> 写入 recent_turn，summary_status=pending
-> 判断是否触发摘要
-> 短 LLM 生成 memory delta
-> 程序校验 delta
-> 写回 active_topics / long_term_memory
-> 下一轮构造 prompt 时只注入相关摘要
```

摘要是被动触发的。AutoPatch-J 启动时不会自动后台总结，也不会监听文件变化。

这样设计是为了避免两个问题：

- CLI 初始化阶段产生不可见成本。
- 启动后没有用户行为，却发生不可预期写入。

## 7. 短 LLM 与 Delta 写入

短 LLM 不输出完整 memory 文件。

它只输出 memory delta：

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

如果 LLM 输出不存在的 id、非法类型、超出范围的项目事实来源，程序会拒绝对应 operation。

这套 delta 机制的价值是：

- LLM 不能随意改坏整个 memory 文件。
- 长期记忆不依赖 LLM 自由生成 key。
- 非法 delta 可以丢弃，不影响已有记忆。
- 项目事实必须有仓库证据，不能由 LLM 猜测沉淀。

## 8. Prompt 注入规则

进入 `code_explain` / `general_chat` 前，系统会构造 memory context。

注入顺序：

1. 相关 `durable_preferences`，最多 5 条。
2. 相关 `project_facts`，最多 5 条。
3. 相关 `active_topics`，最多 3 条。
4. ready 的 recent turn summaries，最多 3 条。
5. pending recent turn 的 `user_text`，最多 2 条。

严格禁止注入：

- `assistant_text`
- 源码全文
- 补丁 diff
- 工具 observation
- reasoning

如果 recent turn 没有 summary，只注入 `user_text`，并标记为“尚未摘要”。不要注入 `assistant_text` 的本地截断版，因为它不能保证代表用户关注点。

## 9. 架构边界与后续演进

### V1 明确不做

- 跨项目记忆。
- 补丁记忆。
- 源码全文记忆。
- 工具输出记忆。
- reasoning 记忆。
- 数据库、embedding、RAG。
- 用户手动编辑 memory 的复杂 UI。
- 自动把 LLM 猜测沉淀为项目事实。

这些限制不是能力缺失，而是 V1 的架构边界。Memory 先服务普通问答连续性，同时保持代码修复链路稳定可控。

### 为什么现在不做

V1 选择 JSON，不使用数据库或向量检索，是为了保持实现简单、状态可审查、失败可恢复。

当前最重要的目标不是把 memory 做成完整知识库，而是先把普通问答的连续性做扎实，并确保它不会影响审计和补丁修复。

过早引入数据库、embedding 或 RAG，会带来额外的索引、迁移、召回和调试复杂度。在当前数据量较小、记忆内容需要人工可审查的阶段，JSON 是更合适的 V1 形态。

同样，补丁记忆、源码全文记忆和 reasoning 记忆也不是简单的“保存更多信息”。这些内容一旦进入长期上下文，很容易让 Agent 在后续修复时扩大范围、引用过期证据，或者把历史解释误当作当前事实。

Memory 的失败策略也服务这个边界：LLM delta 非法时丢弃，不写坏已有 memory；写入失败不影响用户主流程；`/reset` 可以清理 memory，并丢弃未完成摘要结果。

### 未来可以怎么演进

如果真实使用中出现以下问题，可以逐步演进：

- memory 条目增长后，简单相关性检索不够准确。
- 多项目之间需要共享用户稳定偏好。
- 项目事实需要更强的证据索引。
- 用户需要可视化查看、编辑、禁用某条记忆。

演进方向可以包括：

- 将存储从 JSON 平滑迁移到 SQLite。
- 给长期记忆增加 embedding 检索。
- 增加 `/memory` 命令族。
- 将 `project_facts` 和代码索引、README、构建文件建立更明确的证据链接。

这些增强不应改变当前最重要的边界：普通问答 memory 不进入 `code_audit` / `patch_explain` / `patch_revise` 修复链路。
