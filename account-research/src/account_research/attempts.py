"""每条缺口「上次是怎么试的」——把「试过但拿不到」和「从没试过」分开。

## 为什么需要它

2026-11-04 发现一件很贵的事：三份文档里写着「评论内容一条都没采」，
而实际上 11 篇存档里 95 条评论原文一直都在 `<div class="content"><span class="note-text">`。
错误的来源只有一次 `grep comment-item` —— **一次失败的尝试被固化成了「物理上拿不到」**。

一个长期存在的「拿不到」结论，更可能意味着**没人再试过**，而不是真的拿不到。
所以缺口清单不能只写「缺什么」，还要写「上次谁、什么时候、用什么方法试过」——
有这一句，人才能判断：是平台不给，还是我该再试一次。

## 判据

- 只认**详情页通道**的行（`复制文本` / `截图` / `后台导出` / `第三方工具`）。
  主页存档（`公开页`）根本不含藏/评/分享，它出现不代表「试过取这三个数」。
- 「试过」的证据是**那一行自己的字段值**，不是合并后的值：
  合并会把别的通道填进去的数混进来，看不出这次尝试到底拿到了什么。
- 超过 `RETRY_AFTER_DAYS` 天没再试的，标「可重试」——平台会改版，
  一个月前的结论不足以代表今天。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from . import store

# 详情页通道：只有这几类来源可能带藏/评/分享。主页存档一律不算「尝试」。
DETAIL_KINDS = ("复制文本", "截图", "后台导出", "第三方工具")

# 多久没再试就该重试。写死并随提示打印，免得读者猜这个数从哪来。
RETRY_AFTER_DAYS = 30

GAP_FIELDS = ("collects", "comments", "shares")
LABELS = {"collects": "藏", "comments": "评", "shares": "分享"}

# 给人看的方法名。`复制文本` 在这个项目里实际就是「保存详情页 HTML」，
# 直接写类名会让人以为是手工抄的。
METHOD_LABELS = {
    "复制文本": "详情页存档（Ctrl+S 保存的 HTML）",
    "截图": "截图",
    "后台导出": "后台导出",
    "第三方工具": "第三方工具",
}


@dataclass(frozen=True)
class Attempt:
    """一次详情页尝试的记录。字段值取自**那一行**，不是合并后的。"""

    collected_on: str
    source_kind: str
    still_missing: tuple[str, ...]     # 那次尝试之后仍空着的字段

    @property
    def method(self) -> str:
        return METHOD_LABELS.get(self.source_kind, self.source_kind)

    def age_days(self, today: str) -> int | None:
        return _days_between(self.collected_on, today)


def _days_between(start: str, end: str) -> int | None:
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return None


def attempts_by_note(
    rows: Iterable[dict[str, Any]], fields: tuple[str, ...] = GAP_FIELDS
) -> dict[str, Attempt]:
    """每个 note 的**最后一次**详情页尝试。

    同一天有多次尝试时按通道优先级取最后一次（详情页比第三方工具可信）。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        kind = str(row.get("source_kind") or "")
        note_id = str(row.get("note_id") or "")
        if kind in DETAIL_KINDS and note_id:
            grouped.setdefault(note_id, []).append(row)

    found: dict[str, Attempt] = {}
    for note_id, group in grouped.items():
        ordered = sorted(
            group,
            key=lambda row: (
                str(row.get("collected_on") or ""),
                store.SOURCE_RANK.get(str(row.get("source_kind") or ""), 0),
            ),
        )
        latest = ordered[-1]
        found[note_id] = Attempt(
            collected_on=str(latest.get("collected_on") or ""),
            source_kind=str(latest.get("source_kind") or ""),
            still_missing=tuple(
                field for field in fields if not str(latest.get(field) or "").strip()
            ),
        )
    return found


def missing_columns(row: dict[str, Any], fields: tuple[str, ...] = GAP_FIELDS) -> tuple[str, ...]:
    """当前这一行还缺哪几个**列名**。

    ⚠️ 别用 `cli._missing_fields`：那个返回的是给人看的标签（`藏/评`），
    和这里 `still_missing` 的列名（`collects/comments`）不是一套词汇。
    混用会让「当时也没拿到」这段判断**永远不成立**，症状是提示静默变短——
    单测抓过一次，别改回去。
    """
    return tuple(
        field for field in fields
        if str(row.get(field) or "").strip() in ("", "⬜")
    )


def describe(
    attempt: Attempt | None,
    missing: Iterable[str],
    today: str,
    retry_days: int = RETRY_AFTER_DAYS,
) -> str:
    """一句话说清这条缺口的历史。给 `ar gaps` 的每一行用。

    `missing` 必须是**列名**（`collects` 这类），不是标签。
    """
    missing = tuple(missing)
    if attempt is None:
        return (
            "上次尝试：**从未用详情页试过**——只从主页存档知道有这篇"
            "（主页结构里根本不含这些字段，所以这不算试过）"
        )

    age = attempt.age_days(today)
    age_text = f"已 {age} 天" if age is not None and age >= 0 else "日期不明"
    covered = [field for field in missing if field in attempt.still_missing]
    # 当前缺、但那次尝试**读到过**：说明不是平台不给，是数据没进表——这是 bug，不是缺口。
    lost = [field for field in missing if field not in attempt.still_missing]

    if covered:
        names = "/".join(LABELS.get(field, field) for field in covered)
        tail = f"（{age_text}"
        tail += f"，超过 {retry_days} 天，平台可能已改版，值得再试一次）" if (
            age is not None and age >= retry_days
        ) else "）"
        text = (
            f"上次尝试：{attempt.collected_on} {attempt.method}"
            f" → 当时 {names} 也没拿到{tail}"
        )
    else:
        text = f"上次尝试：{attempt.collected_on} {attempt.method}"

    if lost:
        names = "/".join(LABELS.get(field, field) for field in lost)
        text += (
            f"\n    ⚠️ {names} 在那次尝试里**读到过**，当前却为空——"
            f"这不是缺口，是数据没合并进表（先查 `ar validate` 与原始行）"
        )
    return text


def summarize(attempts: dict[str, Attempt], missing_by_note: dict[str, tuple[str, ...]]) -> str:
    """缺口总量的一句话概括：多少从没试过、多少试过没拿到。"""
    never = sum(1 for note_id in missing_by_note if note_id not in attempts)
    tried = len(missing_by_note) - never
    return f"其中 {never} 篇**从未用详情页试过**，{tried} 篇试过但仍没拿到"
