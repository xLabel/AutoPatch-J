## ADDED Requirements

### Requirement: Finding identity and source region
系统 SHALL 为每个成功归一化的 scanner finding 生成必需的应用侧 fingerprint，并保存 repo-relative POSIX path、规则 ID、精确 line/column 与 byte-offset region。fingerprint SHALL 基于版本、engine、规则 ID、路径和精确匹配源码生成，且不得使用 Semgrep 登录态专属 fingerprint 作为本地身份。

#### Scenario: Repeated scan preserves identity
- **WHEN** 相同源码以不同的 raw result 顺序被重复扫描
- **THEN** 每个未变化 finding 的 fingerprint 保持一致

#### Scenario: Duplicate evidence remains distinguishable
- **WHEN** 同文件内存在多个规则、路径和匹配源码均相同的 findings
- **THEN** 系统按源码位置分配确定性 ordinal，使每个 finding 获得不同 fingerprint

#### Scenario: Invalid location rejects scan
- **WHEN** scanner result 缺少安全路径、规则 ID、line/column/offset 或 region 越界
- **THEN** 整次扫描返回 error，且不得产出部分成功 artifact

### Requirement: Complete scanner evidence
系统 MUST 要求 Semgrep payload 显式包含 list 类型的 `results` 和 `errors`，并将缺少任一数组或任何非空 `errors` 视为扫描失败。错误扫描 MUST NOT 保存成功 artifact、进入 finding backlog 或触发 zero-finding review。

#### Scenario: Required result arrays are missing
- **WHEN** Semgrep stdout 为空，或 JSON payload 缺少 `results`、`errors` 中的任一必需数组
- **THEN** 系统报告扫描失败，且不得将该 payload 归一化为零 finding 成功结果

#### Scenario: Parser error is not a clean scan
- **WHEN** Semgrep 返回零 results 但 `errors` 包含解析错误
- **THEN** 系统报告扫描失败，且不得显示零问题结论

#### Scenario: Findings and errors coexist
- **WHEN** Semgrep 同时返回 findings 与非空 `errors`
- **THEN** 系统丢弃该次不完整 findings 集合并返回 error

### Requirement: Patch is bound to its target finding
search-replace patch MUST 保存 old-string 在当前源码中的唯一 `match_region`。关联 finding 的 patch MUST 同时保存目标 identity，并且 `match_region` MUST 与当前目标 finding region 相交。无法证明绑定的 patch MUST NOT 进入 review queue 或 revision preview。

#### Scenario: Patch touches target evidence
- **WHEN** patch 只替换 finding region 内的违规 token
- **THEN** 系统接受关联并将完整目标 identity 与 `match_region` 写入 patch snapshot

#### Scenario: Patch changes unrelated code
- **WHEN** patch 与 finding 属于同一文件但两个 region 不相交
- **THEN** 系统以明确错误拒绝该关联 patch

#### Scenario: Revision inherits current binding
- **WHEN** 关联 finding 的 revision 省略可选 finding handle
- **THEN** 系统在构建预览前继承当前 handle、scan ID 与 target identity，并使用当前 target region 校验新 `match_region`

#### Scenario: Revision cannot switch finding
- **WHEN** revision 显式指定不同 finding，或新 `match_region` 未覆盖当前 target region
- **THEN** 系统拒绝 revision，且不得写入 revised draft session 或替换 workspace item

### Requirement: Pending patch binding follows accepted edits
前序补丁成功应用后，系统 MUST 使用实际 source region 与 changed region 重定位同文件非重叠 pending patch 的 `match_region` 和 target finding region，并刷新 diff 与语法校验。无法重新证明 binding 的草案 MUST 标记为 `STALE_DRAFT`，且不得 apply 或 revise。

#### Scenario: Earlier edit shifts a later finding
- **WHEN** 前序补丁在同文件后续 finding 之前改变 byte 或 line 长度，且两个 region 不相交
- **THEN** 后续草案保持相同 fingerprint、规则、路径、handle 和 scan ID，同时保存重定位后的 regions、diff 与语法结果

#### Scenario: Finding detail follows the rebased location
- **WHEN** 当前 pending finding 已因同文件前序补丁完成 region 重定位，并通过原 handle 请求 finding detail
- **THEN** 系统使用 workspace 中的当前 target region 返回位置和当前源码片段，而不是使用 scan artifact 中的初始 region

#### Scenario: Pending binding cannot be proven
- **WHEN** pending match/target region 与前序 source region 相交，或 old string 在当前源码中缺失、不唯一或偏离预期位置
- **THEN** 系统保留原草案证据并标记 `STALE_DRAFT`，不得猜测新目标

#### Scenario: Stale draft is blocked
- **WHEN** 用户尝试 apply 或 revise `STALE_DRAFT`
- **THEN** 系统不得写文件或生成 replacement，并提示 discard/abort 后重新扫描

### Requirement: Atomic source replacement
系统 MUST 在同目录临时文件中完成全部编码和写入，并使用原子替换提交补丁。系统 MUST 只替换 `match_region` 对应的 bytes，匹配区域外的原始 bytes MUST 保持不变。原子替换前的任何失败或最终 bytes 比较已检测到的外部源码变化 MUST 保留当时的目标文件 bytes。系统不承诺覆盖最终比较完成后发生的非协作竞争写入。

#### Scenario: Encoding fails before replacement
- **WHEN** replacement 无法按原文件编码转换为 bytes
- **THEN** apply 返回失败，目标文件 bytes 完全不变

#### Scenario: Temporary write or replace fails
- **WHEN** 临时文件写入、flush、fsync 或原子替换发生 I/O 异常
- **THEN** apply 返回失败、清理临时文件并保留原文件

#### Scenario: Source change is visible to final comparison
- **WHEN** 目标文件 bytes 在最终比较前被外部修改并被比较观察到
- **THEN** 系统拒绝替换且不覆盖外部修改

#### Scenario: Successful apply preserves file characteristics
- **WHEN** patch 成功应用到具有特定 encoding、newline 和 permission mode 的文件
- **THEN** 新文件保留这些特征并返回实际 source region 与 changed region

#### Scenario: Mixed newlines outside the match stay byte-identical
- **WHEN** 源文件混用 CRLF、LF 或 CR，且 patch 只修改其中一个匹配区域
- **THEN** 匹配区域前后的原始 bytes 完全不变，changed region 的 byte delta 与文件真实长度变化一致

### Requirement: Targeted post-apply verification
系统 SHALL 使用完整的成功 apply 证据，将 apply 前目标 finding region 根据实际 source region 与 changed region 映射为 apply 后验证足迹，不得只使用 replacement region 或文件内是否仍存在相同规则判断目标。apply source MUST 等于草案 `match_region`，source 与 changed MUST 使用相同起点，source MUST 仍与当前目标 region 相交；无法证明这些 binding 时 MUST 返回 `UNVERIFIED` 且不得重扫。映射足迹内同路径、同规则 candidate 表示目标仍存在；足迹外 candidate 只作为其他剩余问题报告。

#### Scenario: Violating text changes inside patch region
- **WHEN** patch 改变了 fingerprint 证据文本但映射后的目标验证足迹内仍触发相同规则
- **THEN** verification outcome 为 `STILL_PRESENT`

#### Scenario: Deleted target suffix leaves violating prefix
- **WHEN** patch 删除或缩短目标 finding 的后半段，但重扫仍在保留的目标前缀报告同规则 candidate
- **THEN** verification outcome 为 `STILL_PRESENT`，不得因 replacement region 未与该前缀相交而报告 `RESOLVED`

#### Scenario: Deleted target prefix leaves shifted violating suffix
- **WHEN** patch 删除或缩短目标 finding 的前半段，但重扫仍在平移后的目标后缀报告同规则 candidate
- **THEN** verification outcome 为 `STILL_PRESENT`

#### Scenario: Full deletion keeps adjacent finding separate
- **WHEN** 目标被完整删除为零长度足迹，且同规则 candidate 仅结束于删除点而不覆盖该点
- **THEN** 该 candidate 只作为其他位置 finding 报告；覆盖或起始于删除点的 candidate 仍视为目标足迹内触发

#### Scenario: Target disappears while another occurrence remains
- **WHEN** 映射后的目标验证足迹内不再触发目标规则，但文件其他位置仍有相同规则 finding
- **THEN** verification outcome 为 `RESOLVED`，并报告其他同规则 finding 数量

#### Scenario: Target and all same-rule findings disappear
- **WHEN** 映射后的目标验证足迹与文件其他位置均不再触发目标规则
- **THEN** verification outcome 为 `RESOLVED` 且同规则剩余数量为零

### Requirement: Verification outcome is explicit
系统 MUST 使用 `RESOLVED`、`STILL_PRESENT`、`UNVERIFIED` 三态表达 post-apply 结论。成功写入与成功验证 MUST 作为两个独立事实展示；验证失败不得自动回滚用户已确认的补丁。

#### Scenario: Scanner cannot provide trustworthy evidence
- **WHEN** post-apply scanner 不可用、超时、返回 error，或 patch 缺少目标 identity、成功 apply 证据或一致的 source/changed binding
- **THEN** verification outcome 为 `UNVERIFIED`，且系统不得宣称目标已解决

#### Scenario: Applied patch remains after failed verification
- **WHEN** 文件已成功原子替换但 verification outcome 为 `STILL_PRESENT` 或 `UNVERIFIED`
- **THEN** review item 标记为 applied，CLI 同时明确显示补丁已应用与验证未通过
