## 1. 路由诊断

- [x] 1.1 增加结构化 route 和 intent 分类结果对象。
- [x] 1.2 更新输入路由，使用诊断方法并保持现有行为。
- [x] 1.3 仅在 debug 模式渲染紧凑的路由 fallback 诊断。

## 2. 源码路径安全

- [x] 2.1 将缺失路径的 first-match 纠正替换为唯一 in-focus 候选解析。
- [x] 2.2 对歧义缺失路径返回清晰的候选提示。
- [x] 2.3 增加唯一候选、歧义候选、缺失路径和 focus 限制场景测试。

## 3. 验证

- [x] 3.1 运行路由和源码读取聚焦测试。
- [x] 3.2 校验该 OpenSpec change。

## 4. 文档规则对齐

- [x] 4.1 按 `AGENTS.md` 铁律将 proposal、design、spec 和 tasks 正文化为中文。
- [x] 4.2 运行 OpenSpec strict validate。

## 5. 归档前规格同步

- [x] 5.1 将 delta specs 同步到 `openspec/specs/` 主规格目录。
