## 背景原因

CLI ReAct 输出和长审计流程会直接影响用户对 AutoPatch-J 的信任。当前渲染可用，但此前围绕颜色、间距、debug/normal 输出发生过回归，说明状态机边界需要更明确。审计流程也可能在一次请求中处理大量 finding，却没有明确的用户可见预算。

## 变更内容

- 增加有界审计批次，让每次 code-audit 请求处理可控数量的 finding。
- 让 ReAct 渲染状态更容易理解和测试。
- 按 intent-oriented 文件拆分 prompt 资产，并降低 scanner planning 噪音。

## 能力变化

### 新增能力

- `bounded-audit-runs`：代码审计以可预测批次处理 finding，并提示用户确认当前补丁后重新发起检查继续。
- `stable-react-cli-rendering`：ReAct 渲染在不同事件序列下保持 normal/debug 输出一致。

### 修改能力

- `prompt-assets`：prompt 文案保持行为等价，但按任务区域组织。
- `scanner-status-display`：planned scanner 不再被强调为可用运行时选择。

## 影响范围

影响 code audit workflow、CLI stream presenter、prompt modules、scanner status display，以及相关 CLI 聚焦测试。
