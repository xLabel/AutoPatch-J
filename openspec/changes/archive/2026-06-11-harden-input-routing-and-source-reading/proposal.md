## 背景原因

意图路由和源码读取 guardrail 会直接影响 AutoPatch-J 是否进入正确工作流，以及 LLM 生成的补丁是否基于正确源码文件。当前 fallback 可以避免崩溃，但会隐藏分类器失败；源码读取在路径不存在时也可能静默改读第一个同名索引文件。

## 变更内容

- 为 command/new-task/review-continuation 路由和意图分类 fallback 增加可观测诊断。
- 普通 CLI 输出保持克制，只在 debug 模式展示 fallback 细节。
- 收紧源码读取路径纠正：仅当缺失路径存在唯一 in-focus 候选时才自动纠正。
- 所有源码读取工具继续强制执行 focus 约束。

## 能力变化

### 新增能力

- `input-routing-diagnostics`：路由和意图分类暴露来源、fallback 和失败原因。
- `safe-source-path-resolution`：源码读取工具避免对歧义路径做自动纠正。

### 修改能力

无。

## 影响范围

影响 `core/user_input`、`cli/input_router.py`、源码读取 function call 工具，以及路由和源码读取相关聚焦测试。
