# AutoPatch-J 上手与操作手册

这里集中说明 AutoPatch-J 的安装、配置和日常操作。项目定位见 [README](../README.md)，补丁应用和验证的完整规则见 [Patch Safety 规格](../openspec/specs/patch-safety/spec.md)。

## 1. 环境要求

- Python `3.10+`
- 待检查的 Java 仓库
- OpenAI 兼容的 LLM API
- 安装 Python 依赖和 managed Semgrep runtime 所需的网络环境

目前实际使用的扫描器是 Semgrep。PMD、SpotBugs 和 Checkstyle 会出现在扫描器列表中，但尚未接入扫描流程。

## 2. 安装

在 AutoPatch-J 仓库根目录创建虚拟环境，并以 editable 模式安装项目。

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

安装完成后可以运行 `autopatch-j`，也可以使用 `python -m autopatch_j`。

## 3. 配置 LLM

配置必须存在于启动 CLI 的进程环境中。AutoPatch-J 不会自动读取 `.env`。`AUTOPATCH_LLM_API_KEY` 必须设置，`AUTOPATCH_LLM_BASE_URL` 和 `AUTOPATCH_LLM_MODEL` 有默认值。

macOS / Linux：

```bash
export AUTOPATCH_LLM_API_KEY="your-api-key"
export AUTOPATCH_LLM_BASE_URL="https://api.deepseek.com"
export AUTOPATCH_LLM_MODEL="deepseek-v4-flash"
```

Windows PowerShell：

```powershell
$env:AUTOPATCH_LLM_API_KEY="your-api-key"
$env:AUTOPATCH_LLM_BASE_URL="https://api.deepseek.com"
$env:AUTOPATCH_LLM_MODEL="deepseek-v4-flash"
```

可用配置：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AUTOPATCH_LLM_API_KEY` | 空 | LLM API Key，必须设置 |
| `AUTOPATCH_LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址，末尾的 `/` 会被移除 |
| `AUTOPATCH_LLM_MODEL` | `deepseek-v4-flash` | 供应商使用的 LLM 标识 |
| `AUTOPATCH_LLM_REASONING_EFFORT` | 空 | 可选值由 LLM 供应商决定 |
| `AUTOPATCH_LLM_EXTRA_BODY` | `{}` | 供应商扩展参数，必须是 JSON object 字符串 |
| `AUTOPATCH_LLM_STREAM_DIALECT` | `standard` | 可选 `standard` 或 `bailian-dsml` |
| `AUTOPATCH_DEBUG` | `false` | 设为 `true` 后显示调试信息和有界 RAW provider 错误 |
| `AUTOPATCH_AUDIT_BATCH_LIMIT` | `4` | 每轮 audit 最多处理的 finding 数量；非法值或非正整数按 `4` 处理 |

如果供应商要求额外的请求字段，可以通过 `AUTOPATCH_LLM_EXTRA_BODY` 传入：

```bash
export AUTOPATCH_LLM_EXTRA_BODY='{"thinking":{"type":"enabled"}}'
```

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

`/init` 会初始化项目运行环境、清空原有补丁审核工作台、安装或检查 managed Semgrep runtime，并重建 Java 符号索引。完成后可以用 `/status` 和 `/scanner` 检查状态。

### 运行内置 demo

仓库中的启动脚本会创建 `.venv`、安装当前项目，然后进入 `examples/demo-repo` 启动 CLI。运行前需要先设置 `AUTOPATCH_LLM_API_KEY`。

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

如果只输入 `@路径`，CLI 会提示继续输入具体要求。收到完整指令后，系统解析检查范围、执行 Semgrep，并把合法的扫描结果加入 finding 队列。每轮最多处理的数量由 `AUTOPATCH_AUDIT_BATCH_LIMIT` 控制。

Agent 会为当前 finding 准备候选补丁。CLI 展示目标文件、diff、修改理由和语法检查结果，然后把补丁放入审核队列。在用户应用、丢弃或中止之前，CLI 会一直显示当前补丁。

## 6. 审核补丁

看到补丁预览后，可以直接输入：

| 输入 | 结果 |
|---|---|
| `apply` | 应用当前补丁，重新检查目标 finding，然后进入下一项 |
| `discard` | 丢弃当前补丁，然后进入下一项 |
| `abort` | 结束本轮审核并丢弃剩余补丁 |

决定之前也可以继续提问或要求修改：

```text
解释为什么要这样修改
把判空改成 Objects.equals 的写法
这个修改会影响哪些调用方？
```

解释和修订都只针对当前 finding。修订补丁仍需命中原来的位置，也不能切换到同一文件中的其他 finding。校验失败时，当前补丁保持不变。

如果同一文件还有其他待审补丁，前一个补丁可能会改变它们的位置。系统会更新不重叠补丁的位置；无法重新定位的补丁会显示 `STALE_DRAFT`。这类补丁不能继续 apply 或 revise，可以选择 `discard`，也可以 `abort` 后按当前源码重新扫描。

补丁应用后会得到以下结果之一：

- `RESOLVED`：修改后的位置不再触发同一条规则。
- `STILL_PRESENT`：修改后的位置仍然触发该规则。
- `UNVERIFIED`：扫描器不可用，或者缺少可靠的补丁应用记录，无法确认补丁仍对应原 finding。

如果结果是 `STILL_PRESENT` 或 `UNVERIFIED`，已经应用的补丁不会自动回滚。

## 7. 代码解释与普通问答

除了审计，也可以让 AutoPatch-J 解释代码或回答工程问题：

```text
@src/main/java/demo/AppConfig.java 解释这个类的职责
为什么这里适合使用不可变对象？
```

这两类请求可以使用项目级 Memory。代码审计、补丁解释和补丁修订不读取普通对话 Memory，具体设计见 [Agent Memory v2 设计说明](memory_design.md)。

## 8. CLI 命令

| 命令 | 作用 |
|---|---|
| `/init` | 初始化当前 Java 项目，准备扫描器并建立索引 |
| `/status` | 查看项目状态和运行诊断 |
| `/scanner` | 查看扫描器状态 |
| `/reindex` | 重建 Java 符号索引 |
| `/reset` | 重置工作台；保留 Memory、Memory 导出和 CLI history |
| `/new` | 结束当前工作状态并创建新的普通对话 thread |
| `/memory ...` | 查看和管理项目级 Memory |
| `/help` | 显示命令和交互关键字 |
| `/quit` | 退出 CLI；退出前执行 Memory 收尾 |

### Memory 子命令

| 命令 | 作用 |
|---|---|
| `/memory status` | 查看 Memory 健康状态、任务和错误 |
| `/memory list` | 列出当前可用的 Memory |
| `/memory show <id>` | 查看一条 Memory 及其来源 |
| `/memory forget <id>` | 忘记派生 Memory；保留原始 turn |
| `/memory clear --confirm` | 清空 thread、turn、派生 Memory 和 job，并创建新 thread；保留现有导出和 CLI history |
| `/memory export` | 创建新的 RAW JSON 快照，不覆盖旧文件 |

Memory 可能包含未脱敏的用户原文和 LLM 服务返回的诊断。查看或分享导出文件前，应先检查其中是否有敏感信息。

## 9. 常见问题

### CLI 提示“系统未初始化”

在目标 Java 仓库中执行 `/init`。源码变化较大、索引需要更新时，可以执行 `/reindex`。

### `.env` 中有 API Key，CLI 仍提示缺少配置

AutoPatch-J 不读取 `.env`。请把变量设置到启动 CLI 的 shell 或系统环境中，然后重启进程。

### LLM 返回 401、403 或 404

- `401`：检查 `AUTOPATCH_LLM_API_KEY`。
- `403`：检查账号是否有权使用当前 LLM，以及账户余额和供应商访问策略。
- `404`：检查 `AUTOPATCH_LLM_BASE_URL` 和 `AUTOPATCH_LLM_MODEL`。

需要查看 LLM 服务返回的原始错误时，设置 `AUTOPATCH_DEBUG=true` 并重启 CLI。错误正文可能包含敏感信息，分享前需要检查。

### 扫描器不可用或扫描失败

先执行 `/scanner` 查看状态，再用 `/init` 检查 managed Semgrep runtime。Semgrep 返回的数据缺少必要字段、包含错误或 finding 位置无效时，AutoPatch-J 会中止本次扫描，不会把结果当作零 finding。

### 当前补丁显示 `STALE_DRAFT`

这表示当前源码已经无法和补丁保存的位置可靠对应。选择 `discard` 丢弃当前项，或者 `abort` 后重新扫描；不要继续应用这份草案。

### Memory 显示 degraded

先用 `/memory status` 查看错误。`/memory show <id>` 和 `/memory export` 可以帮助排查；`/memory clear --confirm` 会删除全部 Memory 业务数据，确认不再需要这些数据后再执行。恢复规则见 [Memory 设计说明](memory_design.md)。

## 10. 开发与测试

安装测试依赖：

```bash
python -m pip install -e '.[test]'
```

运行完整测试：

```bash
pytest -q
```

行为变更应先补充对应的聚焦测试。修改核心工作流或多个模块后，再运行完整测试集。
