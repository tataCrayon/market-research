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

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_file_does_not_need_inbox(self):
        code = main(["ingest", str(self.single), "--on", "2026-10-27", "--data", str(self.data), "--ledger", ""])
        self.assertEqual(code, 0)
        self.assertEqual(len(store.read_table(self.data, "accounts")), 1)
        self.assertFalse(self.single.exists())          # 已归档

    def test_status_reports_next_step(self):
        main(["ingest", str(self.single), "--on", "2026-10-27", "--data", str(self.data), "--ledger", ""])
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


if __name__ == "__main__":
    unittest.main()
