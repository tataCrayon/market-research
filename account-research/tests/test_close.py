"""`close`：假设结案。

这组测试来自一次真实死路（2026-11-12）：`ar status` 的 D2 告警连喊 10 天
「证伪或证实都算结案，唯独不能继续挂着『未验证』」，而命令行里**没有一条路能做到**：

```
ar add hypotheses --file h.json  → 新增 0 行（键已存在，append-only 静默跳过）
ar --help | grep close           → 无
PRD 说的「自动降 已证伪（超期未验）」→ 不在枚举里，V6 直接拒
```

所以这里测的不只是函数行为，还有**那条死路是否真的通了**：
结案能写进表、写进去之后 D2 会闭嘴、历史状态仍在。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import alerts, close, schema, store  # noqa: E402
from account_research.close import CloseError, close_hypothesis  # noqa: E402

TODAY = "2026-11-12"


def hypothesis(
    hypothesis_id: str = "H-1",
    status: str = "未验证",
    due_on: str = "2026-11-02",
    recorded_on: str = "",
    close_note: str = "",
) -> dict:
    return {
        "hypothesis_id": hypothesis_id,
        "statement": "某个涨粉类断言",
        "support": "支持",
        "counter": "反证",
        "experiment": "实验",
        "status": status,
        "due_on": due_on,
        "recorded_on": recorded_on,
        "close_note": close_note,
    }


class TestClosedStatusIsInTheEnum(unittest.TestCase):
    def test_overdue_unverified_is_a_legal_status(self):
        """PRD §4.1 写的那个词，必须先能通过 V6——否则「按 PRD 做」等于「被拒」。"""
        self.assertIn("超期未验", schema.HYPOTHESIS_STATUS)

    def test_closed_set_is_a_subset(self):
        for status in schema.HYPOTHESIS_CLOSED:
            self.assertIn(status, schema.HYPOTHESIS_STATUS)

    def test_open_statuses_are_not_closable(self):
        """用「未验证/验证中」结案只是把告警重新点亮一次，必须拒。"""
        for status in ("未验证", "验证中"):
            with self.assertRaises(CloseError):
                close_hypothesis([hypothesis()], "H-1", status, on=TODAY)


class TestCloseHypothesis(unittest.TestCase):
    def test_produces_a_new_row_with_new_status(self):
        closed = close_hypothesis([hypothesis()], "H-1", "已证伪", on=TODAY)
        self.assertEqual(closed["status"], "已证伪")
        self.assertEqual(closed["recorded_on"], TODAY)

    def test_keeps_original_fields(self):
        """结案行是「原行 + 新状态」，不是只写状态——否则折叠后会丢掉假设正文。"""
        closed = close_hypothesis([hypothesis()], "H-1", "已证伪", on=TODAY)
        self.assertEqual(closed["statement"], "某个涨粉类断言")
        self.assertEqual(closed["experiment"], "实验")
        self.assertEqual(closed["due_on"], "2026-11-02")

    def test_overdue_requires_a_note(self):
        """「超期未验」= 我没做。不写原因，三个月后没人分得清「放弃」还是「忘了」。"""
        with self.assertRaises(CloseError) as ctx:
            close_hypothesis([hypothesis()], "H-1", "超期未验", on=TODAY)
        self.assertIn("--note", str(ctx.exception))

    def test_overdue_accepts_a_note(self):
        closed = close_hypothesis([hypothesis()], "H-1", "超期未验",
                                  note="没做实验", on=TODAY)
        self.assertEqual(closed["close_note"], "没做实验")

    def test_unknown_hypothesis_lists_known_ids(self):
        with self.assertRaises(CloseError) as ctx:
            close_hypothesis([hypothesis("H-1")], "H-9", "已证伪", on=TODAY)
        self.assertIn("H-1", str(ctx.exception))

    def test_already_closed_same_status_is_refused(self):
        """表只追加，重复写会多一条删不掉的无信息行。"""
        rows = [hypothesis(status="已证伪", recorded_on="2026-11-01")]
        with self.assertRaises(CloseError) as ctx:
            close_hypothesis(rows, "H-1", "已证伪", on=TODAY)
        self.assertIn("已经是", str(ctx.exception))

    def test_closing_an_already_closed_the_other_way_is_allowed(self):
        """已证伪 → 已证实 是真信息（后来发现证伪错了），必须允许。"""
        rows = [hypothesis(status="已证伪", recorded_on="2026-11-01")]
        closed = close_hypothesis(rows, "H-1", "已证实", on=TODAY)
        self.assertEqual(closed["status"], "已证实")

    def test_does_not_mutate_input(self):
        rows = [hypothesis()]
        close_hypothesis(rows, "H-1", "已证伪", on=TODAY)
        self.assertEqual(rows[0]["status"], "未验证")

    def test_same_day_second_close_is_refused(self):
        """同一天结案两次会撞键：`preview_append` **静默跳过**，而命令照打「新增 1 行」。

        「看起来成功其实没写」正是本次要消灭的失败模式，所以在命令层就拦住。
        """
        rows = [hypothesis(recorded_on=""), ]
        closed = close_hypothesis(rows, "H-1", "已证伪", on=TODAY)
        with self.assertRaises(CloseError) as ctx:
            close_hypothesis(rows + [closed], "H-1", "已证实", on=TODAY)
        self.assertIn("同一天只能结案一次", str(ctx.exception))

    def test_next_day_close_is_allowed(self):
        rows = [hypothesis(recorded_on="")]
        closed = close_hypothesis(rows, "H-1", "已证伪", on=TODAY)
        later = close_hypothesis(rows + [closed], "H-1", "已证实", on="2026-11-13")
        self.assertEqual(later["status"], "已证实")


class TestLatestHypotheses(unittest.TestCase):
    def test_newest_recorded_on_wins(self):
        rows = [hypothesis(recorded_on=""), hypothesis(status="超期未验", recorded_on=TODAY)]
        latest = store.latest_hypotheses(rows)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["status"], "超期未验")

    def test_legacy_blank_recorded_on_still_single(self):
        """老数据的 recorded_on 是空串，同一假设只有一行——行为不变。"""
        latest = store.latest_hypotheses([hypothesis(), hypothesis("H-2")])
        self.assertEqual(len(latest), 2)

    def test_same_day_last_written_wins(self):
        rows = [hypothesis(status="已证伪", recorded_on=TODAY),
                hypothesis(status="已证实", recorded_on=TODAY)]
        self.assertEqual(store.latest_hypotheses(rows)[0]["status"], "已证实")


class TestClosingSilencesD2(unittest.TestCase):
    """整条链的验收：结案 → 写进去 → 告警闭嘴。"""

    def test_before_close_the_alert_fires(self):
        self.assertEqual(len(alerts.overdue_hypotheses([hypothesis()], TODAY)), 1)

    def test_after_close_the_alert_is_silent(self):
        rows = [hypothesis()]
        closed = close_hypothesis(rows, "H-1", "超期未验", note="没做", on=TODAY)
        self.assertEqual(alerts.overdue_hypotheses(rows + [closed], TODAY), [])

    def test_history_rows_do_not_keep_the_alert_alive(self):
        """这是关键：旧「未验证」行还在表里（证据），但**不能让它继续报警**。

        不折叠的话，结案就只是加了一行废数据——告警照响，人会以为命令没生效。
        """
        rows = [hypothesis(recorded_on="2026-10-01"),
                hypothesis(status="已证伪", recorded_on=TODAY)]
        self.assertEqual(alerts.overdue_hypotheses(rows, TODAY), [])


class TestD2MessagePointsAtTheCommand(unittest.TestCase):
    def test_alert_tells_you_how_to_close(self):
        """告警必须给出可执行的下一步。E11：只说「该做」不給「怎么做」= 没人会做。"""
        found = alerts.overdue_hypotheses([hypothesis()], TODAY)
        self.assertIn("ar close hypothesis", found[0].message)


if __name__ == "__main__":
    unittest.main()
