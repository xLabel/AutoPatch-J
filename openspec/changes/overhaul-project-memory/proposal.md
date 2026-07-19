## Why

AutoPatch-J 已有持久 Memory，但当前实现只按更新时间注入普通对话索引，repair intent 完全隔离，且没有按模型 context window 管理 recent history、tool result、compaction 与 durable recall。结果是 Memory 会“存下来却用不好”，长会话也只能依赖固定字符上限，无法发挥 DeepSeek 1M context 的能力。

## What Changes

- 新增以 DeepSeek 1M context window 为运行基线的请求级 context 管理：统一估算 token、预留输出、按压力卸载可重读 tool result、生成结构化 checkpoint，并在 provider overflow 后重建且最多重试一次。
- **BREAKING**：以干净 schema v3 重建项目级 Memory，不迁移 schema v2 或旧 `memory.json`；删除旧 JSON 清理兼容逻辑。
- 将 durable Memory 改为可执行的 semantic records：显式保存 subject、statement、strength、origin、recall mode、路径适用范围、Store-owned logical identity、revision 与当前 revision provenance。
- 将写入信号收窄为明确用户偏好、明确/采纳的项目决定及保守的重复纠正；单纯 apply 补丁不产生长期记忆，也不学习未经可靠验证的修复套路。
- 将读取改为 query-aware 双通道 Memory Map 与受限 `memory_search`/`memory_read`：standing constraint 优先，其他条目按当前 intent、用户输入、路径、finding 与补丁绑定确定性召回。
- **BREAKING**：`code_audit`、zero-finding review、`patch_explain` 与 `patch_revise` 可以读取项目内 `user_preference`/`project_decision`，但不得读取 discussion 或任意 RAW history；Memory 始终是非权威约束，当前源码和 finding 仍是唯一代码事实证据。
- 将 `/new` 改为按旧 thread watermark 有界 flush；超时后创建新 thread并明确警告，未完成 job 继续持久化重试。
- 系统接入和测试完成后重写 `docs/memory_design.md`，并在 `docs/getting_started.md` 的配置参考中记录 context window 与 output reserve 环境变量；不修改 README。

## Capabilities

### New Capabilities

- `context-window-management`: 定义 DeepSeek 1M context profile、请求预算、recent history、tool result pruning、structured checkpoint、重建与 overflow recovery。

### Modified Capabilities

- `persistent-conversation-memory`: 将 v2 普通对话 Memory 修改为 schema v3、精确信号写入、query-aware recall、repair intent 受限使用与 watermark thread boundary。
- `typed-memory-runtime`: 扩展 typed item、revision、recall policy、request-local ID admission 和 degraded projection 行为。
- `llm-prompt-structure`: 将 Memory 从 system instruction 中移出，作为当前用户消息之前的 request-local advisory context，并在 compaction 后确定性重注入。

## Impact

- 主要影响 `core/memory/`、Agent request/message/context 管理、Memory function tools、task profiles、LLM config/request handling、CLI thread lifecycle 与对应测试。
- `memory.db` schema 变为 v3，现有 v2 数据不会迁移；项目尚未发布，本 change 不承担旧数据兼容。
- 不增加 embedding、向量数据库、FTS、远端 Memory service、新 CLI 命令或第三方运行依赖。
- patch binding、源码读取、review queue、apply 与 verification 公共行为保持不变；Memory 不能绕过现有 patch-safety guardrail。
