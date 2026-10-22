# account-research —— 账号调研产品项目

> **产品定位**：把「对标账号为什么能做起来」变成**有证据、有判决**的拆解报告——唯一把对标拆解做成有封闭判决、有证据分级、有 ⬜ 纪律、有双镜自审的 AI 产品。
> **形态判决**（见 [`../docs/product-research-account-research-2026-10-21.md`](../docs/product-research-account-research-2026-10-21.md)）：🔧 改方向——**Skill 先行、Web 放大**。采集行为的产物是 HTML 文件，Skill 形态下「把 HTML 贴给 AI」是「保存 HTML」的自然延伸；PC Web 工具（纯客户端解析）作为放大路径，触发条件见 `web/README.md`。

## 目录导览

| 目录 | 装什么 | 状态 |
|---|---|---|
| `skill/` | **方法论**（原 `skills/account-research/` 整体迁入）：SKILL.md 九步主干 + 红线 + 输出格式；references/（指标字典、采集手册、E2E 流程、HTML 模板、落地镜）；examples/（真实 case）；账号库台账（只追加） | v0.2.0：首个真实 case 已跑通（数字生命卡兹克，判决 🧪 先验证） |
| `src/parser/` | **P3 解析器**（产品化第一个组件）：本地 HTML → 结构化数据表，纯本地文件处理 | 待建（v0.2 待办 #4，等 account/ 存档回传后对真实格式开发） |
| `web/` | **副端 PC Web 工具**：纯客户端解析（File API，数据不出浏览器）+ 静态托管 | 占位，未启动（触发条件见其 README） |
| `tests/` | 验收测试：对真实存档跑通、产出与报告数据节一致 | 待建（随解析器） |
| `docs/` | 产品工程文档：路线图、架构决策记录（ADR） | 待建 |
| `data/` | 结构化数据输出（gitignore，仅本地） | —— |
| `../account/` | 采集原始存档（仓库根，gitignore，仅本地） | 待用户回传（2026-10-22 重建采集） |

## 当前进度（2026-10-22）

- v0.2.0 真实 case 完成：数字生命卡兹克（14.4 万粉），判决 🧪 先验证；结构层 / 变现层 ⬜ 待 3 篇详情页存档
- v0.2 毕业三条件：静态清单 ✅ / 双镜 ✅ / **动态重跑 ❌ 未做**
- 7 天补数据硬线：10-28 到期（case 存档日 10-21 起算）
- 详细交接见 [`../docs/handoff-account-research-2026-10-22.md`](../docs/handoff-account-research-2026-10-22.md)

## 路线图（overall-workflow §8 产品化路线）

| 阶段 | 载体 | 人的操作 | 自动化部分 |
|---|---|---|---|
| 现在（v0.2） | Skill（AI 对话驱动） | 存 HTML + 打包给 AI | 解析 → 拆解 → 判决 → 渲染 |
| v0.3 | Skill + 诊断模式 | 同左 + 后台导出 CSV | + 漏斗排除 |
| v1.0+（产品化） | `web/` 工具 | 上传 HTML 包 | 同左 + 多账号管理 + 定时报告 |

## 使用

方法论入口 = [`skill/SKILL.md`](skill/SKILL.md)。把 Skill 装进 AI 工具的技能目录时，拷贝整个 `skill/` 目录。

## 纪律速查

继承仓库契约 [`AGENTS.md`](../AGENTS.md) 四条铁律（判决落地 / 证据分级 / 未取证 ≠ 没问题 / 反谄媚）；项目特有纪律见本目录 [`AGENTS.md`](AGENTS.md)。
