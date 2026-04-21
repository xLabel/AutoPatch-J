# AutoPatch-J

## 项目概述

**AutoPatch-J** 是一个专为 Java 仓库设计的极简 AI 代码补丁智能体。它作为一个交互式命令行 Shell 运行，能够扫描仓库中的漏洞或问题（使用本地安装的带有 Java 规则的 Semgrep），审查发现的问题，并通过提示词驱动，自动起草、验证并应用极简的查找-替换（search-replace）代码补丁。它集成了 Tree-sitter 进行 Java 语法验证，以确保在应用生成的补丁之前其代码的完整性。

### 核心技术
*   **语言**: Python (>=3.11)
*   **UI**: `prompt_toolkit` 用于交互式 Shell，`rich` 用于格式化输出。
*   **扫描**: `semgrep`（本地化运行器，使用特定的 Java 规则 `runtime/semgrep/rules/java.yml`）。
*   **验证**: `tree-sitter` 和 `tree-sitter-java` 用于基于 AST 的 Java 代码语法检查。
*   **大模型集成**: 兼容 OpenAI 接口端点（通过 `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` 配置）。使用带有流式工具调用聚合和 JSON 模式的 LLM 规划器来进行补丁起草。

## 架构与特性
*   **交互式 Shell**: 提供一个带有 `/init`、`/status`、`/tools`、`/reindex` 等命令的交互式 REPL。
*   **提及与自动补全**: 支持 `@mention` 用于路径解析，以及 `@query` + `Tab` 用于自动补全，帮助将范围缩小到特定的文件或目录。
*   **本地状态**: 维护一个本地状态目录（`.autopatch/`）用于缓存、semgrep 执行细节和配置，从而保持工作区的整洁。
*   **编辑审查门控**: 起草的补丁会保持在挂起状态以供审查（`看看 patch`），通过 Tree-sitter 检查 Java 语法的正确性，并且只有在确认后才会被应用（`应用这个patch`）。应用后会执行重新扫描（ReScan）以验证修复。

## 构建与运行

### 设置与引导
在本地设置项目（创建 `.venv`，安装依赖项，并配置本地 `semgrep` 运行时）：
```bash
python3 scripts/bootstrap_local_runtime.py
```

### 运行 CLI
启动交互式的 AutoPatch-J shell：
```bash
.venv/bin/python -m autopatch_j
```

### 运行测试
运行单元测试套件：
```bash
python3 -m unittest discover -s tests -t . -v
```

## 开发约定

*   **虚拟环境**: 强调依赖隔离。项目依赖于其自身本地化的 `.venv` 以及特定本地实例的 `semgrep` (`runtime/semgrep/bin/<platform>/semgrep`)，有意忽略全局环境的可执行文件或路径。
*   **提示词驱动的工作流**: 功能通过斜杠命令（例如 `/init`）和由 LLM 规划器映射的自然语言请求（例如 `扫描整个仓库的问题`，`修复第1个问题`）相结合来暴露。
*   **验证优于假设**: 编辑经过严格的验证。Java 编辑在应用前严格要求进行 Tree-sitter 验证；如果缺失依赖项，编辑应用阶段将被阻塞以防止代码损坏。
*   **演示仓库**: 在 `examples/demo-repo` 提供了一个可运行的示例仓库，用于测试扫描和补丁应用工作流。

---

## Agent 协作与开发指导

以下规则和方向是作为 AI Agent 在参与项目开发时的长期行为准则。

### 协作规则
1. 每个小范围但完整的功能点或修改点，都要及时提交到 git。
2. commit message 默认使用中文，除非是英文专有名词、代码符号或命令。
3. 每次提交前，运行和本次修改范围匹配的最小验证。
4. 不要把无关重构混在同一个 commit 里。
5. 提交时顺序执行 `git add` 和 `git commit`，不要并行执行，避免抢 `.git/index.lock`。
6. 除非用户明确要求写代码，否则设计讨论、评估和提问阶段不要自动修改文件。
7. `AGENTS.md`（或此处指导）默认使用中文；英文只用于 Python、CLI、Agent、function_call、scanner、Semgrep、Tree-sitter、LLM、Enum 等专有名词，或代码、命令、路径。

### 项目方向
- `AutoPatch-J` 是面向 Java 仓库的本地 CLI Agent。
- 优先使用显式、可检查的基础模块，不使用屏蔽底层细节的 Agent-SDK。
- 核心链路要保持可读：session 状态、context 构建、tool 调度、校验、人工确认门禁。
- `tools` 表示暴露给 Agent planner 的 function_call 工具。
- `scanners` 表示静态扫描器适配器，例如 Semgrep、PMD、SpotBugs、Checkstyle。
- 名称是协议时，优先使用 `Enum` 或集中常量，不在内部代码中散落裸字符串。

### 目录和命名
- Python 源码放在 `src/autopatch_j` 下，遵循常见 Python 开源项目的 `src` 布局。
- 大文件需要按职责拆分；强内聚的模块可以放到同一个子目录中。
- 文件名要和 CLI 命令、业务概念或模块职责直观对应，不保留历史命名包袱。
- 不要保留没有调用点的历史入口、兼容函数、调试函数或“以后可能会用”的代码。
- 不要引入 `catalog`、`registry`、`factory` 这类中间层，除非它们确实降低复杂度。

### Scanner 适配约定
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

### Tools 适配约定
- `tools` 是 Agent function_call 工具层，不是第三方二进制工具目录。
- `ALL_TOOLS` 是工具展示和查找用的列表。
- `get_tool()` 是唯一 tool 查找入口。
- `execute_tool()` 是统一执行入口。
- tool 名称属于协议，优先使用 `ToolName`，不要散落裸字符串。
- tool 文件名要表达能力本身，例如 `scan.py`、`edit.py`，不要重复强调项目名中已经表达的 Java 语义。

### 实现边界
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

### Commit 风格
本项目采用 Conventional Commits 规范，并默认使用中文描述。

- **格式**: `<type>: <中文描述>`（禁止使用 scope）。
- **常用类型**:
  - `feat`: 用户可见的新功能
  - `fix`: 用户可见的问题修复
  - `docs`: 文档变更
  - `test`: 测试相关变更
  - `refactor`: 重构（不改变用户可见行为）
  - `style`: 代码格式、空格、lint（不改变逻辑）
  - `chore`: 维护性杂项
- **标题准则**:
  - 使用简洁的中文动词开头：`新增`、`修复`、`更新`、`移除`、`重构`、`优化`。
  - 中英文混排时，在中文与英文/数字/文件名之间添加空格。
  - 示例：`feat: 新增 login 命令`、`fix: 修复 config.toml 为空时的启动错误`。
- **破坏性变更**: 在 type 后加 `!`。示例：`feat!: 调整会话列表返回结构`。
- **提交粒度**: 每个 commit 应代表一个逻辑变更，避免将无关修改混在一个 commit 中。
- **AI 协作**: 
  - 严禁为了显得像人工编写而伪造人工提交记录。
  - 在提交前检查 diff，确保不包含敏感信息（secrets/tokens）或无关格式化变动。
  - 推荐流程：`git status` -> `git diff HEAD` -> `git add` -> `git commit`。

### 验证默认值
- Python 代码修改后，优先运行 `python3 -m compileall -q src`。
- CLI 命令行为变化时，补充最小 CLI smoke 验证。
- 提交前运行 `git diff --check`。
- 除非用户明确要求，不运行或补充单元测试。

### Git 工作流坑点
- 不要并行执行 `git add` 和 `git commit`。两者都会更新 git index，可能抢 `.git/index.lock`。
- 如果 commit 遇到 index lock，先确认 lock 是否已经消失，再顺序重试，不要直接做破坏性清理。