"""`matrix`：跨账号矩阵视图（S4）。

这组测试盯的是**四类会被自己骗过去的输出**，而不是 HTML 长得对不对：

1. **样本不足时不许装**：形式只有 1 种达 3 篇、母题只有 2 篇，
   都必须显示「样本不足」，而不是给一个看起来权威的中位数。
2. **空值不许写成 0**：没采到数据的账号显示 ⬜，显示 `0.0` 会被读成「中位赞就是 0」。
3. **未知/缺失不许静默合并**：`topic` 全空不是「没有选题」，是标注缺失，必须显式可见。
4. **不评分不排名**（PRD §7 N5）：页面里不能出现任何加权总分。
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import matrix, schema  # noqa: E402
from account_research.report import collect as collect_report  # noqa: E402

COLUMNS = {
    "accounts": schema.ACCOUNT_COLUMNS,
    "notes": schema.NOTE_COLUMNS,
    "findings": schema.FINDING_COLUMNS,
    "playbook": schema.PLAYBOOK_COLUMNS,
    "hypotheses": schema.HYPOTHESIS_COLUMNS,
    "verdicts": schema.VERDICT_COLUMNS,
}


def write_table(data_dir: Path, name: str, rows: list[dict]) -> None:
    import csv

    with (data_dir / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS[name]), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS[name]})


class MatrixFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.text = Path(tempfile.mkdtemp())
        self._write()

    def _write(self, playbook: list[dict] | None = None):
        write_table(self.data, "accounts", [
            {"account_id": "a1", "nickname": "甲号", "fans": "1000", "collected_on": "2026-11-01"},
            {"account_id": "a2", "nickname": "乙号", "fans": "500", "collected_on": "2026-11-01"},
            {"account_id": "a3", "nickname": "空号", "collected_on": "2026-11-01"},
        ])
        write_table(self.data, "notes", [
            # 甲号：图文 3 篇 + 视频 3 篇，两种形式都够 3 篇 → 可以比较
            *[{"account_id": "a1", "note_id": f"n1-{i}", "title": f"图文{i}", "form": "图文",
               "likes": str(100 * (i + 1)), "topic": "母题A", "pinned": "0",
               "published_at": "2026-10-01", "source_kind": "公开页",
               "collected_on": "2026-11-01", "grade": "②"} for i in range(3)],
            *[{"account_id": "a1", "note_id": f"n1-v{i}", "title": f"视频{i}", "form": "视频",
               "likes": "10", "topic": "母题B", "pinned": "0",
               "published_at": "2026-10-02", "source_kind": "公开页",
               "collected_on": "2026-11-01", "grade": "②"} for i in range(3)],
            # 乙号：只有图文 2 篇 → 不到 3 篇，不许给中位数
            *[{"account_id": "a2", "note_id": f"n2-{i}", "title": f"乙{i}", "form": "图文",
               "likes": "50", "topic": "", "pinned": "0",
               "published_at": "2026-10-03", "source_kind": "公开页",
               "collected_on": "2026-11-01", "grade": "②"} for i in range(2)],
        ])
        write_table(self.data, "findings", [])
        write_table(self.data, "hypotheses", [])
        write_table(self.data, "verdicts", [])
        write_table(self.data, "playbook", playbook if playbook is not None else [
            {"action_id": "A-1", "action": "动作一", "mechanism": "机制",
             "requires": "条件", "failure_condition": "信源断供时失效",
             "supporting_accounts": "1", "counterexample": "",
             "evidence_ids": "F-1", "verifiable": "可验证"},
        ])

    def tearDown(self):
        for item in self.text.iterdir():
            item.unlink()
        self.text.rmdir()
        self.tmp.cleanup()

    def build(self) -> matrix.MatrixData:
        reports = [
            collect_report(self.data, account_id, self.text, today="2026-11-12")
            for account_id in ("a1", "a2", "a3")
        ]
        return matrix.collect(reports, self.data, today="2026-11-12")


class TestSupportCount(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(matrix.support_count("1"), 1)

    def test_number_with_account_name(self):
        """`1（知新的AI笔记）` 是表里的真实写法，`int()` 会在它上面崩。"""
        self.assertEqual(matrix.support_count("1（知新的AI笔记）"), 1)

    def test_unreadable_becomes_zero(self):
        """读不到当 0 → 在打法库里显示「未达成」，这正是它该被看见的状态。"""
        self.assertEqual(matrix.support_count(""), 0)
        self.assertEqual(matrix.support_count("单账号"), 0)


class TestTopicMatrix(MatrixFixture):
    def test_unlabeled_is_its_own_row(self):
        """空 topic 是「没标注」，不是「没有」——必须单独成行。"""
        rows = matrix.topic_matrix([{"topic": "", "likes": "1"}, {"topic": "A", "likes": "2"}])
        self.assertEqual([row.topic for row in rows][0], matrix.UNLABELED_LABEL)

    def test_unlabeled_sorts_first(self):
        """数据质量问题比任何母题都该先被看见。"""
        rows = matrix.topic_matrix([
            {"topic": "A"}, {"topic": "A"}, {"topic": "A"}, {"topic": "", "likes": "1"},
        ])
        self.assertEqual(rows[0].topic, matrix.UNLABELED_LABEL)

    def test_median_hidden_below_threshold(self):
        """2 篇的中位数不是「这个母题的水平」，是那 2 篇的平均运气。"""
        rows = matrix.topic_matrix([{"topic": "A", "likes": "10"}, {"topic": "A", "likes": "20"}])
        self.assertIsNone(rows[0].median)

    def test_median_shown_at_threshold(self):
        rows = matrix.topic_matrix([{"topic": "A", "likes": str(v)} for v in (10, 20, 30)])
        self.assertEqual(rows[0].median, 20)


class TestRenderOverview(MatrixFixture):
    def test_empty_account_shows_placeholder_not_zero(self):
        """`0.0` 会被读成「中位赞就是 0」，⬜ 才是「没采到」。"""
        html = matrix.render(self.build())
        section = html.split("1. 账号总览")[1].split("2. 图文")[0]
        self.assertIn("空号", section)
        self.assertNotIn(">0.0<", section)

    def test_no_composite_score(self):
        """PRD §7 N5：禁止跨指标加权成综合分。

        判据不是「页面上不出现『评分』二字」——那句话本身就在解释「这里不做综合评分」。
        真正要检查的是**表头里没有跨指标的合成列**，以及没有隐藏的分数属性。
        """
        html = matrix.render(self.build())
        headers = re.findall(r"<th[^>]*>(.*?)</th>", html)
        for header in headers:
            for banned in ("综合", "评分", "指数", "总分", "得分"):
                self.assertNotIn(banned, header, f"表头出现了合成指标：{header}")
        for banned in ("data-score", "data-rank", "综合指数", "对标价值"):
            self.assertNotIn(banned, html)

    def test_sort_is_by_single_metric(self):
        """排序依据必须是最醒目那一列的中位赞，且页面上写明了这一点。"""
        html = matrix.render(self.build())
        section = html.split("1. 账号总览")[1].split("2. 图文")[0]
        self.assertIn("中位点赞（单一公开指标）", section)


class TestRenderForms(MatrixFixture):
    def test_insufficient_samples_says_so(self):
        html = matrix.render(self.build())
        section = html.split("2. 图文 vs 视频")[1].split("3. 母题矩阵")[0]
        self.assertIn("不足", section)

    def test_comparable_account_gets_a_conclusion(self):
        html = matrix.render(self.build())
        section = html.split("2. 图文 vs 视频")[1].split("3. 母题矩阵")[0]
        self.assertIn("甲号", section)
        self.assertIn("图文", section)

    def test_no_cross_account_merge(self):
        """跨账号合并需要 ≥3 个号同向，页面明说不做。"""
        html = matrix.render(self.build())
        self.assertIn("本页不做这个合并", html)


class TestRenderTopics(MatrixFixture):
    def test_all_unlabeled_is_flagged_as_missing_labels(self):
        html = matrix.render(self.build())
        section = html.split("3. 母题矩阵")[1].split("4. 打法库")[0]
        self.assertIn("标注缺失", section)

    def test_unlabeled_uses_grey_segment(self):
        """未标注段必须用灰段（--seg-na），不得被合并隐藏或冒用母题色。"""
        html = matrix.render(self.build())
        section = html.split("3. 母题矩阵")[1].split("4. 打法库")[0]
        self.assertIn(matrix.UNLABELED_COLOR, section)

    def test_segments_sum_to_100(self):
        html = matrix.render(self.build())
        section = html.split("3. 母题矩阵")[1].split("4. 打法库")[0]
        widths = [float(value) for value in re.findall(r'style="width:([\d.]+)%', section)]
        self.assertTrue(widths)
        for chunk in _chunks(widths):
            self.assertAlmostEqual(sum(chunk), 100.0, places=0)

    def test_aria_label_describes_the_bar(self):
        """读屏用户该拿到结论，不是一串没有语义的 span。"""
        html = matrix.render(self.build())
        self.assertIn('role="img"', html)
        self.assertIn("aria-label=", html)


def _chunks(widths: list[float]) -> list[list[float]]:
    """按「合计回到 100」把宽度切回每张图一组，用于逐图校验。"""
    groups, current = [], []
    for value in widths:
        current.append(value)
        if abs(sum(current) - 100.0) < 0.5:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


class TestRenderPlaybook(MatrixFixture):
    def test_single_account_is_marked_unmet(self):
        html = matrix.render(self.build())
        section = html.split("4. 打法库")[1].split("5. 失效条件")[0]
        self.assertIn("未达成", section)
        self.assertIn("0/1 条", section)

    def test_two_accounts_clears_the_gate(self):
        self._write(playbook=[{
            "action_id": "A-1", "action": "动作一", "mechanism": "机制",
            "requires": "条件", "failure_condition": "失效条件",
            "supporting_accounts": "2", "counterexample": "",
            "evidence_ids": "F-1", "verifiable": "可验证",
        }])
        html = matrix.render(self.build())
        section = html.split("4. 打法库")[1].split("5. 失效条件")[0]
        self.assertIn("1/1 条", section)
        self.assertIn("已跨号", section)

    def test_missing_failure_condition_is_flagged(self):
        """没有失效条件的打法等于「建议全抄」。"""
        self._write(playbook=[{
            "action_id": "A-1", "action": "动作一", "mechanism": "机制",
            "requires": "条件", "failure_condition": "",
            "supporting_accounts": "1", "counterexample": "",
            "evidence_ids": "F-1", "verifiable": "可验证",
        }])
        html = matrix.render(self.build())
        self.assertIn("⚠️ 缺失", html)


class TestRenderFailureList(MatrixFixture):
    def test_lists_every_action(self):
        self._write(playbook=[
            {"action_id": "A-1", "action": "动作一", "failure_condition": "条件一失效",
             "supporting_accounts": "1", "evidence_ids": "F-1"},
            {"action_id": "A-2", "action": "动作二", "failure_condition": "",
             "supporting_accounts": "1", "evidence_ids": "F-2"},
        ])
        html = matrix.render(self.build())
        section = html.split("5. 失效条件清单")[1]
        self.assertIn("A-1", section)
        self.assertIn("条件一失效", section)
        self.assertIn("⬜ 缺失", section)


class TestOfflineHygiene(MatrixFixture):
    """PRD §7 N1/N2 + output-html-template §2：断网双击必须能开。"""

    def test_no_external_resources(self):
        html = matrix.render(self.build())
        self.assertNotIn("<script", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("@import", html)

    def test_div_balance(self):
        html = matrix.render(self.build())
        self.assertEqual(html.count("<div"), html.count("</div>"))

    def test_light_theme(self):
        """浅色底深色字，禁止深色底。"""
        html = matrix.render(self.build())
        self.assertIn("--paper:#FAF7F1", html)
        self.assertIn("--ink:#2B2925", html)

    def test_numeric_alignment_class_present(self):
        """数字列右对齐 + tabular-nums，重绘不抖。"""
        html = matrix.render(self.build())
        self.assertIn('class="num"', html)
        self.assertIn("tabular-nums", html)


if __name__ == "__main__":
    unittest.main()
