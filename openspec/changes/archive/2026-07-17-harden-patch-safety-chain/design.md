## Context

扫描快照目前只保存规则 ID、文件路径、起止行和展示 snippet。`F1` 只是 scan 内数组下标，patch snapshot 又只复制 `target_check_id` 与 `target_snippet`，因此 post-apply 重扫无法区分同文件同规则的多个 finding，也会在违规代码发生文本变化时误报目标已解决。另一方面，patch engine 使用 `"w"` 直接覆盖目标文件，编码或写入异常可能在返回失败前截断源码；Semgrep JSON 中的 `errors` 也没有进入结果状态。

本 change 采用 clean break。系统尚未上线，不保留旧 artifact/workspace 读取、迁移或 fallback，也不让旧 snippet heuristic 继续参与验证。

## Goals / Non-Goals

**Goals:**

- 让每个 scanner finding 具有可持久化、与 scan 排序无关的应用侧 identity 和精确源码 region。
- 强制关联 finding 的 patch 实际修改目标 region，并用 apply 的真实 source/changed region 映射目标验证足迹，识别目标是否仍存在。
- 让同文件 pending patch 在已知前序编辑后保持可信绑定，无法证明时明确阻断。
- 让 revision 在构建预览前继承并校验当前 finding binding，不能依赖事后字段回填。
- 区分目标已解决、目标仍存在和无法验证三种结果。
- 让不完整扫描 fail closed，并让补丁写入失败保持原文件不变。

**Non-Goals:**

- 不跟踪跨 branch、commit、文件 rename 或任意重构后的长期 finding 身份。
- 不依赖 Semgrep 登录版 fingerprint，不新增 scanner plugin 抽象。
- 不迁移旧 scan artifact、workspace 或 patch snapshot。
- 不自动回滚已由用户确认并成功落盘的补丁。
- 不处理 pending review 路由和 `/init` 清理问题。

## Decisions

### 使用应用侧 fingerprint 与精确 region

新增 `SourceRegion`，保存 1-based、end-exclusive 的 line/column，以及 0-based、end-exclusive 的 byte offsets。Semgrep adapter 必须验证 path、check ID 与 region，并用 start/end offsets 从源码读取精确匹配字节。

fingerprint v1 的基础输入依次为版本标识、engine、规范化 check ID、repo-relative POSIX path 和仅做换行归一化的匹配字节，使用 SHA-256。相同基础 hash 的 finding 按 offsets 排序并附加稳定 ordinal，最终格式为 `apj-v1:<hex>:<ordinal>`。绝对行号不进入 hash，因此无关的前置行移动不会改变 identity；同文本重复项由 ordinal 区分。

不使用 Semgrep `extra.fingerprint`，因为当前 CE JSON/SARIF 输出为 `requires login`，并非本地可靠契约。fingerprint 负责把原始 finding 稳定带入 artifact、patch 和 workspace；代码证据被补丁改变后，post-apply 关联以 source/changed region 映射出的目标验证足迹为准，避免假设 hash 在任意代码变化后仍相同。

### 让 patch region 成为 typed result

`create_draft` 返回 updated source、diff 和 old-string 的精确 region。该 `match_region` 必须进入 runtime draft 和 workspace snapshot。`SearchReplaceDraftBuilder` 在关联 F1 时要求该 region 与目标 finding region 相交；同文件但不相交的 patch 直接拒绝。

`apply_patch` 不再返回裸布尔值，而是返回包含 `applied`、替换前 source region、替换后 changed region、error code 和 message 的 result。apply 时重新定位唯一 old string，并要求实际 region 等于草案持久化的 `match_region`；关联草案还必须与当前 `target_finding.region` 相交。

### 重定位同文件 pending patch binding

`FindingIdentity` 的 fingerprint、check ID 和 path 表示稳定身份；workspace 中 `target_finding.region` 表示当前绑定位置。前序补丁应用后不另设 `target_region`，而是创建 fingerprint/check/path 不变、region 已重定位的新 `FindingIdentity`。初始 scan location 仍由 `source_scan_id` 对应 artifact 保存。

patch engine 根据成功 apply 返回的 source region 与 changed region 计算 byte delta。完全位于 source region 前方的 pending region 保持 offsets；完全位于后方的 region 同步平移 offsets；与 source region 相交的 match 或 finding region 无法安全证明，草案进入 `STALE_DRAFT`。新 line/column 必须从替换后的真实 bytes、encoding 和 newline 重新计算，不能只累加行差。

重定位后必须在同一源码快照上重新建立 unique match、diff 和 updated source，并要求实际 match 等于预期位置。workflow 随后重新执行语法校验。fingerprint、finding handle、scan ID、rationale 和队列顺序均保持不变。old string 缺失、不唯一、位置不符、编码或边界异常时 fail closed；stale 草案只允许 discard/abort 后重新扫描，不自动按 check ID、snippet 或最近位置重绑。

### 在 revision 构建前继承 binding

普通 proposal 继续从 scan artifact 建立初始 association。revision 先读取当前 pending draft；省略 finding handle 时继承当前 handle、scan ID 和 `target_finding`，显式 handle 只允许规范化后仍等于当前 handle。共享 builder 使用继承后的当前 region 完成 overlap 校验，成功后才写入 revised draft session。

`ReviewWorkspaceManager.replace_current_patch` 不再补回缺失 association。它只接受 file path、handle、scan ID、完整 target identity 和 region 都与当前项一致，且 match region 仍覆盖 target region 的完整 replacement。

### 让 finding detail 使用当前 binding

scan artifact 保存 finding 的初始 identity、描述和规则证据，workspace 中当前 pending draft 的 `target_finding.region` 则表示经过前序补丁重定位后的当前位置。`get_finding_detail` 在请求句柄与当前 pending draft 的 handle、scan ID 一致时，使用 workspace region 刷新当前源码片段和返回位置；其他句柄仍返回 scan snapshot 位置。工具只构造临时 finding 视图，不回写 scan artifact，也不增加新的 location 历史层。

### 通过实际编辑映射目标验证足迹

verifier 接收完整的成功 `PatchApplicationResult`，并在重扫前要求 apply 的 source region 等于草案 `match_region`、source 与 changed 使用相同起点、source 仍与当前 target finding region 相交。任一 binding 无法证明时返回 `UNVERIFIED`，不使用可能来自其他 apply 的裸 region 继续判断。

设 apply 前目标为 `T=[t0,t1)`、实际替换源为 `S=[s0,s1)`、apply 后 replacement 为 `C=[s0,c1)`，且 `delta=|C|-|S|`。验证足迹包含未被替换的目标前缀、完整 replacement 和按 delta 平移的目标后缀：起点在 `t0 < s0` 时取 `t0`，否则取 `s0`；终点在 `t1 > s1` 时取 `t1 + delta`，否则取 `c1`。该映射不会改变 `changed_region` 的精确 replacement 语义，pending draft 仍按原 source/changed 计算 byte delta。

重扫只筛选相同规范化 path 与 check ID 的 candidates。任何 candidate 的 region 与映射后的目标足迹相交，都表示目标仍触发，结果为 `STILL_PRESENT`；足迹内没有 candidate 时为 `RESOLVED`。纯删除得到零长度足迹时，覆盖或起始于删除点的 candidate 视为相交，仅结束于删除点的相邻 candidate 不属于目标。足迹外同规则 findings 只计入剩余问题数量，不改变目标结论。

fingerprint 相等可用于说明证据完全未变，但不能脱离 region 单独把文件其他位置判成目标；重复证据的 ordinal 可能在更早 finding 消失后变化，因此不增加 fingerprint fallback。这样既能识别删除或缩短后仍保留的违规前后缀，也能在一个文件有多处同规则 finding 时只判断本次目标位置。

### 使用三态 verification outcome

`VerificationResult.is_resolved` 替换为 `VerificationOutcome.RESOLVED`、`STILL_PRESENT`、`UNVERIFIED`。scanner 不可用、结果错误、没有目标 identity、没有成功 apply 证据或 apply region 与草案 binding 不一致均为 `UNVERIFIED`，不能伪装成漏洞仍存在或已解决。

apply 成功与 verification 成功是两个事实。文件成功写入后 review item 仍标记 `APPLIED`；CLI 另外展示三态验证结论，不为验证失败自动回滚用户已确认的改动。

### 扫描完整性 fail closed

Semgrep payload 必须显式包含 list 类型的 `results` 和 `errors`。缺少任一顶层数组、`errors` 非空，或任一 result 缺少安全 path、check ID、line/column/offset 时，整次扫描返回 `error` 和空 findings。错误扫描不保存 artifact，也不进入 zero-finding LLM review。错误摘要只保留有限条目，避免终端输出失控。

### 同目录临时文件原子替换

patch engine 只对 replacement 做换行恢复和原编码转换，再使用已验证 `match_region` 的 byte offsets 将原始 prefix bytes、replacement bytes 和原始 suffix bytes 拼成最终内容。normalized source 只用于匹配、diff 和语法校验，不参与整文件落盘；因此混合 CRLF/LF/CR 文件中，匹配区域外的 bytes 保持不变，changed region 的 byte delta 也与真实文件长度变化一致。replacement 内的新换行继续使用现有文件级 newline 检测结果，不引入逐行换行映射。

patch engine 在触碰目标前构造并编码完整最终 bytes。它在同目录写入唯一临时文件，flush、`fsync` 并恢复原 permission mode；替换前再次比较目标 bytes，检测最终比较可观察到的外部修改；最后使用 `os.replace`。所有 replace 前的异常都清理临时文件并保留当时的目标文件。

`os.replace` 不提供 expected-content 参数，最终比较与 replace 之间仍存在非协作写入窗口。advisory lock 只能约束采用相同协议的写入者，不能兑现任意外部进程都不会被覆盖的强保证，因此不增加目录 `fsync`、跨进程锁、备份文件或断电恢复协议。本 change 保证目标不会暴露部分写入，并保证 replace 前失败及最终比较已观察到的外部变化不会被本次 patch 覆盖。

## Risks / Trade-offs

- [相同证据的 ordinal 会在更早重复项被增删时变化] → post-apply 匹配以 source/changed 映射的目标足迹为准，fingerprint 不承担任意重构后的长期关联。
- [严格 region 绑定会拒绝只修改 finding 外部上下文的修复] → 要求 patch old string 包含或触及目标证据，换取可证明的验证边界。
- [前序 patch 与后续 finding region 相交时无法安全重定位] → 将后续草案标记为 `STALE_DRAFT`，要求 discard/abort 后重新扫描，不猜测新目标。
- [最终比较与 `os.replace` 之间仍有非协作竞争窗口] → 明确收窄契约；若未来需要绝对单写者保证，另建 change 设计协作锁协议或平台级事务能力。
- [任何 Semgrep errors 都会降低可用性] → P0 优先保证扫描结论完整；未来如需 partial scan，必须另建显式 capability。
- [原子 replace 会创建新 inode] → 保留原 permission mode；不承诺保留扩展属性或 inode identity。

## Migration Plan

没有生产迁移。实现切换后只生成新格式 scan artifact 和 workspace；开发环境需要重新扫描并建立 review queue。不得自动删除本地状态，不提供旧字段读取或转换。

若实现需要回退，应整体回退代码并清理开发环境中由新版本生成的临时 scan/workspace，而不是让同一 runtime 同时读取两种格式。

## Open Questions

无。fingerprint 范围、目标成功定义、三态结果、apply 后不回滚及不兼容旧数据均已确认。

## Decision Record

- 2026-07-16：将原始 finding region 的直接 apply 校验调整为持久化 `match_region` 与可重定位的 `target_finding.region`；同时将任意外部写入不被覆盖的强契约收窄为最终比较可观察范围，原因是当前跨平台 `os.replace` 不提供 content CAS。
- 2026-07-16：将整文件 newline restore 调整为基于 `match_region` 的 raw-byte splice，并让当前 pending finding 的 detail 使用 workspace region；原因是混合换行会改写未审核 bytes，而 scan artifact location 在队列重定位后不再代表当前位置。
- 2026-07-16：将 post-apply 的 changed-only 判断调整为使用完整 apply result 映射目标验证足迹；原因是删除或缩短 replacement 会使仍存的目标前缀或后缀落在 changed region 之外，导致错误宣称 `RESOLVED`。
