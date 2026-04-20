# AutoPatch-J Agent 指导

## 协作规则

1. 每个小范围但完整的功能点或修改点，都要及时提交到 git。
2. commit message 默认使用中文，除非是英文专有名词、代码符号或命令。
3. 每次提交前，运行和本次修改范围匹配的最小验证。
4. 不要把无关重构混在同一个 commit 里。
5. 提交时顺序执行 `git add` 和 `git commit`，不要并行执行，避免抢 `.git/index.lock`。
6. 除非用户明确要求写代码，否则设计讨论、评估和提问阶段不要自动修改文件。
7. `AGENTS.md` 默认使用中文；英文只用于 Python、CLI、Agent、function_call、scanner、Semgrep、Tree-sitter、LLM、Enum 等专有名词，或代码、命令、路径。

## 项目方向

- `AutoPatch-J` 是面向 Java 仓库的本地 CLI Agent。
- 优先使用显式、可检查的基础模块，不使用屏蔽底层细节的 Agent-SDK。
- 核心链路要保持可读：session 状态、context 构建、tool 调度、校验、人工确认门禁。
- `tools` 表示暴露给 Agent planner 的 function_call 工具。
- `scanners` 表示静态扫描器适配器，例如 Semgrep、PMD、SpotBugs、Checkstyle。
- 名称是协议时，优先使用 `Enum` 或集中常量，不在内部代码中散落裸字符串。

## 目录和命名

- Python 源码放在 `src/autopatch_j` 下，遵循常见 Python 开源项目的 `src` 布局。
- 大文件需要按职责拆分；强内聚的模块可以放到同一个子目录中。
- 文件名要和 CLI 命令、业务概念或模块职责直观对应，不保留历史命名包袱。
- 不要保留没有调用点的历史入口、兼容函数、调试函数或“以后可能会用”的代码。
- 不要引入 `catalog`、`registry`、`factory` 这类中间层，除非它们确实降低复杂度。

## Scanner 适配约定

- `src/autopatch_j/scanners` 保持轻量直观。
- 使用 `ScannerName` 表达 scanner 身份，不在内部传裸字符串。
- `ALL_SCANNERS` 是 CLI 展示和查找用的 scanner 列表。
- `get_scanner()` 是唯一 scanner 查找入口。
- `DEFAULT_SCANNER_NAME` 表达 v1 默认 scanner，目前指向 Semgrep。
- 每个 scanner 一个独立 `.py` 文件；未实现的 scanner 也保留占位实现。
- 未实现 scanner 返回 `ScannerMeta(selected=False)`，提示文案使用“接入中，敬请期待”。
- Semgrep 由 AutoPatch-J 管理，不依赖用户 `PATH` 中安装的 `semgrep`。
- scanner 规则、资源和安装辅助逻辑放在 `src/autopatch_j/scanners/resources/<scanner-name>/`。
- `tools` 可以调用 `scanners`，但 scanner 适配器不反向感知 Agent tool 调度。

## Tools 适配约定

- `tools` 是 Agent function_call 工具层，不是第三方二进制工具目录。
- `ALL_TOOLS` 是工具展示和查找用的列表。
- `get_tool()` 是唯一 tool 查找入口。
- `execute_tool()` 是统一执行入口。
- tool 名称属于协议，优先使用 `ToolName`，不要散落裸字符串。
- tool 文件名要表达能力本身，例如 `scan.py`、`edit.py`，不要重复强调项目名中已经表达的 Java 语义。

## 实现边界

- 使用 Python 做编排语言。
- 除了 Agent-SDK 之外，可以使用能降低开发成本的普通 SDK 或库。
- 避免引入重依赖，除非它明显降低复杂度。
- 修改代码时保持最小范围。
- 日常代码修改不要更新 `README.md`，除非用户明确要求更新文档。
- 不要新增、修改、检查或运行单元测试，除非用户明确要求测试工作。
- MVP 阶段项目可以没有 `tests/` 目录，优先聚焦 `src/autopatch_j` 业务逻辑。
- 自定义类名和模块名优先使用通用 LLM 命名。除非引用第三方 SDK/API 兼容面，否则避免 provider-specific 命名，例如 OpenAI。
- 环境变量不使用 `AUTOPATCH_` 前缀。
- patch 生成和 patch 应用必须保持分离。
- 有副作用的动作必须放在用户明确确认之后。

## Commit 风格

- 推荐示例：
  - `实现最小 CLI 骨架与项目初始化`
  - `接入扫描路由与 Semgrep 结果归一化`
  - `抽离 AgentDecision 决策层`

- 避免模糊 message：
  - `update`
  - `fix`
  - `misc changes`

## 验证默认值

- Python 代码修改后，优先运行 `python3 -m compileall -q src`。
- CLI 命令行为变化时，补充最小 CLI smoke 验证。
- 提交前运行 `git diff --check`。
- 除非用户明确要求，不运行或补充单元测试。

## Git 工作流坑点

- 不要并行执行 `git add` 和 `git commit`。两者都会更新 git index，可能抢 `.git/index.lock`。
- 如果 commit 遇到 index lock，先确认 lock 是否已经消失，再顺序重试，不要直接做破坏性清理。
