"""详情页 HTML 通道：og:url 判别、互动栏切片、正文归档。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.cli import main  # noqa: E402
from account_research.detail import extract_detail, interact_bar_slice, looks_like_detail_html  # noqa: E402
from account_research.initialstate import open_archive  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestDetailExtraction(unittest.TestCase):
    def setUp(self):
        self.html = (FIXTURES / "minimal_detail.html").read_text(encoding="utf-8")
        self.fields = extract_detail(self.html)

    def test_og_url_identifies_note(self):
        self.assertEqual(self.fields.note_id, "6adc2f910000000024039fde")
        self.assertTrue(looks_like_detail_html(self.html))

    def test_counts_taken_from_interact_bar_not_background_feed(self):
        """真实页面的坑：背景推荐流里有几十个 like-wrapper，必须先切交互栏。"""
        self.assertEqual(self.fields.likes, "697")
        self.assertEqual(self.fields.collects, "180")
        self.assertEqual(self.fields.comments, "34")

    def test_bar_slice_picks_last_container(self):
        bar = interact_bar_slice(self.html)
        self.assertIn("697", bar)
        self.assertNotIn("99999", bar)

    def test_title_body_tags(self):
        self.assertEqual(self.fields.title, "测试详情页标题")
        self.assertIn("第一段正文", self.fields.body)
        self.assertNotIn("#Kimi", self.fields.body)  # 标签从正文剥离，单独存
        self.assertEqual(self.fields.tags, ("#Kimi", "#大模型"))

    def test_relative_date_never_becomes_published_at(self):
        self.assertEqual(self.fields.missing.count("published_at"), 0)  # 不在必填里
        self.assertNotIn("3天前", self.fields.as_row_updates().values())

    def test_missing_share_is_reported_not_invented(self):
        self.assertNotIn("shares", self.fields.as_row_updates())


class TestDetailClassification(unittest.TestCase):
    def test_og_url_wins_over_empty_detail_map(self):
        """实测纠正：noteDetailMap 为空不代表是主页——og:url 指向 /explore/ 就是详情页。"""
        archive = open_archive(FIXTURES / "minimal_detail.html")
        self.assertEqual(archive.kind, "note_detail")

    def test_profile_still_recognized(self):
        self.assertEqual(open_archive(FIXTURES / "minimal_profile.html").kind, "profile")


class TestParseDetailCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "collected_on": "2026-10-27", "grade": "②"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_note_from_detail_page_has_title_but_no_fake_date(self):
        code = main(["parse-detail", str(FIXTURES / "minimal_detail.html"), "--on", "2026-10-27",
                     "--data", str(self.data), "--ledger", "", "--account", "acc1"])
        self.assertEqual(code, 0)
        row = store.read_table(self.data, "notes")[0]
        self.assertEqual(row["title"], "测试详情页标题")
        self.assertEqual(row["published_at"], "")
        self.assertEqual(row["collects"], "180")
        self.assertEqual(row["source_kind"], "复制文本")

    def test_existing_note_keeps_home_page_title(self):
        store.append_rows(self.data, "notes", [{
            "account_id": "acc1", "note_id": "6adc2f910000000024039fde", "published_at": "2026-10-24",
            "title": "主页存档给的标题", "form": "视频", "likes": "700", "pinned": "0",
            "source_file": "home.html", "source_kind": "公开页", "collected_on": "2026-10-27", "grade": "②",
        }])
        code = main(["parse-detail", str(FIXTURES / "minimal_detail.html"), "--on", "2026-10-27",
                     "--data", str(self.data), "--ledger", "", "--account", "acc1"])
        self.assertEqual(code, 0)
        merged = store.latest_per_note(store.read_table(self.data, "notes"))[0]
        self.assertEqual(merged["title"], "主页存档给的标题")   # 主页的更权威
        self.assertEqual(merged["form"], "视频")
        self.assertEqual(merged["collects"], "180")           # 详情页补的

    def test_home_page_snapshot_rejected_by_parse_detail(self):
        code = main(["parse-detail", str(FIXTURES / "minimal_profile.html"), "--on", "2026-10-27",
                     "--data", str(self.data), "--ledger", ""])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
