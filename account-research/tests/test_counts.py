"""平台计数换算：`1.2万` 不是 12000，是「12000 到 12999 之间的某个数」。

这个文件存在的理由是一次真实的崩：详情页某篇破万后，互动栏渲染成 `1.2万`，
`int("1.2万")` 让 `ar gaps` / `ar links` 直接 ValueError——整条命令不可用。
更坏的一半是静默的：统计那一侧把它当成「没有点赞」，于是**样本悄悄少一篇**，
`excluded` 还是 0，没有一个字提示。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.counts import Count, consistent, is_approx, parse_count, to_count  # noqa: E402


class TestParseCount(unittest.TestCase):
    def test_plain_integer_is_exact(self):
        parsed = parse_count("4581")
        self.assertEqual(parsed.value, 4581)
        self.assertFalse(parsed.approx)
        self.assertEqual((parsed.low, parsed.high), (4581, 4581))

    def test_thousands_separator(self):
        self.assertEqual(to_count("12,345"), 12345)

    def test_wan_is_approximate_with_interval(self):
        parsed = parse_count("1.2万")
        self.assertEqual(parsed.value, 12000)
        self.assertTrue(parsed.approx)
        # 平台只给 3 位有效数字：真值落在 [12000, 13000)
        self.assertEqual((parsed.low, parsed.high), (12000, 12999))
        self.assertEqual(parsed.text(), "≈12,000")

    def test_two_decimal_wan_has_narrower_interval(self):
        parsed = parse_count("1.25万")
        self.assertEqual(parsed.value, 12500)
        self.assertEqual((parsed.low, parsed.high), (12500, 12599))

    def test_yi(self):
        self.assertEqual(to_count("1.4亿"), 140000000)

    def test_k_and_m_suffix(self):
        self.assertEqual(to_count("14.4K"), 14400)
        self.assertEqual(to_count("3.4k"), 3400)
        self.assertEqual(to_count("2M"), 2000000)

    def test_zero_is_not_blank(self):
        """0 是有效数字，不能被当成 ⬜ —— 没赞和没采到是两件事。"""
        self.assertEqual(to_count("0"), 0)

    def test_blank_and_garbage_return_none(self):
        for raw in (None, "", "  ", "⬜", "未取证", "-", "abc", "1.2万3"):
            self.assertIsNone(to_count(raw), f"{raw!r} 应该解析失败")

    def test_negative_rejected(self):
        """计数不该为负——收下负数只说明解析口径出错了。"""
        self.assertIsNone(parse_count("-5"))


class TestIsApprox(unittest.TestCase):
    def test_exact_false_round_true(self):
        self.assertFalse(is_approx("4581"))
        self.assertTrue(is_approx("1.2万"))

    def test_unparsable_is_not_approx(self):
        self.assertFalse(is_approx(""))


class TestConsistent(unittest.TestCase):
    def test_round_and_exact_overlapping_are_consistent(self):
        """`1.2万` 与 `12345` 不是相等，但说的是同一件事——不该报冲突。"""
        self.assertTrue(consistent(parse_count("1.2万"), parse_count("12345")))

    def test_exact_outside_round_interval_is_conflict(self):
        """`1.2万` 与 `15000` 差得太远，是真冲突（可能是取了别人的卡片）。"""
        self.assertFalse(consistent(parse_count("1.2万"), parse_count("15000")))

    def test_both_exact_must_match_exactly(self):
        self.assertTrue(consistent(parse_count("697"), parse_count("697")))
        self.assertFalse(consistent(parse_count("697"), parse_count("698")))

    def test_none_is_not_consistent(self):
        self.assertFalse(consistent(None, parse_count("1")))


class TestCountText(unittest.TestCase):
    def test_text_marks_approximation(self):
        self.assertEqual(Count(value=12000, approx=True).text(), "≈12,000")
        self.assertEqual(Count(value=4581).text(), "4,581")


if __name__ == "__main__":
    unittest.main()
