"""采集额度：按页面动作计数，且必须只读本地表。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.cli import main  # noqa: E402

DAY = "2026-10-28"


def account(account_id: str, on: str = DAY, kind: str = "公开页") -> dict:
    return {"account_id": account_id, "nickname": account_id, "collected_on": on,
            "source_kind": kind, "grade": "②"}


def note(note_id: str, on: str = DAY, kind: str = "公开页") -> dict:
    return {"account_id": "acc1", "note_id": note_id, "likes": 100, "pinned": "0",
            "collected_on": on, "source_kind": kind, "grade": "②", "title": "t", "form": "图文"}


class TestQuota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["quota", "--data", str(self.data), "--on", DAY, *extra])
        return code, buffer.getvalue()

    def test_profile_parse_counts_as_one_action_not_thirty(self):
        """一次主页解析产出 32 行笔记，但那只是一个页面动作——额度按动作算。"""
        store.append_rows(self.data, "accounts", [account("acc1")])
        store.append_rows(self.data, "notes", [note(f"n{i}") for i in range(32)])
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("主页：已采 1 / 3", output)
        self.assertIn("详情：已采 0 / 30", output)

    def test_detail_rows_consume_detail_quota(self):
        store.append_rows(self.data, "accounts", [account("acc1")])
        store.append_rows(self.data, "notes", [note("n1", kind="复制文本"), note("n2", kind="复制文本")])
        _, output = self._run()
        self.assertIn("详情：已采 2 / 30", output)

    def test_over_quota_exits_nonzero_and_says_stop(self):
        store.append_rows(self.data, "accounts", [account(f"a{i}") for i in range(4)])
        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("今天停手", output)

    def test_previous_day_does_not_consume_today(self):
        store.append_rows(self.data, "accounts", [account("old", on="2026-10-27")])
        _, output = self._run()
        self.assertIn("主页：已采 0 / 3", output)

    def test_quota_is_read_only(self):
        """额度是节流表不是执行器：跑完不得往库里写任何东西。"""
        store.append_rows(self.data, "accounts", [account("acc1")])
        before = {name: store.read_table(self.data, name) for name in ("accounts", "notes")}
        self._run()
        after = {name: store.read_table(self.data, name) for name in ("accounts", "notes")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
