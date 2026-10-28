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
    index: dict[str, str] = {}
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
            try:
                literal = clean_js_literals(extract_literal(html))
                _scan_state(json.loads(literal), index)
            except Exception:
                # 存档解析失败时退化为正则——它能容忍被脚本改写过的存档
                _scan_text(html, index)
            _scan_text(html, index)
    return index


def note_url(note_id: str, token: str = "") -> str:
    """拼链接。有 token 带 token，没有就退回裸链接（并预期它会 404）。"""
    base = f"https://www.xiaohongshu.com/explore/{note_id}"
    if not token:
        return base
    return f"{base}?xsec_token={token}&xsec_source=pc_user"
