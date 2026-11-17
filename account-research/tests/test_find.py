"""`find`：跨表检索——把「不能检索」变成一个确定性命令。

## 这组测试盯什么

PRD §1 把「不能问答」的根因写成「没有可检索单元：结论散落在 markdown 段落里」。
`ar find` 就是那个缺失的入口，所以它必须守住四条**会自己骗自己**的口径：

1. **查不到就说「库内无证据」**，不许给近似结论、不许用标题凑答案；
2. **每条命中都带可回查的编号**（finding_id / action_id / hypothesis_id / note_id + 文件行号）；
3. **筛选项不许被静默忽略**——给了 `--status` 却只搜了 findings 表，等于假装筛过；
4. **不评分不排名**（N5）：顺序只按表内编号，不按互动量重排。

第 3 条是本组最贵的：项目的失败模式一直是「看起来做了」，
而一个被丢掉的过滤条件不会报错，只会让结果变多、变得像「全都不满足」。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import find, schema  # noqa: E402
from account_research.cli import main  # noqa: E402

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


class LibraryFixture(unittest.TestCase):
    """一个两账号、四张表都非空的小库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.text = Path(self.tmp.name) / "notes-text"
        self.text.mkdir()
        write_table(self.data, "accounts", [
            {"account_id": "acc1", "nickname": "甲号", "fans": "1000", "collected_on": "2026-11-01"},
            {"account_id": "acc2", "nickname": "乙号", "fans": "500", "collected_on": "2026-11-01"},
        ])
        write_table(self.data, "notes", [
            {"account_id": "acc1", "note_id": "n1", "title": "AI 工具测评", "topic": "AI工具",
             "form": "图文", "likes": "900", "collects": "300", "comments": "50",
             "pinned": "0", "source_kind": "公开页", "collected_on": "2026-11-01", "grade": "②"},
            {"account_id": "acc1", "note_id": "n2", "title": "做菜日记", "topic": "",
             "form": "视频", "likes": "100", "pinned": "0",
             "source_kind": "公开页", "collected_on": "2026-11-01", "grade": "②"},
            # 同一篇的另一条通道行：合并后只该出现一次（E57：多来源必须有挑选规则）
            {"account_id": "acc1", "note_id": "n1", "title": "AI 工具测评", "likes": "900",
             "collects": "310", "source_kind": "复制文本", "collected_on": "2026-11-02", "grade": "②"},
        ])
        write_table(self.data, "findings", [
            {"finding_id": "F-1", "account_id": "acc1", "layer": "定位",
             "statement": "定位结论：面向 AI 工具使用者", "grade": "②", "used_in_verdict": "1",
             "evidence_ref": "报告 §2", "sampled_on": "2026-11-01",
             "sample_basis": "样本 2 篇", "applicability": "仅本号"},
            {"finding_id": "F-2", "account_id": "acc1", "layer": "选题",
             "statement": "选题结论：AI 母题贡献了主要爆文", "grade": "③-a",
             "used_in_verdict": "1", "evidence_ref": "报告 §3", "sampled_on": "2026-11-01",
             "sample_basis": "样本 2 篇", "applicability": "仅本号"},
            {"finding_id": "F-3", "account_id": "acc2", "layer": "产能",
             "statement": "产能结论：日更不可持续", "grade": "②", "used_in_verdict": "0",
             "evidence_ref": "报告 §6", "sampled_on": "2026-11-01",
             "sample_basis": "样本 1 篇", "applicability": "仅本号"},
        ])
        write_table(self.data, "playbook", [
            {"action_id": "A-1", "action": "四段骨架写 AI 选题",
             "mechanism": "结构稳定", "requires": "事实基底",
             "failure_condition": "没有可具体化的事实基底", "supporting_accounts": "1",
             "counterexample": "近期篇只拿到百级", "evidence_ids": "F-1", "verifiable": "可验证"},
            {"action_id": "A-2", "action": "名人观点转述",
             "mechanism": "借认知差", "requires": "人物有共识认知度",
             "failure_condition": "", "supporting_accounts": "2（甲号、乙号）",
             "counterexample": "", "evidence_ids": "F-1, F-3", "verifiable": "可验证"},
        ])
        write_table(self.data, "hypotheses", [
            {"hypothesis_id": "H-1", "statement": "公共性选题带来增量", "support": "两篇差 41 倍",
             "counter": "混杂变量", "experiment": "同日两稿", "status": "未验证",
             "due_on": "2026-12-01", "recorded_on": "2026-11-01"},
            {"hypothesis_id": "H-2", "statement": "评论率可作代理指标", "support": "三篇一致",
             "counter": "样本少", "experiment": "已跑一次", "status": "已证实",
             "due_on": "2026-11-20", "recorded_on": "2026-11-05"},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def search(self, *keywords, **kwargs):
        return find.search(self.data, list(keywords), text_dir=self.text, **kwargs)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(list(argv) + ["--data", str(self.data), "--text-dir", str(self.text)])
        return code, buffer.getvalue()


class TestMatching(LibraryFixture):
    def test_keywords_are_anded(self):
        """多个关键字是 AND：任一词不中就不算命中。

        OR 会把「AI」搜成半个库——而检索的价值在于缩小，不在于列举。
        """
        hits = self.search("AI", "母题").hits
        self.assertEqual([hit.key for hit in hits], ["F-2"])

    def test_matching_is_case_insensitive(self):
        self.assertTrue(self.search("ai").hits)

    def test_every_table_is_searched_by_default(self):
        result = self.search("AI")
        self.assertEqual(set(result.tables), set(find.TABLES))
        self.assertTrue({"F-1", "F-2", "A-1"} <= {hit.key for hit in result.hits})

    def test_hits_carry_a_traceable_id(self):
        """每条命中都必须能回查到表与行——否则它只是一段被搜出来的文字。"""
        for hit in self.search("AI").hits:
            self.assertTrue(hit.key, hit)
            self.assertIn(hit.table, find.TABLES)

    def test_merged_tables_do_not_pretend_to_know_the_line_number(self):
        """`notes` / `hypotheses` 检索的是折叠后的视图，行号对不上 CSV 的物理行。

        写一个错的行号比不写更坏：人会照着去翻，然后发现那里是另一条记录。
        """
        hits = self.search("AI").hits
        note_hit = next(hit for hit in hits if hit.table == "notes")
        finding_hit = next(hit for hit in hits if hit.table == "findings")
        self.assertEqual(note_hit.where, "notes.csv")
        self.assertRegex(finding_hit.where, r"^findings\.csv:\d+$")

    def test_note_hits_are_merged_across_channels(self):
        """同一篇有两个通道行时必须只出一次（否则同一个命中被数两遍）。"""
        hits = [hit for hit in self.search("测评").hits if hit.table == "notes"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].key, "n1")
        self.assertEqual(str(hits[0].row.get("collects")), "310",
                         "合并后应取更完整的那次观测")

    def test_hypothesis_uses_latest_record(self):
        """假设表可以有多行状态：检索必须只看最新那条（旧行是历史，不是现状）。"""
        write_table(self.data, "hypotheses", [
            {"hypothesis_id": "H-1", "statement": "公共性选题带来增量", "status": "未验证",
             "due_on": "2026-12-01", "recorded_on": "2026-11-01"},
            {"hypothesis_id": "H-1", "statement": "公共性选题带来增量", "status": "超期未验",
             "due_on": "2026-12-01", "recorded_on": "2026-11-10", "close_note": "没时间做"},
        ])
        hits = [hit for hit in self.search("公共性").hits if hit.table == "hypotheses"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(str(hits[0].row.get("status")), "超期未验")


class TestEmptyResult(LibraryFixture):
    def test_no_hit_answers_with_the_library_is_silent(self):
        """查不到就明说「库内无证据」，不许用相近结论凑答案。"""
        code, output = self.run_cli("find", "核聚变")
        self.assertEqual(code, 0)
        self.assertIn(find.NO_EVIDENCE, output)

    def test_empty_library_also_says_no_evidence(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["find", "AI", "--data", str(empty), "--text-dir", str(self.text)])
        self.assertEqual(code, 0)
        self.assertIn(find.NO_EVIDENCE, buffer.getvalue())

    def test_empty_result_offers_only_mechanical_next_steps(self):
        """下一步只能是「去跑哪条命令」，不能是「大概是这个意思」。"""
        _, output = self.run_cli("find", "核聚变")
        self.assertIn("ar status", output)


class TestFilters(LibraryFixture):
    def test_layer_filter_restricts_scope_instead_of_being_ignored(self):
        """`--layer` 只有 findings 有这个字段：范围必须收窄并**说出来**。

        静默忽略的后果：结果里混进 playbook，看起来「筛过了」，其实一个条件都没生效。
        """
        result = self.search("AI", layer="选题")
        self.assertEqual(result.tables, ("findings",))
        self.assertEqual([hit.key for hit in result.hits], ["F-2"])
        self.assertTrue(result.scope_note)

    def test_form_filter_restricts_to_notes(self):
        result = self.search("", form="视频")
        self.assertEqual(result.tables, ("notes",))
        self.assertEqual([hit.key for hit in result.hits], ["n2"])

    def test_empty_keyword_with_filter_lists_the_subset(self):
        """不写关键字、只给筛选条件 = 列子集。这是「按字段筛」的最小形态。"""
        result = self.search("", layer="定位")
        self.assertEqual([hit.key for hit in result.hits], ["F-1"])

    def test_conflicting_filters_are_an_error_not_a_silent_drop(self):
        """`--layer` 属于 findings、`--status` 属于 hypotheses：两者不可能同时成立。

        这时必须报错。悄悄丢掉其中一个，就是让用户以为自己筛过两件事。
        """
        with self.assertRaises(find.FilterError) as ctx:
            self.search("AI", layer="选题", status="未验证")
        # 提示里要出现的是**用户敲的那个参数名**，不是内部字段名
        self.assertIn("--layer", str(ctx.exception))
        self.assertIn("--status", str(ctx.exception))

    def test_table_argument_must_be_compatible_with_the_filters(self):
        with self.assertRaises(find.FilterError) as ctx:
            self.search("AI", tables=["playbook"], layer="选题")
        self.assertIn("playbook", str(ctx.exception))

    def test_min_support_filters_playbook_by_verified_accounts(self):
        """`supporting_accounts` 是自由文本（`1` / `1（知新的AI笔记）` / `2`）。

        直接 int() 会在带括号那种形态上崩——所以解析必须与 matrix 共用同一个函数。
        """
        result = self.search("", min_support=2)
        self.assertEqual(result.tables, ("playbook",))
        self.assertEqual([hit.key for hit in result.hits], ["A-2"])

    def test_grade_filter_accepts_a_prefix(self):
        """`--grade ③` 要同时命中 ③-a 与 ③-b：等级有三个三级值，人不会记全。"""
        self.assertEqual([hit.key for hit in self.search("AI", grade="③").hits], ["F-2"])

    def test_account_filter_spans_the_tables_that_have_it(self):
        """`--account` 在四张表里都有意义（notes/findings/verdicts/accounts），
        在没有它的表（playbook/hypotheses/fields）里必须被**排除而不是忽略**。"""
        result = self.search("结论", account="acc2")
        self.assertEqual(set(result.tables), {"notes", "findings", "verdicts", "accounts"})
        self.assertNotIn("playbook", result.tables)
        self.assertNotIn("hypotheses", result.tables)
        self.assertEqual([hit.key for hit in result.hits], ["F-3"])


class TestDisciplineFlags(LibraryFixture):
    def test_third_party_grade_used_in_verdict_is_flagged(self):
        """③ 级结论进了判决是 V4 违例。检索结果必须当场标出来。"""
        hit = next(item for item in self.search("母题").hits if item.key == "F-2")
        self.assertTrue(any("V4" in flag for flag in hit.flags), hit.flags)

    def test_playbook_without_failure_condition_is_flagged(self):
        hit = next(item for item in self.search("名人").hits if item.key == "A-2")
        self.assertTrue(any("V5" in flag for flag in hit.flags), hit.flags)

    def test_playbook_below_cross_account_threshold_is_flagged(self):
        hit = next(item for item in self.search("四段骨架").hits if item.key == "A-1")
        self.assertTrue(any("≥2" in flag for flag in hit.flags), hit.flags)

    def test_missing_note_fields_are_shown_as_missing_not_zero(self):
        """n2 没有藏/评：显示 ⬜，绝不写 0——0 是观测到的数。"""
        hit = next(item for item in self.search("做菜").hits if item.key == "n2")
        rendered = find.render(self.search("做菜"))
        self.assertIn("⬜", rendered)
        self.assertNotIn("藏 0", rendered)
        self.assertEqual(hit.row.get("collects") or "", "")


class TestOutput(LibraryFixture):
    def test_render_lists_table_name_and_id(self):
        text = find.render(self.search("AI"))
        self.assertIn("F-1", text)
        self.assertIn("findings", text)

    def test_render_is_deterministic(self):
        self.assertEqual(find.render(self.search("AI")), find.render(self.search("AI")))

    def test_order_is_by_id_not_by_popularity(self):
        """N5：不评分不排名。结果顺序只跟表内编号走，不按赞数重排。"""
        keys = [hit.key for hit in self.search("", form="图文").hits]
        self.assertEqual(keys, sorted(keys))

    def test_json_output_round_trips(self):
        code, output = self.run_cli("find", "AI", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["query"], ["AI"])
        self.assertTrue(payload["hits"])
        self.assertIn("table", payload["hits"][0])

    def test_limit_truncates_display_only(self):
        result = self.search("AI")
        limited = find.render(result, limit=1)
        self.assertIn("只显示", limited)
        self.assertIn(str(len(result.hits)), limited)


class TestTextSearch(LibraryFixture):
    def setUp(self):
        super().setUp()
        (self.text / "n1.txt").write_text(
            "第一段：AI 工具的真实成本。\n第二段：我花了三天。\n", encoding="utf-8")
        (self.text / "n1.images.txt").write_text(
            "## 图 1\n这一步用 OCR 才看得到的关键词：提示词工程\n", encoding="utf-8")
        (self.text / "n2.comments.txt").write_text(
            "读者说：这跟 AI 没关系吧\n", encoding="utf-8")

    def test_text_is_opt_in(self):
        """默认只搜表：正文是文件不是表，混进默认范围会让「命中」含义变得不确定。"""
        self.assertFalse([hit for hit in self.search("提示词工程").hits if hit.table == "text"])

    def test_text_hit_carries_file_and_line(self):
        hits = [hit for hit in self.search("提示词工程", text=True).hits if hit.table == "text"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].key, "n1")
        self.assertEqual(hits[0].line, 2)
        self.assertIn("images.txt", hits[0].where)

    def test_text_search_covers_all_three_channels(self):
        """正文 / 图上文字 / 评论是**三个独立文件**，检索必须都看。

        只搜 `<note_id>.txt` 就等于把「内容在图上」的那批篇目永久排除在检索之外——
        而实测正文层只占全文中位 28.1%，那批恰恰最需要被搜到。
        """
        hits = self.search("", text=True).hits
        layers = {hit.layer for hit in hits if hit.table == "text"}
        self.assertEqual(layers, {"正文", "图上文字", "评论"})

    def test_comment_layer_is_labelled_apart_from_body(self):
        """评论是「读者说什么」，正文是「他写什么」——混成一个层就分不清证据来源。"""
        hits = [hit for hit in self.search("没关系", text=True).hits if hit.table == "text"]
        self.assertEqual([hit.layer for hit in hits], ["评论"])


if __name__ == "__main__":
    unittest.main()
