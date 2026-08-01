## Context

见 `proposal.md`。当前系统已经在 `AgentSession`、`ScopeResolver` 的显式路径分支和 `SourceReader` 中检查项目状态目录，但默认项目枚举、SymbolIndex、未锁定的 `search_symbols` 与 Semgrep 路径入口没有统一执行同一约束。软链接因此可以跨过前置范围筛选，直到末端读取才被拒绝。

## Goals / Non-Goals

**Goals:**

- 在每个会形成或消费源码范围的信任边界拒绝解析后位于 `.autopatch-j/**` 或仓库外的路径。
- 保持 scanner finding 的 fail-closed 原子语义，确保非法路径不能产生部分成功 artifact。
- 让既有旧索引记录在不迁移数据库的情况下立即对 LLM 不可见。

**Non-Goals:**

- 不建立新的路径类型、配置开关或索引 schema。
- 不改变正常仓库源码、内部安全软链接、歧义候选或 patch review 行为。
- 不清理或迁移历史 `index.db`；下一次正常 rebuild 自然移除非法记录。

## Decisions

### 在信任边界复用现有路径原语

各子系统继续组合 `resolve_repo_path`、`to_repo_relative_path`、`is_project_state_path` 与 `is_path_in_focus`。不新增统一 resolver 抽象，避免为一次安全修复扩大公共接口和重构面；前置枚举、工具输出、scanner 输入与 scanner 输出分别保留独立 guard，形成纵深防御。

### 有效软链接保留字面入口，安全判断使用解析目标

项目枚举和 SymbolIndex 先按解析目标验证仓库边界与状态目录，再为有效仓库内源码保留现有字面路径。这样既阻断状态目录别名，也不改变正常内部软链接的用户行为。

### Scanner 输出在读取源码前 fail closed

Semgrep target 选择静默跳过不属于源码范围的输入，沿用现有非法 target 行为。Scanner result 是外部进程返回的证据，若其路径落入状态目录，则在 `read_bytes()` 前抛出安全路径错误；现有结果聚合逻辑会把整次扫描转为 error，避免部分 finding 进入 artifact 或 backlog。

### 消费端过滤旧索引，不增加迁移

SymbolIndex rebuild 负责阻止新非法记录，`search_symbols` 无条件经过 `is_path_in_focus()`，用于屏蔽旧索引或测试替身中的非法记录。该消费端 guard 是运行时安全校验，不是旧 schema 兼容或数据迁移。

## Risks / Trade-offs

- [多个边界存在少量重复校验] → 复用同一组现有路径原语，并用聚焦测试锁定一致行为；不以新增抽象换取有限去重。
- [非法索引记录可能占用搜索 limit，减少安全候选数量] → rebuild 会清除正常运行产生的旧记录；本 change 优先闭合泄漏边界，不改变搜索分页语义。
- [只包含非法 target 的直接 scanner 调用可能得到 skipped] → 这是现有无有效 target 行为；外部 scanner 伪造非法 finding 时仍会整次 error。

## Migration Plan

- 无数据迁移和 feature flag。
- 新版本消费端过滤立即生效；项目下次执行 SymbolIndex rebuild 后移除旧非法记录。
- 回滚仅需还原本 change 的代码与规格文件，不涉及持久数据恢复。
