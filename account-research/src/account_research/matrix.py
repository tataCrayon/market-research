"""S4 矩阵视图：把「跨账号能答什么」做成一张离线 HTML。

## 它回答哪三个问题（PRD §8，答不出就是没做完）

| # | 问题 | 本页对应区块 |
|---|---|---|
| Q1 | 这个领域里图文 vs 视频哪种形式的中位互动更高？ | `render_forms`（样本不足显式声明） |
| Q2 | 有哪些打法在 ≥2 个账号上都被验证有效？ | `render_playbook`（<2 直接标「未达成」） |
| Q3 | 我想做「X 母题」，谁做过、做到什么程度、我抄会死在哪？ | `render_topics` + `render_failure_list` |

## 三条硬约束（PRD §7 与项目纪律）

1. **N5 不评分、不排名**：只按单一公开指标排序并标口径，禁止跨指标加权成综合分。
2. **N3 三处同源**：数字全部来自 `report.ReportData`，与 markdown 报告同一对象。
   本模块**不做再计算**——连百分比都是「计数 ÷ 分母」，且分母印在旁边。
3. **N1/N2 零外部依赖**：无 CDN、无字体外链、无脚本、无图片。断网双击可开。

## 为什么直接拼 HTML，不先写 markdown 再转

试过「复用 `report.py` 的 markdown 段 + 自己写个小渲染器」，理由是不重复。
但那个渲染器要认表头、分隔行、加粗、内联 span、转义边界——**等于重写一个 markdown 解析器**，
而它只服务本模块，没有第二处用它。自己拼 HTML 代码更短、更少边界、可测。

markdown 与 HTML 的同源**不靠共用字符串**，靠共用 `ReportData`——
这才是「双输出同源」的可执行含义（同一个数字对象，两种排版）。

## 为什么母题用 `notes.topic` 而不在这里重新聚类

`topic` 是拆解时写进表的标签，属既有结论。本页只聚合，**不重新聚类**——
否则就成了「页面自己产出结论」，与「空表不得被填充」冲突。

这个决定立刻暴露了一件真事：卡兹克那 49 行 `topic` **全为空**（历史数据在 topic 列
出现之前入库），知新有 7 个母题。若把空值静默合并，页面会显示
「卡兹克没有母题分布」这种假结论；显式显示「未标注 49/49」，读者立刻知道是**标注缺失**。
"""

from __future__ import annotations

import html
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import store
from .counts import to_count
from .report import ReportData

# 占比条分段色板。顺序固定：同一母题在每次重新生成的页面里颜色一致。
SEGMENT_COLORS = (
    "var(--seg-1)", "var(--seg-2)", "var(--seg-3)",
    "var(--seg-4)", "var(--seg-5)", "var(--seg-6)",
)
UNLABELED_COLOR = "var(--seg-na)"
UNLABELED_LABEL = "未标注"

# 一个母题要几个样本才允许显示中位数。低于这个数只报计数——
# 2 篇的中位数不是「这个母题的水平」，是那 2 篇的平均运气。
MIN_SAMPLES_FOR_MEDIAN = 3

# 支撑账号数达到几条才算「跨账号验证过」。PRD §8 Q2 定的是 ≥2。
CROSS_ACCOUNT_THRESHOLD = 2

STYLE = """
:root{
  --paper:#FAF7F1; --surface:#FFFEFA; --surface-2:#F2EDE2;
  --ink:#2B2925; --ink-2:#6E6A61; --ink-3:#9A948A; --line:#E5DDCE;
  --pine:#3F6B4F; --pine-deep:#2C4E39; --pine-soft:#DCE7DC;
  --terracotta:#C05B3C; --terracotta-deep:#A34A30; --terracotta-soft:#F3E0D6;
  --seg-1:#3F6B4F; --seg-2:#C05B3C; --seg-3:#7FA183;
  --seg-4:#D99B82; --seg-5:#7A7440; --seg-6:#5B7B8C;
  --seg-na:#C9C2B4;
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:24px; --sp-6:32px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
         "Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--font);
     font-variant-numeric:tabular-nums;font-size:14px;line-height:1.6}
.page{max-width:1040px;margin:0 auto;padding:var(--sp-6) var(--sp-4);
      display:grid;gap:var(--sp-5)}
h1{font-size:20px;margin:0 0 var(--sp-2)}
h2{font-size:16px;margin:0 0 var(--sp-3)}
h3{font-size:14px;margin:0 0 var(--sp-2)}
p{margin:var(--sp-2) 0}
.sub{color:var(--ink-2);font-size:13px;margin:0}
.hint{color:var(--ink-3);font-size:12px}
.card{background:var(--surface);border:1px solid var(--line);
      border-radius:var(--sp-2);padding:var(--sp-4)}
table{width:100%;border-collapse:collapse;font-size:13px;margin:var(--sp-3) 0}
th,td{text-align:left;padding:var(--sp-2) var(--sp-3);
      border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink-3);font-weight:500;background:var(--surface-2)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.warn{color:var(--terracotta-deep);background:var(--terracotta-soft);
      border-radius:var(--sp-1);padding:0 var(--sp-2);font-size:12px;
      white-space:nowrap;display:inline-block}
.ok{color:var(--pine-deep);background:var(--pine-soft);
    border-radius:var(--sp-1);padding:0 var(--sp-2);font-size:12px;
    white-space:nowrap;display:inline-block}
.stackbar{display:flex;height:12px;border-radius:6px;overflow:hidden;
          background:var(--surface-2);margin:var(--sp-3) 0 0}
.stackbar .seg{height:100%}
.stackbar .seg:first-child{border-radius:6px 0 0 6px}
.stackbar .seg:last-child{border-radius:0 6px 6px 0}
.legend{list-style:none;margin:var(--sp-3) 0 0;padding:0;
        display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
        gap:var(--sp-2) var(--sp-4)}
.legend li{display:flex;align-items:center;gap:var(--sp-2);font-size:13px}
.legend i{width:10px;height:10px;border-radius:3px;flex:none}
.legend .num{margin-left:auto;color:var(--ink-2)}
.legend-count{color:var(--ink-3);font-size:12px;min-width:56px;text-align:right}
.footnote{color:var(--ink-3);font-size:12px;border-top:1px solid var(--line);
          padding-top:var(--sp-3)}
@media print{*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _count(value: Any) -> str:
    """计数显示：空值与 ⬜ 一律显式写 ⬜，不写 0。0 与「没观测到」是两件事。"""
    text = str(value if value is not None else "").strip()
    return "⬜" if text in ("", "⬜", "None") else _e(text)


def support_count(text: Any) -> int:
    """从 `supporting_accounts` 里读出「几个账号」。

    这一列书上写的是自由文本（`1` / `1（知新的AI笔记）` / `2`），
    直接 `int()` 会在第二种形态上崩。只取开头数字，读不到当 0——
    **0 会让它在打法库里显示「未达成」，那正是它该被看见的状态。**
    """
    match = re.match(r"\s*(\d+)", str(text or ""))
    return int(match.group(1)) if match else 0


@dataclass
class TopicRow:
    """母题矩阵的一行。**只装原值**，不装任何加权分。"""

    topic: str
    notes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.notes)

    @property
    def likes(self) -> list[int]:
        return [value for value in (to_count(row.get("likes")) for row in self.notes)
                if value is not None]

    @property
    def median(self) -> float | None:
        """样本不足 `MIN_SAMPLES_FOR_MEDIAN` 时返回 None——宁可不显示，也不给假中位。"""
        if len(self.likes) < MIN_SAMPLES_FOR_MEDIAN:
            return None
        return statistics.median(self.likes)

    @property
    def mean(self) -> float | None:
        return statistics.mean(self.likes) if self.likes else None


def topic_matrix(notes: Iterable[dict[str, Any]]) -> list[TopicRow]:
    """按 `topic` 聚合。**空 topic 单独成行并排最前**——它是「没标注」，不是「没有」。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in notes:
        topic = str(row.get("topic") or "").strip() or UNLABELED_LABEL
        grouped.setdefault(topic, []).append(row)
    rows = [TopicRow(topic, items) for topic, items in grouped.items()]
    rows.sort(key=lambda item: (item.topic != UNLABELED_LABEL, -item.count, item.topic))
    return rows


@dataclass
class MatrixData:
    """跨账号视图的全部输入。每个字段都是现成的表行，本层不产出新结论。"""

    accounts: list[ReportData]
    playbook: list[dict[str, Any]]
    today: str = ""


def collect(reports: Iterable[ReportData], data_dir: str | Path, today: str = "") -> MatrixData:
    """从多个 `ReportData` 组出矩阵视图。playbook 是全库共享的，与账号无关。"""
    data_dir = Path(data_dir)
    rows = store.read_table(data_dir, "playbook") if data_dir.is_dir() else []
    return MatrixData(accounts=list(reports), playbook=rows, today=today)


# ---- 渲染（直接产出 HTML） ----


def _table(headers: Iterable[str], rows: Iterable[Iterable[str]], numeric: Iterable[int] = ()) -> str:
    """生成表格。`numeric` 里的列下标右对齐——其余列保持左对齐。"""
    numeric = set(numeric)
    head = "".join(
        f'<th class="{"num" if index in numeric else ""}">{cell}</th>'
        for index, cell in enumerate(headers)
    )
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="{"num" if index in numeric else ""}">{cell}</td>'
            for index, cell in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_overview(data: MatrixData) -> str:
    """§1 账号总览。**只按中位赞排序 + 标口径**，不做综合评分（N5）。"""
    ordered = sorted(data.accounts, key=lambda item: -(item.stats.likes.median or 0))
    rows = []
    for item in ordered:
        stats = item.stats
        burst = stats.burst.get(3, {})
        # 空账号（还没采到笔记）不显示 0——0 会被读成「中位赞就是 0」。
        # 实测 FuzzyPop 就是这种状态，显示 `0.0` 比显示 ⬜ 更误导。
        has_sample = stats.n_sample > 0
        rate = f"{burst['rate']:.1f}%（{burst['hits']}/{stats.n_sample}）" if burst else "⬜"
        verdict = item.latest_verdict
        rows.append([
            _e(item.nickname),
            _count(item.account.get("fans")),
            str(stats.n_sample) if has_sample else '<span class="warn">⬜ 0 篇</span>',
            (f"{_e(stats.window[0])} → {_e(stats.window[1])}" if has_sample
             else '<span class="hint">无笔记</span>'),
            f"{stats.likes.mean:.1f}" if has_sample else "⬜",
            f"<strong>{stats.likes.median:.1f}</strong>" if has_sample else "⬜",
            str(stats.likes.maximum) if has_sample else "⬜",
            rate,
            _e(verdict.get("verdict")) if verdict else "⬜ 未拆解",
        ])
    return (
        '<div class="card"><h2>1. 账号总览</h2>'
        '<p class="hint">排序依据：<strong>样本中位点赞（单一公开指标）</strong>。'
        "不是综合评分——PRD §7 N5 禁止跨指标加权，本页只列原值。</p>"
        + _table(
            ["账号", "粉丝", "样本", "时间窗", "篇均赞", "中位赞", "最高", "爆文 K=3", "判决"],
            rows, numeric=(1, 2, 4, 5, 6),
        )
        + '<p class="hint">「爆文 K=3」阈值 = 该账号自身中位赞 ×3，'
        "<strong>跨账号不可直接比</strong>——每个号的分母是它自己。"
        "这是相对分档，不是平台爆文定义。</p></div>"
    )


def render_forms(data: MatrixData) -> str:
    """§2 Q1：形式对照。**样本不足就明说不足**，不给一个看起来权威的中位数。"""
    rows = []
    conclusions = []
    for item in data.accounts:
        for name in sorted(item.stats.forms):
            picks = [row for row in item.sample if str(row.get("form") or "未知") == name]
            values = sorted(v for v in (to_count(row.get("likes")) for row in picks) if v is not None)
            info = item.stats.forms[name]
            if len(values) >= MIN_SAMPLES_FOR_MEDIAN:
                median_cell = f"{statistics.median(values):.1f}"
                verdict_cell = "—"
            else:
                median_cell = f'<span class="warn">⬜ 样本 &lt;{MIN_SAMPLES_FOR_MEDIAN}</span>'
                verdict_cell = '<span class="hint">样本不足，不给判定</span>'
            rows.append([
                _e(item.nickname), _e(name), str(info["n"]),
                f"{info['mean']:.1f}", median_cell, verdict_cell,
            ])
        medians = {}
        for name in item.stats.forms:
            picks = [row for row in item.sample if str(row.get("form") or "未知") == name]
            values = [v for v in (to_count(row.get("likes")) for row in picks) if v is not None]
            if len(values) >= MIN_SAMPLES_FOR_MEDIAN:
                medians[name] = statistics.median(values)
        if not item.sample:
            conclusions.append(
                f"<p>· <strong>{_e(item.nickname)}</strong>："
                f'<span class="warn">⬜ 没有笔记</span> 无法比较任何形式——'
                f"这个号还没采到数据（缺主页存档）</p>"
            )
        elif len(medians) < 2:
            conclusions.append(
                f"<p>· <strong>{_e(item.nickname)}</strong>：只有 {len(medians)} 种形式达到 "
                f"{MIN_SAMPLES_FOR_MEDIAN} 篇，<strong>不足以比较</strong>——"
                f"报一个「图文更好」会是编的。</p>"
            )
        else:
            best = max(medians, key=lambda key: medians[key])
            conclusions.append(
                f"<p>· <strong>{_e(item.nickname)}</strong>：中位赞最高的是 "
                f"<strong>{_e(best)}</strong>（{medians[best]:.1f}）——"
                f"这是账号内单点观察，不是平台规律。</p>"
            )
    return (
        '<div class="card"><h2>2. 图文 vs 视频（Q1）</h2>'
        '<p class="hint">问题：这个领域里哪种形式的中位互动更高？</p>'
        + _table(["账号", "形式", "篇数", "篇均赞", "中位赞", "判定"], rows, numeric=(2, 3, 4))
        + "<h3>Q1 结论</h3>" + "".join(conclusions)
        + '<p class="hint">跨账号合并结论（例如「图文普遍优于视频」）需要 ≥3 个账号同向，'
        "本页不做这个合并——<strong>样本不够时合并只会制造假规律</strong>。</p></div>"
    )


def render_topics(data: MatrixData) -> str:
    """§3 Q3 上半：母题矩阵。未标注段**必须显式可见**且用灰段，不得合并隐藏。"""
    blocks = ['<div class="card"><h2>3. 母题矩阵（Q3 上半）</h2>',
              '<p class="hint">问题：我想做「X 母题」，谁做过、做到什么程度？'
              "母题标签来自 <code>notes.topic</code>（拆解时写入），本页只聚合、不重新聚类。</p>"]
    for item in data.accounts:
        rows = topic_matrix(item.notes)
        total = sum(row.count for row in rows) or 1
        segments, legend = [], []
        for index, row in enumerate(rows):
            color = UNLABELED_COLOR if row.topic == UNLABELED_LABEL else SEGMENT_COLORS[index % len(SEGMENT_COLORS)]
            pct = round(100.0 * row.count / total, 1)
            segments.append(
                f'<span class="seg" style="width:{pct}%;background:{color}" '
                f'title="{_e(row.topic)} {pct}%（{row.count} 篇）"></span>'
            )
            legend.append(
                f'<li><i style="background:{color}"></i><span>{_e(row.topic)}</span>'
                f'<span class="num">{pct}%</span>'
                f'<span class="legend-count num">{row.count} 篇</span></li>'
            )
        aria = f"{item.nickname} 母题占比：" + "、".join(
            f"{row.topic} {round(100.0 * row.count / total, 1)}%" for row in rows
        )
        blocks.append(f"<h3>{_e(item.nickname)}</h3>")
        blocks.append(
            f'<div class="stackbar" role="img" aria-label="{_e(aria)}">'
            + "".join(segments) + "</div>"
        )
        blocks.append('<ul class="legend">' + "".join(legend) + "</ul>")
        table_rows = []
        for row in rows:
            median = row.median
            mean = row.mean
            table_rows.append([
                _e(row.topic), str(row.count), f"{round(100.0 * row.count / total, 1)}%",
                f"{median:.1f}" if median is not None else f'<span class="warn">⬜ &lt;{MIN_SAMPLES_FOR_MEDIAN} 篇</span>',
                f"{mean:.1f}" if mean is not None else "⬜",
            ])
        blocks.append(_table(["母题", "篇数", "占比", "中位赞", "篇均赞"], table_rows,
                             numeric=(1, 2, 3, 4)))
        unlabeled = next((row for row in rows if row.topic == UNLABELED_LABEL), None)
        if unlabeled and unlabeled.count == total:
            blocks.append(
                f'<p><span class="warn">⚠️ 标注缺失</span> '
                f"<strong>{_e(item.nickname)}</strong> 的全部 {total} 篇都没有 topic 标签——"
                f"这不是「没有选题」，是<strong>标注缺失</strong>。"
                f"母题结论只在有标签的账号上成立，不得跨账号套用。</p>"
            )
    blocks.append("</div>")
    return "".join(blocks)


def render_playbook(data: MatrixData) -> str:
    """§4 Q2：打法库。支撑账号数 <2 的行**直接标「未达成」**，不做温和表述。"""
    ordered = sorted(
        data.playbook,
        key=lambda row: (-support_count(row.get("supporting_accounts")), str(row.get("action_id"))),
    )
    crossed = [row for row in ordered
               if support_count(row.get("supporting_accounts")) >= CROSS_ACCOUNT_THRESHOLD]
    rows = []
    for row in ordered:
        support = support_count(row.get("supporting_accounts"))
        reached = support >= CROSS_ACCOUNT_THRESHOLD
        failure = str(row.get("failure_condition") or "").strip()
        rows.append([
            _e(row.get("action_id")),
            _e(str(row.get("action") or "")[:70]),
            _e(row.get("supporting_accounts")),
            '<span class="ok">✅ 已跨号</span>' if reached else '<span class="warn">⬜ 未达成</span>',
            _e(failure) if failure else '<span class="warn">⚠️ 缺失</span>',
            _e(row.get("evidence_ids")),
        ])
    status = (
        f"<p><strong>当前状态：{len(crossed)}/{len(ordered)} 条达到 ≥{CROSS_ACCOUNT_THRESHOLD} "
        f"个账号验证。</strong></p>"
    )
    if not crossed:
        status += (
            '<p><span class="warn">PRD §8 Q2 尚未达成</span> '
            "为什么全是 ⬜：每条打法的 <code>supporting_accounts</code> 都是 1。"
            "这不是数据没填，是<strong>两个号的打法确实没有交集</strong>——"
            "卡兹克是四段骨架、知新是三层骨架，结构层完全不重叠。"
            "要达成 Q2 必须再拆第三个号，且它得复用前两个之一的动作。</p>"
        )
    return (
        '<div class="card"><h2>4. 打法库（Q2）</h2>'
        '<p class="hint">问题：有哪些打法在 ≥2 个账号上都被验证有效？</p>'
        + status
        + _table(["编号", "动作", "支撑账号", "跨号验证", "失效条件", "证据"], rows)
        + "</div>"
    )


def render_failure_list(data: MatrixData) -> str:
    """§5 Q3 下半：失效条件清单。「我抄会死在哪」的答案在这里。"""
    blocks = ['<div class="card"><h2>5. 失效条件清单（Q3 下半）</h2>',
              '<p class="hint">问题：我抄会死在哪？——每条打法的「什么时候不成立」。</p>']
    missing = 0
    for row in data.playbook:
        failure = str(row.get("failure_condition") or "").strip()
        counter = str(row.get("counterexample") or "").strip()
        if not failure:
            missing += 1
        blocks.append(
            f"<h3>{_e(row.get('action_id'))} · {_e(str(row.get('action') or '')[:50])}</h3>"
        )
        if failure:
            blocks.append(f"<p>· <strong>什么时候不成立</strong>：{_e(failure)}</p>")
        else:
            blocks.append(
                '<p>· <strong>什么时候不成立</strong>：<span class="warn">⬜ 缺失</span> '
                "没有失效条件的建议等于「建议全抄」</p>"
            )
        if counter:
            blocks.append(f"<p>· <strong>已知反例</strong>：{_e(counter)}</p>")
    if missing:
        blocks.append(
            f'<p><span class="warn">⚠️ {missing} 条缺失效条件</span> '
            "本页把它们标成缺失而不是留空——留空会让人以为「没有限制」。</p>"
        )
    blocks.append("</div>")
    return "".join(blocks)


def render_footer(data: MatrixData) -> str:
    """§6 本页没做到的。与 markdown 报告 §6 同构，避免两处口径不一致。"""
    return (
        '<div class="card"><h2>6. 本页没做到的事</h2>'
        "<p>· <strong>不评分、不排名</strong>：只有单一指标排序 + 口径标注（PRD §7 N5）</p>"
        "<p>· <strong>不做跨账号合并结论</strong>：样本不够时合并只会制造假规律</p>"
        "<p>· 涨粉 / 曝光 / 完播率仅作者后台可得，全 ⬜；<strong>任何互动率都没有分母</strong></p>"
        "<p>· 报价与商单收入不可得，变现层只能描述可见痕迹</p>"
        f'<p class="footnote">本页由 <code>ar matrix</code> 从 <code>data/*.csv</code> 渲染'
        f"（{_e(data.today)}）。数字与 <code>ar report</code> 同源："
        "同一份 <code>ReportData</code>，不做二次计算。</p></div>"
    )


def render(data: MatrixData) -> str:
    """渲染成单文件 HTML。**无脚本、无外链、无图片**——断网双击可开。"""
    title = "账号矩阵 · " + " / ".join(item.nickname for item in data.accounts)
    body = "".join([
        render_overview(data),
        render_forms(data),
        render_topics(data),
        render_playbook(data),
        render_failure_list(data),
        render_footer(data),
    ])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<main class="page">
<header>
  <h1>{_e(title)}</h1>
  <p class="sub">数据截至 {_e(data.today)} · 共 {len(data.accounts)} 个账号
  · 打法 {len(data.playbook)} 条 · 本页为纯静态文件：无脚本、无外链、断网可开</p>
</header>
{body}
</main>
</body>
</html>
"""
