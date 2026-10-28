"""占位账号被真实主页替换：append-only 的唯一例外，必须有测试守着。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402

ACC = "686011fb000000001b023a74"
DAY = "2026-10-28"


def placeholder() -> dict:
    return {
        "account_id": ACC, "nickname": "知新的AI笔记", "platform": "xiaohongshu",
        "source_kind": "复制文本", "collected_on": DAY, "grade": "②",
        "note": "由详情页/采集块自动建号；**缺主页存档**，粉丝/简介等为 ⬜",
    }


def real_profile() -> dict:
    return {
        "account_id": ACC, "nickname": "知新的AI笔记", "red_id": "49626736847",
        "desc": "聚焦 AI 大厂动态", "ip_location": "江苏", "fans": 16821, "follows": 59,
        "interactions": 312910, "liked": 241438, "collected": 71472, "posted": 321,
        "platform": "xiaohongshu", "source_file": "知新的AI笔记 - 小红书.html",
        "source_kind": "公开页", "collected_on": DAY, "grade": "②",
        "note": "userPageData（主页 SSR 存档）",
    }


class TestPlaceholderUpgrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_placeholder_is_detected(self):
        self.assertTrue(store.is_placeholder_account(placeholder()))
        self.assertFalse(store.is_placeholder_account(real_profile()))

    def test_same_day_profile_replaces_placeholder(self):
        """真实踩到的 bug：占位行占了 (account_id, 当日) 这个键，主页数据被静默跳过。"""
        store.append_rows(self.data, "accounts", [placeholder()])
        upgraded, replaced = store.upgrade_placeholders(
            store.read_table(self.data, "accounts"), "accounts", [real_profile()]
        )
        self.assertEqual(replaced, 1)
        self.assertEqual(upgraded[0]["fans"], 16821)

    def test_order_independent_within_batch(self):
        """同一次提交里先建占位再给主页，也必须替换。"""
        upgraded, replaced = store.upgrade_placeholders([], "accounts", [placeholder(), real_profile()])
        self.assertEqual(replaced, 0)  # 空表时没有旧行可替换
        self.assertEqual(len(upgraded), 0)

    def test_real_row_never_replaced_by_placeholder(self):
        upgraded, replaced = store.upgrade_placeholders(
            [real_profile()], "accounts", [placeholder()]
        )
        self.assertEqual(replaced, 0)
        self.assertEqual(upgraded[0]["fans"], 16821)

    def test_real_row_not_overwritten_by_another_real_row(self):
        """两行都是真实主页时不动——那是不同时点的观测，不是覆盖关系。"""
        other = dict(real_profile(), fans=20000, collected_on=DAY)
        upgraded, replaced = store.upgrade_placeholders([real_profile()], "accounts", [other])
        self.assertEqual(replaced, 0)
        self.assertEqual(upgraded[0]["fans"], 16821)

    def test_notes_table_is_never_upgraded(self):
        """其它表一律不动——那是证据，不能被改写。"""
        note = {"account_id": ACC, "note_id": "n1", "note": "缺主页存档", "likes": "1"}
        upgraded, replaced = store.upgrade_placeholders([note], "notes", [dict(note, likes="999")])
        self.assertEqual(replaced, 0)
        self.assertEqual(upgraded[0]["likes"], "1")


if __name__ == "__main__":
    unittest.main()
