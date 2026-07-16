## Why

当前补丁链路缺少可持久化的 finding 身份与精确位置，导致 post-apply 重扫只能用规则 ID 和旧代码片段猜测目标 finding 是否消失；同文件多个待审补丁又会在前序修改改变文件长度后继续使用初始 scan location，从而误拒绝仍然合法的后续补丁。与此同时，直接覆盖写文件可能在编码或 I/O 失败时截断源码，Semgrep 的不完整结果也可能被误报为零 finding。P0 需要从数据契约开始重建安全链路，确保系统既不损坏源码，也不在证据不足时宣称修复成功。

## What Changes

- 为每个 finding 建立 AutoPatch-J 自有的版本化 fingerprint 和精确源码 region，并将身份从 scan artifact 贯穿到 patch workspace；fingerprint、规则和路径保持稳定，workspace 中的 region 表示当前绑定位置。
- search-replace patch 必须持久化 old string 的唯一 `match_region`；关联 finding 的 patch 必须覆盖当前 finding region，否则拒绝进入 review queue 或 revision preview。
- 前序补丁成功落盘后，同文件非重叠 pending drafts 按实际编辑区域确定性重定位并刷新 diff 与语法校验；无法重新证明绑定的草案进入 `STALE_DRAFT`，不得 apply 或 revise。
- post-apply 验证改为 `RESOLVED`、`STILL_PRESENT`、`UNVERIFIED` 三态，并由实际 source/changed region 将 apply 前目标映射为 apply 后验证足迹；删除或缩短 replacement 不得遗漏仍存的目标证据，同文件其他位置的同规则 finding 也不再混淆目标结论。
- Semgrep 返回任何 `errors` 或无法构造完整 finding identity 时，整次扫描 fail closed，不保存成功 artifact，也不进入 zero-finding 流程。
- 补丁采用同目录临时文件和原子替换落盘，并在最终替换前比较源码 bytes；原子替换前的失败和最终比较已观察到的外部变化均保留当时的目标文件。
- **BREAKING**：scan artifact 与 workspace patch snapshot 使用新的必需 identity/region/binding 结构；系统未上线，因此不兼容、不迁移旧格式，也不保留旧 snippet heuristic。

## Capabilities

### New Capabilities

- `patch-safety`: 定义可信扫描结果、稳定 finding 身份、finding 与 patch region 绑定、原子补丁落盘及精准 post-apply 验证。

### Modified Capabilities

无。

## Impact

- 影响 scanner 结果归一化、finding/patch 领域类型、scan/workspace JSON、search-replace patch 引擎、补丁草案生成与修订、review queue 重定位、post-apply verifier 和 CLI apply 展示。
- `get_finding_detail` 输出增加 fingerprint 与完整 region；`propose_patch`、`revise_patch` 的 function-call 输入 schema 不变。
- 不新增外部依赖，不接入 Semgrep 登录版 fingerprint，不扩展为跨 branch、commit 或 rename 的长期 finding tracking。
