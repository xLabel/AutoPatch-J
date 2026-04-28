# AutoPatch-J

> 中文 · [English](./README.md)

<p align="center">
  <strong>面向 Java 的 AI 代码修复智能体</strong><br/>
  一个以 <code>Workflow</code> 为主控、以 <code>Agent</code> 为决策引擎的命令行系统，支持代码检查、代码讲解、补丁生成与人工确认。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/LLM-DeepSeek-111827?style=flat-square" alt="DeepSeek" />
  <img src="https://img.shields.io/badge/Architecture-Workflow%20%2B%20Agent-4F46E5?style=flat-square" alt="Workflow + Agent" />
  <img src="https://img.shields.io/badge/Scanner-Semgrep-22C55E?style=flat-square" alt="Semgrep" />
  <img src="https://img.shields.io/badge/Index-SQLite%20%2B%20Tree--sitter-0EA5E9?style=flat-square" alt="SQLite + Tree-sitter" />
  <img src="https://img.shields.io/badge/CLI-Rich%20%2B%20prompt--toolkit-F59E0B?style=flat-square" alt="Rich + prompt-toolkit" />
</p>

## 项目简介

AutoPatch-J 是一个面向 **Java 仓库** 的 AI 代码修复 CLI。  
它不把大模型当成“自由漫游整个仓库的黑盒助手”，而是把大模型放进一套受控的工程化链路里：

- 先做 **意图识别**
- 再做 **范围解析**
- 必要时执行 **静态扫描**
- 按 finding 逐项推进 **补丁生成**
- 最后进入 **人工确认工作台**

它解决的不是“如何让模型多说一点”，而是“如何让代码修复这件事更稳定、更可验证、更可回看”。

## 核心亮点

### Workflow + Agent，而不是纯 Agent 放飞

系统的主控权在 `Workflow`，不在 LLM：

- `Workflow` 管意图、范围、状态和补丁队列
- `Agent` 管讲解、甄别、补丁生成和补丁重写

这让系统既保留了 Agent 的灵活性，又尽量避免纯 Agent 常见的失控问题：乱扫全库、重复读文件、跨范围推理、长上下文漂移、补丁不可复核。

### 补丁是一等对象，不是临时回复

每个补丁都会被结构化保存为待确认项，包含：

- 目标文件
- 关联 finding
- unified diff
- 修复理由
- 语法校验结果

### `@mention` 只认文件和目录

当前 `@mention` 的正式能力只包括：

- 文件
- 目录

例如：

```text
autopatch-j> @src/main/java/demo/UserService.java 检查代码
autopatch-j> @src/main/java/demo 解释一下这个目录
```

### 扫描器与 LLM 是协作关系，不是替代关系

当前默认扫描器是 **Semgrep**。  
其余扫描器适配位已预留，但尚未真正接入主链路：

- PMD（Planned）
- SpotBugs（Planned）
- Checkstyle（Planned）

LLM 并不直接“凭感觉修代码”，而是优先建立在真实 finding 和源码证据之上。

### 对长会话做了显式收束

项目在多轮交互里显式控制：

- Scope 锁定
- Tool 白名单
- 历史消息脱水
- 聊天输出压缩
- 待确认补丁工作台

它更像一个可运行的工程系统，而不是一个单次问答机器人。

## 当前能力

### 代码检查

```text
autopatch-j> @LegacyConfig.java 帮我看看这个文件有没有明显问题
autopatch-j> @src/main/java/demo 把这个目录扫一遍
autopatch-j> 看一下这个项目里有没有空指针风险
```

特点：

- 本地先扫描
- finding 逐项推进
- 支持 `old_string` 失配后的单次重试
- 生成补丁后自动进入确认流

### 代码讲解

```text
autopatch-j> @LegacyConfig.java 这个文件是干嘛的
autopatch-j> @src/main/java/demo 解释一下这个目录
```

特点：

- 不触发扫描
- 单文件讲解默认不越界追踪
- 多文件讲解允许受控符号导航
- 输出默认压缩为简洁说明

### 补丁解释 / 补丁重写

一旦进入待确认状态，可以继续追问：

```text
autopatch-j> 为什么这么改？
autopatch-j> 会影响性能吗？
autopatch-j> 改成 Objects.equals 的写法
autopatch-j> 加一行注释说明原因
```

系统会自动区分：

- `patch_explain`
- `patch_revise`

### 编程相关聊天

`general_chat` 当前被限制在工程相关范围内：

- 编程语言
- 算法
- 调试
- 架构
- 工具使用
- 项目本身

它不是泛生活问答机器人。

## 一条真实链路

以 `code_audit` 为例，一次完整执行会按下面的顺序推进：

1. 用户输入先进入 `IntentDetector` (意图检测)
2. `ScopeService` 解析代码范围
3. 路由命中 `code_audit`
4. `ScannerRunner` 先做本地静态扫描
5. `BacklogManager` 按 finding 逐项推进
6. `Agent` 基于当前 finding 调用工具取证和生成补丁
7. `PatchEngine` 负责 `old_string` 匹配、diff 生成
8. `PatchVerifier` 执行语法和语义复核
9. `CliWorkflowController` 把结果写入 `WorkspaceManager`
10. 最后进入人工确认阶段：`apply / discard / abort / revise`

其他分流入口：

- `code_explain`：`Agent`
- `general_chat`：`ChatFilter -> Agent`
- `patch_explain / patch_revise`：`CliWorkflowController + Agent`

## 架构速览

### `cli/`

交互层，负责：

- Prompt 输入
- 命令分发
- 面板渲染
- 自动补全

关键入口：

- `src/autopatch_j/cli/app.py`

### `core/`

系统骨架，负责：

- 意图识别：`IntentDetector`
- 会话连续性判断：`ConversationRouter`
- 范围解析：`ScopeService`
- 扫描驱动：`ScannerRunner`
- 待办管理：`BacklogManager`
- 工作台事务：`WorkspaceManager`
- 状态持久化：`ArtifactManager`
- 补丁验证：`PatchVerifier`
- 符号索引：`SymbolIndexer`
- 输出过滤：`ChatFilter`
- 补丁引擎：`PatchEngine`

### `agent/`

LLM 层，负责：

- Task Profile
- ReAct 循环
- Tool 调用
- Prompt 编排
- History 脱水
- 流式方言解析：`agent/dialect/`

关键文件：

- `src/autopatch_j/agent/agent.py`
- `src/autopatch_j/agent/prompts.py`
- `src/autopatch_j/agent/llm_client.py`

### `tools/`

提供给 Agent 的工具适配器：

- `read_source_code`
- `get_finding_detail`
- `propose_patch`
- `search_symbols`

### `scanners/`

静态扫描器适配层。当前真正跑通的是 **Semgrep**。

## LLM 是如何被使用的

### Task Profile，而不是单一聊天模式

当前 Agent 有 5 个明确的任务入口：

- `code_audit`
- `code_explain`
- `general_chat`
- `patch_explain`
- `patch_revise`

每个任务都有独立的：

- System Prompt
- Tool 白名单
- 输出约束

### Tool 权限是非对称的

例如：

- `code_audit`：允许 `get_finding_detail / read_source_code / propose_patch`
- `code_explain`：单文件只开 `read_source_code`
- `patch_revise`：允许 `search_symbols / read_source_code / get_finding_detail / propose_patch`

这不是限制模型，而是把模型的自由度放在真正需要的地方。

### ReAct 被保留，但被 Workflow 收束

Agent 仍然是 ReAct 风格：

1. 接收任务 Prompt
2. 判断是否调工具
3. 观察结果
4. 继续推理直到生成回答或补丁

但它始终受到这些约束：

- Tool 白名单
- Focus Scope
- Workspace 事务状态
- 历史消息脱水

这正是 AutoPatch-J 的关键设计取舍：  
**让 Agent 保留智能，让 Workflow 保留边界。**

## 工程细节

### 1. 逐 finding 推进

`code_audit` 不是“扫描一次后把所有问题扔给 LLM 自由发挥”，而是由 `BacklogManager` 建立一个 finding backlog，逐项推进。

收益：

- 当前目标始终明确
- 单个 finding 失败不会吞掉后续 finding
- patch retry 更可控

### 2. Patch 安全链路

`PatchEngine` 在 draft 阶段会检查：

- 文件是否存在
- `old_string` 是否匹配
- 匹配是否唯一
- diff 是否可生成

真正 `apply` 时，`PatchVerifier` 会执行 `Tree-sitter` 语法校验。
真正 `apply` 后，`PatchVerifier` 会对目标文件重新扫描，验证对应 finding 是否真的消失。

### 3. 上下文控制

项目显式做了几层 Context Engineering：

- `@mention` 转真实文件集
- Workbench Prompt 注入当前工作台状态
- History Dehydration 压缩旧消息
- 聊天输出压缩与去 Markdown 化

目标不是“让模型看到更多”，而是“让模型只看到对当前任务真正有用的东西”。

### 4. SQLite + Tree-sitter 索引

`SymbolIndexer` 使用：

- `SQLite` 做本地全量文件索引
- `Tree-sitter` 提取 Java 源码的 `class / method`

它提供了向后兼容的降级保护，且对主流 IDE 的临时构建目录（如 `target/`, `node_modules/`）进行了智能隔离。

### 5. Finding 证据纠偏

优先按 `path + line range` 回源文件提取真实代码片段，而不是盲信扫描器返回的原始 snippet。

这让 finding 证据更稳定，也减少了 LLM 因脏片段而误判的概率。

## 快速开始

### 环境要求

- Python `3.10+`
- OpenAI-Compatible LLM 接口

安装依赖：

```bash
pip install -e .[test]
```

### 环境变量

可以通过系统的环境变量进行配置，或者在根目录创建 `.env`（但推荐使用系统全局配置，以保持项目工作区纯净）：

```bash
set LLM_API_KEY=your_api_key
set LLM_BASE_URL=https://api.deepseek.com
set LLM_MODEL=deepseek-v4-flash
```

### 启动

#### Windows 自动环境构建与启动（推荐）

直接双击或执行内置脚本。脚本会自动检查 Python 环境、创建 `.venv` 虚拟环境，并同步所有的依赖项，真正实现“即拉即用”：

```bash
run_on_windows.bat
```

默认会进入内置的演示工程：

```text
examples/demo-repo
```

#### 手动运行

如果您的环境已配置完毕：

```bash
python -m autopatch_j
```

## 目录结构

```text
src/autopatch_j/
├─ agent/         # LLM Client、Prompt、ReAct Loop、Task Profile、Dialect 策略
├─ cli/           # prompt-toolkit + Rich 交互层、Workflow 调度
├─ core/          # 域模型、扫描、索引、事务工作台、补丁验证与生命周期
├─ scanners/      # Semgrep 与扩展扫描器适配位
└─ tools/         # 暴露给 Agent 可调用的工具集

examples/demo-repo/   # 内置漏洞演示仓库
tests/                # 回归测试套件
```

---

如果你想最快进入代码，建议从这里开始阅读核心控制流：

1. `src/autopatch_j/cli/app.py`
2. `src/autopatch_j/cli/workflow_controller.py`
3. `src/autopatch_j/agent/agent.py`
4. `src/autopatch_j/core/patch_engine.py`
5. `src/autopatch_j/core/scanner_runner.py`
