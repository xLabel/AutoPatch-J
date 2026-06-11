# AutoPatch-J 项目协作守则

## 项目结构

AutoPatch-J 是一个面向 Java 代码修复的 Python CLI Agent。源码位于 `src/autopatch_j/`。

关键目录：

- `cli/`：命令路由、交互入口和工作流编排。
- `agent/`：ReAct 执行、任务画像和提示词资产。
- `tools/function_calls/`：暴露给 LLM function call 的工具。
- `core/`：领域服务、补丁队列、review 状态、项目索引和 memory。
- `llm/`：LLM 客户端、调用意图和供应商适配。
- `scanners/`：静态扫描器集成。
- `tests/`：pytest 测试。
- `examples/`：演示项目和示例输入。
- `docs/`：设计说明和项目文档。
- `openspec/`：OpenSpec 规格、change 和归档内容。

## 开发与验证命令

- `pip install -e .[test]`：以 editable 模式安装项目和测试依赖。
- `pytest -q`：运行完整回归测试。
- `pytest tests/test_source_read_tools.py -q`：运行单个聚焦测试文件。
- `autopatch-j`：安装后启动 CLI。

项目配置来自环境变量，例如 `AUTOPATCH_LLM_API_KEY`、`AUTOPATCH_LLM_BASE_URL` 和 `AUTOPATCH_LLM_MODEL`。

## 输出语言

- 默认使用中文输出结论、总结、验证报告、问题说明和建议。
- 技术名词、命令、文件名、路径、API、类名、函数名、配置键、协议名、产品名等需要保持英文时，保留英文原文。
- OpenSpec 相关输出同样以中文为主；正文、问题说明、建议、结论、验证报告和归档评估必须使用中文。除 `OpenSpec`、change 名称、schema 名称、命令、路径、状态枚举、代码标识符和引用原文外，不要大段输出英文。
- 如果 OpenSpec 技能模板自带英文标题或固定字段，可以保留少量字段名，但字段下的解释内容必须使用中文。
- commit message 是本规则的明确例外，必须遵守“提交规则”中的英文格式要求。

## 编码风格

- 使用 Python 3.10+ 类型标注，保持改动聚焦在当前任务。
- 优先沿用项目既有模式，不为局部问题引入不必要的新抽象。
- 函数、变量和模块使用 `snake_case`；类使用 `PascalCase`。
- function call 工具名以 `FunctionToolName` 等枚举式约束为准。
- 注释只写能解释非显然行为的内容，避免重复代码本身。

## 测试要求

- 测试框架使用 `pytest`。
- 行为变更必须增加或更新聚焦测试，尤其是工具 schema、任务画像、路径/focus 约束、补丁生成、源码读取和 CLI 工作流。
- 小范围改动优先运行相关聚焦测试；跨模块重构或核心流程调整后运行 `pytest -q`。
- 测试名称使用 `test_...`，fixture 优先保持局部，只有复用价值明确时才上提。
- 未确认需要兼容旧行为时，不要为了旧格式、旧字段或旧 API 增加测试。

## 修改授权

- 修改或创建项目文件前必须获得用户明确授权。
- 未获得授权时，只能做只读分析、规划和建议。
- 用户本地或个人规则放在 `AGENTS.local.md`，不得提交该文件。
- 工作区可能已有用户或其他工具产生的改动；不要回滚、覆盖或格式化与当前任务无关的文件。

## 提交规则

- 只有在用户完成 review 或明确批准 commit 后，才可以提交代码。
- commit 前必须运行 `git status --short`，确认工作区状态。
- 只 stage 当前任务实际处理过的文件，必须使用精确路径，不得用 `git add .` 等方式纳入无关文件。
- 未跟踪文件默认视为用户文件，除非确认是当前任务创建，否则不要提交。
- commit message 必须全程使用英文，禁止出现中文；该规则适用于 subject 和 body。
- commit subject 必须使用 `<type>: <lowercase english phrase>` 格式。
- 允许的 type 包括 `fix`、`feat`、`refactor`、`docs`、`test` 和 `chore`。
- commit subject 必须只使用英文小写，不得引用会引入大写字母的标识符。

示例：

- `fix: handle empty source context`
- `test: cover source block fallback`
- `refactor: split source reading tools`

Pull Request 说明应描述行为变化、列出验证命令，并注明任何面向用户的工作流或工具 schema 变化。

## 兼容逻辑决策

- 兼容逻辑、迁移逻辑、旧格式读取、旧配置字段支持、fallback 读取、旧 API 保留等，必须视为显式架构决策，不得由 LLM 默认添加。
- 当项目是否上线、是否已有外部用户、是否存在不可丢历史数据不明确时，不得自行假设“需要兼容”或“不需要兼容”。
- 准备添加兼容逻辑前，必须先说明要兼容的旧行为、不兼容的后果、代码复杂度、测试成本、心智负担，以及推荐选择和理由。
- 只有用户明确要求、OpenSpec 的 `proposal.md`/`design.md`/`spec.md` 明确要求，或已确认存在上线版本、外部用户、生产数据、不可自动恢复历史状态时，才可以实现兼容逻辑。
- 如果决定不做兼容，应按最新规格实现干净逻辑，不为旧 schema、旧配置、旧状态或旧行为增加读取、迁移或测试。
- 如果决定做兼容，必须同步写入 OpenSpec 产物，并明确兼容范围、触发条件、保留期限或后续清理条件。

## OpenSpec 使用

- 使用 OpenSpec 技能或命令前，如果已有当前 change，必须读取该 change 的 `proposal.md`、`design.md`、`tasks.md` 和相关 `spec.md`，不要只凭用户描述执行。
- 如果是新建 change，应先按 explore/propose 流程建立规格产物，再进入实现或验证。
- `proposal.md` 表示目标、原因、范围和影响；修订时应保持当前目标清晰，重大范围变化必须显式记录。
- `spec.md` 表示最终行为契约；修订时以最新真相为准，可以整理为干净版本，不保留历史流水账，不允许保留互相冲突的新旧行为。
- `design.md` 表示技术方案和关键决策理由；正文必须描述当前方案，重大技术转向应追加简短决策记录。
- `tasks.md` 表示执行账本；change 一旦执行过 `apply`，已完成 `[x]` 任务不得静默删除、无痕覆盖或改写成从未发生过的样子。
- 未执行 `apply` 前，OpenSpec artifacts 可以重排、合并或重写；执行过 `apply` 后，`tasks.md` 只能追加、标注替代或补充说明。
- 如果 `verify`、用户反馈或实现发现导致 `proposal.md`、`design.md` 或 `spec.md` 变化，必须在 `tasks.md` 追加新的任务阶段，例如“规格重对齐”“验证发现后的修复”“后续代码修复”。
- 如果旧任务与最新 `proposal.md`、`design.md` 或 `spec.md` 不一致，必须在 `tasks.md` 标注该任务已被后续任务替代，不能删除旧任务来掩盖变更历史。
- `proposal.md`、`design.md` 或 `spec.md` 更新后，必须重新审视 `tasks.md`，确保新增、删除、替代和验证任务覆盖上游规格变化。
- 如果目标、能力边界或 change 名称已经不能准确描述当前工作，应新建 change，不要继续塞进原 change。
- 执行 OpenSpec `apply`、`verify` 或 `archive` 前后，应优先运行严格校验，例如 `openspec validate <change> --strict`。
- 在 Windows PowerShell 被执行策略拦截时，可改用 `openspec.cmd validate <change> --strict` 或 `cmd /c openspec validate <change> --strict`。
- OpenSpec 生成或修改的规格文档必须避免模板残留、互相矛盾的约束和未解释的重大方案跳变。
- `tasks.md` 不记录每个命令或微小编辑，只记录有审计价值的规格、实现、测试、验证和修复节点。

### OpenSpec 示例

`design.md` 重大转向记录模板：

- `<YYYY-MM-DD>`：将 `<旧方案/模糊点>` 调整为 `<新方案>`，原因是 `<关键原因>`。

`tasks.md` 被替代任务标注模板：

- `[x] 1.3 初始实现 <旧方案>。（后续由 2.2 替代：<新方案>）`

`tasks.md` 追加修复阶段模板：

```md
## 2. 规格重对齐

- [x] 2.1 更新 `proposal.md`，明确 <范围/目标变化>。
- [x] 2.2 更新 `design.md`，记录 <关键技术转向>。
- [x] 2.3 更新 `spec.md`，同步 <最终行为契约>。

## 3. 后续代码修复

- [ ] 3.1 根据最新规格修复 <实现点>。
- [ ] 3.2 增加测试覆盖 <关键场景>。
- [ ] 3.3 运行测试和 OpenSpec 严格校验。
```

错误做法：

- 删除已完成任务，假装最初就是新方案。
- 在 `spec.md` 同时保留互相冲突的新旧行为。
- 只更新 `design.md` 或 `spec.md`，但不追加 `tasks.md` 修复阶段。
- 把当前 change 的具体技术选择写进 `AGENTS.md`，导致后续无关 change 被错误套用。
