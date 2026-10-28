"""书签脚本的静态约定检查。

**这不是功能测试，是红线测试。** 书签脚本活在浏览器里、能拿到用户已登录的页面，
一旦有人为了"省事"往里加抓取逻辑，整个项目的合规前提就没了。这里把允许和禁止的
写法钉成断言——改坏了测试立刻红。

注意：检查前先剥掉注释。注释里会提到 `fetch`、`cookie` 这些词（用来说明"不许用"），
直接全文搜索会把说明文字当成违规代码。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "capture-bookmarklet.js"
GUIDE = ROOT / "docs" / "书签安装指南.md"


def strip_comments(source: str) -> str:
    """去掉 // 行注释与 /* */ 块注释，只留可执行代码。"""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)


class TestBookmarkletRedLines(unittest.TestCase):
    def setUp(self):
        self.raw = SCRIPT.read_text(encoding="utf-8")
        self.code = strip_comments(self.raw)

    def test_no_network_calls(self):
        for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
            self.assertNotIn(banned, self.code, f"代码里出现了 {banned}：这会让它变成程序化采集")

    def test_no_navigation_or_polling(self):
        for banned in ("location.href =", "location.assign", "location.reload", "setInterval", "setTimeout"):
            self.assertNotIn(banned, self.code, f"代码里出现了 {banned}：翻页/轮询属于批量采集")

    def test_no_session_or_storage_access(self):
        for banned in ("document.cookie", "localStorage", "sessionStorage", "indexedDB"):
            self.assertNotIn(banned, self.code, f"代码读了 {banned}：会话数据不该被采集")

    def test_only_one_click_and_it_is_the_download_link(self):
        clicks = re.findall(r"\.click\(\)", self.code)
        self.assertEqual(len(clicks), 1, "只允许点一次，且必须是自己创建的下载链接")
        self.assertIn("link.click()", self.code)

    def test_download_is_local_blob(self):
        self.assertIn("new Blob", self.code)
        self.assertIn("URL.createObjectURL", self.code)

    def test_reads_single_node_not_a_list(self):
        self.assertIn("document.querySelector(", self.code)
        self.assertNotIn("document.querySelectorAll", self.code)  # 遍历列表 = 批量

    def test_counts_are_left_for_human(self):
        """赞/藏/评三个字段必须留空：数字由人照页面填，工具只输出互动栏原文。"""
        for field in ("likes", "collects", "comments"):
            self.assertIn(f'"{field}: "', self.code)
        self.assertIn("bar_text", self.code)
        self.assertIn("candidates", self.code)
        # 不允许出现「把解析值拼进 likes 字段」的写法
        self.assertNotRegex(self.code, r'"likes: "\s*\+\s*[a-zA-Z]')


class TestGuideMatchesScript(unittest.TestCase):
    def setUp(self):
        self.text = GUIDE.read_text(encoding="utf-8")

    def test_guide_has_installable_bookmarklet(self):
        self.assertIn("javascript:(function(){", self.text)
        self.assertIn("ar-note-", self.text)
        self.assertIn("AR-NOTE-v1", self.text)

    def test_guide_states_boundaries(self):
        for phrase in ("不发网络请求", "不模拟平台控件", "不翻页", "不读 Cookie"):
            self.assertIn(phrase, self.text)

    def test_guide_points_at_source_file(self):
        self.assertIn("capture-bookmarklet.js", self.text)


if __name__ == "__main__":
    unittest.main()
