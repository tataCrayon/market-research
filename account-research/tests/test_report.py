"""`report`：把四张结论表渲染成人能读的 markdown。

这组测试的重点不是「字符串长得对」，而是三条**结构性质**：

1. **纯函数**：同样的数据永远得到同样的文本（报告可复现）。
2. **不吞结论**：库里有的 layer / 表行必须都出现在报告里，
   未知 layer 归「其它」而不是丢弃——报告「看起来完整」是最坏的漏。
3. **口径与 stats 一致**：报告里的分母必须等于 `stats.n_sample`。
   两处不一致会造出「同一个任务两个分母」那种 bug（实测过一次）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import report, schema  # noqa: E402

HEADER = {
    "accounts": schema.ACCOUNT_COLUMNS,
    "notes": schema.NOTE_COLUMNS,
    "findings": schema.FINDING_COLUMNS,
    "playbook": schema.PLAYBOOK_COLUMNS,
    "hypotheses": schema.HYPOTHESIS_COLUMNS,
    "verdicts": schema.VERDICT_COLUMNS,
}


def write_table(data_dir: Path, name: str, rows: list[dict]) -> None:
    import csv

    columns = HEADER[name]
    with (data_dir / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


class ReportFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.text = Path(tempfile.mkdtemp())
        write_table(self.data, "accounts", [{
            "account_id": "acc1", "nickname": "测试号", "red_id": "r1", "fans": "1000",
            "posted": "10", "interactions": "5000", "ip_location": "上海",
            "desc": "测试简介", "collected_on": "2026-11-01",
        }])
        write_table(self.data, "notes", [
            {"account_id": "acc1", "note_id": "n1", "published_at": "2026-10-01",
             "title": "爆款标题", "form": "图文", "likes": "900", "collects": "300",
             "comments": "50", "shares": "10", "pinned": "0", "source_kind": "公开页",
             "collected_on": "2026-11-01", "grade": "②"},
            {"account_id": "acc1", "note_id": "n2", "published_at": "2026-10-02",
             "title": "普通标题", "form": "图文", "likes": "100", "pinned": "0",
             "source_kind": "公开页", "collected_on": "2026-11-01", "grade": "②"},
            {"account_id": "acc1", "note_id": "n3", "published_at": "2026-09-01",
             "title": "置顶标题", "form": "图文", "likes": "5000", "pinned": "1",
             "source_kind": "公开页", "collected_on": "2026-11-01", "grade": "②"},
        ])
        write_table(self.data, "findings", [
            {"finding_id": "F-1", "account_id": "acc1", "layer": "定位",
             "statement": "定位结论", "grade": "②", "used_in_verdict": "1",
             "evidence_ref": "n1", "sampled_on": "2026-11-01",
             "sample_basis": "样本 2 篇", "applicability": "仅本号"},
            {"finding_id": "F-2", "account_id": "acc1", "layer": "产能",
             "statement": "产能结论（非四层之一）", "grade": "③-a",
             "used_in_verdict": "0", "evidence_ref": "n2", "sampled_on": "2026-11-01",
             "sample_basis": "样本 2 篇", "applicability": "仅本号"},
        ])
        write_table(self.data, "playbook", [
            {"action_id": "A-1", "action": "动作一", "mechanism": "机制一",
             "requires": "需要一", "failure_condition": "信源断供时失效",
             "supporting_accounts": "1（测试号）", "counterexample": "某个反例",
             "evidence_ids": "F-1", "verifiable": "可验证"},
            {"action_id": "A-2", "action": "动作二", "mechanism": "机制二",
             "requires": "需要二", "failure_condition": "",
             "supporting_accounts": "1", "counterexample": "",
             "evidence_ids": "F-2", "verifiable": "可验证"},
        ])
        write_table(self.data, "hypotheses", [{
            "hypothesis_id": "H-1", "statement": "假设一", "support": "支持",
            "counter": "反证", "experiment": "实验", "status": "未验证",
            "due_on": "2026-12-01",
        }])
        write_table(self.data, "verdicts", [
            {"account_id": "acc1", "verdict": "🧪 先验证", "reason": "旧理由",
             "next_step": "旧下一步", "cheapest_test": "旧验证", "abandon_signal": "旧信号",
             "decided_on": "2026-10-01", "due_on": "2026-10-08",
             "mirror_method": "旧方法镜", "mirror_practice": "旧落地镜"},
            {"account_id": "acc1", "verdict": "🔧 部分对标", "reason": "新理由",
             "next_step": "下一步", "cheapest_test": "最便宜的验证",
             "abandon_signal": "放弃信号", "decided_on": "2026-11-01",
             "due_on": "2026-11-08", "mirror_method": "方法镜内容",
             "mirror_practice": "落地镜内容"},
        ])

    def tearDown(self):
        for item in self.text.iterdir():
            item.unlink()
        self.text.rmdir()
        self.tmp.cleanup()

    def render(self, **kwargs) -> str:
        data = report.collect(self.data, "acc1", self.text, today="2026-11-12")
        return report.render(data, **kwargs)


class TestRenderContenth(ReportFixture):
    def test_header_uses_latest_verdict(self):
        """表只追加：历史判决仍在，但报告头只能用一个——不选最新就会挂着过期判决。"""
        text = self.render()
        self.assertIn("🔧 部分对标", text.split("## 0")[0])
        self.assertNotIn("旧理由", text.split("## 0")[0])

    def test_history_verdict_is_still_listed(self):
        """历史判决是证据，不能因为「不是最新」就从报告里消失。"""
        self.assertIn("旧理由", self.render())

    def test_denominator_matches_stats(self):
        """§0 的分母必须等于 stats.n_sample。

        两处不一致会造出「同一个任务里两个分母」，模型引用 a/b 时被迫二选一。
        """
        data = report.collect(self.data, "acc1", self.text, today="2026-11-12")
        text = report.render(data)
        self.assertEqual(data.stats.n_sample, 2)
        self.assertIn(f"**样本 {data.stats.n_sample} 篇**", text)
        # n1 有收藏、n2 没有 → 1/2
        self.assertIn(f"有收藏 1/{data.stats.n_sample}", text)

    def test_empty_layer_is_not_rendered(self):
        text = self.render()
        self.assertIn("### 定位", text)
        self.assertNotIn("### 选题", text)

    def test_unknown_layer_goes_to_extra_not_dropped(self):
        """`产能` 不是四层之一，但它是一个真结论——丢掉会让报告「看起来完整」。"""
        text = self.render()
        self.assertIn("产能结论", text)
        self.assertIn("### 其它", text)

    def test_missing_failure_condition_is_flagged(self):
        """没有失效条件的打法等于「建议全抄」，报告里必须显眼。"""
        self.assertIn("**⚠️ 缺失**", self.render())

    def test_counterexample_listed(self):
        self.assertIn("某个反例", self.render())

    def test_unavailable_fields_are_explained(self):
        """⬜ 要带理由。只写 ⬜ 读者分不清「漏采」和「永远拿不到」。"""
        text = self.render()
        self.assertIn("永远拿不到的（不是漏采）", text)
        self.assertIn("报价 / 商单收入", text)


class TestRenderPurity(ReportFixture):
    def test_same_data_same_text(self):
        """纯函数：同样的表必须渲染出逐字节相同的报告（否则无法 diff、无法复现）。"""
        self.assertEqual(self.render(), self.render())

    def test_notes_limit_does_not_change_stats(self):
        """切掉的是表格，不是样本。"""
        full = self.render(notes_limit=0)
        trimmed = self.render(notes_limit=1)
        for marker in ("**样本 2 篇**", "篇均 500.0", "中位 500.0"):
            self.assertIn(marker, full)
            self.assertIn(marker, trimmed)

    def test_notes_limit_truncates_rows(self):
        trimmed = self.render(notes_limit=1)
        self.assertIn("只显示前 1 篇", trimmed)

    def test_no_pipe_inside_table_cells(self):
        """表格单元格里的 `|` 会把 markdown 表撑坏——必须转义。

        只对**表格**里的字段转义。散文段落（如 finding 的 statement）不转义：
        把正文里的 `|` 改成全角会篡改原话。
        """
        write_table(self.data, "hypotheses", [
            {"hypothesis_id": "H-1", "statement": "含 | 竖线的假设", "support": "支持",
             "counter": "反证", "experiment": "实验", "status": "未验证",
             "due_on": "2026-12-01"},
        ])
        text = self.render()
        self.assertIn("含 ／ 竖线的假设", text)
        # 表头行必须是完整的一行——被撑坏的话列数会对不上
        header = next(line for line in text.splitlines() if line.startswith("| 编号 |"))
        self.assertEqual(header.count("|"), 8)

    def test_prose_is_not_escaped(self):
        """散文里的 `|` 原样保留：转义会篡改原话，而原话是证据。"""
        write_table(self.data, "findings", [
            {"finding_id": "F-1", "account_id": "acc1", "layer": "定位",
             "statement": "原文含 | 竖线", "grade": "②", "used_in_verdict": "0",
             "evidence_ref": "n1", "sampled_on": "2026-11-01",
             "sample_basis": "", "applicability": ""},
        ])
        self.assertIn("原文含 | 竖线", self.render())


class TestEmptyLibrary(ReportFixture):
    def test_no_verdict_says_so_instead_of_crashing(self):
        write_table(self.data, "verdicts", [])
        text = self.render()
        self.assertIn("还没有判决", text)

    def test_no_findings_says_so(self):
        write_table(self.data, "findings", [])
        self.assertIn("还没有 findings", self.render())

    def test_unknown_account_raises_with_known_list(self):
        with self.assertRaises(ValueError) as ctx:
            report.collect(self.data, "nope", self.text, today="2026-11-12")
        self.assertIn("acc1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
