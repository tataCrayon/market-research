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
