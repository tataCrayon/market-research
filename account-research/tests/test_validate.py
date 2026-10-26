"""V1–V8 校验器：每条规则都必须能被一段坏数据撞出来（否则它只是注释）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.validate import validate_dir  # noqa: E402


def account_row() -> dict:
    return {"account_id": "acc1", "nickname": "n", "collected_on": "2026-10-22", "grade": "②"}


def note_row(**overrides) -> dict:
    row = {
        "account_id": "acc1", "note_id": "n1", "published_at": "2026-10-01", "title": "t",
        "form": "图文", "likes": 100, "pinned": "0",
        "source_file": "f.html", "source_kind": "公开页", "collected_on": "2026-10-22", "grade": "②",
    }
    row.update(overrides)
    return row


class ValidatorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        store.append_rows(self.data, "accounts", [account_row()])

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self) -> set[str]:
        return {issue.code for issue in validate_dir(self.data)}

    def test_clean_data_has_no_issues(self):
        store.append_rows(self.data, "notes", [note_row()])
        self.assertEqual(self.codes(), set())


class TestRules(ValidatorCase):
    def test_v1_rejects_verdict_outside_closed_set(self):
        store.append_rows(self.data, "verdicts", [{
            "account_id": "acc1", "verdict": "值得考虑", "decided_on": "2026-10-22",
            "next_step": "x", "cheapest_test": "y", "abandon_signal": "z",
        }])
        self.assertIn("V1", self.codes())

    def test_v2_rejects_verdict_missing_three_piece(self):
        store.append_rows(self.data, "verdicts", [{
            "account_id": "acc1", "verdict": "⏸ 只观察", "decided_on": "2026-10-22",
            "next_step": "补详情页", "cheapest_test": "", "abandon_signal": "7 天补不到降 ❌",
        }])
        self.assertIn("V2", self.codes())

    def test_v3_warns_on_missing_provenance(self):
        store.append_rows(self.data, "notes", [note_row(collected_on="")])
        issues = validate_dir(self.data)
        self.assertTrue(any(issue.code == "V3" and issue.level == "WARN" for issue in issues))

    def test_v4_rejects_secondary_grade_used_in_verdict(self):
        store.append_rows(self.data, "findings", [{
            "finding_id": "f1", "account_id": "acc1", "statement": "爆文集中在 A 母题",
            "grade": "③-a", "used_in_verdict": "1",
        }])
        self.assertIn("V4", self.codes())

    def test_v5_rejects_action_without_failure_condition(self):
        store.append_rows(self.data, "playbook", [{
            "action_id": "p1", "action": "抄四段骨架", "failure_condition": "",
        }])
        self.assertIn("V5", self.codes())

    def test_v6_rejects_growth_claim_in_verdict(self):
        store.append_rows(self.data, "findings", [{
            "finding_id": "f2", "account_id": "acc1", "statement": "做 C 母题涨粉最快",
            "grade": "②", "used_in_verdict": "1",
        }])
        self.assertIn("V6", self.codes())

    def test_v6_rejects_hypothesis_without_experiment(self):
        store.append_rows(self.data, "hypotheses", [{
            "hypothesis_id": "h1", "statement": "公开性强的选题转化更高",
            "status": "未验证", "experiment": "", "due_on": "",
        }])
        self.assertIn("V6", self.codes())

    def test_v7_rejects_orphan_note(self):
        store.append_rows(self.data, "notes", [note_row(account_id="ghost")])
        self.assertIn("V7", self.codes())


class TestLedgerDateOrder(unittest.TestCase):
    def test_v8_rejects_backdated_ledger_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "台账.md"
            ledger.write_text(
                "| 账号名 | 平台 | 领域 | 粉丝 | 摘要 | 最近更新 |\n|---|---|---|---|---|---|\n"
                "| A | 小红书 | x | 1 | y | 2026-10-22 |\n| B | 小红书 | x | 1 | y | 2026-10-01 |\n",
                encoding="utf-8",
            )
            codes = {issue.code for issue in validate_dir(Path(tmp), ledger)}
            self.assertIn("V8", codes)


class TestAddIsValidateFirst(unittest.TestCase):
    """拒收的行不得落盘——append-only 表没有回滚（agent-run-001 §4 发现的缺陷）。"""

    def test_rejected_row_leaves_table_untouched(self):
        import json

        from account_research.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            store.append_rows(data, "accounts", [account_row()])
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps([{
                "action_id": "p-x", "action": "抄骨架", "failure_condition": "",
            }], ensure_ascii=False), encoding="utf-8")
            code = main(["add", "playbook", "--file", str(bad), "--data", str(data), "--ledger", ""])
            self.assertEqual(code, 1)
            self.assertEqual(store.read_table(data, "playbook"), [])

    def test_accepted_row_is_written_once(self):
        import json

        from account_research.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            store.append_rows(data, "accounts", [account_row()])
            good = Path(tmp) / "good.json"
            row = {"action_id": "p-1", "action": "抄骨架", "failure_condition": "依赖团队时失效"}
            good.write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")
            for _ in range(2):
                self.assertEqual(main(["add", "playbook", "--file", str(good), "--data", str(data), "--ledger", ""]), 0)
            self.assertEqual(len(store.read_table(data, "playbook")), 1)


if __name__ == "__main__":
    unittest.main()
