# account-research —— 账号调研产品项目

> **产品定位**：把「对标账号为什么能做起来」变成**有证据、有判决**的拆解报告——唯一把对标拆解做成有封闭判决、有证据分级、有 ⬜ 纪律、有双镜自审的 AI 产品。
> **形态判决**（见 [`../docs/product-research-account-research-2026-10-21.md`](../docs/product-research-account-research-2026-10-21.md)）：🔧 改方向——**Skill 先行、Web 放大**。采集行为的产物是 HTML 文件，Skill 形态下「把 HTML 贴给 AI」是「保存 HTML」的自然延伸；PC Web 工具（纯客户端解析）作为放大路径，触发条件见 `web/README.md`。

## 目录导览

| 目录 | 装什么 | 状态 |
|---|---|---|
| `skill/` | **方法论**：SKILL.md 九步主干 + 红线 + 输出格式；references/（指标字典、采集手册、E2E 流程、数据层、HTML 模板、落地镜）；examples/（真实 case）；账号库台账（只追加） | v0.3.3：九步之后的「落库」升级为 `ar debrief` 一键拆解，方法论开始指挥宿主 runtime |
| `src/account_research/` | **确定性处理层**（Python 标准库，零依赖）：`ar probe/parse/validate/stats/add/selftest/ingest/debrief/report/matrix/close/find`——存档解析、六张表、V1–V8 校验器、统计、拆解编排、报告渲染、矩阵视图、假设结案、**跨表检索**；`ar images` 图片通道（正文在图上时的取字路径，OCR 依赖可选） | ✅ v0.1.0 已对真实存档跑通（32 篇 / 置顶 2 / 样本 30）；图片通道对 5 篇真实详情页跑通（5–26 图/篇）；`debrief` 两次真跑落库；`report` 渲 markdown；`matrix` 渲离线 HTML；`close` 结案假设；`find` 跨表检索（查不到写「库内无证据」） |
| `agent/` | **Agent 契约**（八项必填 + 动作自治上限 + 5 条评估） | 草稿（缺「备份联系人」；契约见其文件） |
| `web/` | **副端 PC Web 工具**：纯客户端解析（File API，数据不出浏览器）+ 静态托管 | 占位，未启动（触发条件见其 README） |
| `extension/` | **浏览器扩展 v8.1**：点一下 → 面板 → **主页与笔记页各存一份单文件 MHTML（含图）并直接入库**。主页连 `__INITIAL_STATE__` 子树一起带回；state 为空时退到 DOM 腿（粉丝/简介/卡片仍在，只有发布日与置顶缺） | 可用，见其 README |
| `tests/` | 验收测试：590 个单测（合成 fixture）+ `ar selftest` 真实存档回归 | ✅ 全绿 |
| `docs/` | 产品工程文档：方向卡、PRD、case 复盘、Agent 运行记录 | 见其 README |
| `data/` | 结构化数据（六张 CSV，gitignore，仅本地） | —— |
| `../account/` | 采集原始存档（仓库根，gitignore，仅本地） | 本地在（卡兹克 6 份详情页 + 主页存档 + 图片） |

## 当前进度（2026-11-18）

- **核心工作流已跑通七层**：CLI（解析/校验/统计 590 单测 + 15 项真实存档回归）→ Skill（v0.3.23，`ar debrief` 一键拆解）→ **报告（`ar report`，不再手写）** → **矩阵（`ar matrix`，离线 HTML）** → **检索（`ar find`，跨表 + 带编号）** → **采集（扩展 v8.1：主页 + 详情页，一次点击）** → Agent（契约 v0.1 + 一次真实运行）
- **采集是「一个插件、一次点击」，主页也算**：装一次本地宿主（`ar host --register`），之后
  点扩展图标 → 按「采集」→ **直接入库**。不需要终端、不需要下载目录、不需要 `ar watch`。
  面板上能看到今日已采 / 库里篇数 / 失败在哪一层。
  v8 起主页也能采（`pageCapture` 剥掉 `<script>`，所以扩展把 `__INITIAL_STATE__` 子树
  单独带回来落成 `.state.json` 旁车）。**v8.1 补了第二条腿**：小红书是 CSR，首屏
  state 里账号头部是空的，所以「能不能采」改看 DOM 里有没有 `.user-name`；
  没有 state 时走 `profile_dom` 腿，发布日与置顶写 ⬜（页面上是「3 天前」，
  置顶卡在 DOM 里与普通卡一模一样）。
  见 [`docs/一个插件-原生消息-20261116.md`](docs/一个插件-原生消息-20261116.md)
- **采集不再有「准入名单」**：`accounts` 是目标/进度清单，不是白名单。
  归属由详情页里的作者链接证明；作者不在库里就建**占位账号**（粉丝/简介 ⬜，
  备注「缺主页存档」），补一次主页即整行替换。拒绝是静默丢数据，占位行是可见、可清理的。
- **采集端实测过两次真数据**：一次成功（16 图 / 73 子资源 / 图文全入库），
  一次暴露**静默错数据**（`dom_slides` 搜索越过 slide 边界「借用邻居的本地路径」，
  16 张只有 3 张有字节却报「已绑全」）。已修。
  见 [`docs/采集端-v6-实测复盘-20261113.md`](docs/采集端-v6-实测复盘-20261113.md)
- **token 有保质期**：`xsec_token` 是会话级参数，从 21 天前的存档里读出来基本必然 404。
  `ar links` 现在会报 token 年龄并在过期时明说「别从这里点，回主页点进去」
- 数据层：六张表 + V1–V8 校验器 + 纪律告警（D1 判决到期 / D2 假设逾期 / D3 台账断流）；回归基线 32 篇（置顶 2 / 样本 30）
- **解析能力**：详情页双通道（state + DOM），分享数、绝对发布日、视频时长可得；圆整值（`1.2万`）保留不确定性并标 ≈。见 [`docs/解析能力-补完-20261103.md`](docs/解析能力-补完-20261103.md)
- **评论原文通道**：11 篇存档共 95 条首屏评论（作者/原话/时间/属地），落 `<note_id>.comments.txt`。此前「评论一条都没采」的结论是错的。见 [`docs/评论原文通道-20261104.md`](docs/评论原文通道-20261104.md)
- **拆解能力已跨账号验证**：2 个号完成拆解（卡兹克 14.4 万粉 / 知新 1.68 万粉）。
  **「结构不是瓶颈」可复现，「量级从哪来」不可复现**
- **报告渲染**：`ar report` 把 `findings/playbook/hypotheses/verdicts` 渲成可读 markdown，
  **报告里没有任何手写数字**，改表即改报告。见 [`docs/报告渲染-20261112.md`](docs/报告渲染-20261112.md)
- 可视化：**S4 首版已落地**（`ar matrix` 跨账号矩阵，单文件离线 HTML）。`ar links --out` 生成的是**待采清单看板**，不是统计视图——两者别搞混
- **纪律执行面补齐**：`ar close hypothesis <id> --status 超期未验 --note "..."`。此前 D2 连喊 10 天
  「该结案」而命令行做不到（三条路全死）——根因是表键 `hypothesis_id` 让结案在物理上不存在。
  见 [`docs/假设结案-20261112.md`](docs/假设结案-20261112.md)
- 真实缺口：56 篇缺详情（其中 **54 篇从未用详情页试过**，`ar gaps` 逐条标出）；
  FuzzyPop 零数据（无主页存档）；**尚无一条打法在 ≥2 个账号上验证**（PRD §8 Q2 未达成，矩阵页 §4 直接标「未达成」）；
  3 条假设已按纪律降为 `超期未验`——**它们都需要自号发内容，调研侧永远做不到**
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
| **S2（已完成）** | `ar debrief` 调 `dsh headless` | 存 HTML → `ar debrief --account <id>` | + 一键拆解、产物先待审再 `--commit` 落库 | ✅ **三个账号级别的真跑**（卡兹克 ×2 轮、知新 ×1 轮） |
| S3 | 采集提效（粘贴/截图入口 → 插件） | 更省力的采集 | + 归属校验前置 | 部分（`extension/` 可用了） |
| **S2.5（已完成）** | `ar report` 渲染 | 一条命令 | 四张结论表 → 可读 markdown，**零手写数字** | ✅ 两个账号均出报告 |
| S4 | 可视化（跨账号矩阵 HTML） | 点开看 | + 同源渲染（共用 `ReportData`） | ✅ 首版（矩阵视图） |

> 🔧 **2026-11-12：S4 首版落地。** `ar matrix` 生成单文件离线 HTML（无脚本/外链/图片），
> 回答 PRD §8 的 Q1（形式对照）、Q2（跨账号打法）、Q3（母题矩阵 + 失效清单）。
> 实测三问的答案：**Q2 = 0/17，一条都没达成**——两个号的打法没有交集，页面直接标「未达成」。
> 见 [`docs/矩阵视图-20261112.md`](docs/矩阵视图-20261112.md)。

> 🔧 **2026-11-12：报告不再手写。** `ar report` 把 `findings/playbook/hypotheses/verdicts`
> 渲成 markdown 之前，两份报告都是手抄数字、手排版式的——每拆一个号就要重抄一遍，
> 而手抄会漂（实测把 27/31 写成 29/31，读起来完全合理）。
> 现在「改表即改报告」，版式与口径只维护一处。见 [`docs/报告渲染-20261112.md`](docs/报告渲染-20261112.md)。

> 🔧 **2026-11-04：S2 的能力已跨账号验证。** 知新的AI笔记那份拆解证明了两件事：
> ① 「结构不是瓶颈」这个核心结论**可复现**（不同骨架、同骨架内差 29 倍 vs 卡兹克的 41 倍）；
> ② 但「量级从哪来」**不可复现**（卡兹克吃选题公共性，知新吃人物认知度）——
> 所以这套能力不能被做成模板，只能一次次跑。报告见
> [`skill/examples/知新的AI笔记-对标拆解报告.md`](skill/examples/知新的AI笔记-对标拆解报告.md)。

## 渲染报告（S2.5，2026-11-12 起）

```bash
python account-research/ar.py report --account <id>                            # 打印到终端
python account-research/ar.py report --account <id> --out 报告.md              # 落盘
python account-research/ar.py report --account <id> --notes-limit 10           # 逐篇表只显示 10 行
```

`--notes-limit` **只影响表格行数，不影响任何统计**——切掉的是显示，不是样本。
报告结构：判决 → §0 数据底稿 + 覆盖度 + 永远拿不到的 → §1 逐篇 → §2 四层结论
→ §3 可抄清单（失效条件缺失会标 ⚠️）→ §4 假设 → §5 判决三件套 + 双镜 → §6 取证边界。

## 矩阵视图（S4，2026-11-12 起）

```bash
python account-research/ar.py matrix --out 矩阵.html                 # 全部账号
python account-research/ar.py matrix --account <id> --out 矩阵.html   # 只渲一个
```

单文件 HTML，**无脚本、无外链、无图片**，断网双击可开。六个区块：
账号总览（单指标排序）→ 形式对照（Q1）→ 母题矩阵（Q3 上半）→ 打法库（Q2）
→ 失效条件清单（Q3 下半）→ 本页没做到的事。

三个「不许装」的地方：**样本不足不给中位数**（`<3` 篇只报计数）、
**空值不写成 0**（没采到数据是 ⬜，不是「中位赞 0」）、
**未标注不合并隐藏**（`topic` 全空的账号会显式标「标注缺失」）。

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
