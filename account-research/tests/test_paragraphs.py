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

from account_research.detail import body_paragraphs, extract_detail, is_list_item  # noqa: E402

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


class TestListItems(unittest.TestCase):
    """项目符号必须各自成段。

    实测（2026-10-29）：那篇 110 赞的笔记正文层有 4 条项目符号，
    被合并成了一段——「8 段 + 4 条列表」这个结构证据丢了一半。
    而**清单式说明正是那篇的骨架**（正文里说「接了一点点自动化任务做演示：」然后逐条列）。
    把它们压成一段，会让人以为作者只是堆了一句话。
    """

    def test_bullets_split_into_separate_paragraphs(self):
        raw = "演示：\n\t\n- 甲。\n- 乙。\n- 丙。\n\t\n结束。"
        paras = body_paragraphs(raw)
        self.assertEqual(len(paras), 5, f"应为 5 段（1 引 + 3 列表 + 1 收），实际 {paras}")
        self.assertEqual([p for p in paras if is_list_item(p)], ["- 甲。", "- 乙。", "- 丙。"])

    def test_various_bullet_styles(self):
        raw = "- 甲\n• 乙\n· 丙\n1. 丁\n2、戊\n3) 己"
        paras = body_paragraphs(raw)
        self.assertEqual(len(paras), 6, f"六种符号都要认，实际 {paras}")

    def test_plain_wrapped_lines_still_merge(self):
        """普通折行不能因为「看起来像列表」被切开——它没有项目符号。"""
        paras = body_paragraphs("第一行\n第二行\n- 真列表")
        self.assertEqual(paras, ("第一行 第二行", "- 真列表"))

    def test_negative_number_is_not_a_bullet(self):
        """「2026.」是年份不是编号；行内出现的「-」也不是项目符号。"""
        self.assertFalse(is_list_item("2026 年发生了变化"))
        self.assertFalse(is_list_item("这件事 - 说起来 - 很复杂"))


if __name__ == "__main__":
    unittest.main()
