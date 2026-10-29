"""采集进度视图与单文件 ingest。"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.cli import main  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestSingleFileIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.single = self.work / "一篇.html"
        shutil.copy(FIXTURES / "minimal_profile.html", self.single)
        # 正文归档只写临时目录：真实 account/notes-text/ 是证据
        self.text_dir = Path(self.tmp.name) / "notes-text"

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_file_does_not_need_inbox(self):
        code = main(["ingest", str(self.single), "--on", "2026-10-27", "--data", str(self.data),
                     "--ledger", "", "--text-dir", str(self.text_dir)])
        self.assertEqual(code, 0)
        self.assertEqual(len(store.read_table(self.data, "accounts")), 1)
        self.assertFalse(self.single.exists())          # 已归档

    def test_ingest_moves_files_dir_along_with_html(self):
        """`_files/` 必须跟着 HTML 一起走。

        实测踩过：Kimi 那篇的 HTML 进了 `processed/`，26 张图却留在 `account/` 根目录成孤儿。
        当时靠 `locate_files_dir` 反查救了回来——**但那是补丁，不该是常态**：
        孤儿目录一旦被挪走或删掉，图片通道就直接失效，而 HTML 看起来完全正常。
        """
        files_dir = self.work / "一篇_files"
        files_dir.mkdir()
        (files_dir / "1040g0k0abc!nd_dft_wlteh_webp_3").write_bytes(b"fake-image")
        code = main(["ingest", str(self.single), "--on", "2026-10-27", "--data", str(self.data),
                     "--ledger", "", "--text-dir", str(self.text_dir)])
        self.assertEqual(code, 0)
        self.assertFalse(files_dir.exists(), "_files 应已搬走，不留孤儿")
        archived = list((self.work / "processed").rglob("一篇_files"))
        self.assertEqual(len(archived), 1, "应搬到 processed/<日期>/ 下跟 HTML 同层")
        self.assertTrue((archived[0] / "1040g0k0abc!nd_dft_wlteh_webp_3").is_file())

    def test_status_reports_next_step(self):
        main(["ingest", str(self.single), "--on", "2026-10-27", "--data", str(self.data),
              "--ledger", "", "--text-dir", str(self.text_dir)])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["status", "--data", str(self.data)])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("笔记", output)
        self.assertIn("下一步", output)

    def test_status_on_empty_library_points_to_ingest(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["status", "--data", str(self.data)])
        self.assertEqual(code, 0)
        self.assertIn("ar ingest", buffer.getvalue())

    def test_status_separates_missing_body_from_missing_numbers(self):
        """「藏/评齐」不等于「能分析」。

        实测踩过：status 只报「待补 60 篇」，而实际要打开 62 篇——
        差在 2 篇数字齐、却没正文的。这两个缺口混成一个数字，
        会让人以为「数字齐的那批已经完事了」，于是永远不去补正文。
        """
        main(["ingest", str(self.single), "--on", "2026-10-27", "--data", str(self.data),
              "--ledger", "", "--text-dir", str(self.text_dir)])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            main(["status", "--data", str(self.data)])
        output = buffer.getvalue()
        self.assertIn("缺正文", output)
        self.assertIn("实际还要打开", output)


if __name__ == "__main__":
    unittest.main()
