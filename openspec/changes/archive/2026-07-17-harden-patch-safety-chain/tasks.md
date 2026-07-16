## 1. Finding 身份与扫描证据

- [x] 1.1 引入严格的 `SourceRegion`、`FindingIdentity` 与新版 `Finding` JSON schema，并将当前调用点切换到显式 identity/region；不保留旧 schema 读取或迁移逻辑。
- [x] 1.2 重构 Semgrep 结果解析：严格校验 errors、必需字段、路径与字节区间，并基于规则、仓库相对路径、匹配字节和稳定序号生成应用侧 fingerprint。
- [x] 1.3 增加 finding identity、重复证据、稳定重扫、异常结果 fail-closed 的聚焦测试，并运行相关 scanner/model 测试文件。

## 2. 补丁边界与原子落盘

- [x] 2.1 让 search/replace draft 返回匹配区域，并将补丁目标持久化为 `FindingIdentity`；构建阶段拒绝未覆盖目标 finding 区域的补丁。
- [x] 2.2 将补丁应用改为结构化结果，并通过同目录临时文件、预编码、flush/fsync、权限保留、源文件并发变化检查和 `os.replace` 完成原子替换。
- [x] 2.3 增加目标区域约束、编码/换行/权限保留、写入失败和源文件变化的聚焦测试，并运行 patch、workspace 与 tool 测试文件。

## 3. 精确验证与 CLI 状态

- [x] 3.1 引入 `RESOLVED`、`STILL_PRESENT`、`UNVERIFIED` 三态验证结果，并只用同路径、同规则且与本次变更区域相交的 finding 判定目标仍存在。（后续由 10.3 替代：使用实际 source/changed 映射目标验证足迹。）
- [x] 3.2 更新 apply/review CLI：原子落盘成功后保持 `APPLIED`，同时单独展示验证三态与同规则其他位置的剩余数量。
- [x] 3.3 增加目标消失、目标仍在、同规则其他位置仍在和扫描失败的聚焦测试，并运行 verifier、CLI 与 workflow 测试文件。

## 4. 回归与规格校验

- [x] 4.1 运行受影响模块的聚焦 pytest，修复由干净 schema 切换暴露出的当前调用点和测试夹具问题。
- [x] 4.2 运行 `pytest -q` 完整回归，确认全部测试通过。
- [x] 4.3 运行 `openspec validate harden-patch-safety-chain --strict`，确认实现后的 OpenSpec 产物严格校验通过。

## 5. CR 后规格重对齐

- [x] 5.1 更新 `proposal.md`，补充同文件队列重定位、revision binding 与落盘契约边界。
- [x] 5.2 更新 `design.md`，记录 mutable location、rebase 算法、stale 行为和 TOCTOU 取舍。
- [x] 5.3 更新 `spec.md`，形成无冲突的最终行为契约。
- [x] 5.4 运行 OpenSpec 严格校验。

## 6. Case P1：队列重定位与落盘契约

- [x] 6.1 扩展 draft、snapshot、apply result 和 rebase result 数据契约。
- [x] 6.2 强化 apply match binding，并实现确定性 region rebase。
- [x] 6.3 在 review workflow 中刷新同文件 pending drafts，并提供 stale CLI 行为。
- [x] 6.4 收窄原子落盘契约，更新对应聚焦测试。

## 7. Case P2：revision binding

- [x] 7.1 在 revision build 前继承并验证当前 association。
- [x] 7.2 移除 workspace manager 的事后 association 回填并增加防御校验。
- [x] 7.3 增加 revision bypass 聚焦测试。

## 8. 回归与严格验证

- [x] 8.1 更新所有 draft/snapshot 测试夹具并运行受影响测试。
- [x] 8.2 运行完整 `pytest -q` 回归。
- [x] 8.3 运行 OpenSpec 严格校验、diff 检查和工作区审计。

## 9. CR 后续：扫描完整性、字节保真与重定位取证

- [x] 9.1 更新 `design.md`、`spec.md` 与任务账本，明确必需 Semgrep 数组、raw-byte splice 和当前 finding region 取证契约。
- [x] 9.2 拒绝缺少 `results` 或 `errors` 的 Semgrep payload，并增加 parser 与 scanner 聚焦测试。
- [x] 9.3 使用原始 prefix/suffix bytes 落盘 replacement，并增加混合换行与后续 draft rebase 测试。
- [x] 9.4 让 `get_finding_detail` 对当前 pending handle 使用 workspace target region，并增加重定位取证测试。
- [x] 9.5 运行聚焦测试、完整回归、OpenSpec strict validate、diff 检查和工作区审计。

## 10. CR 后续：删除足迹与目标验证

- [x] 10.1 更新 `proposal.md`、`design.md`、`spec.md` 与任务账本，使用完整 apply 证据映射目标验证足迹。
- [x] 10.2 增加删除、缩短、零长度边界、无效 apply binding 与 CLI 证据传递测试。
- [x] 10.3 让 verifier 接收完整 `PatchApplicationResult`，实现目标足迹映射并 fail closed 处理不一致 binding。
- [x] 10.4 运行聚焦测试、完整回归、OpenSpec strict validate、diff 检查和工作区审计。
