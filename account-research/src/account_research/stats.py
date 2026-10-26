"""确定性统计：篇均 / 中位 / 分形式 / 爆文率。

爆文率是个口径坑（字典 §4：「爆文」至少三种定义，同一样本数字不同）。
所以这里强制把 K 值和定义随结果一起打印，禁止只给一个百分比。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

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


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "")) if str(value).strip() else None
    except (TypeError, ValueError):
        return None


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
    """置顶篇单列不进样本统计（字典 §6：置顶是账号门面，不是常规产能）。"""
    scoped = [row for row in notes if not account_id or str(row.get("account_id")) == account_id]
    usable = [
        row for row in latest_per_note(scoped)
        if str(row.get("collected_on") or "").strip() and _int_or_none(row.get("likes")) is not None
    ]
    pinned = [row for row in usable if str(row.get("pinned") or "0") in ("1", "true", "True")]
    sample = [row for row in usable if row not in pinned]
    likes = [_int_or_none(row.get("likes")) or 0 for row in sample]
    result = SampleStats(
        account_id=account_id,
        n_total=len(usable),
        n_pinned=len(pinned),
        n_sample=len(sample),
        likes=_like_stats(likes),
        excluded=len(scoped) - len(usable),
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
        buckets.setdefault(str(row.get("form") or "未知"), []).append(_int_or_none(row.get("likes")) or 0)
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
