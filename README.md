# market-research

**一组调研 Skill 的合集**——教 AI 把「说不清的问题」变成**有证据、有判决**的结论。

> 调研的价值不在信息量，在**判决**。
> 没有规则约束时，AI 会给你 20 条链接和一句「建议进一步调研」；有了这套 Skill，它必须落到
> **做 / 改方向 / 先验证 / 卡合规 / 放弃** 里的一个，并附上最便宜的验证和放弃信号。

---

## 现有 Skill

| Skill | 覆盖领域 | 心脏（该领域最容易被偷工、又最决定成败的一步） |
|---|---|---|
| [`app-market-research`](skills/app-market-research/SKILL.md) | App / 小程序立项：市场 → 竞品 → 三端合规 → 做不做 | **三端硬约束七查** + **双镜自审**（v1.1 接入通用协议）：上架门槛 / 审核红线 / 资质备案 / 能力边界 / 付费分成 / 分发获客 / 成本周期 |
| [`game-research`](skills/game-research/SKILL.md) | 游戏 / 小游戏立项：市场 → 竞品 → 版号合规 → 买量单位经济 → 玩法成立 → 验证阶梯 → 做不做 | **三道独立筛子（版号 × 买量 × 玩法）+ 双镜自审**：版号决定能不能上线，LTV−CPI 决定能不能回本，玩法成立决定有没有人玩 |

规划中（欢迎按模板贡献）：`career-research`（职业发展路径）。

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
skills/app-market-research/
├── SKILL.md                        v1.1 主流程 + 输出模板 + 红线 + 双镜自审 + 验证清单
├── references/
│   ├── three-platform-constraints.md   三端七查清单 + 查证入口
│   ├── research-sources.md             数据源 + 检索语句模板
│   └── practitioner-review.md          App/小程序从业者审视八轴实例化（落地镜）
└── examples/
    ├── 家庭药箱小程序-立项调研报告.md    真实产出样例（含明确判决）
    └── 从业者审视-家庭药箱.md           通用八轴审 App 样例（双镜协议跨 Skill 复用实证）

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
```

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
