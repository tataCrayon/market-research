"""watch：守着采件箱自动入库。

它监视的是**本地文件夹**，不是网页。测试要钉住三件事：
① 文件落地即入库并归档；② 已处理过的不重复入库；③ 坏的不能让它死掉。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AR = ROOT / "ar.py"


class TestWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data = base / "data"
        self.inbox = base / "inbox"
        self.inbox.mkdir()
        shutil.copytree(ROOT / "data", self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _spawn(self) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-u", str(AR), "watch",
             "--path", str(self.inbox), "--interval", "0.5",
             "--on", "2026-10-28", "--data", str(self.data), "--ledger", ""],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )

    def test_dropped_file_is_ingested_and_archived(self):
        before = len((self.data / "notes.csv").read_text(encoding="utf-8").splitlines())
        proc = self._spawn()
        try:
            time.sleep(1.2)
            shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "一篇.html")
            deadline = time.time() + 15
            archived = False
            while time.time() < deadline:
                if (self.inbox / "processed").exists() and any((self.inbox / "processed").rglob("*.html")):
                    archived = True
                    break
                time.sleep(0.4)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.assertTrue(archived, "文件没有被自动入库归档")
        after = len((self.data / "notes.csv").read_text(encoding="utf-8").splitlines())
        self.assertGreater(after, before, "notes 表没有新增行")

    def test_bad_file_does_not_kill_the_watcher(self):
        proc = self._spawn()
        try:
            time.sleep(1.2)
            (self.inbox / "垃圾.html").write_text("<html>none</html>", encoding="utf-8")
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), "监视线程被坏文件搞死了")
            self.assertTrue((self.inbox / "垃圾.html").exists(), "坏文件应留在原地待排查")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
