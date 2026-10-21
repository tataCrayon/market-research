# 进度交接：account-research（2026-10-22 00:30 存档）

> **用途**：跨机器接续。另一台电脑 `git pull` 后读本文件即可恢复全部上下文。
> **真相源**：本仓库 `tataCrayon/market-research`（main 分支）。本地 `.workbuddy/memory/` 被 gitignore，不随仓库同步——关键信息已誊入本文件。

---

## 一、当前状态（一句话）

`account-research` **v0.2.0 首个真实 case 已跑通**：数字生命卡兹克（小红书 AI 资讯类头部，14.4 万粉），判决 **🧪 先验证**。方法论的纸面自洽已被真实数据检验，剩下的是补齐动作，不是方向问题。

## 二、本 case 的关键结论（别重新推）

1. **采集方式验证成立**：用户保存的主页 HTML 内嵌 `__INITIAL_STATE__`，32 篇五字段（标题/发布时间/赞/形式/置顶）完整可解析。「保存网页 HTML」作为默认采集方式的前提在真实数据上成立。
2. **两个解析坑**（写解析器前必读）：
   - `__INITIAL_STATE__` 是 JS 字面量非严格 JSON，`undefined` 值需清洗为 `null` 才能 `json.loads`
   - 存档混入**采集者本人会话数据**（登录态/浏览历史）——曾把 viewer 简介误当目标简介。必须按 target userId/nickname 做归属校验，不匹配即丢弃
3. **列表页 HTML 只给点赞**：单篇收藏/评论/分享仅详情页有 → 深拆 3 篇的详情页存档不是可选项，是赞藏比与结构卡的前提
4. **⬜ 密度**：关键层（定位/选题）0 阻塞；结构层/变现层/评论区因详情页未采集整层 ⬜
5. **母题效率差**：C 母题（Agent/Skill 新形态）17% 篇数贡献 33% 爆文、篇均 1,160 最高；A 母题（快讯情绪）30% 篇数是基本盘但最重产能；视频非其引擎（篇均 274 vs 图文 725）
6. **爆文率口径坑实测**：K=3 → 16.7%，K=5 → 10.0%（双值并列，判决不依赖）

## 三、待办（按优先级）

| # | 动作 | 负责 | 完成判据 |
|---|---|---|---|
| 1 | 用户补存 **3 篇详情页 HTML**（爆款 4,562「对不起，AI这个速度…」+ 中位 373「实测好用的10个prompt」+ 近期 10-21「爆肝两天，我给AI造了座赛博工厂！」） | 用户（约 3 分钟） | 回传后我补 §4 结构卡 / §5 变现图 / 单篇赞藏比，判决可升 🔧 |
| 2 | **动态重跑**（v0.2 毕业三条件之三，唯一未做）：换数据源撞结论——如让用户改用手抄/粘贴一份 10 篇子样本，或隔周重存主页看指标漂移 | AI | 结论不变或变化可解释 |
| 3 | **HTML 可视化**（用户明确指令后置，勿提前做）：占比条 + 账号库页面 + 数据图，模板见 `skills/account-research/references/output-html-template.md`，必须与 md 报告同源 | AI | 双输出数字零不一致 |
| 4 | **解析器 v1**（产品化第一个组件，overall-workflow.md 已定义边界）：只处理本地 HTML，禁访问平台域名 | AI | 对本次 account/ 存档跑通，产出与报告 1.2 节一致 |
| 5 | v0.3 诊断模式（心脏 B 漏斗排除，依赖专业号后台导出——需求侧未验证，勿提前开发） | — | 有真实用户带后台数据求助再启动 |

**升级/放弃硬线**：7 天内补不到详情页 → 降级 ⏸ 并按 playbook §4 评估 L2；按 C 母题发 5 篇仍低于中位数 364 → 停止对标 C。

## 四、本次未入库的本地资产

- `account/`（11MB，已 gitignore）：数字生命卡兹克主页 HTML + 59 个配套文件。**含采集者会话数据，禁入公开仓**。换机器后需用户重新保存（SOP：主页滚 2-3 屏各存 1 次 + 深拆 3 篇详情页）
- 本地 `.workbuddy/memory/2026-10-21.md`、`MEMORY.md`：项目记忆，关键内容已誊入本文件

## 五、环境备忘（另一台电脑大概率不同，先探测再推）

push 代理态势是动态的：先 socket 探测 `127.0.0.1:7897`——开着 → 保留 `.gitconfig` URL 专属代理（只清沙箱 env 代理）；关着 → 四件套全清直连（`-c http.sslBackend=schannel -c http.proxy= -c https.proxy= -c http.https://github.com.proxy=`）。提交信息含中文/反引号时用 Python subprocess 传，勿内联 bash。

## 六、下次开局建议（给另一台电脑的第一条指令）

> 「读 docs/handoff-account-research-2026-10-22.md，然后按待办 #1 等我发详情页」或「直接开始待办 #4 写解析器」。

## 七、本仓库约定速查

- 提交信息：`<type>: <中文简述>`，type ∈ feat/fix/docs/chore/skill；每次实质修改 Skill 须往 `docs/lessons-learned.md` 追加一条
- 契约入口 `AGENTS.md`；分发版 `tataCrayon/app-market-research`（单技能仓，同步方式 = 克隆后 `cp -r skills/app-market-research/. <clone>/`；注意 `../../docs/dual-mirror-review.md` 相对链接在单技能仓失效，待处理）
- account-research 文件地图：`SKILL.md`（九步+红线+输出格式）/ `立项.md`（形态决策史）/ `references/`（metrics 字典、data-sourcing-playbook 采集协议、overall-workflow E2E、output-html-template）/ `examples/`（本 case 报告）/ `账号库台账.md`（只追加）
