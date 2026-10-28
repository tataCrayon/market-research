"""采集看板：把一个本地 HTML 生成出来，验证它只是页面、不含抓取逻辑。

2026-10-28 新增一条现实约束：**裸 `/explore/<id>` 链接已被平台判 404**
（`error_code=300031 当前笔记暂时无法浏览`），必须带 `xsec_token` 才打得开。
所以看板要能表达「有 token / 无 token」两种状态，并把无 token 的明确标红。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.board import render_board  # noqa: E402

URL = "https://www.xiaohongshu.com/explore/{note_id}"
TOKENS = {"aaa": "TOKEN_A", "bbb": "TOKEN_B"}


def row(note_id: str, likes: int, title: str = "标题") -> dict:
    return {"note_id": note_id, "likes": likes, "title": title}


class TestBoard(unittest.TestCase):
    def setUp(self):
        self.html = render_board(
            [(row("aaa", 900), False), (row("bbb", 100, "带<尖括号>"), True)], URL, "测试号",
            tokens=TOKENS,
        )

    def test_links_carry_token(self):
        """带 token 的链接必须真的拼进去——否则点开是 404。"""
        self.assertIn("?xsec_token=TOKEN_A&xsec_source=pc_user", self.html)
        self.assertIn("?xsec_token=TOKEN_B&xsec_source=pc_user", self.html)
        self.assertIn('href="https://www.xiaohongshu.com/explore/aaa', self.html)
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

    def test_missing_token_is_flagged_loudly(self):
        """没有 token 的篇目必须显式标红 + 页顶给出 404 说明，不能假装能打开。"""
        html = render_board([(row("ccc", 5), False)], URL, "测试号", tokens={})
        self.assertIn("无 token", html)
        self.assertIn("404", html)
        self.assertIn("error_code", html)


if __name__ == "__main__":
    unittest.main()
