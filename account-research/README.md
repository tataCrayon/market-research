# account-research —— 账号调研产品项目

> **产品定位**：把「对标账号为什么能做起来」变成**有证据、有判决**的拆解报告——唯一把对标拆解做成有封闭判决、有证据分级、有 ⬜ 纪律、有双镜自审的 AI 产品。
> **形态判决**（见 [`../docs/product-research-account-research-2026-10-21.md`](../docs/product-research-account-research-2026-10-21.md)）：🔧 改方向——**Skill 先行、Web 放大**。采集行为的产物是 HTML 文件，Skill 形态下「把 HTML 贴给 AI」是「保存 HTML」的自然延伸；PC Web 工具（纯客户端解析）作为放大路径，触发条件见 `web/README.md`。

## 目录导览

| 目录 | 装什么 | 状态 |
|---|---|---|
| `skill/` | **方法论**：SKILL.md 九步主干 + 红线 + 输出格式；references/（指标字典、采集手册、E2E 流程、数据层、HTML 模板、落地镜）；examples/（真实 case）；账号库台账（只追加） | v0.3.3：九步之后的「落库」升级为 `ar debrief` 一键拆解，方法论开始指挥宿主 runtime |
| `src/account_research/` | **确定性处理层**（Python 标准库，零依赖）：`ar probe/parse/validate/stats/add/selftest/ingest/debrief`——存档解析、六张表、V1–V8 校验器、统计、拆解编排；`ar images` 图片通道（正文在图上时的取字路径，OCR 依赖可选） | ✅ v0.1.0 已对真实存档跑通（32 篇 / 置顶 2 / 样本 30）；图片通道对 5 篇真实详情页跑通（5–26 图/篇）；`debrief` 两次真跑落库 |
| `agent/` | **Agent 契约**（八项必填 + 动作自治上限 + 5 条评估） | 草稿（缺「备份联系人」；契约见其文件） |
| `web/` | **副端 PC Web 工具**：纯客户端解析（File API，数据不出浏览器）+ 静态托管 | 占位，未启动（触发条件见其 README） |
| `extension/` | **浏览器扩展**：详情页一键存档（替代 Ctrl+A → 记事本 → 存盘） | 可用，见其 README |
| `tests/` | 验收测试：196 个单测（合成 fixture）+ `ar selftest` 真实存档回归 | ✅ 全绿 |
| `docs/` | 产品工程文档：方向卡、PRD、case 复盘、Agent 运行记录 | 见其 README |
| `data/` | 结构化数据（六张 CSV，gitignore，仅本地） | —— |
| `../account/` | 采集原始存档（仓库根，gitignore，仅本地） | 本地在（卡兹克 6 份详情页 + 主页存档 + 图片） |

## 当前进度（2026-10-26）

- **核心工作流已跑通三层**：CLI（解析/校验/统计 33 单测 + 15 项真实存档回归）→ Skill（v0.3.1 加「落库」环节）→ Agent（契约 v0.1 + 一次真实运行）
- 数据层：六张表 + V1–V8 校验器；回归基线 32 篇（置顶 2 / 样本 30）、爆文率 K=3 16.7% / K=5 10.0%
- 可视化：**按方向卡「先不做」**，S1 只做确定性部分
- 上阶段遗留：动态重跑（换数据源撞结论）仍未做；3 篇详情页数据靠人工转录，未入 `notes`（藏/评/分享覆盖度 0/30，如实计 ⬜）
- 方向见 [`docs/方向卡-v0.1.md`](docs/方向卡-v0.1.md)；详细规格见 [`docs/prd-v0.1-账号知识库与领域矩阵.md`](docs/prd-v0.1-账号知识库与领域矩阵.md)

## 2026-10-29 补的一条：图片通道

「小红书很多内容在图片里」不是采集问题——用「网页，全部」保存时图已经落在 `<存档>_files/` 里了（5–26 张/篇）。缺的是两步：把图和笔记对上（join key 只能是 URL 末段），把字取成文本（本地 OCR，离线）。见 [`docs/图片内容通道.md`](docs/图片内容通道.md)。

```bash
python account-research/ar.py images          # 清单：每篇几张图、本地命中几张、正文多少字
python account-research/ar.py images --ocr     # + OCR 落 account/notes-text/<note_id>.images.txt
```

**硬边界（R7）**：图字节永不进模型上下文。一张 1440×2400 的笔记图约 4.3k tokens，24 张 ≈ 10 万——这是本项目唯一一次真的把请求撑爆成 `Input exceeds the context limit` 的原因。

## 路线图

| 阶段 | 载体 | 人的操作 | 自动化部分 | 状态 |
|---|---|---|---|---|
| S0（已完成） | Skill（AI 对话驱动） | 存 HTML + 打包给 AI | 解析 → 拆解 → 判决 → 渲染 | ✅ |
| **S1（已完成）** | CLI 数据层（本地表） | 存 HTML → `ar ingest` | 解析 → 校验 → 统计 → 落库（正文与图上文字一次落盘） | ✅ 跑通 |
| **S2（已完成）** | `ar debrief` 调 `dsh headless` | 存 HTML → `ar debrief --account <id>` | + 一键拆解、产物先待审再 `--commit` 落库 | ✅ 两次真跑 |
| S3 | 采集提效（粘贴/截图入口 → 插件） | 更省力的采集 | + 归属校验前置 | 部分（`extension/` 可用了） |
| S4 | 可视化（Web / HTML 视图） | 点开看 | + 同源渲染 | 未开始 |

## 一键拆解（S2，2026-11-01 起）

```bash
python account-research/ar.py debrief --account <id> --dry-run   # 只看任务，不落盘不调模型
python account-research/ar.py debrief --account <id>             # 真跑 → payload.json 待复核
python account-research/ar.py debrief --account <id> --replay <payload.json> --commit  # 改完重走闸门并落库
```

分工的判据只有一条：**确定性的活留在代码里，只有归纳判断交给模型**。统计、列名、闸门、落库都在 `ar` 这一侧；
模型侧运行时被关掉网络、shell、写文件权限（`tool-fs` 只留 read），结论只能从 stdout 交回。
默认不落库是因为 append-only 表没有回滚——先写后校验的代价是脏行永久留下。详见
[`skill/references/data-layer.md`](skill/references/data-layer.md) §8。

## 使用

方法论入口 = [`skill/SKILL.md`](skill/SKILL.md)。把 Skill 装进 AI 工具的技能目录时，拷贝整个 `skill/` 目录。

## 纪律速查

继承仓库契约 [`AGENTS.md`](../AGENTS.md) 四条铁律（判决落地 / 证据分级 / 未取证 ≠ 没问题 / 反谄媚）；项目特有纪律见本目录 [`AGENTS.md`](AGENTS.md)。
