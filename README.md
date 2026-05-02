# AutoPatch-J

<p align="center">
  <strong>面向 Java 仓库的 AI 代码修复 Agent</strong><br/>
  Workflow 负责边界与状态，Agent 负责推理与工具调用，让代码检查、补丁生成和人工确认进入一条可复核的工程链路。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/LLM-OpenAI%20Compatible-111827?style=flat-square" alt="OpenAI Compatible LLM" />
  <img src="https://img.shields.io/badge/Architecture-Workflow%20%2B%20Agent-4F46E5?style=flat-square" alt="Workflow + Agent" />
  <img src="https://img.shields.io/badge/Scanner-Semgrep-22C55E?style=flat-square" alt="Semgrep" />
  <img src="https://img.shields.io/badge/Index-SQLite%20%2B%20Tree--sitter-0EA5E9?style=flat-square" alt="SQLite + Tree-sitter" />
  <img src="https://img.shields.io/badge/CLI-Rich%20%2B%20prompt--toolkit-F59E0B?style=flat-square" alt="Rich + prompt-toolkit" />
</p>

## 为什么不是又一个 AI Coding Bot

纯 Agent 很容易看起来聪明，但工程上经常失控：它可能乱扫全库、跨范围读文件、把旧上下文当成当前事实、重复调用工具，最后给出一段无法复核的补丁文本。

AutoPatch-J 的设计答案是把 LLM 放进一条受控链路里：

- 用户输入先做意图识别和范围解析。
- 代码检查优先建立在静态扫描 finding 和源码证据之上。
- Agent 只在当前任务开放的工具白名单内行动。
- 补丁先成为待确认对象，再由用户决定是否应用。
- 普通问答可以有记忆，但不会进入补丁修复链路。

目标不是让模型自由发挥，而是让 Java 代码修复更稳定、更可验证、更可回看。

## 核心架构取舍

### Workflow owns state, Agent owns reasoning

> Workflow 管状态，Agent 管推理。

`Workflow` 负责状态和边界：意图、scope、扫描、finding 队列、补丁队列和人工确认。

`Agent` 负责推理和执行：解释代码、判断 finding、调用工具、生成补丁草案、按反馈重写当前补丁。

这个分工让 Agent 保留智能，同时避免把系统状态交给 LLM 自行维护。

### Scanner provides evidence, LLM performs triage

> 扫描器提供证据，LLM 负责判断。

默认扫描器是 **Semgrep**。扫描器负责提供可定位的 finding，LLM 负责基于 finding、源码片段和当前 scope 做取证、解释和最小修复。

其余扫描器适配位已预留：

- PMD（Planned）
- SpotBugs（Planned）
- Checkstyle（Planned）

AutoPatch-J 不鼓励 LLM “凭感觉修代码”。在 `code_audit` 中，LLM 应该围绕证据工作。

### Patch is a review item, not a chat reply

> 补丁是待确认对象，不是聊天回复。

补丁不是聊天回复里的临时文本。每个补丁都会进入人工确认队列，包含：

- 目标文件
- 关联 finding
- unified diff
- 修复理由
- 语法校验结果

用户可以 `apply / discard / abort`，也可以继续要求解释或重写当前补丁。

### Memory helps chat, not repair

> 记忆服务问答，不污染修复。

AutoPatch-J 有项目级普通问答记忆，但它只服务 `code_explain` 和 `general_chat`。

它不会进入：

- `code_audit`
- `patch_explain`
- `patch_revise`

原因很直接：修复链路必须以当前 finding、当前源码和当前补丁队列为准，不能被历史聊天、旧偏好或算法题讨论污染。

详细设计见 [Agent Memory 设计说明](docs/memory_design.md)。

### Tool access is asymmetric by intent

> 工具权限按任务非对称开放。

不同任务开放不同工具。模型不是拿到所有能力后自由探索，而是在当前 intent 允许的边界内行动。

这种非对称权限设计让系统既能利用 LLM 的推理能力，也能避免工具调用失控。

## 五类任务边界

| IntentType | 场景 | 主要工具能力 | 使用 Memory | 说明 |
|---|---|---|---:|---|
| `code_audit` | 检查代码并生成补丁 | `get_finding_detail` / `read_source_code` / `propose_patch` | 否 | 以当前 scope、finding 和源码证据为准 |
| `code_explain` | 解释项目、目录、文件或代码 | `read_source_code` / `search_symbols` | 是 | 可继承用户对项目的关注点 |
| `general_chat` | Java、算法、调试、架构和工程常识问答 | 无工具或轻量上下文 | 是 | 可继承用户偏好和近期话题 |
| `patch_explain` | 解释当前待确认补丁 | `search_symbols` / `read_source_code` | 否 | 只解释当前补丁，不生成新补丁 |
| `patch_revise` | 按反馈重写当前补丁 | `search_symbols` / `read_source_code` / `get_finding_detail` / `revise_patch` | 否 | 只替换当前补丁，不修改后续队列 |

`IntentDetector` 使用短 LLM 判断用户输入属于哪类 intent，但程序会做状态兜底。例如没有待确认补丁时，即使 LLM 返回 `patch_explain` 或 `patch_revise`，程序也会拒绝这些补丁态意图。

## 能做什么

### 代码检查

```text
autopatch-j> @LegacyConfig.java 检查代码
autopatch-j> @src/main/java/demo 扫描这个目录
autopatch-j> 看一下这个项目里有没有空指针风险
```

- 本地静态扫描优先。
- finding 按队列逐项推进。
- 静态扫描无结果时，可对焦点文件执行轻量 LLM 复核。
- 候选补丁先暂存，Workflow 判定成功后才进入人工确认队列。

### 代码讲解

```text
autopatch-j> @LegacyConfig.java 这个文件是干嘛的
autopatch-j> @src/main/java/demo 解释一下这个目录
autopatch-j> 这个项目是干什么的
```

- 不触发扫描。
- 单文件讲解默认不越界追踪。
- 多文件或项目级讲解允许受控符号导航。
- 可使用普通问答记忆继承用户近期关注点。

### 补丁解释与重写

```text
autopatch-j> 为什么这么改？
autopatch-j> 这个补丁会影响性能吗？
autopatch-j> 改成 Objects.equals 的写法
autopatch-j> 加一行注释说明原因
```

- `patch_explain` 只解释当前补丁。
- `patch_revise` 只重写当前补丁。
- 后续补丁队列不会被自动修改。

### 工程相关聊天

`general_chat` 被限制在工程相关范围：

- Java 语法
- 算法题
- 调试方法
- 架构建议
- 工具使用
- 当前项目相关问题

它不是泛生活问答入口。

## 快速开始

### 环境要求

- Python `3.10+`
- OpenAI 兼容 LLM 接口

安装依赖：

```bash
pip install -e .[test]
```

### 环境变量

当前配置读取系统环境变量，不会自动加载 `.env` 文件。

至少需要配置：

```bash
set AUTOPATCH_LLM_API_KEY=your_api_key
```

常用配置：

```bash
set AUTOPATCH_LLM_BASE_URL=https://api.deepseek.com
set AUTOPATCH_LLM_MODEL=deepseek-v4-flash
set AUTOPATCH_DEBUG=true
set AUTOPATCH_LLM_STREAM_DIALECT=standard
```

说明：

- `AUTOPATCH_LLM_API_KEY`：必填；缺失时不会创建 LLM 客户端。
- `AUTOPATCH_LLM_BASE_URL`：OpenAI 兼容地址，默认 `https://api.deepseek.com`。
- `AUTOPATCH_LLM_MODEL`：模型名，默认 `deepseek-v4-flash`。
- `AUTOPATCH_DEBUG`：仅设置为 `true` 时开启完整调试输出。
- `AUTOPATCH_LLM_STREAM_DIALECT`：支持 `standard`、`bailian-dsml`。
- `AUTOPATCH_LLM_REASONING_EFFORT`：透传给支持该参数的供应商。
- `AUTOPATCH_LLM_EXTRA_BODY`：供应商私有扩展参数，必须是 JSON 字符串，默认 `{}`。

### 启动

Windows 推荐直接执行：

```bash
run_on_windows.bat
```

脚本会检查 Python 环境、创建 `.venv`、同步依赖，并默认进入内置演示工程：

```text
examples/demo-repo
```

手动启动：

```bash
python -m autopatch_j
```

### 常用命令

```text
/init       初始化当前项目并建立索引
/status     查看项目状态、LLM 模型、调试模式、补丁缓冲区和符号索引
/scanner    查看扫描器状态、版本和说明
/reindex    重建本地代码符号索引
/reset      清空工作台状态、Agent 对话历史和普通问答记忆
/help       显示命令帮助
/quit       退出程序
```

`AUTOPATCH_DEBUG` 控制 CLI 输出详细程度：

- 默认关闭：折叠思考链和工具输出详情，只显示 `思考中...`、工具名和简短摘要。
- 开启后：展示完整思考链与工具输出详情。

## 系统如何运转

一次 `code_audit` 通常按下面的路径推进：

```text
用户输入
-> IntentDetector 判断任务类型
-> ScopeService 解析 @mention 或当前项目范围
-> ScannerRunner 执行 Semgrep 扫描
-> BacklogManager 按 finding 逐项推进
-> Agent 调用工具读取 finding 与源码
-> propose_patch 生成候选补丁
-> Workflow 判断本轮 finding 是否完成
-> WorkspaceManager 写入待确认补丁队列
-> 用户 apply / discard / abort / 反馈重写
-> PatchEngine + PatchVerifier 执行应用和复核
```

这条链路的核心约束是：LLM 负责推理，Workflow 负责边界，工具负责可复核动作。

## 目录结构

```text
src/autopatch_j/
├─ cli/       # prompt-toolkit + Rich 交互层、命令处理、Workflow 调度、流式输出
├─ core/      # 意图、范围、扫描、索引、工作台、补丁生命周期、普通问答记忆
├─ agent/     # ReAct 循环、Task Profile、Prompt 编排、消息脱水
├─ llm/       # OpenAI 兼容 LLM 客户端、调用意图策略、供应商流式方言
├─ tools/     # 暴露给 Agent 的 function call 工具
└─ scanners/  # Semgrep 及扩展扫描器适配位

examples/demo-repo/   # 内置演示仓库
tests/                # 回归测试
docs/                 # 架构设计文档
```

## 代码阅读入口

如果想快速理解主流程，建议按这个顺序读：

1. `src/autopatch_j/cli/app.py`
2. `src/autopatch_j/cli/workflow_controller.py`
3. `src/autopatch_j/agent/agent.py`
4. `src/autopatch_j/core/input_classifier.py`
5. `src/autopatch_j/core/workspace_manager.py`
6. `src/autopatch_j/core/memory/`

如果想理解普通问答记忆的设计边界，直接看 [Agent Memory 设计说明](docs/memory_design.md)。
