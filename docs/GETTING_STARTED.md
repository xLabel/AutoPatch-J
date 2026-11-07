# AutoPatch-J 上手与操作手册

这里集中说明 AutoPatch-J 的安装、配置和日常操作。项目定位见 [README](../README.md)，行为契约和代码依据统一见 [SPEC](SPEC.md)。

## 1. 环境要求

- Python `3.14+`
- 待检查的 Java 仓库
- OpenAI 兼容的 LLM API
- 安装 Python 依赖和 managed Semgrep runtime 所需的网络环境

目前实际使用的扫描器是 Semgrep，`/scanner` 仅显示已经实现的扫描器。其他接入方向见 README 展望。

## 2. 安装

在 AutoPatch-J 仓库根目录创建虚拟环境，并以 editable 模式安装项目。

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints.txt -e .
```

Windows（PowerShell 7）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints.txt -e .
```

安装完成后可以运行 `autopatch-j`，也可以使用 `python -m autopatch_j`。

## 3. 配置 LLM

配置必须存在于启动 CLI 的进程环境中。AutoPatch-J 不会自动读取 `.env`。`AUTOPATCH_LLM_API_KEY` 必须设置，`AUTOPATCH_LLM_BASE_URL` 和 `AUTOPATCH_LLM_MODEL` 有默认值。

macOS / Linux：

```bash
export AUTOPATCH_LLM_API_KEY="your-api-key"
export AUTOPATCH_LLM_BASE_URL="https://api.deepseek.com"
export AUTOPATCH_LLM_MODEL="deepseek-flash"
```

Windows（PowerShell 7）：

```powershell
$env:AUTOPATCH_LLM_API_KEY="your-api-key"
$env:AUTOPATCH_LLM_BASE_URL="https://api.deepseek.com"
$env:AUTOPATCH_LLM_MODEL="deepseek-flash"
```

完整环境变量、默认值和校验规则见 [SPEC 配置](SPEC.md#61-配置与协议)。`AUTOPATCH_DEBUG=true` 开启调试。启动时读取一次环境；缺 Key 或显式模型配置无效时，明确报错并保留帮助、诊断和退出；依赖模型的功能不可用。有默认值的可选参数未配置不报错，正常使用默认 DeepSeek 只需 API Key。修改环境后重新启动 CLI。

`AUTOPATCH_LLM_REQUEST_LIMIT` 控制一次 Agent 运行的模型请求次数，摘要也共享额度。未设置时使用 SDK 默认 50；正整数可提高或降低上限，`none` 取消上限。它不表示用户对话轮数、上下文容量或费用上限，`/status` 显示当前值。

使用内置 `deepseek-flash` 或 `deepseek-v4-pro` 时，应用已经定义 context profile，不接受容量覆盖。使用其他模型时，必须根据实际模型能力同时设置正整数 `AUTOPATCH_LLM_CONTEXT_WINDOW` 与 `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS`，且为输入保留安全余量；缺失或不合法会报告配置错误。应用只支持标准 Chat Completions function-call 协议，协议、流解析和 reasoning 回传由 Pydantic AI 与原生 SDK 处理，请求策略按任务用途固定。

## 4. 在 Java 仓库中启动

先激活安装了 AutoPatch-J 的虚拟环境，再进入待检查的 Java 仓库。CLI 会把启动目录当作项目根目录。

```bash
cd /path/to/your-java-repository
autopatch-j
```

也可以使用模块入口：

```bash
python -m autopatch_j
```

第一次进入项目时执行：

```text
/init
```

`/init` 会准备项目运行环境、安装或修复固定版本的 managed Semgrep runtime，并重建 Java 符号索引，保留当前任务和待审提案。扫描器准备成功后才重建索引，故障立即停止当前初始化，保留 CLI 输入。未执行成功的索引构建仍明确要求 `/init`，重启也不会把未构建状态显示为就绪。完成后可以用 `/status` 和 `/scanner` 检查状态；查询只探测版本，不触发安装。

### 运行内置 demo

仓库中的启动脚本会创建 `.venv`、安装当前项目，然后进入 `examples/demo-repo` 启动 CLI。使用 Agent 前需要设置 `AUTOPATCH_LLM_API_KEY`；缺 Key 仍可进入诊断界面。扫描器准备统一由显式 `/init` 执行。

macOS：

```bash
./run_on_macos.sh
```

Windows：

```bat
run_on_windows.bat
```

macOS 脚本会在启动前准备 managed Semgrep runtime。Windows 脚本启动 CLI 后，还需要执行 `/init` 来准备扫描器和索引。

## 5. 第一次审计

在指令中用 `@` 指定 Java 文件或目录：

```text
@src/main/java 检查这个目录中的安全问题
@src/main/java/demo/AppConfig.java 审计这个文件并给出修复建议
```

Agent 根据具体目标调用扫描、源码读取和符号查询；需要时继续阅读同仓库关联代码。扫描结果是待核查候选，不代表必须修改，零 finding 也不能否定你明确指出的缺陷。

第一次要求修复时，Agent 会准备修复目标与需要修改的确切文件。界面展示写范围申请后，输入：

```text
allow
```

`allow` 批准已展示的文件并继续准备当前提案，不修改源码。后续需要增加文件时，同样先展示并批准。单纯 `@文件`、解释或读取关联代码不会自动扩大写范围。

## 6. 审核与应用

一次只准备一项完整提案；同一问题必需的多文件、多段修改一起展示。CLI 显示所有文件的完整 diff、修改理由和语法结果。准备失败不会覆盖原待审提案。

| 输入 | 结果 |
|---|---|
| `apply` | 批准并应用已展示版本的完整提案，再反馈验证 |
| `discard` | 丢弃当前待审提案并结束任务 |
| `abort` | 明确结束当前任务，不撤销已写入源码 |

决定前可继续输入：

```text
只解释为什么需要修改调用方
这个修改会影响哪些业务行为？
将判空改为 Objects.equals，并重新展示完整提案
```

解释默认只读。修订围绕同一问题，成功后重新预览并等待 apply；失败则原提案仍待审。另一问题需要先完成当前提案，或明确 discard/abort/new，即使两个问题在同一文件。只读调查其他问题可以继续。

apply 前先检查所有文件的完整基线。任一文件变化则不写任何文件，需要按当前源码重新准备并审核。通过后逐文件原子写入；不承诺多文件整体回滚。第二个文件失败时，第一个文件可能已经生效，界面会分别说明已写、未写和不确定项。

语法通过不等于编译或业务测试通过。有可靠扫描绑定时，应用后复扫映射的目标区域，执行完成后结果为 passed 或 failed。passed 只表示该区域没有再触发同规则，不证明所有业务行为正确；编译和测试当前未自动执行，明确显示未验证。扫描器或存储故障会明确报错、停止当前操作并保留 CLI，不撤销已经批准的源码修改；`unverified` 仅表示未绑定目标或尚未执行的编译、测试等。

可以继续问“上次应用写入了哪些文件，验证结果是什么？”Agent 会获得当前任务的执行事实；源码随后被外部修改时，会区分旧验证与当前证据。预检拒绝或写入中断会保留可保存的具体原因。结束任务后不提供完整的跨任务执行历史。

## 7. 项目问答与长期知识

调查 Java 方法的使用方时，Agent 可以分页定位声明、查询当前源码中的调用和方法引用，并继续读取相应代码块。相同方法名可能属于不同类型，这些候选需要结合源码判断；索引过期、原生语法诊断或结果截断会明确提示，不能把未查到当成不存在。行/全文与 AST 块由 Agent 独立选择；块未命中不会自动改用行窗。Tree-sitter 不要求 Java 编译通过，源码存在语法错误时仍可返回带诊断的原生识别结构；解析器本身故障则停止依赖它的当前操作和 Agent，CLI 保留输入。

```text
@src/main/java/demo/AppConfig.java 解释这个类的职责
本项目为什么选择不可变配置对象？
我确认这是长期项目约定，请记入项目笔记
```

项目问答、代码取证、扫描、修复和提案解释共享完整 SDK 会话，重启可继续；任务与提案独立保存，不从聊天文本猜审核状态。根 AGENTS 和源码祖先 AGENTS 作为正式规范读取，明确引用的其他规则按需阅读。

长期笔记保存用户约定和少量已核实、有来源的项目知识；易变知识使用前重核。当前补丁的临时要求另绑定任务，可明确撤回或替换，不会继续作为有效要求。未决实质冲突先说明并澄清。详见 [项目 Memory](SPEC.md#7-项目-memory)。

Agent 会利用自动注入的主笔记；主笔记保存精简约定及带适用情境的主题入口，涉及相关模块或问题时再读取或搜索详细主题，已有上下文足够时不重复读取。无需自行整理一套记忆文档；可用自然语言说明长期约定，或通过下述命令检查和修订。apply 本身不再启动额外的总结请求。

## 8. CLI 命令

| 命令 | 作用 |
|---|---|
| `/init` | 初始化当前 Java 项目，准备扫描器并建立索引 |
| `/status` | 查看项目状态和运行诊断 |
| `/scanner` | 查看扫描器状态 |
| `/reset` | 重置任务与索引并关闭运行时；保留扫描证据、锁、会话、笔记、导出和终端历史 |
| `/new` | 归档当前项目会话、开启空会话，清理待审草案并保留笔记 |
| `/memory ...` | 查看和管理项目级 Memory |
| `/help` | 显示命令和交互关键字 |
| `/quit` | 关闭模型客户端及运行资源后退出 |

### Memory 子命令

| 命令 | 作用 |
|---|---|
| `/memory status`、`list` | 读取当前项目笔记文件列表 |
| `/memory show [path]` | 查看笔记内容，默认 MEMORY.md |
| `/memory forget <path>` | 删除笔记文件；MEMORY.md 受保护，使用明确自然语言修订或 clear |
| `/memory clear --confirm` | 清空项目笔记，保留项目会话、导出和终端历史 |
| `/memory export` | 创建现有笔记的 JSON 导出，不覆盖旧文件 |

项目会话保存在 `.autopatch-j/conversations.sqlite3`，长期笔记保存在 `.autopatch-j/memory.sqlite3`。删除笔记不会擦除项目会话中的旧内容；如需开始独立对话，使用 `/new`。导出仅含 notebook 文件，不是完整对话或 RAW 审计记录。

旧 memory.db、memory_summary.md 不再作为输入，也不会自动迁移或删除；如有需要可自行备份查看。项目不再生成 Markdown 审阅投影，也没有后台 Memory 提取任务。

## 9. 常见问题

### CLI 提示“系统未初始化”

在目标 Java 仓库中执行 `/init`。源码变化较大、索引需要更新时再次执行 `/init`；扫描器已经就绪时不会重复安装。

### 提示单轮额度触限，任务尚未完成

确认界面显示会话进展已保存后，可以输入“继续”；退出后重新启动也可续接。下一轮使用新的请求额度和已保存的原生历史，不自动应用提案。工具调用多的任务可以提高 `AUTOPATCH_LLM_REQUEST_LIMIT`，或设为 `none`，但耗时与费用也可能增加。

如果提示会话未保存，本轮取证不能保证续接；已提交的任务或提案仍以当前工作台为准，需要重新核对缺失证据。模型故障会结束本次运行；取消正常结束。两者均保留此前历史与原提案，不自动重放失败回合。

### `.env` 中有 API Key，CLI 仍提示缺少配置

AutoPatch-J 不读取 `.env`。请把变量设置到启动 CLI 的 shell 或系统环境中，然后重启进程。

### LLM 返回 401、403 或 404

- `401`：检查 `AUTOPATCH_LLM_API_KEY`。
- `403`：检查账号是否有权使用当前 LLM，以及账户余额和供应商访问策略。
- `404`：检查 `AUTOPATCH_LLM_BASE_URL` 和 `AUTOPATCH_LLM_MODEL`。

需要查看 LLM 服务返回的原始错误时，设置 `AUTOPATCH_DEBUG=true` 并重启 CLI。错误正文可能包含敏感信息，分享前需要检查。

### 扫描器不可用或扫描失败

先执行 `/scanner` 查看状态，再用 `/init` 检查 managed Semgrep runtime。Semgrep 返回的数据缺少必要字段、包含错误或 finding 位置无效时，AutoPatch-J 会中止本次扫描，不会把结果当作零 finding。

`missing` 表示受管程序缺失，`broken` 表示版本探测失败，`version_mismatch` 表示实际版本不符合要求；只有 `ready` 才允许扫描。界面展示实际探测版本，不把要求版本当作已安装版本。`/init` 先安装扫描器，再构建索引；任一步故障立即报错并停止初始化，不继续后续步骤；CLI 保留输入，处理原因后可再次执行 `/init`。

### 提示源码基线已变化

当前文件或真实路径与已审提案不同，完整预检失败时没有文件写入。请重新读取、准备提案，再审核。不会自动重定位旧草案。

### 提示部分写入、applying 或状态未保存

先核对界面中的逐文件写入记录和当前 bytes 对照。已经写入的源码不会自动回滚；applying/interrupted 状态禁止再次 apply。确认实际文件后，用 abort 或 /new 明确结束，再按现状提出下一项修复。

写入前的 applying 状态无法保存时不写源码；写后保存失败会明确报告修改已生效、状态未保存，不继续复扫或自动恢复。

### 修复状态文件损坏

程序会明确报错，保留原文件，不当作空闲或自动重建。若在交互输入前读取状态或展示预览失败，程序报告一次并保留输入，同时使旧审核画面失效。帮助命令不反复读取故障状态；恢复展示后须重新输入人工操作。请先备份并核查 `.autopatch-j/repair.json` 及目标源码，确认写入事实后再处理损坏状态。

### 笔记或项目会话存储不可用

存储异常会明确提示。笔记加载失败时，本轮 Agent 停止，CLI 保留输入，不发起缺少笔记的模型请求；会话读取失败也不会使用空历史。普通回答已经生成而保存失败时，回答仍显示，并提示实际已提交的业务事实和会话未确认保存，随后恢复输入。纯并发版本冲突属于业务拒绝，会保留已生成回答与已提交事实、拒绝过期保存，并允许继续输入；不会覆盖其他进程的修改。

先保存需要保留的输出，并检查项目状态目录的权限和存储情况；不会自动迁移或重建损坏的数据库。`/memory clear --confirm` 只用于明确删除当前笔记，不承诺修复任意数据库损坏。

### 模型响应不完整或请求达到上限

异常 finish reason 或缺少流结束标记会使本轮失败，不会执行该响应工具或提交未完成草案。额度或循环限制产生的有效完整提案仍可进入人工审核；界面会区分任务受限和完整完成。必要上下文过大时缩小范围，或使用 `/new` 开始新项目会话。

## 10. 开发与测试

开发安装、Python 规范和静态检查命令统一见 [Python 规范：安装与检查](PYTHON_STYLE.md#3-安装与检查)。仅运行测试时也可按 [AGENTS](../AGENTS.md#实现与验证) 安装测试依赖。

运行确定性完整回归：

```bash
pytest -q
```

默认回归不调用真实 LLM。测试执行、真实 LLM 授权和开发协作规则统一见 [AGENTS](../AGENTS.md#实现与验证)，产品侧测试隔离契约见 [SPEC](SPEC.md#8-验证契约与能力限制)。
