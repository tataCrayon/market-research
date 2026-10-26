# src/parser —— 已迁移（2026-10-26）

> ⚠️ **本目录已不是代码落点。** 解析器已实现在 [`../account_research/`](../account_research/)，本 README 保留的是**真实存档特性的实测记录**——那些特性已经写成代码与测试，这里作历史存档与交接说明。

## 代码在哪

| 能力 | 文件 |
|---|---|
| `__INITIAL_STATE__` 提取 + JS 字面量清洗（`undefined` → `null`） | [`../account_research/initialstate.py`](../account_research/initialstate.py) |
| 页面类型判别 + 归属校验 | 同上（`classify` / `target_user_ids` / `viewer_user_id`） |
| 账号行 / 笔记行解析 | [`../account_research/parser.py`](../account_research/parser.py) |
| 六张表读写（只追加） | [`../account_research/store.py`](../account_research/store.py) |
| V1–V8 校验器 | [`../account_research/validate.py`](../account_research/validate.py) |
| 确定性统计 | [`../account_research/stats.py`](../account_research/stats.py) |

命令行入口：`python account-research/ar.py probe|parse|validate|stats|add|selftest`。

## 当初记下的四条真实存档特性（均已实现）

1. 列表页 HTML 内嵌 `__INITIAL_STATE__`，32 篇五字段（标题 / 发布时间 / 赞 / 形式 / 置顶）完整可解析 → `initialstate.flatten_note_items` + `parser.parse_notes`
2. `__INITIAL_STATE__` 是 JS 字面量非严格 JSON——`undefined` 值需清洗为 `null` 才能 `json.loads` → `initialstate.clean_js_literals`（逐字符走，**不清洗字符串内**的 `undefined`）
3. 存档混入**采集者本人会话数据**——必须按 target userId 做归属校验 → `parser.parse_notes` 丢弃非目标作者并留证据；`ar probe` 会显式打印「采集者 userId ≠ 目标 userId」
4. 列表页不含单篇收藏 / 评论 / 分享 → 解析器**永远留空**（`None`），不估算、不补全

## 新增的两条（写代码时才撞出来的，见 docs/lessons-learned.md D4）

5. 累计互动的 `type` 是**单数** `interaction`（不是 `interactions`）——按复数映射会让字段永远为空
6. 搜索页的 `search.feeds` 实测为 0，结果全在 `feed.feeds`（33 条来自 33 个不同作者）；判别要靠 `<title>` 含「搜索」+ `userPageData.basicInfo` 缺失 + 多作者 feed

## 验收

`python account-research/ar.py selftest`（对 `account/` 真实存档回归；无存档时 SKIP 而非 PASS）。
