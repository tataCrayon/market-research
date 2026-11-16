"""纪律告警：到期未复核 / 假设逾期 / 台账断流。

这三条在 SKILL.md 里都是**硬性纪律**（「到期二判 / 降级」「追踪中断必须写进报告」），
但 2026-11-04 盘点时发现：库里一条判决和一条假设早已过期，`ar status` 一个字都没提。
纪律只活在自然语言里 = 没有纪律（E10 / E11 同源）。

**这个文件最重要的一组断言是「不误报」**：
已被新判决替代的旧判决不是欠账，是历史。把它一起报出来就是制造噪音，
噪音多了真欠账也会被忽略（E12：闸门的第一职责是不误杀）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import alerts  # noqa: E402

TODAY = "2026-11-04"


def verdict(decided_on: str, due_on: str, account: str = "acc1", text: str = "🔧 部分对标") -> dict:
    return {"account_id": account, "decided_on": decided_on, "due_on": due_on, "verdict": text}


def hypothesis(hyp_id: str, due_on: str, status: str = "未验证") -> dict:
    return {"hypothesis_id": hyp_id, "due_on": due_on, "status": status}


class TestOverdueVerdicts(unittest.TestCase):
    def test_overdue_latest_verdict_is_reported(self):
        rows = [verdict("2026-10-26", "2026-11-02")]
        found = alerts.overdue_verdicts(rows, TODAY)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "D1")
        self.assertIn("已过期 2 天", found[0].message)

    def test_superseded_verdict_is_not_noise(self):
        """10-26 的判决 due 11-02 已过，但 11-01 已有新判决替代它 —— 那是历史，不是欠账。

        实测就是这么一对：如果把两条都报，`ar status` 每次都会喊一条永远不用处理的告警。
        """
        rows = [verdict("2026-10-26", "2026-11-02"), verdict("2026-11-01", "2026-11-06")]
        found = alerts.overdue_verdicts(rows, TODAY)
        self.assertEqual(found, [])

    def test_superseded_by_other_acc_does_not_cover(self):
        """替代关系只在**同一个账号**内成立。别人的新判决不能替这条销账。"""
        rows = [verdict("2026-10-26", "2026-11-02", account="acc1"),
                verdict("2026-11-01", "2026-11-20", account="acc2")]
        self.assertEqual(len(alerts.overdue_verdicts(rows, TODAY)), 1)

    def test_not_yet_due_is_silent(self):
        self.assertEqual(alerts.overdue_verdicts([verdict("2026-11-01", "2026-11-06")], TODAY), [])

    def test_due_today_is_silent(self):
        """到期日当天不算过期——那正是该干活的那天。"""
        self.assertEqual(alerts.overdue_verdicts([verdict("2026-11-01", TODAY)], TODAY), [])

    def test_blank_due_on_is_not_reported(self):
        """没约定就没有违约。空 due 不许被当成「已过期」。"""
        self.assertEqual(alerts.overdue_verdicts([verdict("2026-10-26", "")], TODAY), [])

    def test_bad_date_format_is_not_guessed(self):
        self.assertEqual(alerts.overdue_verdicts([verdict("2026-10-26", "下周")], TODAY), [])


class TestOverdueHypotheses(unittest.TestCase):
    def test_open_hypothesis_past_due_is_reported(self):
        found = alerts.overdue_hypotheses([hypothesis("H-1", "2026-11-02")], TODAY)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "D2")

    def test_validating_state_also_counts_as_open(self):
        found = alerts.overdue_hypotheses([hypothesis("H-1", "2026-11-02", "验证中")], TODAY)
        self.assertEqual(len(found), 1)

    def test_closed_hypotheses_are_silent(self):
        """已证伪 / 已证实 = 结案。过期不再是问题。"""
        rows = [hypothesis("H-1", "2026-11-02", "已证伪"),
                hypothesis("H-2", "2026-11-02", "已证实")]
        self.assertEqual(alerts.overdue_hypotheses(rows, TODAY), [])


class TestLedgerStaleness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "台账.md"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, *rows: str) -> None:
        body = "\n".join([
            "| 账号名 | 平台 | 领域标签 | 粉丝区间 | 母题 | 最近更新 |",
            "|---|---|---|---|---|---|",
            *rows,
        ])
        self.path.write_text(body + "\n", encoding="utf-8")

    def test_stale_ledger_is_reported(self):
        # 实测口径：最后一条 2026-10-22，今天 11-04 → 13 天
        self._write("| 某号 | 小红书 | AI | 1万 | — | 2026-10-22 采集 |")
        found = alerts.ledger_staleness(self.path, TODAY)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "D3")
        self.assertIn("13 天", found[0].message)

    def test_recent_ledger_is_silent(self):
        self._write("| 某号 | 小红书 | AI | 1万 | — | 2026-11-01 采集 |")
        self.assertEqual(alerts.ledger_staleness(self.path, TODAY), [])

    def test_threshold_is_ten_days_by_contract(self):
        """10 天这个数是 SKILL.md 定的，不是随手挑的——钉住它，改了要么改文档要么改测试。"""
        self.assertEqual(alerts.LEDGER_STALE_DAYS, 10)
        self._write("| 某号 | 小红书 | AI | 1万 | — | 2026-10-25 采集 |")   # 恰好 10 天
        self.assertEqual(alerts.ledger_staleness(self.path, TODAY), [])
        self._write("| 某号 | 小红书 | AI | 1万 | — | 2026-10-24 采集 |")   # 11 天
        self.assertEqual(len(alerts.ledger_staleness(self.path, TODAY)), 1)

    def test_missing_file_is_an_alert_not_a_pass(self):
        """读不到台账 ≠ 台账没问题。静默通过比报警更危险（E8 同源）。"""
        found = alerts.ledger_staleness(self.path, TODAY)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "D3")

    def test_date_comes_from_update_column_not_fans_column(self):
        """粉丝列里的「2026-10-21 公开页」是**观察值日期**，不是台账更新时间。

        混用会让断流判断永远触发不了——观察值日期比台账更新时间新，选 max 就是自欺。
        """
        self._write("| 某号 | 小红书 | AI | 14.4万（2026-11-04 公开页） | — | 2026-10-22 追加 |")
        dates = alerts.ledger_dates(self.path)
        self.assertEqual(dates, ["2026-10-22"])

    def test_header_and_separator_skipped(self):
        self._write()
        self.assertEqual(alerts.ledger_dates(self.path), [])


class TestCollect(unittest.TestCase):
    def test_orders_and_combines(self):
        found = alerts.collect(
            [verdict("2026-10-26", "2026-11-02")],
            [hypothesis("H-1", "2026-11-02")],
            None,
            TODAY,
        )
        self.assertEqual([a.code for a in found], ["D1", "D2"])

    def test_clean_state_returns_nothing(self):
        self.assertEqual(alerts.collect([], [], None, TODAY), [])


if __name__ == "__main__":
    unittest.main()
