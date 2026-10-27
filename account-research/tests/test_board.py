"""采集看板：把一个本地 HTML 生成出来，验证它只是页面、不含抓取逻辑。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.board import render_board  # noqa: E402

URL = "https://www.xiaohongshu.com/explore/{note_id}"


def row(note_id: str, likes: int, title: str = "标题") -> dict:
    return {"note_id": note_id, "likes": likes, "title": title}


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.html = render_board([(row("aaa", 900), False), (row("bbb", 100, "带<尖括号>"), True)], URL, "测试号")

    def test_links_are_plain_anchors_with_note_url(self):
        self.assertIn(f'href="{URL.format(note_id="aaa")}"', self.html)
        self.assertIn('target="_blank"', self.html)

    def test_no_fetching_logic_in_board(self):
        """看板必须是纯静态页：出现这些字样就说明有人往里加了抓取/自动化。"""
        for banned in ("fetch(", "XMLHttpRequest", "setInterval", ".click()", "webkitRequestFileSystem"):
            self.assertNotIn(banned, self.html)
        self.assertNotIn("<script", self.html.lower())

    def test_title_is_escaped(self):
        self.assertIn("带&lt;尖括号&gt;", self.html)
        self.assertNotIn("带<尖括号>", self.html)

    def test_counts_and_step_hints(self):
        self.assertIn("共 2 篇", self.html)
        self.assertIn("1 篇已有正文", self.html)
        self.assertIn("Ctrl+S", self.html)

    def test_state_tag_reflects_has_text(self):
        self.assertIn("缺数字", self.html)
        self.assertIn("未采", self.html)


if __name__ == "__main__":
    unittest.main()
