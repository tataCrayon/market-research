"""xsec_token 索引：从**自己的存档**里读回平台发的访问参数，拼出能打开的链接。

这个模块存在的唯一理由是一个实测事实：
裸 `/explore/<id>` 现在返回 `/404?...error_code=300031 当前笔记暂时无法浏览`。
而主页 SSR 存档里每张 noteCard 都自带 `xsecToken`——它本来就在你的硬盘上。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.tokens import build_index, note_url  # noqa: E402

NOTE_A = "6ac719230000000014039dd8"
NOTE_B = "68ccee2c000000001400bb08"
TOKEN_A = "ABJV9aUMPAG9oBlaJ8YvqqsyXDLvC_etTeWVAEGlO4u1Y"
TOKEN_B = "ABaB8Jcy6cpbvdU9lu00K_i7eOLz5qylcHtNYew_fWXt4"

BLOCK = '{"id":"%s","noteCard":{"noteId":"%s","xsecToken":"%s"},"xsecToken":"%s"}'


def archive_html(*pairs: tuple[str, str]) -> str:
    body = ",".join(BLOCK % (nid, nid, tok, tok) for nid, tok in pairs)
    state = '{"user":{"noteQueries":[{"userId":"u1"}]},"feeds":[%s]}' % body
    return f"<html><script>window.__INITIAL_STATE__={state}</script></html>"


class TestBuildIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_token_from_note_card(self):
        (self.dir / "a.html").write_text(archive_html((NOTE_A, TOKEN_A)), encoding="utf-8")
        index = build_index([self.dir])
        self.assertEqual(index.get(NOTE_A), TOKEN_A)

    def test_multiple_archives_merge(self):
        (self.dir / "a.html").write_text(archive_html((NOTE_A, TOKEN_A)), encoding="utf-8")
        (self.dir / "b.html").write_text(archive_html((NOTE_B, TOKEN_B)), encoding="utf-8")
        index = build_index([self.dir])
        self.assertEqual(index.get(NOTE_A), TOKEN_A)
        self.assertEqual(index.get(NOTE_B), TOKEN_B)

    def test_skips_files_without_initial_state(self):
        (self.dir / "plain.html").write_text("<html>nothing</html>", encoding="utf-8")
        self.assertEqual(build_index([self.dir]), {})

    def test_falls_back_to_regex_on_broken_state(self):
        """存档被脚本改写导致 JSON 不合法时，正则兜底仍要能拿到 token。"""
        broken = "<html><script>window.__INITIAL_STATE__=" + '{"id":"%s","xsecToken":"%s"}' % (NOTE_A, TOKEN_A) + "{oops</script></html>"
        (self.dir / "broken.html").write_text(broken, encoding="utf-8")
        self.assertEqual(build_index([self.dir]).get(NOTE_A), TOKEN_A)

    def test_missing_dir_is_tolerated(self):
        self.assertEqual(build_index([self.dir / "nope"]), {})


class TestNoteUrl(unittest.TestCase):
    def test_with_token(self):
        url = note_url(NOTE_A, TOKEN_A)
        self.assertIn("?xsec_token=" + TOKEN_A, url)
        self.assertIn("xsec_source=pc_user", url)

    def test_without_token_returns_bare_url(self):
        """没 token 时给裸链接——并让调用方自己标注它会 404，不能假装正常。"""
        self.assertEqual(note_url(NOTE_A), f"https://www.xiaohongshu.com/explore/{NOTE_A}")


if __name__ == "__main__":
    unittest.main()
