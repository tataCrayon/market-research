"""采集闭环：采件箱 ingest、缺口清单 gaps。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.cli import main  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

BLOCK = """===AR-NOTE-v1===
note_id: n1
title: 一篇笔记
likes: 4566
collects: 1612
comments: 457
shares: ⬜
duration: 00:14
human_note: 视频 14 秒，画面 9/10 不可读
bar_text: 赞 4566 收藏 1612 评论 457
===END===
"""


def note_row(note_id: str, likes: int, **extra) -> dict:
    row = {
        "account_id": "acc1", "note_id": note_id, "published_at": "2026-10-01", "title": note_id,
        "form": "图文", "likes": likes, "pinned": "0", "source_file": "f.html",
        "source_kind": "公开页", "collected_on": "2026-10-22", "grade": "②",
    }
    row.update(extra)
    return row


class TestGaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "collected_on": "2026-10-22", "grade": "②"}])
        store.append_rows(self.data, "notes", [
            note_row("low", 50),
            note_row("high", 4000),
            note_row("done", 900, collects="20", comments="3", shares="1"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_gaps_sorted_by_likes_and_skips_complete(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["gaps", "--data", str(self.data)])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("缺藏/评的篇目：2 篇", output)
        self.assertLess(output.index("high"), output.index("low"))  # 高赞优先
        self.assertNotIn("note_id=done", output)


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.inbox = Path(self.tmp.name) / "inbox"
        self.inbox.mkdir()
        shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "主页.html")
        shutil.copy(FIXTURES / "minimal_search.html", self.inbox / "搜索页.html")
        (self.inbox / "详情.txt").write_text(BLOCK, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        return main(["ingest", str(self.inbox), "--on", "2026-10-22",
                     "--data", str(self.data), "--ledger", ""])

    def test_mixed_inbox_ingested_and_archived(self):
        self.assertEqual(self._run(), 0)
        accounts = store.read_table(self.data, "accounts")
        raw = store.read_table(self.data, "notes")
        merged = store.latest_per_note(raw)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["nickname"], "测试账号")
        # 原始行 3 = n1 主页 + n2 主页 + n1 详情（两条独立观测）；合并后 2 篇
        self.assertEqual(len(raw), 3)
        self.assertEqual(len(merged), 2)
        first = next(row for row in merged if row["note_id"] == "n1")
        self.assertEqual(first["collects"], "1612")           # 详情块补上的
        self.assertEqual(first["published_at"], "2025-10-21")  # 块里没给 → 保留主页的值，不覆盖成空
        archived = list((self.inbox / "processed" / "2026-10-22").iterdir())
        self.assertEqual({p.name for p in archived}, {"主页.html", "详情.txt"})
        self.assertTrue((self.inbox / "搜索页.html").is_file())  # 被拒的不归档，留着让人看

    def test_second_run_is_idempotent(self):
        self.assertEqual(self._run(), 0)
        # 归档后重新丢同一份进来，入库不应重复
        shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "主页2.html")
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(store.latest_per_note(store.read_table(self.data, "notes"))), 2)

    def test_empty_inbox_is_not_an_error(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(main(["ingest", str(empty), "--on", "2026-10-22",
                               "--data", str(self.data), "--ledger", ""]), 0)


if __name__ == "__main__":
    unittest.main()
