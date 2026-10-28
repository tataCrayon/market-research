"""生成「爆款优先」综合看板：把跨账号的高价值采集目标拼成一页。

为什么需要它：`ar links` 是按账号出的清单。但「先采哪几篇」的判断是跨账号的——
知新那篇 4512 的爆款，比卡兹克第 20 篇更值得先采。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.board import render_board  # noqa: E402
from account_research.tokens import build_index as build_token_index  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
NOTE_URL = "https://www.xiaohongshu.com/explore/{note_id}"

# 优先级：跨账号挑「量级最极端」的篇目，而不是按账号分组
PRIORITY = [
    # (account 昵称, note_id 或 None, 说明)
    ("知新的AI笔记", "68ccee2c000000001400bb08", "最高赞 4512（置顶·13个月前）"),
    ("知新的AI笔记", "6addb1a6000000002403a5fa", "次高 3085（近期·同一母题）"),
    ("知新的AI笔记", "68c965bd000000001300d9d0", "509（置顶·与 4512 同母题）"),
    ("数字生命卡兹克", "6ac719230000000014039dd8", "4566（已入库，验算用）"),
]


def main() -> int:
    notes = store.read_table(DATA, "notes")
    accounts = {r["account_id"]: r for r in store.read_table(DATA, "accounts")}
    merged = {str(r["note_id"]): r for r in store.latest_per_note(notes)}

    picked = []
    for nickname, note_id, why in PRIORITY:
        row = merged.get(note_id)
        if row is None:
            continue
        picked.append((row, False))
        print(f"✓ {nickname} | {row.get('likes')} 赞 | {row.get('title')} | {why}")

    print(f"\n共 {len(picked)} 篇进入爆款优先清单")
    tokens = build_token_index([DATA.parents[1] / "account"])
    hit = sum(1 for row, _ in picked if tokens.get(str(row.get("note_id") or "")))
    html = render_board(picked, NOTE_URL, "跨账号 · 爆款优先", tokens=tokens)
    target = DATA / "todo-爆款优先.html"
    target.write_text(html, encoding="utf-8")
    print(f"看板已写入 {target}（带 token {hit}/{len(picked)}）")

    # 顺带把 FuzzyPop 主页地址也提示出来（主页不走 note 链接）
    placeholder = [a for a in accounts.values() if store.is_placeholder_account(a)]
    if placeholder:
        print("\n仍需补主页的账号：")
        for a in placeholder:
            print(f"  {a.get('nickname') or '(未知)'} → "
                  f"https://www.xiaohongshu.com/user/profile/{a['account_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
