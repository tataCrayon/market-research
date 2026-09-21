# 游戏立项验证门（Validation Gate）取证清单

> 本文件是 `game-research` 的「验证阶梯」与「平台 KPI 门」底表。立项阶段只给查证入口 + 方向性基准 + 来源日期，**禁止把基准当写死数字**——留存 / ARPDAU / wishlist 门槛随年份与市场漂移，每次调研必须现场重取。
> 证据等级：②专业（行业报告 / 平台官方 / 上市公司实践）；①权威（Steam / 微信 / NPPA 官方文档）。
> 核心思想（来自 Supercell / Zynga / Schell / Zukowski 实战）：**验证从最便宜的开始，先证伪需求再花钱建。**

---

## 1. 验证阶梯（最便宜先上，禁止跳级烧钱）

| 层 | 动作 | 成本 | 验证哪条主张 | 出处（②专业） |
|---|---|---|---|---|
| **L0 概念测试** | fake-door（在现有产品 / 落地页放「即将上线」按钮测点击率）；concept-ad（5 词卖点 + 素材小预算投 CTR）；社区 storyboard | ¥0–几千，无需建 | 吸量 / 需求 | Zynga 5 词 pitch + 广告测点击（GDC 实践）；GameAnalytics painted-door |
| **L1 可玩切片** | paper / 极简可玩原型，去掉目标数值，纯核心循环；Schell「build-a-toy」镜——没目标也让人想玩？5–10 人 playtest | ¥0，≤1 周 | 玩法成立 | Schell《The Art of Game Design》Lens 15 |
| **L2 封闭 Demo** | 核心循环 + 留存埋点，测 D1 / D7 | ≤2 周，小预算 | 留存 | 行业通用 |
| **L3 小流量 / soft-launch** | 见下表按战场 | 见战场 | 单位经济 + 规模 | Supercell soft-launch 协议 |

**铁律**：L0 / L1 未过时不得跳到 L2 / L3 烧钱。很多人「想法」阶段连 L1 都没做就立项，是最高频的浪费源——版号能过、CPI 能回本，但核心循环根本不好玩，全白搭。

---

## 2. 平台 KPI 门（L3，按 GATE 战场映射）

### 2a. 微信 / 抖音小游戏（IAA / IAP）
- 自查版 IAA Demo + 小预算买量实测 **CPI / D1 / D7 / eCPM**，反推 LTV。
- **有效 CPI = 展示 CPI ÷ D1 留存**（D1 低则有效 CPI 暴涨，这是小游戏最常见的自欺：只看展示 CPI 不看留存）。
- kill 阈值（方向性）：D1 < 30% 立即停 UA 修核心循环；D7 < 品类基准（休闲约 15–25%）→ 留存不过关。

### 2b. 出海 / iOS / 安卓 App（F2P soft-launch）
- soft-launch 市场：CA / AU / NZ（英语行为 proxy，CPI 低）；PH（低 CPI 走量）；Nordics（高 ARPU）。**避开 US / UK / JP / DE（CPI 太高，小规模取不到统计信度）**。
- 品类基准（② 2026，方向性，每次重取）：

| 指标 | 休闲 | 中重度 | 低于则动作 |
|---|---|---|---|
| D1 留存 | 40%+ | 35%+ | <30% 停 UA 修核心循环 |
| D7 留存 | 20%+ | 15%+ | 查 session 深度 |
| D30 留存 | 10%+ | 8%+ | 查 progression 系统 |
| ARPDAU | $0.05–0.15 | $0.10–0.30 | 重估变现事件 |

- 升级全球发布条件：三留存达标 + 变现信号验证 + LiveOps 压测 + 本地化完成。
- 预算（②）：soft-launch $10k–30k / 月 × 8–12 周，总 $50k–120k——**这是产品验证费，不是营销费**。

### 2c. Steam / 独立（wishlist 验证）
- 建 Steam 页 + Next Fest Demo，攒 wishlist。**7,000 = 发布地板**（Zukowski 2026 基准；档位 Bronze 5k / Silver 8k / Gold 50k / Diamond 90k）。
- 首周转化 ~12–25%（中位 ~12%）；**收入 ≈ wishlist × $5**（Boxleiter，genre $4–8）。
- 算法看「发布日转化速度」：1k/5k 转化(20%) > 500/50k 转化(1%)——wishlist 数量 alone 不够，发布日转化速度才触发推荐。
- kill 阈值：WL < 5k 且 demo 转化弱 → 重做卖点 / 核心，而非硬上。

---

## 3. 玩法成立（设计验证，Schell 镜）

- **Lens 15 The Toy**：没目标 / 数值也让人想玩？否 → 核心循环未成立，先改。
- **Lens 18 Flow / Lens 91 Playtesting**：难度曲线、反馈是否到位。
- **Rule of Loop**：测—改循环越多越好；原型只为回答一个问题（核心好玩吗？），其余可 paper。
- 立项阶段用 L1（可玩切片 + 5–10 人 playtest）证「玩法成立」主张，**不可跳过**——这是与版号、买量并列的第三道独立筛子。

---

## 4. 量化 kill 文化（决策纪律）

- Supercell：近 10 款 7 款死于原型、2 款死于 soft-launch、1 款全球发布（~90% 砍掉率）。**砍是生产环节，不是失败**；沉没成本不该拖住决策。
- 行业硬阈值：D1 < 30% 停 UA；soft-launch 三留存未达品类基准 → 迭代或砍；Steam WL < 5k 且弱转化 → 重做。
- 决策由小团队基于数据驱动（Supercell 团队自决），但**数据不过线就砍**；「感觉该做另一个游戏了」本身也是有效信号。

---

## 5. 取证入口（每次调研现场重取）

- 留存 / ARPDAU 基准：DataEye、广大大、GameAnalytics、Deconstructor of Fun、appalize。
- Steam wishlist：howtomarketagame.com（Zukowski）、SteamDB、GameDiscoverCo。
- soft-launch 市场选择：上述 Go-to-Market 指南（GameGrowthAdvisor / galaxy4games）。
- 设计镜：Schell《The Art of Game Design》（100 lenses）。

> 所有数字为方向性基准，标注来源与日期；本 Skill 判决不得依赖未标注来源的基准。基准会过期，复用旧结论视为违反红线。
