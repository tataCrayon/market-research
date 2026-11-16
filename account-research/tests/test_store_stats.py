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

    def test_two_channels_same_day_both_kept(self):
        """主页与详情页同一天是两次独立观测，不能被去重吃掉。"""
        store.append_rows(self.data, "notes", [note("n1", 100)])
        detail = note("n1", 100, collected_on="2026-10-22")
        detail["source_kind"] = "复制文本"
        detail["collects"] = 500
        added, skipped = store.append_rows(self.data, "notes", [detail])
        self.assertEqual((added, skipped), (1, 0))
        merged = store.latest_per_note(store.read_table(self.data, "notes"))
        self.assertEqual(merged[0]["likes"], "100")
        self.assertEqual(merged[0]["collects"], "500")

    def test_latest_per_note_merges_fields_by_channel_priority(self):
        base = note("n1", 100)
        base["title"] = "标题在主页才有"
        detail = note("n1", 110, collected_on="2026-10-23")
        detail.update({"source_kind": "复制文本", "title": "", "collects": 430, "duration": "00:14"})
        merged = store.latest_per_note([base, detail])[0]
        self.assertEqual(merged["likes"], 110)      # 后到覆盖
        self.assertEqual(merged["title"], "标题在主页才有")  # 空值不覆盖
        self.assertEqual(merged["collects"], 430)
        self.assertEqual(merged["duration"], "00:14")


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


class TestRoundedCountsInStats(unittest.TestCase):
    """`1.2万` 进统计：**不许丢行**，而且要知道是哪几行。

    旧实现在 `usable` 那一步用 `_int_or_none(likes) is not None` 过滤，`1.2万` 返回 None，
    于是整行被静默丢出样本——`n_total` 少一篇、`excluded` 仍是 0、没有一个字提示。
    丢的还偏偏是破万那几篇，也就是最该被分析的那几篇。
    """

    def test_rounded_row_stays_in_sample(self):
        rows = [note("a", 100), note("b", 200), note("c", "1.2万")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.n_sample, 3, "破万那篇必须留在样本里，不能被静默丢掉")
        self.assertEqual(result.n_total, 3)

    def test_rounded_row_counted_with_converted_value(self):
        rows = [note("a", 1000), note("b", "1.2万")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.likes.maximum, 12000)
        self.assertEqual(result.likes.mean, 6500.0)

    def test_approx_notes_names_the_exact_rows(self):
        """只给个数字不够用——报告要点名到篇。"""
        rows = [note("a", 100), note("big", "1.2万"), note("big2", "3.4万")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.approx_rows, 2)
        self.assertEqual(set(result.approx_notes), {"big", "big2"})

    def test_exact_rows_are_not_flagged(self):
        result = summarize([note("a", 4581)], "acc1")
        self.assertEqual(result.approx_rows, 0)
        self.assertEqual(result.approx_notes, ())

    def test_unparsable_is_counted_not_silent(self):
        """真正解析不了的写法才该出样本，而且**必须报数**。"""
        rows = [note("a", 100), note("bad", "看主页"), note("bad2", "")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.n_sample, 2)      # 空点赞本来就不算可用
        self.assertEqual(result.unparsable, 1)

    def test_pinned_rounded_row_not_counted_in_approx(self):
        """置顶篇不进样本，它的圆整值也不该算进 approx_rows——否则提示会多报一篇。"""
        rows = [note("a", 100), note("p", "1.2万", pinned="1")]
        result = summarize(rows, "acc1")
        self.assertEqual(result.approx_rows, 0)
        self.assertEqual(result.n_pinned, 1)


if __name__ == "__main__":
    unittest.main()
