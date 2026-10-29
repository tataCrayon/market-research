"""正文分段：小红书的段落分隔是裸换行，不是 <p>/<br>。

2026-10-29 踩到的坑：第一版正文抽取把空白全压成一个空格，
900 字的长文变成一整块——**「他分了几段」这个结构证据直接消失**。
而段落数正是爆款拆解要看的东西（钩子在第几段、有没有空行隔断、结尾有无追问）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.detail import body_paragraphs, extract_detail  # noqa: E402

# 真实存档里的形状：段落之间是 `\n\t\n`（换行 + 缩进空白 + 换行），不是 <p>
REAL_SHAPE = """<span class="note-text">
刷视频时看到个观点，说我们可能只有几年时间适应工作方式的巨变。
\t
想想身边确实在变：设计师朋友不再熬夜肝图了。

关键问题来了：谁会被淘汰？
</span>"""

DESC_HTML = f"""<html><head>
<meta property="og:url" content="https://www.xiaohongshu.com/explore/68ccee2c000000001400bb08" />
<title>测试 - 小红书</title></head><body>
<div class="note-scroller"><div class="note-content">
  <h1 id="detail-title">普通人如何应对AI时代的变化</h1>
  <div id="detail-desc">{REAL_SHAPE}
    <a id="hash-tag">#人工智能</a></div>
</div></div>
<div class="interact-container">
  <span class="like-wrapper"><span class="count">4512</span></span>
  <span class="collect-wrapper"><span class="count">4475</span></span>
  <span class="chat-wrapper"><span class="count">186</span></span>
</div>
</body></html>"""


class TestBodyParagraphs(unittest.TestCase):
    def test_blank_line_splits_paragraphs(self):
        paras = body_paragraphs(REAL_SHAPE)
        self.assertEqual(len(paras), 3, f"应切出 3 段，实际 {paras}")

    def test_single_newline_is_joined_not_split(self):
        """段落内部的单换行是排版折行，不是分段——不能切出新段。"""
        raw = "第一行\n第二行\n\n新段落"
        paras = body_paragraphs(raw)
        self.assertEqual(len(paras), 2)
        self.assertIn("第一行 第二行", paras[0])

    def test_tab_and_fullwidth_space_count_as_blank(self):
        raw = "甲\n\t\n乙\n\u3000\n丙"
        self.assertEqual(len(body_paragraphs(raw)), 3)

    def test_empty_input(self):
        self.assertEqual(body_paragraphs(""), ())
        self.assertEqual(body_paragraphs("  \n\n  "), ())

    def test_paragraph_text_is_trimmed(self):
        paras = body_paragraphs("  甲  \n\n  乙  ")
        self.assertEqual(paras, ("甲", "乙"))

    def test_extract_detail_keeps_paragraphs(self):
        fields = extract_detail(DESC_HTML)
        self.assertEqual(len(fields.paragraphs), 3)
        self.assertEqual(fields.body, " ".join(fields.paragraphs))

    def test_paragraph_evidence_is_usable(self):
        """段落数要能直接拿来做判断——这是它存在的理由。"""
        fields = extract_detail(DESC_HTML)
        self.assertGreater(len(fields.paragraphs), 1, "压平成一整块就失去了结构证据")
        self.assertTrue(fields.paragraphs[0].startswith("刷视频时看到个观点"))


if __name__ == "__main__":
    unittest.main()
