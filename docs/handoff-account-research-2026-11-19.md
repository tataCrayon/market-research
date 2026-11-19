# 进度交接：account-research（2026-11-19 15:30 存档）

> **用途**：换工具 / 换机器接续。新会话先读这一份，再决定动哪里。
> **真相源**：本仓库 `tataCrayon/market-research` main 分支。
> **上一份交接**：[`handoff-account-research-2026-10-22.md`](handoff-account-research-2026-10-22.md)（v0.2.0 时期，已大量过时，只作历史）。

---

## 一、当前状态（一句话）

`account-research` 的**采集闭环已经打通**：扩展一次点击 → 主页 + 详情页直接入库，
不需要终端、不需要下载目录。工程侧 **590 例单测全绿 / `ar validate` 0 ERROR 0 WARN /
`ar selftest` 15/15**。剩下的是**补数据**与**第三个账号**，不是功能缺口。

| 项 | 值 |
|---|---|
| 分支 | `main`，**本地领先 origin/main 且未 push**（差多少现查：`git rev-list --count origin/main..HEAD`） |
| 最新提交 | 现查 `git log --oneline -1`——**这里不写死**，写死的提交号下一次提交就过期 |
| 工作区 | 干净（`git status` 无改动） |
| 单测 | `cd account-research && python -m pytest -q` → **590 passed** |
| 扩展 | `extension/` **v8.1.0**，扩展 ID `onabfkpgiapeffmjogkdnhllamejdehm` |
| 宿主 | 已注册（`.host/com.account_research.capture.json`），协议 **v2**，`ar host --doctor` 全绿 |
| Skill 版本 | `skill/SKILL.md` frontmatter `version: 0.3.23` |

---

## 二、这一轮（11-18 / 11-19）改了什么——别重新推导

两次真机失败各牵出一层问题，已全部修完并提交：

### 提交 `cd31f0a` —— 采集端 v8（三环死锁）

用户点采集，面板显示 `入库✗ 宿主没回结果`。真相是**三件事叠在一起**：

| 层 | 真实发生 |
|---|---|
| 归属校验 | 那篇笔记的作者不在 `accounts` 里 → 被当「外来作者」拒收 |
| `host.py` | `ingest` 非 0 退出，但响应里**没有 `error` 字段** |
| 扩展 | `reply.error` 为空 → 显示「宿主没回结果」 |

而作者不在 `accounts`，是因为**扩展采不了主页**（`pageCapture` 剥掉 `<script>`，
主页数据全在 `__INITIAL_STATE__`）。环是：详情页要作者 → 作者要从主页来 → 主页采不了。

三处一起改：

1. **归属默认收**：`--no-new-authors` 成了显式开关（默认建占位账号）。
   `accounts` 是目标清单，**不是准入白名单**；归属真凭据是详情页作者链接。
2. **失败必带原因**：`host.ingest_payload` 从 `ar ingest` 输出里按 ❌ > ⏭ > ⚠️
   挑结论那一句作为 `error`。
3. **state 隧道**：扩展摘 `__INITIAL_STATE__` 的 `user`/`note`/`feed`/`search` 子树
   → 随 `begin` 上传 → 宿主落 `<stem>.state.json` 旁车 → `open_archive` 优先读。
   协议升 v2（v1 宿主会静默忽略 `state`）。

顺带修：MHTML 全拆包失败时 `ingest` 返回 0（「什么都没做却报成功」）。

### 提交 `8ff03f1` —— 采集端 v8.1（DOM 腿）

紧接着真机又失败：主页**明明完整渲染在屏幕上**，面板说「主页还没渲染出账号信息」。

判据取错了：v8 用 `state.user.userPageData.basicInfo.nickname` 判「渲染好没好」，
而**小红书是 CSR**——首屏 state 里那棵树是空的，账号头部是挂载后由接口填进 DOM 的，
所以那个门槛在真机上永远过不了。

修法两条：

1. 扩展改看 `.user-name` 在不在 DOM 里；`hasState` 降级成「发布日/置顶能不能补」。
2. `open_archive` 加第三档 `profile_dom`：只靠 DOM 解析主页。

DOM 腿实测能拿：昵称 / 小红书号 / IP / 简介 / 粉丝 / 关注 / 获赞与收藏 /
全部卡片（id、标题、赞、形式）。**拿不到：发布日、置顶**——
页面上是「3 天前」这种相对时间；置顶卡在 DOM 里与普通卡一模一样
（32 张逐张比对，`pin`/`top`/`置顶`/`sticky` 一个标记都没有）。

所以这两列写 **⬜ 而不是猜**。`pinned` 写 `""` 不写 `"0"`：前者是「不知道」，
后者是「确认不是置顶」——写成 0 会让「置顶 2 / 样本 30」静默变成「置顶 0 / 样本 32」。

### 连带产出的文档（读这三份就能恢复上下文）

| 文档 | 内容 |
|---|---|
| [`../account-research/docs/采集端-v8-三环死锁-20261118.md`](../account-research/docs/采集端-v8-三环死锁-20261118.md) | 死锁的完整推理 + 离线复刻验证 |
| [`../account-research/docs/采集端-v8.1-DOM腿-20261119.md`](../account-research/docs/采集端-v8.1-DOM腿-20261119.md) | 判据取错、两条腿逐字段对照表、为什么置顶真的取不到 |
| [`lessons-learned.md`](lessons-learned.md) E77–E84 | 八条可移植教训（兜底文案会撒谎 / 准入名单是自锁装置 / 通道性质不能外推 / 缺两列≠数据不可用 / 存档内部可能自相矛盾 …） |

---

## 三、库的现状（`ar status` 实测，2026-11-19）

```
账号 3 个 | 笔记 68 篇（原始 97 行，含多渠道观测）
⚠️ 1 个账号只有详情页、缺主页存档（粉丝/简介 ⬜）：FuzzyPop
有正文归档 13 篇 | 有图上文字 12 篇 | 藏/评齐全 12 篇 | 缺藏/评 56 篇 | 缺正文 55 篇
→ 实际还要打开 56 篇详情页
校验：0 ERROR / 0 WARN ✅
```

表行数：`accounts` 3 / `notes` 97 / `findings` 32 / `playbook` 17 / `hypotheses` 9 / `verdicts` 3。

三个账号：数字生命卡兹克（14.4 万粉，20 条 findings）、知新的AI笔记（1.68 万粉，12 条）、
FuzzyPop（**占位号**，只有详情页，粉丝/简介 ⬜）。

---

## 四、待办（按优先级，都不需要重新设计）

### P0 · 6 条纪律告警（`ar status` 会喊，已逾期）

| 码 | 对象 | 逾期 | 动作 |
|---|---|---|---|
| D1 | `62c98736000000001501e075`（卡兹克）2026-11-01 判「🔧 部分对标」 | 13 天 | 二判或降级 |
| D1 | `686011fb000000001b023a74`（知新）2026-11-04 判「🔧 部分对标」 | 8 天 | 同上 |
| D2 | `H-KZK-02 / 03 / 04` | 6 天 | `ar close hypothesis H-KZK-0X --status 超期未验` |
| D3 | 台账最后一条 2026-11-04 | 15 天 | 补一次采集，或在报告里明写「这段时间没追」 |

**这是最便宜的一步**：D2 三条各一条命令。但注意——`超期未验` 不是「证伪」，
是「没做实验」。写进报告时要这么说。

### P1 · 补 56 篇缺藏/评

`ar links --limit 5 --out account-research/data/todo-detail.md` 拿清单 →
中键连开几篇（图片栏滑到底）→ 点扩展图标 → 自动入库。
**现在这一步已经不需要终端了**，这是本轮改动最大的实际收益。

### P2 · 第三个账号（决定产品形态的分水岭）

> 这不是「再采一个号」，是**验证 PRD §9 的放弃信号 #1**：
> 「3 个账号填完，同一字段填齐率 <70% → 退回单账号报告形态」。

选号硬条件（已给过用户，未变）：

- 图文占比 ≥80%（视频内容不可恢复——MHTML 只有封面）
- 近 90 天 ≥20 篇，其中 ≥10 篇正文可读 500+ 字
- 粉丝 1.5 万 – 15 万
- 不是纯搬运号
- **优先 AI 领域但能看到 ≥2 类真实变现路径**（橱窗/课程/商单痕迹/商务承接）——
  因为变现层现有 6 条 findings 全是「无证据」（`F-KZK-08/09/31/32`）

**采之前必须先预注册一条假设**（防选择性偏差），`due_on` = 采集日 + 7 天。

### P3 · 已知但本轮没做

| 缺口 | 说明 |
|---|---|
| **搜索页采集** | `search.feeds` 与 `feed.feeds` 在存档里都是空的，结果只在 DOM。需要新写 DOM 解析器，且产出必须是**候选账号清单**而不是往 `notes` 写行——平台排过序的样本会把 `ar stats` 的中位数污染成「平台推荐位的中位数」。实测那份搜索页：30 卡片 / 29 作者 / 赞数 600·648·33·71·15·407·22·557，**排第一的不是赞最高的** |
| **存档内 state 与 DOM 打架** | 调试时发现那份真实主页存档里，`__INITIAL_STATE__` 的笔记 id 与 DOM 卡片 id **交集为 0**（`data-index` 从 259 起，三份滚动位快照分别是 0–31/10–41/11–42）。成因未查清（滚动加载的旧 state？Ctrl+S 与 state 的时间差？）。**没搞清就不写成校验器**——那时会把正常情况判成错。目前靠「整份存档只走一条腿」规避 |
| 知新 12 条 findings 编号错标 | 标成 `F-KZK-21..32`（KZK 是卡兹克的缩写） |
| `notes.topic` 只填了 46/97 | 全部是知新的；卡兹克 50 行空白 |
| `web/` | 仍是占位，触发条件见其 README |
| `agent/account-analyst.md` | 仍是草稿（缺「备份联系人」一节） |

---

## 五、本地未入库资产（换机器会丢）

| 路径 | 体量 | 说明 |
|---|---|---|
| `account/` | **222 MB / 2038 文件** | 采集原始存档，**含采集者会话数据，禁入公开仓**。换机器要么整目录拷走，要么按 SOP 重采 |
| `account-research/data/` | 188 KB / 23 文件 | 七张 CSV + 各种 `todo-*.md`。**拷走这 23 个文件就能接续全部结论** |
| `account-research/.host/` | 小 | 宿主注册产生，含本机 Python 绝对路径 → **必须在新机重跑 `ar host --register`** |
| `.tmp/` | — | 一次性调试脚本，已在 gitignore，不用管 |

---

## 六、环境与操作备忘

```powershell
# 全部命令都在 account-research/ 下跑
cd D:\Projects\MyProjects\market-research\account-research

python -m pytest -q            # 590 例
python ar.py status            # 进度 + 告警 + 下一步
python ar.py validate          # V1–V8
python ar.py selftest          # 真实存档回归（无 account/ 会 SKIP）
python ar.py host --doctor     # 宿主全链路体检（含真握手）
python ar.py extension         # 扩展与宿主的安装状态
```

**换机器 / 换工具后必须做一次**：

1. `python ar.py host --register`（重写注册表项，路径变了）
2. `chrome://extensions` → 开发者模式 → 加载 `account-research/extension`
3. `python ar.py host --doctor` 确认握手协议 **v2**

**提交纪律**（本仓库硬门禁）：消息必须以 `AI: ` 开头，正文只写功能变动，
禁对话口吻。校验：`python ~/.agents/git-hooks/check_commit_msg.py --text <msg>`。
**不得用 `--no-verify`**。

**编辑纪律**：UTF-8 文件不要走 shell 文本往返；`data-layer.md` 上 `edit` 工具会报
`ReplaceFileW EIO`，用显式 `encoding="utf-8", newline=""` 的 Python 脚本改。
`write` 也会在个别文件上撞同一个错，重试一次或用 Python 脚本兜底。

**一条会「偶发」红的测试**：`tests/test_watch.py::test_detail_page_goes_all_the_way_through`
跑的是完整详情页入库，**会真的加载 local OCR 引擎**（rapidocr-onnxruntime）。
机器内存紧张时它会在最后一步打印
`OpenBLAS error: Memory allocation still failed after 10 retries` 然后退出非 0，
于是那条断言「本次自动入库 1 个文件」失败——**它测的逻辑已经过了**（详情页解析、写表都成功了），
失败发生在之后的 OCR 环节。

判断方法：看输出里有没有 `✅ …详情页 note_id=…` 和 `notes：新增 1 行`。
有 → 是环境内存问题，不是回归；紧接着重跑一次通常就绿。
**不要为了让它过而把断言改松**——那条断言守的正是「详情页能走完整条路」。
（2026-11-19 实测出现过一次，重跑即过，590 全绿。）

---

## 七、下一句该说什么（给自己/新工具的第一条指令）

> 「读 `docs/handoff-account-research-2026-11-19.md`，先跑 §七 的自查清单，
> 然后把 D2 那三条假设结案。之后按 P2 帮我选第三个账号。」

如果要继续做功能而不是补数据，优先级是**搜索页采集**（P3 第一项）——
它是目前唯一还没接上的采集通道，但**必须产出候选清单、不能直接写 `notes`**。

### 交接自查清单（新工具接手前先跑一遍）

| # | 动作 | 期望 |
|---|---|---|
| 1 | `git status --porcelain` | 空（干净） |
| 2 | `cd account-research && python -m pytest -q` | `590 passed` |
| 3 | `python ar.py validate` | `0 ERROR / 0 WARN` |
| 4 | `python ar.py selftest` | `15/15 通过`（无 `account/` 时是 SKIP，**不是 PASS**） |
| 5 | `python ar.py status` | 6 条告警 + 一句「下一步」 |
| 6 | `python ar.py extension` | 扩展 `8.1.0` + 宿主「已注册」 |

第 2 项若只有 `test_detail_page_goes_all_the_way_through` 一条红，先看 §六那条
「会偶发红的测试」——那是 OCR 引擎的内存问题，不是回归，重跑通常就绿。

---

## 八、一句话总结这轮学到的东西

前两轮修的都不是「功能没做」，而是**判据取错了地方**：
拿 state 判「页面渲染好了没有」（该看 DOM），拿账号表判「这篇能不能收」（该看作者链接）。
两次真机失败，两次都是**代码相信了一个变量，而人相信了眼睛**。
新的判据一律落在**产物**上，不落在「我认为会填这个数据的中间变量」上。
