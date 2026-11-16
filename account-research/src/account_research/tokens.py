"""从**已保存的存档**里取出每篇笔记的 `xsec_token`，用来拼出能打开的链接。

## 为什么需要它（2026-10-28 实测）

小红书现在对裸 `/explore/<note_id>` 链接返回 404 页：
`error_code=300031 当前笔记暂时无法浏览`，URL 里带 `redirectPath` 指回原笔记。
也就是说**光有 note_id 已经打不开页面了**，必须带 `xsec_token`。

## token 从哪来：你自己的存档里本来就有

主页 SSR 存档的每个 noteCard 都自带 `xsecToken`：

    {"noteId":"6ac719...","noteCard":{...,"xsecToken":"ABJV9a..."}, "xsecToken":"..."}

所以这不是"绕过限制去拿凭证"，而是**读回平台自己发给你、已经存在你硬盘上的那个访问参数**，
把它拼进链接。**零新增请求**——它只是让"点开一个你本来就该能看的页面"重新工作。

## 边界

- 只读 `account/` 下的本地存档，不发任何请求
- 只索引 `note_id → token` 这一对；不读 cookie、不读 `user.userInfo` 那类会话数据
- 找不到 token 时返回空串，调用方退回裸链接（并明确提示"这篇没有 token，可能打不开"）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .initialstate import MARKER, clean_js_literals, extract_literal

NOTE_ID_RE = re.compile(r'"noteId"\s*:\s*"([0-9a-f]{24})"')
TOKEN_AFTER_ID_RE = re.compile(r'"xsecToken"\s*:\s*"([^"]{16,})"')
# 一份存档里 noteCard 可能很长，token 紧跟在同一对象内；限定搜索窗口避免跨卡片串号
WINDOW = 6000


def _scan_text(text: str, into: dict[str, str]) -> None:
    """在一段 JSON/HTML 文本里，把 (noteId, 紧随其后的 xsecToken) 收进索引。"""
    for match in NOTE_ID_RE.finditer(text):
        note_id = match.group(1)
        if note_id in into:
            continue
        window = text[match.start(): match.start() + WINDOW]
        token = TOKEN_AFTER_ID_RE.search(window)
        if token:
            into[note_id] = token.group(1)


def _scan_state(state: object, into: dict[str, str]) -> None:
    """结构化遍历 __INITIAL_STATE__，比正则更准（卡片边界天然明确）。"""
    if isinstance(state, dict):
        note_id = state.get("id") or state.get("noteId")
        token = state.get("xsecToken")
        if isinstance(note_id, str) and NOTE_ID_RE.fullmatch(f'"noteId":"{note_id}"') and isinstance(token, str) and token:
            into.setdefault(note_id, token)
        card = state.get("noteCard")
        if isinstance(card, dict):
            card_id = card.get("noteId")
            card_token = card.get("xsecToken")
            if isinstance(card_id, str) and isinstance(card_token, str) and card_token:
                into.setdefault(card_id, card_token)
        for value in state.values():
            _scan_state(value, into)
    elif isinstance(state, list):
        for item in state:
            _scan_state(item, into)


def build_index(roots: Iterable[str | Path]) -> dict[str, str]:
    """扫一批 HTML 存档，返回 {note_id: xsec_token}。坏的存档跳过，不影响其它。"""
    return {note_id: entry.token for note_id, entry in build_index_with_source(roots).items()}


@dataclass(frozen=True)
class TokenEntry:
    """一个 token 以及它**从哪份存档、哪一天**读出来的。

    为什么必须记来源（2026-11-12 实测）：清单里给出去的链接有一半打不开，
    而当时的输出只有「带 token 54/56」——**它不区分「token 有效」和「token 存在」**。
    人拿着 404 回来时，唯一能查的就是「这个 token 是哪份存档里的」，
    而旧实现连这个都答不了，只能重扫一遍全库。
    """

    token: str
    source_file: str
    collected_on: str

    @property
    def age_note(self) -> str:
        return f"{self.source_file}（采集于 {self.collected_on}）" if self.collected_on else self.source_file


# token 是从**某一份存档**里读出来的，而存档是「某一天那一刻」的快照。
# 平台按会话/时间发 token，隔一天大概率失效——所以链接能不能打开，
# 与「token 存在」是两件事。这个阈值只用于**提示**，不做任何过滤：
# 过期的 token 有时仍能打开（实测过），删掉它等于白白少一条可试的路径。
TOKEN_STALE_DAYS = 3


def build_index_with_source(roots: Iterable[str | Path]) -> dict[str, TokenEntry]:
    """同 `build_index`，但保留每个 token 的来源档案与采集日。"""
    index: dict[str, TokenEntry] = {}
    for root in roots:
        path = Path(root)
        files = [path] if path.is_file() else sorted(path.rglob("*.html")) if path.is_dir() else []
        for file in files:
            try:
                html = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if MARKER not in html:
                continue
            # 采集日从文件名里取（`卡兹克-主页-20261022.html`）。
            # 取不到就留空，**不用文件修改时间兜底**：复制/解压过的文件 mtime 是今天，
            # 那会让一个过期 token 看起来「今天刚采」——宁可说不知道。
            # 空日期在 `token_age_days` 里返回 None，调用方只报「带 token N/M」，不报新鲜度。
            collected_on = _date_from_name(file.name)
            partial: dict[str, str] = {}
            try:
                literal = clean_js_literals(extract_literal(html))
                _scan_state(json.loads(literal), partial)
            except Exception:
                # 存档解析失败时退化为正则——它能容忍被脚本改写过的存档
                _scan_text(html, partial)
            _scan_text(html, partial)
            for note_id, token in partial.items():
                if note_id not in index:
                    index[note_id] = TokenEntry(
                        token=token,
                        source_file=file.name,
                        collected_on=collected_on,
                    )
    return index


def _date_from_name(name: str) -> str:
    """`…-20261022.html` → `2026-10-22`。取不到返回空串，**不猜**。"""
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def token_age_days(collected_on: str, today: str) -> int | None:
    """token 距今几天。任一日期不可解析返回 None——**不猜**。"""
    from datetime import date

    try:
        return (date.fromisoformat(today) - date.fromisoformat(collected_on)).days
    except (TypeError, ValueError):
        return None


def note_url(note_id: str, token: str = "") -> str:
    """拼链接。有 token 带 token，没有就退回裸链接（并预期它会 404）。"""
    base = f"https://www.xiaohongshu.com/explore/{note_id}"
    if not token:
        return base
    return f"{base}?xsec_token={token}&xsec_source=pc_user"
