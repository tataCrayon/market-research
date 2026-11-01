# market-research

**一组调研 Skill 的合集，外加一个产品项目**——教 AI 把「说不清的问题」变成**有证据、有判决**的结论。
（`account-research` 已按产品立项判决从 Skill 提升为根目录产品项目，方法论在其 `skill/` 子目录；其余 Skill 仍在 `skills/`。）

> 调研的价值不在信息量，在**判决**。
> 没有规则约束时，AI 会给你 20 条链接和一句「建议进一步调研」；有了这套 Skill，它必须落到
> **做 / 改方向 / 先验证 / 卡合规 / 放弃** 里的一个，并附上最便宜的验证和放弃信号。

---

## 现有 Skill

| Skill | 覆盖领域 | 心脏（该领域最容易被偷工、又最决定成败的一步） |
|---|---|---|
| [`app-market-research`](skills/app-market-research/SKILL.md) | App / 小程序立项：市场 → 竞品 → 三端合规 → 做不做 | **三端硬约束七查** + **双镜自审**（v1.1 接入通用协议）：上架门槛 / 审核红线 / 资质备案 / 能力边界 / 付费分成 / 分发获客 / 成本周期 |
| [`game-research`](skills/game-research/SKILL.md) | 游戏 / 小游戏立项：市场 → 竞品 → 版号合规 → 买量单位经济 → 玩法成立 → 验证阶梯 → 做不做 | **三道独立筛子（版号 × 买量 × 玩法）+ 双镜自审**：版号决定能不能上线，LTV−CPI 决定能不能回本，玩法成立决定有没有人玩 |
| [`career-research`](skills/career-research/SKILL.md) | 职业 / 岗位调研（三模式）：**A** 岗位列表 → 职责与要求取证（含**用工性质/外包识别**）→ 命中矩阵与差距清单 → 判决；**B 岗位能力自查**；**C 外包名单维护** | **JD 真取证 × 可迁移证据链对账**：岗位名会蒸发，只有真实 JD + 官方职业编码才算证据；「3 年**相关**经验」的「相关」二字是转岗者的隐性硬门槛；**同一岗位名在自有/派遣/外包下是完全不同的选项**；自查的心脏是**证据件**——无证据的等级不作数 |
| [`account-research`](account-research/skill/SKILL.md) | 自媒体账号调研（**v0.2：真实 case 已跑通**）：小红书对标拆解——公开数据取证 → 定位/选题/结构/变现四层拆解 → 可抄清单 × 死亡清单 → 抄不抄；账号诊断（没流量原因）v0.3 起。**产品项目**（根目录，Skill 先行 / Web 放大） | **平台特异指标 × 可取证边界**：指标跨平台不可移植（拿抖音完播率判小红书 = 作废），公开可见 ≠ 后台可得，拿不到写 ⬜ 未取证；爆文率先取证定义再计算；**⏸「只观察」是公开数据撑不起结论时的正确结局**（须带补数据清单 + ≤7 天预算 + 到期降级 ❌，防变和稀泥） |

规划中（欢迎按模板贡献）：暂无新领域——下一个领域请按 [`docs/SKILL-AUTHORING.md`](docs/SKILL-AUTHORING.md) 自建，心脏步骤须与现有 Skill 不同。

---

## 怎么用（不限定 AI 工具）

三种方式，任选：

**1. 克隆后在仓库里干活** —— 任何 AI 工具都能接

```bash
git clone https://github.com/tataCrayon/market-research.git
```

仓库根部的 `AGENTS.md` 是通用入口：Claude Code（`CLAUDE.md`）、Cursor（`.cursor/rules/`）、
GitHub Copilot（`.github/copilot-instructions.md`）都会自动读到它。其他工具手动喂 `AGENTS.md` 即可。

**2. 把 Skill 装进你的 AI 工具的技能目录**（跨项目可用）

```bash
git clone https://github.com/tataCrayon/market-research.git /tmp/mr
cp -r /tmp/mr/skills/app-market-research ~/.workbuddy/skills/       # WorkBuddy
cp -r /tmp/mr/skills/app-market-research ~/.claude/skills/          # Claude Code
cp -r /tmp/mr/skills/app-market-research <你的项目>/.agents/skills/  # 项目级
```

**3. 直接读来用** —— 把 `SKILL.md` 贴给任意大模型，让它按流程跑；报告骨架在「输出格式」一节。

---

## 每个 Skill 长什么样

```
skills/career-research/
├── SKILL.md                        v1.1.1 三模式(A调研/B能力自查/C名单维护) + 输入参数契约 + 结构化输出 + 红线(R1隐私/R5 UGC/R6用工性质) + 双镜自审
├── references/
│   ├── position-taxonomy.md            岗位图谱构建 + 官方职业分类取证入口 + 健康度判据
│   ├── jd-requirements.md              职责/任职要求字段字典 + 三级软硬拆分 + 检索语句模板
│   ├── capability-model.md             岗位能力模型 + 行为锚定等级(BARS) + 证据件降级规则（模式B）
│   ├── employment-nature.md            用工性质四分法 + 外包识别三级信号 + 反例 + 法定杠杆 + 核验入口
│   ├── outsourcing-suppliers.md        外包/人力服务供应商名单(货架式活文档) + 维护SOP + ①权威人社局名单入口
│   ├── candidate-matching.md           五类过滤条件 + 命中矩阵 + JSON schema + 差距审计 + 隐私红线
│   ├── research-sources.md             数据源分级(含③-b UGC/匿名) + 脉脉·小红书纪律 + 反向指标
│   └── practitioner-review.md          从业者审视八轴 + 职业专属 I/J 两轴实例化（落地镜）
└── examples/
    ├── AI应用方向-职业调研报告.md        模式A 样例（命中矩阵 + JSON 片段；判决已复审改判，见下）
    ├── AI应用开发工程师-岗位能力自查.md   模式B 样例（BARS等级 + 证据降级 + 假差距 + 与模式A 内部一致性验证）
    ├── 模式C-外包名单维护运行记录.md     模式C 样例（6份①权威公示 + 暴露并修复5个schema缺陷 + 法人全称精度）
    └── 评估测试-数据源扩展与判决复审.md   验证实证：静态13/13通过但判决改判 ⏸→🧪 + §9 用工性质回查 + §9.5 第二次修正

skills/app-market-research/
├── SKILL.md                        v1.1 主流程 + 输出模板 + 红线 + 双镜自审 + 验证清单
├── references/
│   ├── three-platform-constraints.md   三端七查清单 + 查证入口
│   ├── research-sources.md             数据源 + 检索语句模板
│   └── practitioner-review.md          App/小程序从业者审视八轴实例化（落地镜）
└── examples/
    ├── 家庭药箱小程序-立项调研报告.md    真实产出样例（含明确判决）
    ├── 归期簿-上线前立项调研报告.md      产品已建成场景样例（判决 ⏸ 卡合规 + 首发去 AI 解锁路径）
    ├── 从业者审视-家庭药箱.md           通用八轴审 App 样例（双镜协议跨 Skill 复用实证）
    ├── 剪映节奏与手法迁移助手-立项调研报告-2026-10-26.md  工具类（非三端）case：GATE 自变量换成「交付形态」四选一
    ├── 拉片报告-模板.md                 报告类产品的可交付物骨架（含机器可读 front-matter）
    └── 拉片报告-填写指南.md             同上的 30 分钟手工产出流程（先卖出、后写代码的验证路径）

skills/game-research/
├── SKILL.md                        主流程(8步双镜) + 输出模板 + 红线 + 双镜自审 + 验证清单
├── references/
│   ├── license-compliance.md          版号与合规硬约束清单 + 查证入口
│   ├── monetization-economics.md      买量单位经济(CPI/LTV/分成)取证清单
│   ├── validation-gate.md             验证阶梯 + 平台 KPI 门 + 量化 kill 阈值
│   └── practitioner-review.md         从业者审视八轴（真实世界 survivability 自审尺）
└── examples/
    ├── 喵喵小馆-微信小游戏合成经营立项调研报告.md        v1.0 样例（🧪先验证 + 版号口径标未取证）
    ├── 喵喵小馆-v1.1-立项调研报告.md                   v1.1 完整重报（含 PLAY/PROVE 一等章节）
    ├── v1.0-vs-v1.1-对比分析.md                       同想法两版对峙（暴露方法论窟窿）
    ├── 草木集-Steam出海-cozy炼金经营立项调研报告.md    Steam 出海 case（填 wishlist KPI 门）
    ├── 矿洞物语-出海SEA-挂机放置卡牌RPG立项调研报告.md  移动 F2P 出海 case（填 soft-launch 移动端 KPI 门，关 residual gap #5）
    └── 从业者审视-喵喵小馆与草木集.md                  从业者八轴拷问两份报告（双镜自审实证）

account-research/                  产品项目（形态特例，2026-10-22 从 skills/ 提升；Skill 先行 / Web 放大）
├── README.md                      项目入口：产品定位 + 目录导览 + 路线图
├── AGENTS.md                      项目契约：继承仓库铁律 + 项目特有纪律（零爬虫边界 / 双输出同源 / 台账只追加）
├── skill/                         方法论（整体迁入，内部结构不变）
│   ├── SKILL.md                   v0.2 对标拆解(小红书) + 封闭判决五选一 + 红线(R1隐私/R2零爬虫/R3口径/R4基准/R6相关≠因果) + 双镜自审
│   ├── 立项.md                     形态决策史（v0.3 定盘；真相源在仓库外，冲突时以外部 idea.md 为准）
│   ├── references/
│   │   ├── xiaohongshu-metrics.md      小红书指标字典：口径标注流程(公开页/后台/工具/不可得) + 账号/内容层指标表 + 爆文率定义分歧 + 基准区间取证规则
│   │   ├── data-sourcing-playbook.md   数据获取操作手册：四层来源协议 + L0 手动采集 SOP + 防封节奏 + 自动化边界
│   │   ├── overall-workflow.md         产品级 E2E 流程：P0 委托 → P7 持续追踪 + 人机分工 + 9 道质量闸门
│   │   ├── output-html-template.md     HTML 输出模板：git式占比条 + 账号库页面 + 数据图（暖白纸张底/松绿/陶土 token + DOM工程卫生清单）
│   │   └── practitioner-review.md      从业者审视八轴 + 账号领域 I/J 补充轴实例化（落地镜）
│   ├── examples/                  v0.2 真实 case：数字生命卡兹克-对标拆解报告.md（判决 🧪 先验证）
│   └── 账号库台账.md               只追加不改写
├── src/parser/                    P3 解析器（产品化第一个组件）：本地 HTML → 结构化数据表
├── web/                           副端 PC Web 工具（纯客户端解析；触发条件见其 README，暂占位）
├── tests/                         验收测试（代码入库，fixtures 指向 ../account/ 不入库）
├── docs/                          产品工程文档（路线图 / ADR）
└── data/                          结构化数据输出（gitignore）
```

> **为什么它是特例**：`account-research` 已按产品立项调研判决「Skill 先行、Web 放大」（见 [`docs/product-research-account-research-2026-10-21.md`](docs/product-research-account-research-2026-10-21.md)），从方法论 Skill 成长为一个产品项目——目录结构按「可供扩展的产品项目」组织，方法论收进 `skill/` 子目录。其余 Skill 新增时仍放 `skills/<name>/`；某个 Skill 将来也要产品化时，提升条件与登记方式见 [`AGENTS.md`](AGENTS.md) §1/§3。

**`references/` 里故意不写死政策数字**——政策按季度变，任何写死的数字都会在下一次调研时变成假证据。
只给查证入口，并要求每条结论带「官方链接 + 查证日期」。

---

## 贡献一个新的调研 Skill

三步：

1. 读 [`AGENTS.md`](AGENTS.md)（仓库契约）与 [`docs/SKILL-AUTHORING.md`](docs/SKILL-AUTHORING.md)（方法论）
2. 复制 [`skills/_template/`](skills/_template/SKILL.md) 改名为 `skills/<领域>-research/`
3. **跑一个真实 case**，把产出物放进 `skills/<领域>-research>/examples/`

> 没有真实样例的 Skill 不算完成——模板写得再漂亮，也不如一次真实调研暴露的问题多。

## 经验沉淀

- [`docs/SKILL-AUTHORING.md`](docs/SKILL-AUTHORING.md) —— 怎么写一个合格的调研 Skill（骨架、铁律、检索技巧、失败模式）
- [`docs/dual-mirror-review.md`](docs/dual-mirror-review.md) —— **报告双镜自审通用协议**（grill-method 方法镜 × practitioner-review 落地镜，八轴通用）：所有调研 Skill 交付前的统一自审标准动作
- [`docs/lessons-learned.md`](docs/lessons-learned.md) —— 实证记录：真实调研里踩过的坑，只追加不改写

## 同步（维护者）

`tataCrayon/app-market-research` 是本仓库中该 Skill 的**单技能分发版**。改完本仓库后同步：

```bash
cp -r skills/app-market-research/. /path/to/app-market-research/
cp -r skills/game-research/.     /path/to/game-research/        # 同上，game-research 分发版
```

## License

MIT © tataCrayon
