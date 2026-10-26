"""主页存档 → accounts / notes 行。

口径纪律：列表页（SSR 主页）只有「标题 / 发布时间 / 点赞 / 形式 / 置顶」五个字段。
收藏、评论、分享在列表页结构里根本不存在 —— 一律留空计为 ⬜，禁止估算填充。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import schema
from .initialstate import Archive, iter_note_cards

_TZ = timezone(timedelta(hours=schema.TZ_OFFSET_HOURS))

FORM_BY_TYPE = {"normal": "图文", "video": "视频"}


def ms_to_date(value: Any) -> str:
    """毫秒时间戳 → 北京时间日期。不依赖机器时区，避免跨机器解析结果漂移。"""
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000, _TZ).strftime("%Y-%m-%d")


def as_int(value: Any) -> int | None:
    """互动数在存档里是字符串。取值失败返回 None（= ⬜），不做任何补齐。"""
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def form_of(card: dict[str, Any]) -> str:
    return FORM_BY_TYPE.get(str(card.get("type") or ""), "未知")


def _interaction_counts(page_data: dict[str, Any]) -> dict[str, int | None]:
    """interactions 里的 type 是平台自己的命名：fans / follows / **interaction**（单数）。

    映射写成显式字典，别靠字段名猜——`interaction` → `interactions` 这一处踩过。
    """
    counts: dict[str, int | None] = {"fans": None, "follows": None, "interactions": None}
    alias = {"fans": "fans", "follows": "follows", "interaction": "interactions"}
    for entry in page_data.get("interactions") or []:
        target = alias.get(str((entry or {}).get("type") or ""))
        if target:
            counts[target] = as_int((entry or {}).get("count"))
    return counts


def primary_target_id(archive: Archive) -> str:
    """一页里出现多个作者 id = 存档混了别人的笔记。取出现最多的那个作为目标账号。"""
    owners = Counter(
        str((card.get("user") or {}).get("userId") or "")
        for card in iter_note_cards(archive.state)
    )
    owners.pop("", None)
    if not owners:
        return archive.target_user_ids[0] if archive.target_user_ids else ""
    return owners.most_common(1)[0][0]


def parse_account(archive: Archive, collected_on: str) -> dict[str, Any]:
    page_data = ((archive.state.get("user") or {}).get("userPageData") or {})
    info = page_data.get("basicInfo") or {}
    counts = _interaction_counts(page_data)
    return {
        "account_id": primary_target_id(archive),
        "nickname": str(info.get("nickname") or ""),
        "red_id": str(info.get("redId") or ""),
        "desc": str(info.get("desc") or "").replace("\n", " ").strip(),
        "ip_location": str(info.get("ipLocation") or ""),
        "fans": counts["fans"],
        "follows": counts["follows"],
        "interactions": counts["interactions"],
        "liked": as_int(page_data.get("liked")),
        "collected": as_int(page_data.get("collected")),
        "posted": as_int(page_data.get("posted")),
        "platform": schema.PLATFORM,
        "source_file": archive.path.name,
        "source_kind": "公开页",
        "collected_on": collected_on,
        "grade": "②",
        "note": "userPageData（主页 SSR 存档）",
    }


def parse_notes(archive: Archive, account_id: str, collected_on: str) -> tuple[list[dict[str, Any]], list[str]]:
    """返回 (行, 丢弃原因)。归属不匹配的卡片一律丢弃并留证据，不做静默过滤。"""
    rows: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for card in iter_note_cards(archive.state):
        owner = str((card.get("user") or {}).get("userId") or "")
        if owner != account_id:
            dropped.append(f"{card.get('noteId') or '?'}: 作者 {owner or '未知'} ≠ 目标 {account_id}")
            continue
        note_id = str(card.get("noteId") or "")
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        info = card.get("interactInfo") or {}
        rows.append({
            "account_id": account_id,
            "note_id": note_id,
            "published_at": ms_to_date(card.get("time")),
            "title": str(card.get("displayTitle") or "").strip(),
            "form": form_of(card),
            "likes": as_int(info.get("likedCount")),
            # 列表页不存在这三个字段 → 保持空 = ⬜，等详情页/复制文本补
            "collects": None,
            "comments": None,
            "shares": None,
            "pinned": "1" if info.get("sticky") else "0",
            "topic": "",
            "source_file": archive.path.name,
            "source_kind": "公开页",
            "collected_on": collected_on,
            "grade": "②",
        })
    return rows, dropped
