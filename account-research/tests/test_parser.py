"""存档解析与页面判别。用合成 fixture（不含任何采集者会话数据），可在任何机器上跑。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.initialstate import (  # noqa: E402
    ArchiveError, clean_js_literals, classify, open_archive, parse_html,
)
from account_research.parser import parse_account, parse_notes  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestCleanJsLiterals(unittest.TestCase):
    def test_undefined_value_replaced(self):
        self.assertEqual(clean_js_literals('{"a":undefined}'), '{"a":null}')

    def test_undefined_inside_string_preserved(self):
        text = '{"a":"请写 :undefined 看看"}'
        self.assertEqual(clean_js_literals(text), text)

    def test_identifier_containing_token_not_touched(self):
        text = '{"undefinedFlag":1}'
        self.assertEqual(clean_js_literals(text), text)


class TestClassify(unittest.TestCase):
    def test_profile_detected_by_page_data(self):
        self.assertEqual(open_archive(FIXTURES / "minimal_profile.html").kind, "profile")

    def test_search_detected_by_title(self):
        self.assertEqual(open_archive(FIXTURES / "minimal_search.html").kind, "search_page")

    def test_skeleton_detected_when_nothing_rendered(self):
        self.assertEqual(open_archive(FIXTURES / "minimal_skeleton.html").kind, "empty_skeleton")

    def test_detail_detected_by_detail_map(self):
        state = parse_html('<title>t</title>__INITIAL_STATE__={"note":{"noteDetailMap":{"k":1}}}')
        self.assertEqual(classify(state), "note_detail")

    def test_missing_marker_raises_archive_error(self):
        with self.assertRaises(ArchiveError):
            parse_html("<html><body>没有内嵌状态</body></html>")


class TestParseProfile(unittest.TestCase):
    def setUp(self):
        self.archive = open_archive(FIXTURES / "minimal_profile.html")
        self.account = parse_account(self.archive, "2026-10-22")

    def test_account_fields_extracted(self):
        self.assertEqual(self.account["nickname"], "测试账号")
        self.assertEqual(self.account["red_id"], "test001")
        self.assertEqual(self.account["fans"], 1000)
        self.assertEqual(self.account["follows"], 20)
        self.assertEqual(self.account["interactions"], 5000)
        self.assertEqual(self.account["grade"], "②")

    def test_viewer_data_never_becomes_account(self):
        """采集者自己的 userInfo 不得污染账号行（case-001 踩过的坑）。"""
        self.assertNotEqual(self.account["nickname"], "采集者")
        self.assertEqual(self.account["account_id"], "target1")
        self.assertEqual(self.archive.viewer_user_id, "viewer1")

    def test_foreign_notes_dropped_with_reason(self):
        rows, dropped = parse_notes(self.archive, self.account["account_id"], "2026-10-22")
        self.assertEqual({row["note_id"] for row in rows}, {"n1", "n2"})
        self.assertEqual(len(dropped), 1)
        self.assertIn("other9", dropped[0])

    def test_pinned_and_form_from_card(self):
        rows, _ = parse_notes(self.archive, self.account["account_id"], "2026-10-22")
        by_id = {row["note_id"]: row for row in rows}
        self.assertEqual(by_id["n1"]["pinned"], "1")
        self.assertEqual(by_id["n2"]["pinned"], "0")
        self.assertEqual(by_id["n1"]["form"], "图文")
        self.assertEqual(by_id["n2"]["form"], "视频")

    def test_list_page_never_invents_collect_comments_shares(self):
        rows, _ = parse_notes(self.archive, self.account["account_id"], "2026-10-22")
        for row in rows:
            self.assertIsNone(row["collects"])
            self.assertIsNone(row["comments"])
            self.assertIsNone(row["shares"])

    def test_published_at_is_beijing_date(self):
        rows, _ = parse_notes(self.archive, self.account["account_id"], "2026-10-22")
        self.assertRegex(rows[0]["published_at"], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
