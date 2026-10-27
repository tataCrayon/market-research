"""notes.csv 里长字段会撑爆表格（正文、笔记摘要）。长文本一律落文件，表里只留引用。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTES = REPO_ROOT / "account-research" / "data" / "notes.csv"
TEXT_DIR = REPO_ROOT / "account" / "notes-text"

HARD_LIMIT = 120       # 超过就报警
WARN_LIMIT = 60


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    rows = store.read_table(NOTES.parent, "notes")
    if not rows:
        print("notes 表为空。")
        return 0
    print(f"行数 {len(rows)}；列数 {len(rows[0])}")
    print()
    print(f"{'列名':<14}{'最长值':>8}{'非空行数':>10}  警示")
    problems = 0
    for column in rows[0]:
        longest = max((len(str(row.get(column) or "")) for row in rows), default=0)
        filled = sum(1 for row in rows if str(row.get(column) or "").strip())
        flag = ""
        if longest > HARD_LIMIT:
            flag = "❌ 过长，应改为文件引用"
            problems += 1
        elif longest > WARN_LIMIT:
            flag = "⚠️ 偏长"
        print(f"{column:<14}{longest:>8}{filled:>10}  {flag}")
    print()
    print(f"正文归档目录：{TEXT_DIR}（存在 {len(list(TEXT_DIR.glob('*.txt'))) if TEXT_DIR.is_dir() else 0} 个 txt）")
    if problems:
        print(f"\n{problems} 列超限：长文本不得进 CSV。")
        return 1
    print("\n✅ 无超长列，表格可读。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
