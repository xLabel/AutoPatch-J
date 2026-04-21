# AutoPatch-J V2 演示仓库

本项目用于演示 AutoPatch-J V2 的核心能力。

## AutoPatch-J V2 项目架构

```text
src/autopatch_j/
├── agent/            # 决策与 LLM 客户端 (ReAct 循环、提示词管理)
├── cli/              # UX 渲染与 App 循环 (Rich 渲染、非阻塞门禁、自动补全)
├── core/             # 核心服务 (Index 索引、Patch 引擎、Artifacts 持久化、Fetcher 代码提取)
├── scanners/         # 扫描器 (Semgrep 底层适配与运行时管理)
├── tools/            # 给模型的工具适配器 (Scan, Edit, Explorer 等 Function Call)
├── validators/       # Java 语法校验 (基于 Tree-sitter 的 JavaSyntaxValidator)
├── config.py         # 全局配置中心 (环境变量、默认值统一收口)
├── paths.py          # 物理路径中心 (全局资源与项目隔离逻辑)
├── __init__.py
└── __main__.py       # 程序主入口
```

## 建议测试流程

1. 进入本项目目录。
2. 执行 `autopatch-j` 进入交互式 Shell。
3. 输入 `/init` 初始化环境并建立符号索引。
4. 体验 `@` 符号补全：输入 `@` 并按 `Tab` 搜索类或方法。
5. 体验 Agent 能力：输入“扫描项目中的安全问题”。
6. 体验门禁确认：让 Agent 修复发现的问题，并在预览面板中输入 `apply` 或提供对话反馈。
