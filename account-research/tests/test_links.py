"""links 命令：把缺口变成可点击的清单。"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.cli import main  # noqa: E402


def note_row(note_id: str, likes: int, **extra) -> dict:
    row = {
        "account_id": "acc1", "note_id": note_id, "published_at": "2026-10-01", "title": "标题-" + note_id,
        "form": "图文", "likes": likes, "pinned": "0", "source_file": "f.html",
        "source_kind": "公开页", "collected_on": "2026-10-27", "grade": "②",
    }
    row.update(extra)
    return row


class TestLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.text_dir = Path(self.tmp.name) / "notes-text"
        self.text_dir.mkdir()
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "collected_on": "2026-10-27", "grade": "②"}])
        store.append_rows(self.data, "notes", [
            note_row("aaa", 100),
            note_row("bbb", 900),
            note_row("ccc", 500, collects="9", comments="2"),
            note_row("ddd", 700, collects="3", comments="1"),
        ])
        # 只有 ddd 有正文；ccc 数字齐但没正文 —— 这种以前会被漏掉
        (self.text_dir / "ddd.txt").write_text("正文", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["links", "--data", str(self.data),
                         "--text-dir", str(self.text_dir), *extra])
        return code, buffer.getvalue()

    def test_sorted_by_likes(self):
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertLess(output.index("bbb"), output.index("ccc"))   # 高赞在前
        self.assertLess(output.index("ccc"), output.index("aaa"))
        self.assertIn("https://www.xiaohongshu.com/explore/bbb", output)

    def test_counts_complete_but_no_body_is_listed(self):
        """数字齐 ≠ 能分析。正文才是要读的东西，缺正文的必须列出来。

        实测踩过：2 篇藏/评齐全但没正文的篇目从来没进过清单，因为旧逻辑
        「数字齐全就不用再打开页面」——理由是「页面只多给正文」，
        而正文恰恰是要分析的东西。**这条错误让那 2 篇一直没被采。**
        """
        _, output = self._run()
        self.assertIn("ccc", output)
        self.assertIn("缺正文", output)

    def test_fully_covered_note_is_skipped(self):
        _, output = self._run()
        self.assertNotIn("ddd", output)      # 数字齐 + 有正文 → 不用再打开

    def test_limit_and_file_output(self):
        target = Path(self.tmp.name) / "todo.md"
        code, _ = self._run("--limit", "1", "--out", str(target))
        self.assertEqual(code, 0)
        text = target.read_text(encoding="utf-8")
        self.assertIn("/explore/bbb", text)
        self.assertNotIn("/explore/aaa", text)

    def test_usage_hint_points_at_new_tab(self):
        _, output = self._run()
        self.assertIn("在新标签页中打开", output)

    def test_extension_is_offered_as_an_easier_path(self):
        """手动 Ctrl+S 是摩擦的根源（case-001：采集占全程 80%）。
        清单里必须同时给出扩展这条路，否则人只会看到最费力的那一种。"""
        _, output = self._run()
        self.assertIn("extension/", output)

    def test_stale_tokens_are_called_out_loudly(self):
        """token 有年龄。只说「带 token 12/12」等于把「有 token」说成「能打开」。

        实测（2026-11-12）：清单里的链接点开是「页面不存在」——token 是 21 天前的存档里读的，
        而输出只报了覆盖数，没有任何一句提示它可能已经失效。
        """
        import dataclasses

        from account_research import tokens as tokens_module

        stale = {
            "bbb": tokens_module.TokenEntry("TOK", "卡兹克-主页-20261022.html", "2026-10-22"),
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            import account_research.cli as cli_module
            original = cli_module.build_token_index
            cli_module.build_token_index = lambda _roots: stale
            try:
                cli_module.main(["links", "--data", str(self.data),
                                 "--text-dir", str(self.text_dir),
                                 "--on", "2026-11-12", "--limit", "0"])
            finally:
                cli_module.build_token_index = original
        output = buffer.getvalue()
        self.assertIn("大概率打不开", output)
        self.assertIn("21 天前", output)
        self.assertIn("主页路径永远带新鲜 token", output)

    def test_fresh_tokens_are_not_flagged(self):
        from account_research import tokens as tokens_module

        fresh = {
            "bbb": tokens_module.TokenEntry("TOK", "卡兹克-主页-20261112.html", "2026-11-12"),
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            import account_research.cli as cli_module
            original = cli_module.build_token_index
            cli_module.build_token_index = lambda _roots: fresh
            try:
                cli_module.main(["links", "--data", str(self.data),
                                 "--text-dir", str(self.text_dir),
                                 "--on", "2026-11-12", "--limit", "0"])
            finally:
                cli_module.build_token_index = original
        self.assertNotIn("大概率打不开", buffer.getvalue())

    def test_retry_hint_does_not_break_the_table(self):
        """「以前采过」的提示行必须插在表格**之前**。

        早先写死 `lines.insert(11, ...)`——那是按当时那份清单的行数算出来的位置。
        清单短一行，提示就会插到表中间，markdown 表被撑坏（表格行之间夹一行 `>`），
        文件看起来还是「生成了」，只是表格废了。改成按内容定位插入点。
        """
        store.append_rows(self.data, "notes", [
            note_row("eee", 450, source_kind="复制文本"),   # 曾经采过详情页
        ])
        target = Path(self.tmp.name) / "todo.md"
        self._run("--limit", "0", "--out", str(target))
        lines = target.read_text(encoding="utf-8").splitlines()
        hint = next(line for line in lines if line.startswith("> 🔁"))
        first_row = next(index for index, line in enumerate(lines)
                         if line.startswith("| 1 |"))
        self.assertLess(lines.index(hint), first_row)
        header = next(line for line in lines if line.startswith("| # |"))
        self.assertEqual(header.count("|"), 6, "表头应是 5 列")


class TestRoundedCountsDoNotBreakLists(unittest.TestCase):
    """平台过万后把计数渲染成 `1.2万`——清单类命令**不能因此整条崩掉**。

    实测（2026-11-03）：只要有一篇破万，`ar gaps` / `ar links` 就
    `ValueError: invalid literal for int() with base 10: '1.2万'`。
    这两个命令的全部价值是「告诉人去点哪几篇」，它不该因为一个单元格的写法而不可用。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.text_dir = Path(self.tmp.name) / "notes-text"
        self.text_dir.mkdir()
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "collected_on": "2026-10-27", "grade": "②"}])
        store.append_rows(self.data, "notes", [
            note_row("low", 300),
            note_row("high", "1.2万"),     # ← 真实平台写法
            note_row("mid", "3,567"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main([*args, "--data", str(self.data)])
        return code, buffer.getvalue()

    def test_links_survives_rounded_count(self):
        code, output = self._run("links")
        self.assertEqual(code, 0)
        # 1.2万 要排在 3,567 前面（换算后比大小，不是按字符串）
        self.assertLess(output.index("high"), output.index("mid"))
        self.assertLess(output.index("mid"), output.index("low"))

    def test_gaps_survives_rounded_count(self):
        code, output = self._run("gaps")
        self.assertEqual(code, 0)
        self.assertIn("high", output.replace("note_id=", ""))

    def test_rounded_value_shown_with_approx_mark(self):
        """清单里展示圆整值时要能看出它不是精确值。"""
        _, output = self._run("gaps")
        self.assertIn("≈12,000", output)


if __name__ == "__main__":
    unittest.main()
