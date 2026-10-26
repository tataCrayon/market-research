# 数据层：表、校验器与 CLI

> **为什么要有这一层**：报告是**叙述**，表是**记录**。叙述不能被下一条结论引用、对比、计数——「整理得差 / 不能检索」的根因不是排版，是没有可检索单元。
> 本文件是 `skill/` 的配套件，分发单技能时随 `skill/` 一起走。工程实现在 `src/account_research/`。

## 1. 谁是真相源

```
存档 HTML/文本（人采集）
   → accounts / notes 表（确定性解析，可 diff、可重跑）
      → findings / playbook / hypotheses（模型判断，带证据等级）
         → verdicts（封闭判决 + 三件套）
            → markdown 报告 / HTML 可视化（渲染视图，禁止出现表里没有的数字）
```

**表是唯一真相源**；报告与可视化都是渲染。三处数字不一致 = 交付未完成（延续「双输出同源」纪律）。

## 2. 六张表

| 表 | 粒度 | 谁写 | 关键约束 |
|---|---|---|---|
| `accounts` | 一账号一行 | CLI（parse） | 变动值必须带 `collected_on`；同账号多次采集追加新行，不改旧行 |
| `notes` | 一篇笔记一行 | CLI（parse / record / parse-text） | 唯一键 = 账号 + 笔记 + 采集日 + **通道**（主页与详情页是两条独立观测，同一天也不互相覆盖）；列表页拿不到的藏/评/分享**留空**，不填 |
| `fields` | 一指标一行 | CLI（seed） | 口径登记：可不可得 + 等级 + 用途；**不含任何基准数值** |
| `findings` | 一条结论一行 | Agent（`ar add`） | 必带 grade + evidence_ref + applicability |
| `playbook` | 一条可抄动作一行 | Agent（`ar add`） | `failure_condition` 必填，否则 V5 拒收 |
| `hypotheses` | 一条假设一行 | Agent（`ar add`） | 状态封闭 + experiment + due_on 必填 |
| `verdicts` | 一账号一判决一行 | Agent（批准后） | 判决五选一 + 三件套非空 |

> `playbook.supporting_accounts` 是本产品最值钱的字段：**1 个号验证过 = 个案，3 个号验证过 = 路径**。

## 3. 校验器 V1–V8（把硬信念编译成代码）

| 码 | 规则 | 拦的是 |
|---|---|---|
| V1 | 判决 ∈ `✅/🔧/🧪/⏸/❌` 五选一 | 「值得考虑」式收尾 |
| V2 | 三件套（next_step / cheapest_test / abandon_signal）任一为空 → 拒收 | 和稀泥判决 |
| V3 | `notes` 行缺 `source_kind` / `collected_on` / `grade` → WARN 且不计入统计 | 无来源数字 |
| V4 | `findings` 缺等级、等级非法，或 ③ 级却 `used_in_verdict=1` → 拒收 | 二手当结论 |
| V5 | `playbook.failure_condition` 为空 → 拒收 | 「建议全抄」 |
| V6 | 判决里出现涨粉/关注转化断言 → 拒收；假设表状态非法或缺实验/到期日 → 拒收 | 相关当因果 |
| V7 | `notes.account_id` 不在 `accounts` 表 → 拒收 | 归属断裂 |
| V8 | 台账日期倒挂、`verdicts.due_on` 早于 `decided_on` → 拒收 | 改写历史行 |

**纪律**：`validate` 不是 0 ERROR 就不算交付。这不是建议，是出口条件。

## 4. CLI（确定性部分，零平台请求）

```bash
python account-research/ar.py probe      <存档...>          # 判页面类型与归属（3 秒级，先于一切解析）
python account-research/ar.py parse      <存档...> --on 2026-10-22   # 主页存档 → accounts / notes
python account-research/ar.py record     --file detail.json --on 2026-10-22   # 详情页人工补数（藏/评/时长）
python account-research/ar.py parse-text --file paste.txt --on 2026-10-22     # 书签小工具的结构化块
python account-research/ar.py validate                     # V1–V8
python account-research/ar.py stats      [--account <id>] [--k 3,5]  # 篇均/中位/形式/爆文率/⬜ 覆盖度
python account-research/ar.py add        <表> --file rows.json       # 唯一写入口，先校验后落盘
python account-research/ar.py selftest                     # 对真实存档跑回归（无存档则 SKIP）
```

**两条通道怎么合并**：`notes` 表里同一篇可能有两行（主页一行、详情一行）。读取时按
**（采集日, 通道优先级）** 升序做**字段级合并**——详情页补上的藏/评/时长不会覆盖主页才有的
标题与形式，空值也永不覆盖已有值。`stats` 用的就是合并后的视图。

**详情页通道的两个入口**：`record`（JSON，手工填）与 `parse-text`（书签小工具
[`../../tools/capture-bookmarklet.js`](../../tools/capture-bookmarklet.js) 产出的 `===AR-NOTE-v1===` 块）。
两者都要求 `note_id` 已在 `notes` 表里——**详情数字不能凭空造，也不能顺手改掉列表页的标题/时间**。

## 5. 页面类型判别（为什么先 probe）

| 类型 | 判据（均有真实存档实证） | 处置 |
|---|---|---|
| `profile` | `user.userPageData.basicInfo.nickname` 非空 | ✅ 可解析 |
| `note_detail` | `note.noteDetailMap` 非空 | ⏭ v1 不解析（CSR 存下来是空壳，改用 Ctrl+A 复制文本） |
| `search_page` | `<title>` 含「搜索」 | ⏭ 内容是他人笔记 |
| `home_feed` | `feed.feeds` 非空 | ⏭ 推荐流，非账号主页 |
| `empty_skeleton` | 三者皆空 | ⏭ 页面没渲染出来 |

**文件名不是证据**：三份命名为「详情页」的存档实际都是主页（`_无效存档/README.md`）。判据只看内容。

## 6. 归属校验（v0.2 踩过的坑，永久固化）

- 目标 id 来源：`noteQueries[].userId` ∪ `noteCard.user.userId`
- 采集者 id：`user.userInfo.userId` —— **这是采集者本人，不是目标**（v0.2 曾把它的简介、小红书号当成目标账号的）
- 规则：卡片作者 ≠ 目标 → 丢弃并留证据；目标 id 与采集者 id 相同 → 提示「采集者即目标，『对标』语义不成立」
