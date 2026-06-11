## 1. 有界审计流程

- [x] 1.1 增加可配置的 audit finding batch limit。
- [x] 1.2 达到 batch 后停止处理，并在仍有未处理 finding 时打印继续提示。
- [x] 1.3 保持 pending patch queue 行为。

## 2. ReAct CLI 渲染

- [x] 2.1 为 reasoning、tool observation 和 final answer 抽出更清晰的渲染状态。
- [x] 2.2 增加 normal/debug 输出的事件序列测试。

## 3. Prompt 和 Scanner 清理

- [x] 3.1 拆分 prompt assets 到更小模块，同时保留 import 兼容。
- [x] 3.2 降低 planned scanner 在状态输出中的展示权重。

## 4. 验证

- [x] 4.1 运行 CLI workflow、stream presenter、prompt 和 scanner 测试。
- [x] 4.2 校验该 OpenSpec change。

## 5. 文档规则对齐

- [x] 5.1 按 `AGENTS.md` 铁律将 proposal、design、spec 和 tasks 正文化为中文。
- [x] 5.2 运行 OpenSpec strict validate。

## 6. 验证发现后的规格澄清

- [x] 6.1 澄清有界审计只持久化 pending patch queue，不持久化剩余 finding backlog。
- [x] 6.2 更新 proposal、design 和 bounded-audit-runs spec，明确后续处理通过重新扫描继续。
- [x] 6.3 运行 OpenSpec strict validate。

## 7. 归档前规格同步

- [x] 7.1 将 bounded-audit-runs 和 stable-react-cli-rendering delta specs 同步到 openspec/specs/ 主规格目录。
