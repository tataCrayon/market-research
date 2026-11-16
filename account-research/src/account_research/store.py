"""CSV 表读写。只追加：同键已存在即跳过，永不覆盖历史行。

为什么用 CSV 而不是数据库：数据不出本机、可 diff、可手改；台账本身就是文件资产。
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from . import schema


def table_path(data_dir: str | Path, name: str) -> Path:
    return Path(data_dir) / f"{name}.csv"


def read_table(data_dir: str | Path, name: str) -> list[dict[str, Any]]:
    path = table_path(data_dir, name)
    if not path.is_file():
        return []
    columns = schema.TABLE_COLUMNS[name]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{col: (row.get(col) or "") for col in columns} for row in reader]


def _row_key(name: str, row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(col) or "") for col in schema.TABLE_KEYS[name])


def _write_atomic(path: Path, name: str, rows: Iterable[dict[str, Any]]) -> None:
    columns = schema.TABLE_COLUMNS[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", delete=False, dir=str(path.parent), suffix=".tmp"
    )
    try:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in columns})
    finally:
        handle.close()
    os.replace(handle.name, path)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


PLACEHOLDER_MARK = "缺主页存档"


def is_placeholder_account(row: dict[str, Any]) -> bool:
    """占位账号 = 由详情页/采集块顺手建出来的账号行，**没有任何主页字段**。

    为什么需要这个概念：`accounts` 的键是 (account_id, collected_on)。同一天先建了占位行，
    当天再采到真实主页存档时键就撞了 → 真实数据被静默跳过。
    实测踩到过：知新的AI笔记的主页存档解析出粉丝 16821，但因为没有新键，一行都没写进去。

    占位行不是证据，是 TODO 标记，所以允许被真实主页行**整行替换**——
    这是 append-only 的唯一例外，理由：留着一个空壳行会永久挡住真数据。
    """
    return PLACEHOLDER_MARK in str(row.get("note") or "")


def _filled_count(row: dict[str, Any], columns: Iterable[str]) -> int:
    return sum(1 for col in columns if str(row.get(col) or "").strip())


def upgrade_placeholders(
    existing: list[dict[str, Any]], name: str, rows: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """用真实主页行替换同键的占位账号行。返回 (新表, 替换数)。

    只对 `accounts` 生效，且只在「旧行是占位、新行不是、且新行信息更多」时替换。
    其它表一律不动——那是证据，不能被改写。
    """
    if name != "accounts":
        return existing, 0
    columns = schema.TABLE_COLUMNS["accounts"]
    index_by_key = {_row_key(name, row): index for index, row in enumerate(existing)}
    upgraded = list(existing)
    replaced = 0
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = _row_key(name, row)
        if key in seen or key not in index_by_key:
            continue
        index = index_by_key[key]
        current = upgraded[index]
        if is_placeholder_account(current) and not is_placeholder_account(row):
            if _filled_count(row, columns) > _filled_count(current, columns):
                upgraded[index] = row
                seen.add(key)
                replaced += 1
    return upgraded, replaced


def preview_append(existing: list[dict[str, Any]], name: str, rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """纯函数：算出「哪些行会新增」，不落盘。

    存在的理由：`ar add` 必须先校验候选状态、再写文件。先写后校验的话，
    一次 V 码拒收就把脏行永久留在 CSV 里（append-only 表没有回滚）。
    """
    known = {_row_key(name, row) for row in existing}
    fresh: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        key = _row_key(name, row)
        if key in known:
            skipped += 1
            continue
        known.add(key)
        fresh.append(row)
    return fresh, skipped


def append_rows(data_dir: str | Path, name: str, rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """返回 (新增行数, 跳过行数)。已存在的键不覆盖——历史行是证据。"""
    existing = read_table(data_dir, name)
    fresh, skipped = preview_append(existing, name, rows)
    if fresh:
        _write_atomic(table_path(data_dir, name), name, existing + fresh)
    return len(fresh), skipped


def replace_table(data_dir: str | Path, name: str, rows: Iterable[dict[str, Any]]) -> None:
    """仅用于口径登记表这类「声明式」表；业务数据一律走 append_rows。"""
    _write_atomic(table_path(data_dir, name), name, rows)


def seed_fields(data_dir: str | Path, checked_on: str) -> int:
    """口径登记表按指标字典初始化；已存在则不动（口径变更靠 append 新行）。"""
    if table_path(data_dir, "fields").is_file():
        return 0
    rows = [{**entry, "checked_on": checked_on} for entry in schema.METRIC_REGISTRY]
    replace_table(data_dir, "fields", rows)
    return len(rows)


# 同一 note 有多个观测时，字段级的覆盖优先级：详情页（复制文本/截图）比主页列表更完整
SOURCE_RANK = {"公开页": 1, "复制文本": 2, "截图": 2, "第三方工具": 0}


def latest_per_note(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个 note 合成一行：按 (采集日, 通道优先级) 升序折叠，非空字段由后到的覆盖。

    为什么是「合并」而不是「取最后一行」：主页存档给标题/时间/赞，详情页只补藏/评/时长。
    若只取最后一行，补完详情反而会丢掉主页才有的字段（标题时间还在，但视频 3 篇的
    形式分布会被空值覆盖）。字段级合并让两条通道各自贡献自己有的部分。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        note_id = str(row.get("note_id") or "")
        if note_id:
            grouped.setdefault(note_id, []).append(row)
    merged: list[dict[str, Any]] = []
    for note_id, group in grouped.items():
        ordered = sorted(
            group,
            key=lambda row: (str(row.get("collected_on") or ""), SOURCE_RANK.get(str(row.get("source_kind") or ""), 0)),
        )
        accumulator: dict[str, Any] = {}
        for row in ordered:
            for field, value in row.items():
                if str(value or "").strip():
                    accumulator[field] = value
        accumulator["note_id"] = note_id
        merged.append(accumulator)
    return merged


def latest_hypotheses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每条假设只留**最新一次记录**：按 `recorded_on` 升序折叠，后到的覆盖。

    为什么需要：假设表原来一条假设终生一行，于是到期后**无法结案**
    （键是 `hypothesis_id`，再写一行会被 `preview_append` 静默跳过）。2026-11-12 起
    `recorded_on` 进键，结案 = 追加一行新状态——历史状态留在表里（那是证据），
    但**判断「现在是什么状态」必须只看最新一行**。

    不折叠的后果很具体：`H-KZK-01` 会同时存在「未验证」与「超期未验」两行，
    D2 告警会拿旧行去报逾期——**结案了却还在响**，比不结案更让人不信任告警。

    列序稳定：同一时刻（同一天多次记录）后写的那行赢，靠 CSV 行序保证。
    """
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for position, row in enumerate(rows):
        key = str(row.get("hypothesis_id") or "").strip()
        if key:
            grouped.setdefault(key, []).append((position, row))
    latest: list[dict[str, Any]] = []
    for key, group in grouped.items():
        # 先按 recorded_on，再按原始行序（同日多次记录取最后写入的那行）
        _, winner = max(group, key=lambda pair: (str(pair[1].get("recorded_on") or ""), pair[0]))
        merged = dict(winner)
        merged["hypothesis_id"] = key
        latest.append(merged)
    return latest
