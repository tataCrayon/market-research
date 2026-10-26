"""详情页人工补数通道：书签小工具的一块结构化文本 → notes 表。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import store  # noqa: E402
from account_research.cli import main  # noqa: E402
from account_research.parser import parse_blocks  # noqa: E402

BLOCK = """随便一些页面文字
===AR-NOTE-v1===
note_id: n1
title: 一篇笔记
likes: 4566
collects: 1612
comments: 457
shares: ⬜
duration: 00:14
human_note: 人看后写的一句话
url: https://example.invalid/explore/n1
raw_len: 812
candidates: 4566 | 1612 | 457
===END===
"""


class TestParseBlocks(unittest.TestCase):
    def test_block_fields_parsed_and_blank_marked(self):
        blocks, errors = parse_blocks(BLOCK)
        self.assertEqual(errors, [])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["note_id"], "n1")
        self.assertEqual(blocks[0]["collects"], "1612")
        self.assertEqual(blocks[0]["shares"], "")  # ⬜ 归一成空，不当作数据
        self.assertEqual(blocks[0]["duration"], "00:14")

    def test_text_outside_blocks_ignored(self):
        blocks, _ = parse_blocks("前言\n随便粘贴的正文\n===AR-NOTE-v1===\nnote_id: x\n===END===\n后记")
        self.assertEqual([b["note_id"] for b in blocks], ["x"])

    def test_unclosed_block_reported_not_silently_dropped(self):
        blocks, errors = parse_blocks("===AR-NOTE-v1===\nnote_id: y\n")
        self.assertEqual(blocks, [])
        self.assertTrue(any("未闭合" in message for message in errors))

    def test_nested_start_reported(self):
        _, errors = parse_blocks("===AR-NOTE-v1===\nnote_id: a\n===AR-NOTE-v1===\n===END===\n")
        self.assertTrue(any("没有" in message and "就开始了新块" in message for message in errors))


class TestRecordCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "collected_on": "2026-10-22", "grade": "②"}])
        base = {
            "account_id": "acc1", "note_id": "n1", "published_at": "2026-10-08", "title": "t",
            "form": "视频", "likes": "100", "pinned": "0", "source_file": "f.html",
            "source_kind": "公开页", "collected_on": "2026-10-22", "grade": "②",
        }
        store.append_rows(self.data, "notes", [base])

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text: str, name: str) -> str:
        path = Path(self.tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_parse_text_appends_detail_row_and_keeps_home_row(self):
        path = self._write(BLOCK, "paste.txt")
        code = main(["parse-text", "--file", path, "--on", "2026-10-22",
                     "--data", str(self.data), "--ledger", ""])
        self.assertEqual(code, 0)
        rows = [r for r in store.read_table(self.data, "notes") if r["note_id"] == "n1"]
        self.assertEqual(len(rows), 2)  # 主页 + 详情两条独立观测
        merged = store.latest_per_note(rows)[0]
        self.assertEqual(merged["collects"], "1612")
        self.assertEqual(merged["title"], "t")   # 详情块没给标题，不能覆盖成空
        self.assertEqual(merged["form"], "视频")

    def test_unknown_note_id_rejected(self):
        path = self._write(BLOCK.replace("note_id: n1", "note_id: ghost"), "paste.txt")
        code = main(["parse-text", "--file", path, "--on", "2026-10-22",
                     "--data", str(self.data), "--ledger", ""])
        self.assertEqual(code, 1)
        self.assertEqual(len(store.read_table(self.data, "notes")), 1)

    def test_record_json_accepted(self):
        path = self._write(json.dumps([{"note_id": "n1", "collects": 430, "comments": 38}]), "d.json")
        code = main(["record", "--file", path, "--on", "2026-10-22",
                     "--data", str(self.data), "--ledger", ""])
        self.assertEqual(code, 0)
        self.assertEqual(store.latest_per_note(store.read_table(self.data, "notes"))[0]["collects"], "430")


if __name__ == "__main__":
    unittest.main()
