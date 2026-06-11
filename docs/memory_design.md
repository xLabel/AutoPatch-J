# AutoPatch-J Agent Memory 设计说明

> 面向 `code_explain` / `general_chat` 的项目级 Context Engine。它不是全局 Agent 记忆，也不是补丁修复记忆。

## 1. 设计目标

AutoPatch-J 的核心工作是围绕 Java 仓库做代码解释、代码审计、补丁生成和人工确认。普通问答需要连续性，但补丁修复链路必须只相信当前 scope、静态扫描 finding 和源码证据。

因此 Memory 的目标不是“让 Agent 永远记住一切”，而是：

- 让普通问答能继承用户关注点、协作偏好和项目讨论结论。
- 让长期记忆有来源、有边界、可裁剪、可审查。
- 让主 LLM 读到自然的 Markdown 结构化上下文，而不是直接吞 JSON。
- 让补丁相关流程完全不受普通聊天历史污染。

## 2. IntentType 边界

| IntentType | 读 Memory | 写 Memory | 原因 |
|---|---:|---:|---|
| `code_audit` | 否 | 否 | 代码修复必须以当前 finding 和源码证据为准 |
| `code_explain` | 是 | 是 | 需要继承用户对项目、目录、文件和代码的关注点 |
| `general_chat` | 是 | 是 | 需要继承工程问答中的用户偏好和近期话题 |
| `patch_explain` | 否 | 否 | 只解释当前待确认补丁，不读取普通问答历史 |
| `patch_revise` | 否 | 否 | 只重写当前补丁，不让历史偏好改变修订边界 |

这个边界是 Memory 设计里最重要的取舍。普通问答可以更聪明，但审计和修复必须可复核、可解释、可控。

## 3. 核心原则

```text
JSON 是事实源，Markdown 结构化上下文是注入格式。
Episode 是材料，长期记忆必须有来源。
LLM 负责理解，程序负责约束。
普通问答有记忆，补丁修复保持隔离。
```

### JSON 存储，Markdown 结构化注入

`memory.json` 是唯一持久化事实源。它方便程序做类型校验、长度裁剪、来源校验、状态失效和原子写入。

主 LLM 看到的不是原始 JSON，而是程序筛选后渲染出来的 Markdown：

```md
## Memory Context

### 用户协作偏好
- answer style: 用户偏好中文、直接、工程化的回答。

### 当前项目画像
- build tool: maven
- java version: 17

### 相关项目理解
- review module: 用户关注 review 模块如何管理 finding 队列。
```

这个组合比“全 JSON”更好读，也比“全 Markdown 存储”更可控。

### Episode Provenance

每轮普通问答先写成 episode。长期记忆不是短 LLM 凭空生成的结论，必须引用合法 `source_episode_ids`。

这使得长期记忆可以回答三个问题：

- 它从哪次对话沉淀而来？
- 它是否仍然 active？
- 如果用户否定它，应该失效哪一条？

### 程序治理

短 LLM 只能输出 Memory Delta，不能输出完整 `memory.json`。程序负责：

- JSON 解析。
- id 校验。
- `source_episode_ids` 校验。
- 类型、状态、置信度和长度校验。
- 容量治理和原子写文件。

非法 delta 被丢弃，不影响主流程。

## 4. 分层模型

Memory 当前使用单个本地 JSON 文件：`.autopatch-j/memory.json`。

顶层结构：

```json
{
  "version": 1,
  "updated_at": "",
  "repo_profile": {},
  "working_memory": {
    "active_topics": [],
    "pending_episode_ids": []
  },
  "episodic_memory": {
    "episodes": []
  },
  "semantic_memory": {
    "user_preferences": [],
    "project_notes": [],
    "codebase_concepts": []
  },
  "procedural_memory": {
    "collaboration_preferences": []
  },
  "maintenance": {
    "last_consolidated_at": "",
    "last_compacted_at": ""
  }
}
```

### repo_profile

`repo_profile` 是程序侧保守提取的仓库元信息：

- 构建工具。
- Java 版本。
- 项目名。
- 模块名。
- 明确依赖特征，例如 Spring Boot、MyBatis。
- 来源文件，例如 `pom.xml`、`build.gradle`、`settings.gradle`。

构建文件只能支撑构建事实，不会被推断成业务用途。

### working_memory

`working_memory` 保存当前会话仍活跃的轻量上下文：

- `active_topics`：近期话题。
- `pending_episode_ids`：尚未被 consolidation 处理的 episode。

它可以被快速裁剪，不承担长期事实职责。

### episodic_memory

`episodic_memory` 保存普通问答发生过的精简经历。

典型 episode：

```json
{
  "id": "episode_20260611_001",
  "intent": "code_explain",
  "user_goal": "用户想理解 review 模块职责",
  "assistant_result": "解释了 finding 队列、补丁确认和 workspace 的关系",
  "summary": "用户关注 review 模块如何管理 finding 队列和补丁确认。",
  "summary_status": "ready",
  "scope_paths": ["src/autopatch_j/core/review"],
  "importance": 3,
  "created_at": "",
  "last_accessed_at": "",
  "access_count": 0
}
```

它不是完整聊天历史，也不保存 reasoning、源码全文、补丁 diff 或工具原始输出。

### semantic_memory

`semantic_memory` 保存更稳定的语义记忆：

- `user_preferences`：用户长期偏好。
- `project_notes`：围绕当前仓库持续讨论后沉淀的项目笔记。
- `codebase_concepts`：代码库高层概念，例如关键模块职责和流程关系。

每条都必须带 `source_episode_ids`。

### procedural_memory

`procedural_memory` 保存协作方式和回答风格：

- 用户偏好中文输出。
- 用户希望先讨论计划再执行。
- 用户不喜欢过度设计。
- 用户希望修改前明确询问授权。

这些是“以后怎么协作”的规则，不和项目事实混在一起。

## 5. 写入与 Consolidation

普通问答链路：

```text
code_explain/general_chat 完成
-> append pending episode
-> 判断是否触发 consolidation
-> 后台短 LLM 读取 pending episodes 和现有 memory 摘要
-> 输出 Memory Delta
-> 程序校验并写回 memory.json
-> 下一轮普通问答注入 Markdown 结构化 Memory Context
```

触发条件：

- pending episodes >= 2。
- 已有 episodes >= 6。
- 用户输入包含明显长期偏好信号。
- 项目级 `code_explain`。
- memory 文件超过软限制。

这不是理论最优阈值，而是一组保守工程默认值：既避免每轮都调用短 LLM，又把短期失忆窗口控制在可接受范围内。

## 6. Memory Delta

短 LLM 输出示例：

```json
{
  "episode_summaries": [
    {
      "episode_id": "episode_20260611_001",
      "summary": "用户关注 review 模块如何管理 finding 队列。"
    }
  ],
  "semantic_operations": [
    {
      "operation": "create_new",
      "type": "project_note",
      "label": "review module",
      "summary": "用户关注 review 模块如何管理 finding 队列和补丁确认。",
      "source_episode_ids": ["episode_20260611_001"],
      "confidence": "high"
    }
  ],
  "procedural_operations": [
    {
      "operation": "create_new",
      "type": "collaboration_preference",
      "label": "answer style",
      "summary": "用户偏好中文、直接、工程化的回答。",
      "source_episode_ids": ["episode_20260611_001"],
      "confidence": "high"
    }
  ]
}
```

如果 delta 引用不存在的 episode id、target id、非法类型或过长字段，程序拒绝对应 operation。

## 7. Prompt 注入

进入 `code_explain` / `general_chat` 前，系统会做轻量相关性评分。当前实现优先使用本地可解释信号：

```text
score =
  lexical match
+ scope path match
+ access count
+ importance
+ confidence
```

scope path 会参与 episode 文本匹配；后续如果需要更强召回，再考虑 recency、staleness 或 embedding。当前阶段不引入向量库。

注入预算：

- procedural memory 最多 5 条。
- repo profile 有值时注入。
- semantic memory 最多 5 条。
- related episodes 最多 3 条。
- active topics 最多 3 条。
- pending user inputs 最多 2 条。

严格禁止注入：

- `assistant_result` 原文。
- 源码全文。
- 补丁 diff。
- 工具 observation。
- reasoning。
- 密钥或日志全文。

## 8. 存储与恢复

Memory 使用项目级 JSON 文件存储，位置是 `.autopatch-j/memory.json`。

选择 JSON 的原因：

- 数据量小，没必要引入数据库。
- 文件可读、可 diff，方便人工审查。
- 程序容易校验字段和裁剪容量。
- `/reset` 可以清理它并丢弃后台未写回结果。

如果 JSON 损坏，系统会把坏文件移动为 `memory.corrupt.json` 或带时间戳的备份，然后用空 Memory 继续运行。

如果版本号和当前 `MEMORY_VERSION` 不一致，系统不做 migration，直接按空 Memory 处理。项目未正式上线，优先保持当前结构干净。

## 9. 容量治理

当前阈值：

| 项 | 阈值 |
|---|---:|
| episodes | 80 |
| active topics | 8 |
| semantic memory 每类 | 50 |
| procedural memory | 30 |
| scope paths | 10 |
| user goal | 1000 字符 |
| assistant result | 2000 字符 |
| label | 60 字符 |
| summary | 300 字符 |
| soft file size | 64KB |
| hard file size | 96KB |

超过软限制时优先裁剪 `episodic_memory` 和 `working_memory`；长期语义记忆和协作偏好是治理后的资产，优先保留。

## 10. 当前明确不做

- 跨项目记忆。
- 补丁记忆。
- 源码全文记忆。
- 工具输出记忆。
- reasoning 记忆。
- MySQL、Redis、向量数据库等服务级中间件。
- 用户手动编辑 Memory 的复杂 UI。
- 自动把 LLM 猜测沉淀为长期记忆。
- 从构建文件推断业务用途。

这些限制不是能力缺失，而是当前阶段的边界。Memory 先把普通问答连续性做好，同时保持代码修复链路稳定可控。

## 11. 后续演进

如果真实使用中出现 Memory 条目明显增长、简单相关性评分不够准确、多项目偏好需要共享、仓库画像需要更强解析能力等问题，可以逐步演进：

- 增加 `/memory` 命令族，让用户查看、禁用或清理某条记忆。
- 使用本地 SQLite 替代单 JSON 文件。
- 给长期记忆增加 embedding 检索。
- 让 code index、README、构建文件和 memory 之间建立更明确的证据链接。

这些增强不应改变最重要的边界：普通问答 Memory 不进入 `code_audit` / `patch_explain` / `patch_revise` 修复链路。
