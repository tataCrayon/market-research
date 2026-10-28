"""多账号归属：一个窗口混采 3 个号，靠 author_id 各归各位。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.detail import AUTHOR_LINK_RE, extract_detail  # noqa: E402

ACC_A = "62c98736000000001501e075"
ACC_B = "614363ea000000000201bdfc"

DETAIL_HTML = """<html><head>
<meta property="og:url" content="https://www.xiaohongshu.com/explore/6adc2f910000000024039fde" />
<title>测试 - 小红书</title></head><body>
<div class="interaction-container"><div class="author-container"><div class="author-wrapper">
  <div class="info">
    <div class="avatar-click"><a href="https://www.xiaohongshu.com/user/profile/{acc}?xsec_token=x">
      <img class="avatar-item"></a></div>
    <a href="https://www.xiaohongshu.com/user/profile/{acc}?channel_type=web_user_page" class="name">
      <span class="username">某博主</span></a>
  </div>
</div></div></div>
<div class="note-scroller">
  <div class="note-content">
    <h1 id="detail-title" class="title">测试标题</h1>
    <div id="detail-desc" class="desc"><span class="note-text">正文。</span>
      <a id="hash-tag">#标签</a></div>
  </div>
</div>
<div class="interact-container">
  <span class="like-wrapper"><span class="count">777</span></span>
  <span class="collect-wrapper"><span class="count">88</span></span>
  <span class="chat-wrapper"><span class="count">9</span></span>
</div>
</body></html>"""


class TestAuthorIdExtraction(unittest.TestCase):
    def test_reads_author_profile_link(self):
        html = DETAIL_HTML.format(acc=ACC_A)
        fields = extract_detail(html)
        self.assertEqual(fields.author_id, ACC_A)
        self.assertEqual(fields.author, "某博主")

    def test_author_link_regex_ignores_background_feed(self):
        """背景推荐流里也有 /user/profile/ 链接，必须只认 author-container 里那个。"""
        html = DETAIL_HTML.format(acc=ACC_A).replace(
            "<body>", f'<body><a href="https://www.xiaohongshu.com/user/profile/{ACC_B}">别人</a>'
        )
        self.assertEqual(extract_detail(html).author_id, ACC_A)
        self.assertEqual(AUTHOR_LINK_RE.search(html).group(1), ACC_A)

    def test_missing_author_link_yields_empty(self):
        html = DETAIL_HTML.format(acc=ACC_A).replace("author-container", "other-container")
        self.assertEqual(extract_detail(html).author_id, "")


class TestMultiAccountRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        store.append_rows(self.data, "accounts", [
            {"account_id": ACC_A, "nickname": "A 号", "collected_on": "2026-10-28", "grade": "②"},
            {"account_id": ACC_B, "nickname": "B 号", "collected_on": "2026-10-28", "grade": "②"},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def _note(self, note_id: str, account_id: str) -> dict:
        return {"account_id": account_id, "note_id": note_id, "title": "旧标题", "form": "图文",
                "likes": "100", "pinned": "0", "collected_on": "2026-10-27",
                "source_kind": "公开页", "grade": "②"}

    def _run(self, rows: list[dict]) -> int:
        import argparse
        from account_research.cli import _detail_rows
        args = argparse.Namespace(data=str(self.data), ledger="", on="2026-10-28", account="")
        return _detail_rows(args, rows, allow_new=True)

    def test_new_note_routed_by_author_id(self):
        code = self._run([{"note_id": "n-new", "title": "新笔记", "author_id": ACC_B,
                           "likes": "500", "collects": "60", "comments": "7"}])
        self.assertEqual(code, 0)
        row = next(r for r in store.read_table(self.data, "notes") if r["note_id"] == "n-new")
        self.assertEqual(row["account_id"], ACC_B, "应按作者 id 归到 B 号，而不是默认第一个")

    def test_foreign_author_is_rejected(self):
        code = self._run([{"note_id": "n-x", "title": "外人笔记", "author_id": "ffffffffffffffffffffffff",
                           "likes": "1"}])
        self.assertEqual(code, 1)
        self.assertEqual(store.read_table(self.data, "notes"), [])

    def test_new_authors_flag_creates_placeholder_account(self):
        """显式开 --new-authors 才收外来作者，且必须建成『缺主页』的占位账号。"""
        import argparse
        from account_research.cli import _detail_rows
        args = argparse.Namespace(data=str(self.data), ledger="", on="2026-10-28",
                                  account="", new_authors=True)
        code = _detail_rows(args, [{
            "note_id": "n-foreign", "title": "外人笔记", "author_id": "ffffffffffffffffffffffff",
            "author_name": "某新博主", "likes": "42", "collects": "7", "comments": "1",
        }], allow_new=True)
        self.assertEqual(code, 0)
        created = [r for r in store.read_table(self.data, "accounts")
                   if r["account_id"] == "ffffffffffffffffffffffff"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["nickname"], "某新博主")
        self.assertIn("缺主页存档", created[0]["note"])
        self.assertEqual(created[0]["fans"], "")   # 粉丝必须留空，不能编
        note = next(r for r in store.read_table(self.data, "notes") if r["note_id"] == "n-foreign")
        self.assertEqual(note["account_id"], "ffffffffffffffffffffffff")

    def test_existing_note_keeps_its_account(self):
        store.append_rows(self.data, "notes", [self._note("n-old", ACC_A)])
        code = self._run([{"note_id": "n-old", "author_id": ACC_B, "likes": "999",
                           "collects": "11", "comments": "2"}])
        self.assertEqual(code, 0)
        merged = store.latest_per_note(store.read_table(self.data, "notes"))[0]
        self.assertEqual(merged["account_id"], ACC_A, "已有笔记不能被改到别的账号名下")
        self.assertEqual(merged["likes"], "999")


if __name__ == "__main__":
    unittest.main()
