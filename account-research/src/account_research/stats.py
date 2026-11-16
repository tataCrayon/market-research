"""确定性统计：篇均 / 中位 / 分形式 / 爆文率。

爆文率是个口径坑（字典 §4：「爆文」至少三种定义，同一样本数字不同）。
所以这里强制把 K 值和定义随结果一起打印，禁止只给一个百分比。

**圆整值的处理（2026-11-03）**：平台过万之后把计数渲染成 `1.2万`。早先 `_int_or_none`
对它返回 None，于是那一行被**静默丢出样本**——`n_total` 少一篇、`excluded` 还是 0、
没有一个字提示。现在走 `counts.to_count`：换算成整数计入统计，同时用 `approx_rows`
把「这个数不是精确值」报出来。样本量不因计数写法而变，这是底线。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from .counts import is_approx, to_count
from .store import latest_per_note


@dataclass
class LikeStats:
    n: int = 0
    mean: float = 0.0
    median: float = 0.0
    maximum: int = 0
    minimum: int = 0
    top20_anchor: int = 0


@dataclass
class SampleStats:
    account_id: str = ""
    n_total: int = 0
    n_pinned: int = 0
    n_sample: int = 0
    likes: LikeStats = field(default_factory=LikeStats)
    forms: dict[str, dict[str, Any]] = field(default_factory=dict)
    burst: dict[int, dict[str, Any]] = field(default_factory=dict)
    coverage: dict[str, str] = field(default_factory=dict)
    window: tuple[str, str] = ("", "")
    excluded: int = 0
    # 样本里有多少篇的点赞是圆整值（`1.2万`）。这些数字参与排序与分档没问题，
    # 但**报告里逐篇引用时必须标 ≈**，不能把 11,500 写成 12,000。
    approx_rows: int = 0
    # 具体是哪几篇。只报个数字不够用：模型知道「有 1 篇是圆整值」却不知道是哪篇，
    # 只能靠猜——而猜错就是把精确值写成近似值、或反过来。名单要直接给它。
    approx_notes: tuple[str, ...] = ()
    # 计数写法坏到连 `counts.parse_count` 都解析不出来的行（既不是数字也不是「万」）。
    # 这类才真的该被排除，但必须**报出来**，不许静默。
    unparsable: int = 0


def _like_values(sample: list[dict[str, Any]]) -> tuple[list[int], tuple[str, ...], int]:
    """→ (点赞整数列, 圆整值的 note_id 列表, 解析失败篇数)。"""
    values: list[int] = []
    approx: list[str] = []
    unparsable = 0
    for row in sample:
        parsed = to_count(row.get("likes"))
        if parsed is None:
            unparsable += 1
            continue
        values.append(parsed)
        if is_approx(row.get("likes")):
            approx.append(str(row.get("note_id") or "?"))
    return values, tuple(approx), unparsable


def _like_stats(values: list[int]) -> LikeStats:
    if not values:
        return LikeStats()
    ordered = sorted(values, reverse=True)
    anchor_index = min(len(ordered), max(1, round(0.2 * len(ordered)))) - 1
    return LikeStats(
        n=len(values),
        mean=round(statistics.mean(values), 1),
        median=statistics.median(values),
        maximum=ordered[0],
        minimum=ordered[-1],
        top20_anchor=ordered[anchor_index],
    )


def summarize(notes: list[dict[str, Any]], account_id: str = "", k_values: tuple[int, ...] = (3, 5)) -> SampleStats:
    """置顶篇单列不进样本统计（字典 §6：置顶是账号门面，不是常规产能）。

    `excluded` 只统计**缺来源**的行（没有采集日 = 五件套不成立）。同一篇的主页行 +
    详情行是两条观测，合并成一篇后**不算被排除**——早期版本把通道重复行计成
    excluded，会报出「因缺来源被排除 3 行」这种错口径。
    """
    scoped = [row for row in notes if not account_id or str(row.get("account_id")) == account_id]
    valid = [row for row in scoped if str(row.get("collected_on") or "").strip()]
    # 「能不能进样本」只看有没有来源，不看计数写法——`1.2万` 是可用的数据，
    # 早先它在这里被当成不可用而整行丢掉，样本量因此悄悄少一篇。
    usable = [row for row in latest_per_note(valid) if str(row.get("likes") or "").strip()]
    pinned = [row for row in usable if str(row.get("pinned") or "0") in ("1", "true", "True")]
    sample = [row for row in usable if row not in pinned]
    likes, approx_notes, unparsable = _like_values(sample)
    result = SampleStats(
        account_id=account_id,
        n_total=len(usable),
        n_pinned=len(pinned),
        n_sample=len(sample),
        likes=_like_stats(likes),
        excluded=len(scoped) - len(valid),
        approx_rows=len(approx_notes),
        approx_notes=approx_notes,
        unparsable=unparsable,
    )
    result.forms = _form_breakdown(sample)
    result.burst = _burst_rates(likes, result.likes.median, k_values)
    result.coverage = _coverage(sample)
    dates = sorted(str(row.get("published_at") or "") for row in sample if row.get("published_at"))
    if dates:
        result.window = (dates[0], dates[-1])
    return result


def _form_breakdown(sample: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[int]] = {}
    for row in sample:
        value = to_count(row.get("likes"))
        if value is not None:
            buckets.setdefault(str(row.get("form") or "未知"), []).append(value)
    return {
        form: {"n": len(values), "mean": round(statistics.mean(values), 1)} if values else {"n": 0, "mean": 0.0}
        for form, values in sorted(buckets.items())
    }


def _burst_rates(likes: list[int], median: float, k_values: tuple[int, ...]) -> dict[int, dict[str, Any]]:
    if not likes or not median:
        return {}
    rates: dict[int, dict[str, Any]] = {}
    for k in k_values:
        threshold = median * k
        hits = sum(1 for value in likes if value >= threshold)
        rates[k] = {
            "threshold": round(threshold, 1),
            "hits": hits,
            "rate": round(100.0 * hits / len(likes), 1),
            "definition": f"相对账号自身：单篇点赞 ≥ 中位数×{k}（民间框架 ③-a，K 值无共识）",
        }
    return rates


def _coverage(sample: list[dict[str, Any]]) -> dict[str, str]:
    total = len(sample)
    if not total:
        return {}
    labels = {"collects": "收藏", "comments": "评论", "shares": "分享"}
    return {
        labels[col]: f"{sum(1 for row in sample if str(row.get(col) or '').strip())}/{total}"
        for col in labels
    }
