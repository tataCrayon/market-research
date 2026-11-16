"""把四张结论表渲染成一份人能从头读到尾的 markdown 报告。

## 为什么需要它

`ar debrief` 把「拆解」跑通了，但产物是四张 CSV。前三份报告（卡兹克、知新）都是**手写的**
——每拆一个号就要重抄一遍数字、重排一遍版式。这个成本会在第 3、第 4 个号上反复付，
而且手抄会漂：实测漂过 27/31 写成 29/31 这种读起来完全合理的错。

## 一条硬约束：数字只能来自表

本模块**不接收任何手写数字**。所有计数都由 `stats` / `store` / `notes` 现算，
所有结论都从 `findings` / `playbook` / `hypotheses` / `verdicts` 逐行渲染。
做不到的时候写 ⬜ —— 与项目纪律「⬜ 不得被填充」一致。

## 为什么是 `ReportData` + `render_*` 两层

项目纪律要求「双输出同源」：markdown 与 HTML 的数字必须一致。
把计算收进 `ReportData`、渲染拆成函数，后续加 HTML 视图时**复用同一个对象**，
结构上就不可能漂——而不是靠「两边都记得改」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from . import store
from .counts import parse_count
from .stats import summarize

# 四层在报告里的显示顺序。与 SKILL.md 的四层拆解一致。
LAYERS = ("定位", "选题", "结构", "变现")

# 这些层之外出现的一律归到「其它」段——**不丢弃**。
# 实测库里就有 `产能` 与 `数据` 两条 layer，它们不是四层之一但确实是结论。
EXTRA_LAYER = "其它"

MISSING = "⬜"

# 报告开头的声明。每次生成都带上，避免读者把自动产物当成人工精读的结果。
GENERATED_BY = "本报告由 `ar report` 从 `data/*.csv` 自动渲染"

# ⚠️ 拿不到，但不该让读者猜「是不是漏了」的字段。列在这里的项**永远**显式写 ⬜ 并给理由。
# 判据：读者会合理地问「这个数呢？」——那就必须回答「拿不到，因为 X」。
UNAVAILABLE = (
    ("粉丝增长曲线", "仅作者后台可得"),
    ("曝光量 / 完播率", "仅作者后台可得；没有它就没有任何互动率的分母"),
    ("报价 / 商单收入", "后台或私域数据，公开页永不可得"),
)


def _is_missing(value: Any) -> bool:
    """空值判定。`0` 是观测到的数，`⬜` 才是没观测到——两者不能混。"""
    return str(value if value is not None else "").strip() in ("", MISSING, "None")


@dataclass
class ReportData:
    """报告要用到的全部数据。**所有数字都在这里算好**，渲染层只做排版。"""

    account: dict[str, Any]
    notes: list[dict[str, Any]]            # 已按 note_id 合并
    sample: list[dict[str, Any]]           # 进统计的样本（去置顶、去无点赞）
    pinned: list[dict[str, Any]]
    stats: Any
    body_ready: set[str] = field(default_factory=set)
    images_ready: set[str] = field(default_factory=set)
    comments_ready: set[str] = field(default_factory=set)
    findings: list[dict[str, Any]] = field(default_factory=list)
    playbook: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    today: str = ""

    @property
    def nickname(self) -> str:
        return str(self.account.get("nickname") or self.account.get("account_id") or "（未知账号）")

    @property
    def latest_verdict(self) -> dict[str, Any] | None:
        """最新一条判决。表是只追加的，历史判决仍在里面，但报告头只该用一个。"""
        if not self.verdicts:
            return None
        return max(self.verdicts, key=lambda row: str(row.get("decided_on") or ""))

    def findings_of(self, layer: str) -> list[dict[str, Any]]:
        if layer == EXTRA_LAYER:
            return [row for row in self.findings if str(row.get("layer") or "") not in LAYERS]
        return [row for row in self.findings if str(row.get("layer") or "") == layer]


def _is_pinned(row: dict[str, Any]) -> bool:
    return str(row.get("pinned") or "").strip() in ("1", "true", "是", "y", "yes")


def _cell(value: Any) -> str:
    """计数单元格：能解析就按 `counts` 的口径显示（圆整值带 ≈），否则原样或 ⬜。"""
    text = str(value or "").strip()
    if not text or text == MISSING:
        return MISSING
    parsed = parse_count(text)
    return parsed.text() if parsed else text


def _text(value: Any, empty: str = MISSING) -> str:
    text = str(value or "").strip()
    return text if text else empty


def _escape(value: Any) -> str:
    """表格单元格里的 `|` 会把 markdown 表撑坏。"""
    return str(value or "").replace("|", "／").replace("\n", " ")


def collect(
    data_dir: str | Path,
    account_id: str,
    text_dir: str | Path,
    today: str = "",
    k_values: tuple[int, ...] = (3, 5),
) -> ReportData:
    """把六张表读成一份 `ReportData`。找不到账号直接抛，不返回半个报告。"""
    data_dir = Path(data_dir)
    text_dir = Path(text_dir)
    accounts = store.read_table(data_dir, "accounts")
    account = next(
        (row for row in accounts if str(row.get("account_id")) == account_id), None
    )
    if account is None:
        known = "、".join(
            f"{row.get('nickname')}({row.get('account_id')})" for row in accounts
        ) or "（空）"
        raise ValueError(f"accounts 表里没有 {account_id}。现有：{known}")

    raw_notes = [
        row for row in store.read_table(data_dir, "notes")
        if str(row.get("account_id")) == account_id
    ]
    notes = store.latest_per_note(raw_notes)
    # 样本定义必须与 stats.summarize 一致：置顶与无点赞行都不进统计。
    # 两处不一致会造出「同一个任务两个分母」那种 bug（实测过一次）。
    sample = [
        row for row in notes
        if not _is_pinned(row) and str(row.get("likes") or "").strip()
    ]
    pinned = [row for row in notes if _is_pinned(row)]

    def ready(suffix: str) -> set[str]:
        return {
            str(row.get("note_id")) for row in notes
            if (text_dir / f"{row.get('note_id')}{suffix}").is_file()
        }

    return ReportData(
        account=account,
        notes=notes,
        sample=sample,
        pinned=pinned,
        stats=summarize(raw_notes, account_id, k_values),
        body_ready=ready(".txt"),
        images_ready=ready(".images.txt"),
        comments_ready=ready(".comments.txt"),
        findings=[row for row in store.read_table(data_dir, "findings")
                  if str(row.get("account_id")) == account_id],
        playbook=store.read_table(data_dir, "playbook"),
        # 假设表现在允许「同一条假设多行」（结案 = 追加新状态），
        # 报告只该显示**最新一次**——否则一条已结案的假设会同时出现「未验证」和「超期未验」。
        hypotheses=store.latest_hypotheses(store.read_table(data_dir, "hypotheses")),
        verdicts=[row for row in store.read_table(data_dir, "verdicts")
                  if str(row.get("account_id")) == account_id],
        today=today or date.today().isoformat(),
    )


# ---- 渲染 ----


def _coverage_line(data: ReportData, suffix: str, label: str) -> str:
    """`有正文 5/30 → 缺 25/30`。分母统一用样本量，与 §统计 同源。"""
    total = len(data.sample)
    have = len({str(row.get("note_id")) for row in data.sample} & _ready_set(data, suffix))
    return f"- 有{label} {have}/{total} → 缺 {total - have}/{total}"


def _ready_set(data: ReportData, suffix: str) -> set[str]:
    return {
        ".txt": data.body_ready,
        ".images.txt": data.images_ready,
        ".comments.txt": data.comments_ready,
    }[suffix]


def render_header(data: ReportData) -> str:
    verdict = data.latest_verdict
    marker = _text(verdict.get("verdict")) if verdict else MISSING
    lines = [
        f"# 🔍 {data.nickname} 小红书对标拆解报告",
        "",
        f"> {GENERATED_BY}（{data.today}）。"
        f"**报告里没有任何手写数字**——所有计数都由代码现算，改表即改报告。",
        "",
    ]
    if verdict:
        lines += [
            f"**判决：{marker}**（{_text(verdict.get('decided_on'))} 判，"
            f"{_text(verdict.get('due_on'))} 到期复核）",
            "",
            f"> {_text(verdict.get('reason'), '（判决理由为空）')}",
            "",
        ]
    else:
        lines += [
            f"**判决：{MISSING}** —— 还没有拆解结论。先跑 `ar debrief` 并 `--commit`。",
            "",
        ]
    return "\n".join(lines)


def render_basis(data: ReportData) -> str:
    """§0 数据底稿：账号字段 + 样本量 + 覆盖度 + ⬜ 清单。

    覆盖度这一节是**唯一**允许出现 `a/b` 的地方——模型与人都从这里取数。
    """
    account = data.account
    stats = data.stats
    rows = [
        "## 0. 数据底稿",
        "",
        "| 项 | 值 | 口径 | 等级 |",
        "|---|---|---|---|",
        f"| account_id | `{_escape(account.get('account_id'))}` | 库内主键 | — |",
        f"| 小红书号 | {_escape(_text(account.get('red_id')))} | 公开页 | ② |",
        f"| 粉丝 | {_escape(_text(account.get('fans')))} | 公开页 | ② |",
        f"| 笔记总数 | {_escape(_text(account.get('posted')))} | 公开页 | ② |",
        f"| 累计获赞与收藏 | {_escape(_text(account.get('interactions')))} | 公开页 | ② |",
        f"| IP 属地 | {_escape(_text(account.get('ip_location')))} | 公开页 | ② |",
        f"| 简介 | {_escape(_text(account.get('desc')))} | 公开页（自我声明） | ② |",
        f"| 取证日 | {_escape(_text(account.get('collected_on')))} | — | — |",
        "",
        "### 0.1 样本与统计",
        "",
        f"- 笔记 {len(data.notes)} 篇｜置顶 {len(data.pinned)} 篇（单列，不进样本）"
        f"｜**样本 {stats.n_sample} 篇**",
        f"- 时间窗：{_text(stats.window[0])} → {_text(stats.window[1])}",
        f"- 点赞：篇均 {stats.likes.mean:.1f} | 中位 {stats.likes.median:.1f} | "
        f"最高 {stats.likes.maximum} | 最低 {stats.likes.minimum} | "
        f"Top20% 锚定线 {stats.likes.top20_anchor}（内部分档，非平台口径）",
    ]
    if stats.forms:
        forms = "｜".join(
            f"{name} {info['n']} 篇（篇均 {info['mean']:.1f}）"
            for name, info in stats.forms.items()
        )
        rows.append(f"- 分形式：{forms}")
    for k, info in stats.burst.items():
        # `rate` 已经是百分数（stats 里乘过 100），这里**不能再乘一次**——
        # 乘两次会得到 1610.0% 这种一眼假、但很容易在重构时混进去的数。
        rows.append(
            f"- 爆文率 K={k}：{info['rate']:.1f}%（{info['hits']}/{stats.n_sample}，"
            f"阈值 ≥{info['threshold']:.0f}）——定义相对账号自身，"
            f"民间框架 ③-a，K 值无共识"
        )
    if stats.approx_rows:
        listed = "、".join(f"`{note_id[:8]}`" for note_id in stats.approx_notes[:8])
        rows.append(
            f"- ⚠️ **{stats.approx_rows} 篇的点赞是圆整值**（{listed}）：已计入上面统计，"
            f"但逐篇引用时必须写 `≈`，不得当精确值"
        )
    rows += [
        "",
        "### 0.2 覆盖度（引用任何 `a/b` 之前先查这里）",
        "",
        _coverage_line(data, ".txt", "正文"),
        _coverage_line(data, ".images.txt", "图上文字"),
        _coverage_line(data, ".comments.txt", "评论原文"),
    ]
    total = len(data.sample)
    for column, label in (("collects", "收藏"), ("comments", "评论数"), ("shares", "分享")):
        have = sum(1 for row in data.sample if not _is_missing(row.get(column)))
        rows.append(f"- 有{label} {have}/{total}")
    dated = sum(1 for row in data.sample if not _is_missing(row.get("published_at")))
    rows.append(f"- 有发布日期 {dated}/{total}")
    rows += [
        "",
        "> 「有评论数」不等于「读过评论」：只看到数字就推断人群，是把缺证据写成证据。"
        "人群画像只能用「有评论原文」的那几篇。",
        "",
        "### 0.3 永远拿不到的（不是漏采）",
        "",
        "| 字段 | 为什么拿不到 |",
        "|---|---|",
    ]
    rows += [f"| {label} | {reason} |" for label, reason in UNAVAILABLE]
    rows.append("")
    return "\n".join(rows)


def render_notes(data: ReportData, limit: int = 0) -> str:
    """§1 逐篇数据。置顶单列在前，样本按发布时间降序。

    `limit` 只影响显示行数，**不影响任何统计**——切掉的是表格，不是样本。
    """
    ordered = sorted(
        data.sample, key=lambda row: str(row.get("published_at") or ""), reverse=True
    )
    shown = ordered[:limit] if limit else ordered
    rows = [
        "## 1. 逐篇数据",
        "",
        f"> 样本 {len(data.sample)} 篇；置顶 {len(data.pinned)} 篇单列。"
        "「正文 / 评论文」两列带 ✅ 表示该篇有对应文本层。",
        "",
        "| # | 发布时间 | 标题 | 赞 | 藏 | 评 | 分享 | 形式 | 正文 | 评论文 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def line(index: int, row: dict[str, Any], pinned: bool = False) -> str:
        note_id = str(row.get("note_id") or "")
        day = _escape(_text(row.get("published_at"))) + ("（置顶）" if pinned else "")
        return (
            f"| {index} | {day} | {_escape(_text(row.get('title')))} "
            f"| {_cell(row.get('likes'))} | {_cell(row.get('collects'))} "
            f"| {_cell(row.get('comments'))} | {_cell(row.get('shares'))} "
            f"| {_escape(_text(row.get('form')))} "
            f"| {'✅' if note_id in data.body_ready else MISSING} "
            f"| {'✅' if note_id in data.comments_ready else MISSING} |"
        )

    index = 0
    for row in data.pinned:
        index += 1
        rows.append(line(index, row, pinned=True))
    for row in shown:
        index += 1
        rows.append(line(index, row))
    if limit and len(shown) < len(ordered):
        rows += ["", f"*（只显示前 {limit} 篇；完整样本 {len(ordered)} 篇）*"]
    return "\n".join(rows)


def render_findings(data: ReportData) -> str:
    """§2 四层结论。按 layer 分组，**未知 layer 归到「其它」而不是丢弃**。

    丢掉未知 layer 会让报告「看起来完整」——但库里明明有 `产能`、`数据` 两条结论，
    报告里却没有，那是最糟的一种漏：读者不知道少了什么。
    """
    if not data.findings:
        return (
            "## 2. 四层结论\n\n"
            f"{MISSING} **还没有 findings**。先跑 `ar debrief --account <id>` 并 `--commit`。\n"
        )
    rows = ["## 2. 四层结论", ""]
    groups = list(LAYERS) + [EXTRA_LAYER]
    for layer in groups:
        items = data.findings_of(layer)
        if not items:
            continue
        rows += [f"### {layer}（{len(items)} 条）", ""]
        for item in items:
            used = str(item.get("used_in_verdict") or "").strip() in ("1", "true", "是")
            rows.append(f"**[{_text(item.get('finding_id'))}]** {_text(item.get('statement'))}")
            rows.append("")
            rows.append(
                f"- 证据等级：{_text(item.get('grade'))}"
                f"{' · **已用于判决**' if used else ' · 未进判决（线索级）'}"
            )
            rows.append(f"- 证据：{_escape(_text(item.get('evidence_ref')))}")
            rows.append(f"- 样本：{_escape(_text(item.get('sample_basis')))}")
            rows.append(f"- 适用范围：{_escape(_text(item.get('applicability')))}")
            rows.append("")
    return "\n".join(rows)


def render_playbook(data: ReportData) -> str:
    """§3 可抄清单。**失效条件为空的行会被标红**——那等于「建议全抄」。

    支撑账号数由 `supporting_accounts` 原样带出，不手填：
    这是 PRD §8 Q2「有没有在 ≥2 个账号上验证过的打法」的唯一数据来源。
    """
    if not data.playbook:
        return "## 3. 可抄清单\n\n（playbook 表是空的）\n"
    rows = [
        "## 3. 可抄清单",
        "",
        "> 每条都必须有**失效条件**——没有失效条件的建议等于「建议全抄」。",
        "",
        "| # | 动作 | 机制 | 需要什么 | 失效条件 | 支撑账号 | 证据 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in data.playbook:
        failure = str(item.get("failure_condition") or "").strip()
        rows.append(
            f"| {_text(item.get('action_id'))} | {_escape(_text(item.get('action')))} "
            f"| {_escape(_text(item.get('mechanism')))} "
            f"| {_escape(_text(item.get('requires')))} "
            f"| {_escape(failure) if failure else '**⚠️ 缺失**'} "
            f"| {_escape(_text(item.get('supporting_accounts')))} "
            f"| {_escape(_text(item.get('evidence_ids')))} |"
        )
    rows += ["", "### 反例（这条为什么可能不成立）", ""]
    for item in data.playbook:
        counter = str(item.get("counterexample") or "").strip()
        if counter:
            rows.append(f"- **{_text(item.get('action_id'))}**：{_escape(counter)}")
    return "\n".join(rows)


def render_hypotheses(data: ReportData) -> str:
    """§4 假设与实验。到期日 + 状态必须同现，否则这条假设永远不会被测。"""
    if not data.hypotheses:
        return "## 4. 待验证假设\n\n（hypotheses 表是空的）\n"
    rows = [
        "## 4. 待验证假设",
        "",
        "| 编号 | 假设 | 支持 | 反证 | 实验 | 状态 | 到期 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in data.hypotheses:
        rows.append(
            f"| {_text(item.get('hypothesis_id'))} "
            f"| {_escape(_text(item.get('statement')))} "
            f"| {_escape(_text(item.get('support')))} "
            f"| {_escape(_text(item.get('counter')))} "
            f"| {_escape(_text(item.get('experiment')))} "
            f"| {_escape(_text(item.get('status')))} "
            f"| {_escape(_text(item.get('due_on')))} |"
        )
    return "\n".join(rows)


def render_verdict(data: ReportData) -> str:
    """§5 判决与三件套。历史判决也列出——只追加的表里它们是证据，不是垃圾。"""
    verdict = data.latest_verdict
    if verdict is None:
        return f"## 5. 判决\n\n{MISSING} 还没有判决。\n"
    rows = [
        "## 5. 判决",
        "",
        f"### {_text(verdict.get('verdict'))}",
        "",
        f"> {_text(verdict.get('reason'))}",
        "",
        "**三件套（缺一条判决即失效）**",
        "",
        f"- **最值钱的一步**：{_text(verdict.get('next_step'))}",
        f"- **最便宜的验证**：{_text(verdict.get('cheapest_test'))}",
        f"- **放弃信号**：{_text(verdict.get('abandon_signal'))}",
        "",
        "### 双镜自审",
        "",
        f"- **方法镜**：{_text(verdict.get('mirror_method'))}",
        f"- **落地镜**：{_text(verdict.get('mirror_practice'))}",
    ]
    history = [row for row in data.verdicts if row is not verdict]
    if history:
        rows += ["", "### 历史判决（只追加，不删）", "",
                 "| 判决 | 决定日 | 到期 | 理由摘要 |", "|---|---|---|---|"]
        for row in sorted(history, key=lambda r: str(r.get("decided_on") or ""), reverse=True):
            rows.append(
                f"| {_escape(_text(row.get('verdict')))} "
                f"| {_escape(_text(row.get('decided_on')))} "
                f"| {_escape(_text(row.get('due_on')))} "
                f"| {_escape(str(row.get('reason') or '')[:60])} |"
            )
    return "\n".join(rows)


def render_footer(data: ReportData) -> str:
    """§6 报告自身的取证边界。**每次都要带**，否则读者会把它当成全量结论。"""
    total = len(data.sample)
    ids = {str(row.get("note_id")) for row in data.sample}
    lines = ["## 6. 本报告没做到的事", ""]
    body = len(ids & data.body_ready)
    comments = len(ids & data.comments_ready)
    if body < total:
        lines.append(
            f"- **正文只读到 {body}/{total} 篇**：结构层结论只在这 {body} 篇上成立，"
            f"不能写成「这个号的通用结构」"
        )
    if comments < total:
        lines.append(
            f"- **评论原文只有 {comments}/{total} 篇**：人群画像最多到 ③-a 初判，"
            f"不能写成比例结论"
        )
    lines += [
        "- 涨粉 / 曝光 / 完播率仅作者后台可得，全 ⬜；**任何互动率都没有分母**，不得计算",
        "- 报价与商单收入不可得，变现层只能描述可见痕迹",
        "",
        f"> {GENERATED_BY}。要改结论改表，不要改这个文件。",
        "",
    ]
    return "\n".join(lines)


def render(data: ReportData, notes_limit: int = 0) -> str:
    """把 `ReportData` 渲染成完整 markdown。**纯函数**：同样的数据永远得到同样的文本。"""
    sections = [
        render_header(data),
        render_basis(data),
        render_notes(data, limit=notes_limit),
        render_findings(data),
        render_playbook(data),
        render_hypotheses(data),
        render_verdict(data),
        render_footer(data),
    ]
    return "\n\n".join(section.rstrip() for section in sections) + "\n"
