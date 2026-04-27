# AutoPatch-J

> [English](./README.md) · 中文

<p align="center">
  <strong>一款针对 Java 的 AI 代码修复智能体</strong><br/>
  以 <code>Workflow</code> 为控制器，<code>Agent</code> 为决策引擎的命令行系统，涵盖代码检查、代码解释、补丁生成及人工确认。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/LLM-DeepSeek-111827?style=flat-square" alt="DeepSeek" />
  <img src="https://img.shields.io/badge/Architecture-Workflow%20%2B%20Agent-4F46E5?style=flat-square" alt="Workflow + Agent" />
  <img src="https://img.shields.io/badge/Scanner-Semgrep-22C55E?style=flat-square" alt="Semgrep" />
  <img src="https://img.shields.io/badge/Index-SQLite%20%2B%20Tree--sitter-0EA5E9?style=flat-square" alt="SQLite + Tree-sitter" />
  <img src="https://img.shields.io/badge/CLI-Rich%20%2B%20prompt--toolkit-F59E0B?style=flat-square" alt="Rich + prompt-toolkit" />
</p>

## 概述

AutoPatch-J 是一款 AI 代码修复 CLI 工具，目前主要针对 **Java 代码库**。  
它不将模型视为不受限制的黑盒助手，而是将其置于受控的工程流水线中：

- 首先识别意图
- 随后解析范围
- 必要时运行静态扫描
- 逐项推进补丁生成
- 最后进入人工确认工作台

本项目并不试图让模型“多说话”，而是致力于让代码修复更加稳定、可验证且可审计。

## 亮点

### Workflow + Agent，而非不受限的 Agent

控制权留在 `Workflow`，而非 LLM：

- `Workflow` 管理意图、范围、状态和补丁队列
- `Agent` 负责解释、分拣、补丁生成和补丁修订

这在保留 Agent 灵活性的同时，减少了纯 Agent 设置的常见故障模式：不受限制地扫描整个仓库、重复读取文件、漂移出范围边界、携带臃肿上下文或产生难以审查的补丁。

### 补丁是一等公民，而非一次性回复

每个补丁都作为结构化的审核项存储，包含：

- 目标文件
- 相关发现 (Finding)
- 标准 Diff
- 理由 (Rationale)
- 语法校验结果

### `@mention` 仅识别文件和目录

`@mention` 的正式能力目前仅包括：

- 文件
- 目录

例如：

```text
autopatch-j> @src/main/java/demo/UserService.java 检查这个文件
autopatch-j> @src/main/java/demo 解释这个目录
```

### 扫描器与 LLM 协同工作

默认扫描器为 **Semgrep**。  
其他扫描器适配器插槽已存在，但尚未进入主路径：

- PMD (计划中)
- SpotBugs (计划中)
- Checkstyle (计划中)

LLM 不凭“直觉”修复代码，它尽可能基于真实的扫描发现和源码证据进行工作。

### 长期会话受到显式控制

系统通过以下方式限制多轮交互：

- 范围锁定 (Scope Locking)
- 工具白名单
- 历史脱水 (History Dehydration)
- 压缩的聊天输出
- 补丁确认工作台

这使得本项目更像一个可运行的工程系统，而非一次性的聊天机器人。

## 当前能力

### 代码检查 (Code inspection)

```text
autopatch-j> @LegacyConfig.java 检查这个文件是否有明显问题
autopatch-j> @src/main/java/demo 扫描这个目录
autopatch-j> 查找这个项目中的空指针风险
```

特点：

- 本地扫描优先
- 发现项逐一推进
- 支持 `old_string` 不匹配后的单次重试
- 补丁草案生成后自动进入补丁确认

### 代码解释 (Code explanation)

```text
autopatch-j> @LegacyConfig.java 这个文件是做什么的
autopatch-j> @src/main/java/demo 解释这个目录
```

特点：

- 不触发扫描
- 单文件解释默认不会跨文件追踪上下文
- 多文件解释允许受控的符号导航
- 输出默认压缩为简洁的解释

### 补丁解释与补丁修订 (Patch explanation and patch revision)

一旦会话进入确认模式，后续提示词可以针对当前补丁继续：

```text
autopatch-j> 为什么要这样改？
autopatch-j> 这会影响性能吗？
autopatch-j> 用 Objects.equals 重写它
autopatch-j> 加一行注释解释原因
```

系统会自动区分：

- `patch_explain`
- `patch_revise`

### 编程相关聊天

`general_chat` 目前限于工程相关话题：

- 编程语言
- 算法
- 调试
- 架构
- 工具使用
- 项目特定问题

它不打算作为一个通用的生活方式聊天机器人。

## 一条真实链路

以 `code_audit` 为例，一次完整执行会按下面的顺序推进：

1. 用户输入先进入 `IntentDetector`
2. `ScopeService` 解析代码范围
3. 路由命中 `code_audit`
4. `ScannerRunner` 先做本地静态扫描
5. `BacklogManager` 按 finding 逐项推进
6. `Agent` 基于当前 finding 调用工具取证和生成补丁
7. `PatchEngine` 负责 `old_string` 匹配和 diff 生成
8. `PatchVerifier` 负责语法校验
9. `CliWorkflowController` 把结果写入 `ActiveWorkspace`
10. 最后进入人工确认阶段：`apply / discard / revise`

其他分流入口：

- `code_explain`：`Agent`
- `general_chat`：`ChatFilter -> Agent`
- `patch_explain / patch_revise`：`CliWorkflowController + Agent`

## 架构速览

### `cli/`

交互层，负责：

- 提示词输入
- 命令分发
- 面板渲染
- 自动补全

主要入口点：

- `src/autopatch_j/cli/app.py`

### `core/`

系统骨干，负责：

- 意图检测：`IntentDetector`
- 会话连续性决策：`ConversationRouter`
- 范围解析：`ScopeService`
- 扫描：`ScannerRunner`
- 发现项待办管理：`BacklogManager`
- 补丁工作台管理：`WorkspaceManager`
- 状态持久化：`ArtifactManager`
- 输出整形：`ChatFilter`
- 补丁应用规则：`PatchEngine`

### `agent/`

LLM 层，负责：

- 任务配置 (Profiles)
- ReAct 循环
- 工具调用
- 提示词合成
- 历史脱水
- 方言处理: `agent/dialect/`

关键文件：

- `src/autopatch_j/agent/agent.py`
- `src/autopatch_j/agent/prompts.py`
- `src/autopatch_j/agent/llm_client.py`

### `tools/`

暴露给 Agent 的工具适配器：

- `read_source_code`
- `get_finding_detail`
- `propose_patch`
- `search_symbols`

### `scanners/`

静态扫描器适配层。目前唯一完全接入主路径的扫描器是 **Semgrep**。

## LLM 如何被使用

### 任务配置而非单一聊天模式

Agent 目前有五个显式的任务入口点：

- `code_audit`
- `code_explain`
- `general_chat`
- `patch_explain`
- `patch_revise`

每个任务拥有自己的：

- 系统提示词
- 工具白名单
- 输出约束

### 工具权限是刻意非对称的

例如：

- `code_audit`：`get_finding_detail / read_source_code / propose_patch`
- `code_explain`：单文件模式仅开放 `read_source_code`
- `patch_revise`：`search_symbols / read_source_code / get_finding_detail / propose_patch`

这并不是为了限制而限制，而是为了将模型的自由度放在真正有用的地方。

### 保留 ReAct，但由 Workflow 约束

Agent 仍然遵循 ReAct 风格的循环：

1. 接收任务提示词
2. 决定是否调用工具
3. 观察结果
4. 继续，直到产生答案或补丁

但循环始终在这些约束下运行：

- 工具白名单
- 焦点范围 (Focus Scope)
- 工作台状态
- 脱水后的历史记录

这是 AutoPatch-J 的关键设计权衡：  
**让 Agent 保留智能，让 Workflow 保持边界。**

## 工程细节

### 1. 发现项逐一推进

`code_audit` 不是“扫描一次后让 LLM 在整个结果集上自由发挥”。  
相反，`BacklogManager` 构建发现项待办并逐项推进。

好处：

- 当前目标保持明确
- 一个发现项失败不会吞掉其余项
- 补丁重试保持受控

### 2. 补丁安全链

在草案阶段，`PatchEngine` 检查：

- 文件是否存在
- `old_string` 是否匹配
- 匹配是否唯一
- 是否可以生成 diff

随后 `PatchVerifier` 运行 `Tree-sitter` 语法校验。

在真实的 `apply` 之后，`PatchVerifier` 会重新扫描目标文件，验证对应的发现项是否真的消失了。

### 3. 上下文控制

项目显式应用了多层上下文工程：

- 将 `@mention` 解析为真实的文件集
- 将当前工作台状态注入工作台提示词
- 通过历史脱水压缩旧消息
- 压缩聊天输出并剥离繁重的 Markdown 结构

目标不是“给模型看更多”，而是“仅向模型展示对当前任务真正有用的内容”。

### 4. SQLite + Tree-sitter 索引

`SymbolIndexer` 使用：

- `SQLite` 进行本地索引
- `Tree-sitter` 提取 `class / method`

它还保持显式的降级状态，因此系统不会被仅仅“看起来还在工作”的回退逻辑所欺骗。

### 5. 修正发现项证据

`FindingSnippetService` 倾向于从 `path + line range` 重建真实的代码片段，而不是盲目信任扫描器返回的原始片段。

这使得发现项证据更稳定，并减少了 LLM 被脏片段或不相关片段误导的机会。

## 快速开始

### 要求

- Python `3.10+`
- 一个兼容 OpenAI 的 LLM 端点

安装依赖：

```bash
pip install -e .[test]
```

### 环境变量

```bash
set LLM_API_KEY=your_api_key
set LLM_BASE_URL=https://api.deepseek.com
set LLM_MODEL=deepseek-v4-flash
```

### 启动

#### Demo 模式

```bash
run.bat
```

默认目标是：

```text
examples/demo-repo
```

#### 手动运行

```bash
python -m autopatch_j
```

## 项目布局

```text
src/autopatch_j/
├─ agent/         # LLM 客户端、提示词、ReAct 循环、任务配置、方言
├─ cli/           # prompt-toolkit + Rich 交互层
├─ core/          # 工作流、范围、扫描、工作台、补丁生命周期
├─ scanners/      # Semgrep 和未来的扫描器适配器
└─ tools/         # 暴露给 Agent 的工具
```

---

如果你想快速进入代码库，从这里开始：

1. `src/autopatch_j/cli/app.py`
2. `src/autopatch_j/cli/workflow_controller.py`
3. `src/autopatch_j/agent/agent.py`
4. `src/autopatch_j/core/patch_engine.py`
5. `src/autopatch_j/core/scanner_runner.py`
