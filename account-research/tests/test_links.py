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


if __name__ == "__main__":
    unittest.main()
