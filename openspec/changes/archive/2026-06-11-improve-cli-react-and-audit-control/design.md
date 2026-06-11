## 上下文

Code audit 当前会循环处理 finding backlog，直到没有 current finding。ReAct stream rendering 已经有 compact/debug 策略，但事件处理仍集中在一个 presenter 文件中。Prompt 资产已有结构，但仍集中在一个较大的模块里。

## 目标 / 非目标

**目标：**

- 让长审计行为可预测。
- 降低 CLI 渲染回归风险。
- 让 prompt 文案更容易 review。
- 避免把 planned scanner 展示成 active feature。

**非目标：**

- 增加新的 scanner。
- 修改 function_call 工具名。
- 重设计 CLI 命令集。

## 决策

- 默认 audit batch size 暂时较小，并由配置控制。
- 当 batch 结束后仍有 finding，只保留已生成的 pending patch queue，并提示用户确认当前补丁后重新发起检查继续。
- 保持渲染行为兼容，但抽出事件状态职责。
- 将 prompt builder 移入更小模块，并 re-export 稳定函数。

## 风险 / 取舍

- 有界审计用部分自动化换取用户控制感。
- 不持久化剩余 finding backlog；补丁应用后旧扫描结果可能与源码状态不一致，重新扫描能让后续处理基于最新证据。
- prompt 文件迁移可能影响多个 import，因此保留 public function name。
