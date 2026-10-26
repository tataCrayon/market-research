"""只追加入库 + 统计口径。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.stats import summarize  # noqa: E402


def note(note_id: str, likes: int, pinned: str = "0", form: str = "图文", collected_on: str = "2026-10-22") -> dict:
    return {
        "account_id": "acc1", "note_id": note_id, "published_at": "2026-10-01", "title": note_id,
        "form": form, "likes": likes, "collects": None, "comments": None, "shares": None,
        "pinned": pinned, "topic": "", "source_file": "f.html", "source_kind": "公开页",
        "collected_on": collected_on, "grade": "②",
    }


class TestAppendOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_key_is_skipped_not_overwritten(self):
        store.append_rows(self.data, "notes", [note("n1", 100)])
        added, skipped = store.append_rows(self.data, "notes", [note("n1", 999)])
        self.assertEqual((added, skipped), (0, 1))
        self.assertEqual(store.read_table(self.data, "notes")[0]["likes"], "100")

    def test_new_collected_on_appends_drift_row(self):
        store.append_rows(self.data, "notes", [note("n1", 100)])
        added, _ = store.append_rows(self.data, "notes", [note("n1", 130, collected_on="2026-10-23")])
        self.assertEqual(added, 1)
        self.assertEqual(len(store.read_table(self.data, "notes")), 2)

    def test_latest_per_note_picks_newest_collected_on(self):
        rows = [note("n1", 100), note("n1", 130, collected_on="2026-10-23")]
        self.assertEqual(store.latest_per_note(rows)[0]["likes"], 130)


class TestStats(unittest.TestCase):
    def test_pinned_excluded_from_sample(self):
        rows = [note("a", 100), note("b", 200, pinned="1"), note("c", 300)]
        result = summarize(rows, "acc1")
        self.assertEqual(result.n_total, 3)
        self.assertEqual(result.n_pinned, 1)
        self.assertEqual(result.n_sample, 2)
        self.assertEqual(result.likes.median, 200)

    def test_burst_rate_reports_definition_and_k(self):
        rows = [note(f"n{i}", value) for i, value in enumerate([100, 100, 100, 100, 1000])]
        result = summarize(rows, "acc1", (3,))
        self.assertEqual(result.likes.median, 100)
        self.assertEqual(result.burst[3]["hits"], 1)
        self.assertEqual(result.burst[3]["rate"], 20.0)
        self.assertIn("K", result.burst[3]["definition"])

    def test_form_breakdown_separates_video_and_image(self):
        rows = [note("a", 100), note("b", 300, form="视频")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.forms["图文"]["n"], 1)
        self.assertEqual(result.forms["视频"]["mean"], 300)

    def test_rows_without_provenance_excluded(self):
        rows = [note("a", 100), note("b", 200, collected_on="")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.n_sample, 1)
        self.assertEqual(result.excluded, 1)

    def test_collect_coverage_zero_when_list_page_only(self):
        result = summarize([note("a", 100)], "acc1")
        self.assertEqual(result.coverage["收藏"], "0/1")


if __name__ == "__main__":
    unittest.main()
