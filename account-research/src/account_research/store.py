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
