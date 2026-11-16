"""`attempts`：每条缺口「上次是怎么试的」。

这组测试来自一次真实事故（2026-11-04）：三份文档写着「评论内容一条都没采」，
而 11 篇详情页存档里 95 条评论原文一直都在。错误来源是一次 `grep comment-item`
没命中——**一次失败的尝试被固化成了「物理上拿不到」**，将近两周没人再试。

所以缺口清单必须能区分两件在屏幕上长得一模一样、处理方式却完全相反的事：
「从没试过」（去采）与「试过但平台不给」（别浪费时间）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import attempts  # noqa: E402
from account_research.attempts import (  # noqa: E402
    Attempt,
    attempts_by_note,
    describe,
    missing_columns,
    summarize,
)


def note(note_id="n1", kind="复制文本", when="2026-11-01", **fields) -> dict:
    row = {"note_id": note_id, "source_kind": kind, "collected_on": when,
           "collects": "", "comments": "", "shares": ""}
    row.update(fields)
    return row


class TestAttemptsByNote(unittest.TestCase):
    def test_public_page_is_not_an_attempt(self):
        """主页存档里根本不含藏/评/分享——它出现不代表试过取这三个数。"""
        self.assertEqual(attempts_by_note([note(kind="公开页")]), {})

    def test_detail_row_counts_as_attempt(self):
        found = attempts_by_note([note(kind="复制文本")])
        self.assertIn("n1", found)
        self.assertEqual(found["n1"].collected_on, "2026-11-01")

    def test_latest_attempt_wins(self):
        rows = [note(when="2026-10-20"), note(when="2026-11-01", collects="9")]
        found = attempts_by_note(rows)
        self.assertEqual(found["n1"].collected_on, "2026-11-01")

    def test_still_missing_read_from_that_row_not_merged(self):
        """「那次拿到了什么」只能看那一行。

        用合并后的值会把别的通道填进去的数混进来，于是「试过没拿到」会被误判成「拿到了」。
        """
        rows = [note(when="2026-11-01", collects="9", comments="1")]
        attempt = attempts_by_note(rows)["n1"]
        self.assertEqual(attempt.still_missing, ("shares",))

    def test_same_day_prefers_more_trusted_channel(self):
        rows = [note(when="2026-11-01", kind="第三方工具"), note(when="2026-11-01", kind="复制文本")]
        self.assertEqual(attempts_by_note(rows)["n1"].source_kind, "复制文本")


class TestDescribe(unittest.TestCase):
    def test_never_tried_says_so_loudly(self):
        """「从没试过」必须和「试过没拿到」在文字上明显不同。"""
        text = describe(None, ("collects",), "2026-11-06")
        self.assertIn("从未用详情页试过", text)
        self.assertIn("不算试过", text)

    def test_tried_and_failed_names_the_fields(self):
        attempt = Attempt("2026-10-28", "复制文本", ("collects", "comments"))
        text = describe(attempt, ("collects", "comments"), "2026-11-06")
        self.assertIn("2026-10-28", text)
        self.assertIn("当时 藏/评 也没拿到", text)
        self.assertIn("已 9 天", text)

    def test_old_attempt_is_flagged_for_retry(self):
        """超过 30 天没再试的，要提示「平台可能已改版」——一个月前的结论不足以代表今天。"""
        attempt = Attempt("2026-09-01", "复制文本", ("collects",))
        self.assertIn("值得再试一次", describe(attempt, ("collects",), "2026-11-06"))

    def test_recent_attempt_is_not_flagged(self):
        attempt = Attempt("2026-11-04", "复制文本", ("collects",))
        self.assertNotIn("值得再试一次", describe(attempt, ("collects",), "2026-11-06"))

    def test_field_read_then_but_empty_now_is_a_bug_not_a_gap(self):
        """那次读到过、现在却为空 → 不是缺口，是数据没进表。

        两者在屏幕上都是「缺 藏」，但一个去采、一个查 bug，混在一起会白花采集时间。
        """
        attempt = Attempt("2026-10-28", "复制文本", ())     # 当时三个字段都拿到了
        text = describe(attempt, ("collects",), "2026-11-06")
        self.assertIn("读到过", text)
        self.assertIn("不是缺口", text)
        self.assertNotIn("也没拿到", text)


class TestMissingColumns(unittest.TestCase):
    def test_returns_column_names_not_labels(self):
        """必须是列名。返回 `藏/评` 这类标签会让 describe 里的比对永远不成立。"""
        self.assertEqual(missing_columns({"collects": "", "comments": "0", "shares": "3"}),
                         ("collects",))

    def test_blank_and_placeholder_are_missing(self):
        self.assertEqual(
            missing_columns({"collects": "⬜", "comments": "  ", "shares": None}),
            ("collects", "comments", "shares"),
        )

    def test_zero_is_present_not_missing(self):
        """零评论是观测到的 0，不是缺。"""
        self.assertEqual(missing_columns({"collects": "0", "comments": "0", "shares": "0"}), ())


class TestSummarize(unittest.TestCase):
    def test_splits_never_tried_from_tried(self):
        attempts_map = {"a": Attempt("2026-10-28", "复制文本", ("collects",))}
        missing = {"a": ("collects",), "b": ("collects",)}
        text = summarize(attempts_map, missing)
        self.assertIn("1 篇**从未用详情页试过**", text)
        self.assertIn("1 篇试过但仍没拿到", text)


class TestNoLabelCollision(unittest.TestCase):
    def test_describe_accepts_what_missing_columns_returns(self):
        """两个函数的返回值必须能直接串起来——曾经一个给标签、一个吃列名，
        症状是提示**静默变短**（少了「当时也没拿到」那半句），没有任何报错。"""
        row = {"collects": "", "comments": "5", "shares": ""}
        columns = missing_columns(row)
        text = describe(Attempt("2026-10-28", "复制文本", ("collects", "shares")), columns, "2026-11-06")
        self.assertIn("也没拿到", text)


if __name__ == "__main__":
    unittest.main()
