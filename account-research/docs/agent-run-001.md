# agent-run-001：账号拆解分析师首次真实运行

> **这份文档是什么**：契约 [`../agent/account-analyst.md`](../agent/account-analyst.md) §6 的「评估证据链接」——首次由 Agent 自主完成的四表落库记录。
> 不是拆解报告。结论在 [`../skill/examples/数字生命卡兹克-对标拆解报告.md`](../skill/examples/数字生命卡兹克-对标拆解报告.md)（v0.3，判决 🔧）。
> 运行日期：2026-10-26｜账号：数字生命卡兹克（`62c98736000000001501e075`）｜执行者：account-analyst（DSH 宿主）

---

## 1. 结论摘要

| 表 | 落库行数 | 关键特征 |
|---|---|---|
| `verdicts` | 1 | 🔧 部分对标；三件套 + 双镜齐全；decided_on 2026-10-26 → due_on 2026-11-02 |
| `findings` | 9 | 四层覆盖（定位 2 / 选题 3 / 结构 3 / 变现 2）；② ×5、③-a ×2、⬜ ×2；used_in_verdict=1 共 4 条 |
| `playbook` | 5 | 每条 failure_condition 非空（80–97 字）；supporting_accounts 全填 1 |
| `hypotheses` | 1 | 「涨粉 / 关注转化」类判断全部拦在假设表；status=未验证，due_on 2026-11-02 |

**最终校验：`0 ERROR / 0 WARN`。**

与 v0.3 报告的一致性：判决同为 🔧 部分对标；三条限定（抄骨架不抄选题 / 抄内容不抄变现 / 天花板取决于一手信源能力）分别落到 verdicts.reason、A-KZK-01/02、F-KZK-04/08/09 与 abandon_signal。

---

## 2. 做了什么（按契约 §5 启动顺序）

1. 读契约 `agent/account-analyst.md`（目的 / 七条禁止 / 自治上限 / 停止条件）。
2. 读方法论 `skill/SKILL.md`（九步、五选一、三件套、红线 R1–R6）。
3. 读已有拆解报告 v0.3（判决 🔧，本 Agent 的结论必须与之一致）。
4. 读过程账 `case-001-卡兹克-执行过程复盘.md`（已知摩擦 F1–F5，避免重复：本次不做任何采集，只用已入库数据）。
5. 读 `data/` 三张表（accounts / notes / fields）+ 读 `src/` 的 `schema.py`、`validate.py`、`store.py`、`cli.py`，把 V1–V8 的真实判据抄清楚后再动笔。
6. 判定样本量：`notes.csv` 32 行 = 置顶 2 + 非置顶 30，`ar stats` 也报「样本 30 篇」，与报告口径一致。

**未做**（越界即违约）：零网络访问、零平台域名、零采集代码；未改 `skill/` `src/` `docs/` 既有文件；未手改任何 CSV。

---

## 3. 跑了哪些命令

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python account-research/ar.py stats` | 样本 30 篇；篇均 703.9 / 中位 366 / 最高 4565 / 最低 107；图文 27（篇均 751.4）、视频 3（篇均 276.7）；爆文率 K=3 → 16.7%（阈值 1,098）、K=5 → 10.0%（阈值 1,830）；⬜ 覆盖度 收藏 0/30、评论 0/30、分享 0/30 |
| 2 | `python account-research/ar.py validate`（基线） | 0 ERROR / 0 WARN |
| 3 | `python account-research/data/tmp/precheck.py` | 4 张表候选行预检 → 0 问题（见 §4） |
| 4 | `ar add findings --file ...` | 新增 9 行 / 跳过 0 |
| 5 | `ar add playbook --file ...` | 新增 5 行 / 跳过 0 |
| 6 | `ar add hypotheses --file ...` | 新增 1 行 / 跳过 0 |
| 7 | `ar add verdicts --file ...` | 新增 1 行 / 跳过 0 |
| 8 | `python account-research/ar.py validate`（终局） | **0 ERROR / 0 WARN** |

临时 JSON 与预检脚本均在 `data/tmp/`（`verdicts.json` / `findings.json` / `playbook.json` / `hypotheses.json` / `precheck.py`），UTF-8 写入。

---

## 4. 拒收（V 码）记录：0 次，原因说明

**本次没有任何 `ar add` 被 V 码拒收。** 这不是运气，而是**先读了 `validate.py` 再写数据**的必然结果，外加一个针对性设计：

> `ar add` 的实现是**先写 CSV、后跑校验**（`cli.py: cmd_add` → `store.append_rows` → `validate_dir`）。一旦被拒收，脏行已经落盘，而本任务的硬约束是「禁改 CSV」——**这意味着一次拒收就等于不可逆污染**。

因此落库前先写了 `data/tmp/precheck.py`：直接 import `validate.py` 里的 `check_verdicts` / `check_findings` / `check_playbook` / `check_hypotheses` 四个函数，对候选 JSON 跑同一套判据，另外补了三项任务级硬约束（findings 的 layer / evidence_ref / sampled_on / sample_basis / applicability 非空、statement 全域不含「涨粉」「关注转化」、四层是否各有 ≥1 条）。预检 0 问题后才写库。

**逐条风险点与规避动作**（按 V 码归口）：

| V 码 | 判据 | 本次规避动作 |
|---|---|---|
| V1 | verdict 必须在五选一内 | 用枚举原值 `🔧 部分对标`（含 emoji 与空格），未写「部分对标」等简写 |
| V2 | 三件套三列非空 | next_step / cheapest_test / abandon_signal 均写满，且都带时间预算与可证伪条件 |
| V4 | grade ∈ ①②③-a③-b⬜；③ 级不得 used_in_verdict=1 | 2 条 ③-a（F-KZK-04 / F-KZK-05）全部 used=0；⬜ 级两条里 F-KZK-08 置 1（校验器允许，语义上正是「变现不可复制」这条判决依据） |
| V5 | playbook.failure_condition 非空 | 5 条全部写了失效条件，且都带「什么情况下这条不该抄」 |
| V6 | 涨粉类断言只进 hypotheses | findings 的 9 条 statement 全域零命中；「涨粉 / 关注转化」只出现在 H-KZK-01 |
| V8 | due_on ≥ decided_on | 2026-10-26 → 2026-11-02（7 天，符合三件套默认窗口） |

**一个已知的工程隐患（建议回写，不由本 Agent 改代码）**：`ar add` 的「先写后校验」顺序让拒收不可回滚。建议改成「先校验候选行、再落盘」，或在 `append_rows` 前调用一次 `check_*`。这属于 `src/` 变更，按契约 §4 只能建议。

---

## 5. 最终 validate 输出（原文）

```
$ python account-research/ar.py validate

校验结果：0 ERROR / 0 WARN
```

`ar add` 内嵌校验的各次输出同样为「无 ERROR/WARN 行」，与终局一致（四表逐次追加后均即时复验）。

---

## 6. 自我批评（缺证据的一处）

**选题层最关键的结论——「选题公共性决定量级」——只有 ③-a 级证据（F-KZK-04），且只建立在同一账号的 2 个数据点（4,566 与 110）上。** 更严重的是：近期篇只有 110 赞，**发布仅约 23 小时**，赞数仍在爬升期；用它跟一篇已满 18 天的爆款比「41 倍量级差」，时间窗本身不对齐。报告 §1.2b 如实记了「发布后约 23h」，但判决的三件套与 F-KZK-06 的「结构差异不成立」都在引用这个差距。

按纪律这条只能停在 ③-a、不能进判决——本次已按此处理（used_in_verdict=0），但它同时也是 verdicts.next_step 的核心依据，**存在「判决动作依赖一条未入判决的证据」的内部张力**。补证路径已写进 H-KZK-01 的 experiment（同日双稿对照，48h 看评论率），到期 2026-11-02；届时若公共事件那条评论率不显著更高，须连带推翻报告 §4.2 并重做对标。

第二处（较轻）：`findings` 里 27 篇的收藏 / 评论仍为 ⬜（`ar stats` 覆盖度 0/30），互动结构结论全部压在 3 篇深拆上，样本量对「账号级常量」这类判断偏小。

---

## 7. 给 owner 的两条建议（只建议，未执行）

1. `src/`：把 `ar add` 改为「先校验、后落盘」，消除「拒收即脏数据」的不可逆窗口（见 §4 末）。
2. `docs/` 决策页：评估项 1（parse 32 篇 / 爆文率 16.7% 与 10.0%）与评估项 3、4、5 已在本文件与 §4 有真实证据；评估项 2 仍由 `ar selftest` 覆盖。契约 §7 的「上线门」可以考虑把本文件登记为证据链接之一，但门禁结论由 owner 定。
