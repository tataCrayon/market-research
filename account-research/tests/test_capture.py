"""采集闭环：采件箱 ingest、缺口清单 gaps。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import cli, store  # noqa: E402
from account_research.cli import main  # noqa: E402
from account_research.images import OcrUnavailable  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOTE_ID = "0000000000000000000000f1"


def detail_html(image_list: list[dict]) -> str:
    """最小详情页：og:url + 正文 + `imageList`（与 tests/test_images.py 同形态）。"""
    raw = json.dumps(image_list, ensure_ascii=False).replace("/", "\\u002F")
    return (
        "<!DOCTYPE html><html><head>"
        f'<meta property="og:url" content="https://www.xiaohongshu.com/explore/{NOTE_ID}" />'
        "</head><body>"
        '<div id="detail-desc" class="desc"><span class="note-text">第一段正文。</span></div>'
        f'<script>window.__INITIAL_STATE__={{"noteDetail":{{"imageList":{raw}}}}};</script>'
        "</body></html>"
    )


class FakeEngine:
    """假 OCR 引擎：不依赖 rapidocr——真跑引擎的是 `ar images --ocr` 对真实存档。"""

    def __call__(self, _path):
        return [[[0, 0, 1, 1], "图上的一段文字", 0.99]], None

BLOCK = """===AR-NOTE-v1===
note_id: n1
title: 一篇笔记
likes: 4566
collects: 1612
comments: 457
shares: ⬜
duration: 00:14
human_note: 视频 14 秒，画面 9/10 不可读
bar_text: 赞 4566 收藏 1612 评论 457
===END===
"""


def note_row(note_id: str, likes: int, **extra) -> dict:
    row = {
        "account_id": "acc1", "note_id": note_id, "published_at": "2026-10-01", "title": note_id,
        "form": "图文", "likes": likes, "pinned": "0", "source_file": "f.html",
        "source_kind": "公开页", "collected_on": "2026-10-22", "grade": "②",
    }
    row.update(extra)
    return row


class TestGaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "collected_on": "2026-10-22", "grade": "②"}])
        store.append_rows(self.data, "notes", [
            note_row("low", 50),
            note_row("high", 4000),
            note_row("done", 900, collects="20", comments="3", shares="1"),
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_gaps_sorted_by_likes_and_skips_complete(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["gaps", "--data", str(self.data)])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("缺藏/评的篇目：2 篇", output)
        self.assertLess(output.index("high"), output.index("low"))  # 高赞优先
        self.assertNotIn("note_id=done", output)


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.inbox = Path(self.tmp.name) / "inbox"
        self.inbox.mkdir()
        # 正文归档只写临时目录：真实 account/notes-text/ 是证据，测试不许碰
        self.text_dir = Path(self.tmp.name) / "notes-text"
        shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "主页.html")
        shutil.copy(FIXTURES / "minimal_search.html", self.inbox / "搜索页.html")
        (self.inbox / "详情.txt").write_text(BLOCK, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        return main(["ingest", str(self.inbox), "--on", "2026-10-22",
                     "--data", str(self.data), "--ledger", "", "--text-dir", str(self.text_dir)])

    def test_mixed_inbox_ingested_and_archived(self):
        self.assertEqual(self._run(), 0)
        accounts = store.read_table(self.data, "accounts")
        raw = store.read_table(self.data, "notes")
        merged = store.latest_per_note(raw)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["nickname"], "测试账号")
        # 原始行 3 = n1 主页 + n2 主页 + n1 详情（两条独立观测）；合并后 2 篇
        self.assertEqual(len(raw), 3)
        self.assertEqual(len(merged), 2)
        first = next(row for row in merged if row["note_id"] == "n1")
        self.assertEqual(first["collects"], "1612")           # 详情块补上的
        self.assertEqual(first["published_at"], "2025-10-21")  # 块里没给 → 保留主页的值，不覆盖成空
        archived = list((self.inbox / "processed" / "2026-10-22").iterdir())
        self.assertEqual({p.name for p in archived}, {"主页.html", "详情.txt"})
        self.assertTrue((self.inbox / "搜索页.html").is_file())  # 被拒的不归档，留着让人看

    def test_second_run_is_idempotent(self):
        self.assertEqual(self._run(), 0)
        # 归档后重新丢同一份进来，入库不应重复
        shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "主页2.html")
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(store.latest_per_note(store.read_table(self.data, "notes"))), 2)

    def test_empty_inbox_is_not_an_error(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(main(["ingest", str(empty), "--on", "2026-10-22",
                               "--data", str(self.data), "--ledger", ""]), 0)


class TestIngestRunsImageChannel(unittest.TestCase):
    """入库顺手跑图上文字，不该等人另记一条 `ar images --ocr`。

    实测漏的代价：7 篇有正文、只有 4 篇有图上文字，漏掉的那 3 篇里就有 26 图那篇长文——
    它的结构结论全在图里，而「有正文」这个状态看起来完全正常，谁也不会去查第二层。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.inbox = Path(self.tmp.name) / "inbox"
        self.inbox.mkdir()
        self.text_dir = Path(self.tmp.name) / "notes-text"
        # 真实顺序：先存主页（笔记进表）→ 再存详情页。详情页找不到底行会被拒收，
        # 那是正确行为（note_id 抄错 / 账号混了），不该为了让测试通过而放宽。
        store.append_rows(self.data, "accounts", [{"account_id": "acc1", "nickname": "测试号",
                                                  "collected_on": "2026-10-27"}])
        store.append_rows(self.data, "notes", [note_row(NOTE_ID, 100, account_id="acc1")])
        name = "1040g0k0abc!nd_dft_wlteh_webp_3"
        files_dir = self.inbox / "详情 - 小红书_files"
        files_dir.mkdir()
        (files_dir / name).write_bytes(b"fake-image")
        (self.inbox / "详情 - 小红书.html").write_text(
            detail_html([{"fileId": "spectrum/abc", "urlDefault": f"http://x/spectrum/{name}"}]),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, factory):
        """`factory` 是零参可调用，冒充 `load_engine()`——成功时返回引擎，失败时抛 OcrUnavailable。"""
        original = cli.load_engine
        cli.load_engine = factory
        try:
            return main(["ingest", str(self.inbox), "--on", "2026-10-27", "--data", str(self.data),
                         "--ledger", "", "--text-dir", str(self.text_dir)])
        finally:
            cli.load_engine = original

    def test_detail_page_gets_image_text_without_a_second_command(self):
        self.assertEqual(self._run(FakeEngine), 0)
        target = self.text_dir / f"{NOTE_ID}.images.txt"
        self.assertTrue(target.is_file(), "入库后应已产出图上文字")
        text = target.read_text(encoding="utf-8")
        self.assertIn("## 图 1", text)
        self.assertIn("篇幅：", text)          # 两行路标必须在，否则模型读不到结构
        self.assertIn("与正文层的关系：", text)

    def test_missing_engine_does_not_fail_the_ingest(self):
        """没装 OCR 是「这一层缺」不是「入库失败」——入库的价值不该被一个可选依赖绑死。"""
        def unavailable():
            raise OcrUnavailable("没装")

        self.assertEqual(self._run(unavailable), 0)
        self.assertEqual(len(store.read_table(self.data, "accounts")), 1, "入库本身应照常成功")
        self.assertFalse((self.text_dir / f"{NOTE_ID}.images.txt").exists())

    def test_no_ocr_flag_skips_the_channel(self):
        code = main(["ingest", str(self.inbox), "--on", "2026-10-27", "--data", str(self.data),
                     "--ledger", "", "--text-dir", str(self.text_dir), "--no-ocr"])
        self.assertEqual(code, 0)
        self.assertFalse((self.text_dir / f"{NOTE_ID}.images.txt").exists())


class TestDuplicateArchives(unittest.TestCase):
    """同一篇笔记有好几份存档时，必须说清哪份最全——否则 OCR 会让缺图的覆盖完整的。

    2026-11-13 实测：先存了一份只带 3 张图的（懒加载没滚完），
    滑到底重存了一份 16 张的。两份都在 `processed/` 里，
    `ar images` 对同一篇打印两行、**没有一行说哪份该用**；
    而 `--ocr` 会依次处理，后跑的旧存档把 `images.txt` 覆盖成 3 张的结果。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.text_dir = self.root / "notes-text"

    def tearDown(self):
        self.tmp.cleanup()

    def _archive(self, name: str, keep: int, total: int) -> Path:
        """造一份 swiper 存档：`total` 个 slide，只有前 `keep` 张有本地文件。"""
        files_dir = self.root / f"{name}_files"
        files_dir.mkdir(exist_ok=True)
        slides = []
        for index in range(total):
            file_name = f"img{index}!webp_3"
            if index < keep:
                (files_dir / file_name).write_bytes(b"x")
                src = f"./{name}_files/{file_name}"
            else:
                src = f"https://sns-webpic-qc.xhscdn.com/20261113/abc/spectrum/{file_name}"
            slides.append(
                f'<div class="swiper-slide" data-swiper-slide-index="{index}">'
                f'<img src="{src}"></div>'
            )
        path = self.root / f"{name}.html"
        path.write_text(
            "<!DOCTYPE html><html><head>"
            f'<meta property="og:url" content="https://www.xiaohongshu.com/explore/{NOTE_ID}" />'
            "</head><body>"
            # 平台自己的「第几张/共几张」计数器——没有它 `declared` 是 None，
            # 而「已绑全 / 不全」这套判断完全建立在它之上。测试里必须给。
            f'<div class="fraction">1/{total}</div>'
            '<div id="noteContainer"><div class="note-slider">'
            f'<div class="swiper-wrapper">{"".join(slides)}</div></div>'
            '<div class="note-scroller"></div></div></body></html>',
            encoding="utf-8",
        )
        return path

    def _run(self, argv: list[str]) -> str:
        import contextlib
        import io

        buffer = io.StringIO()
        original = cli.load_engine
        cli.load_engine = FakeEngine
        try:
            with contextlib.redirect_stdout(buffer):
                main(argv)
        finally:
            cli.load_engine = original
        return buffer.getvalue()

    def test_marks_which_archive_is_complete(self):
        self._archive("旧存档", keep=3, total=16)
        self._archive("新存档", keep=16, total=16)
        output = self._run(["images", "--root", str(self.root)])
        self.assertIn("这份最全", output)
        self.assertIn("旧存档（少 13 张图）", output)

    def test_ocr_only_runs_on_the_best_archive(self):
        """两行都跑 OCR 的话，后跑的那份会覆盖 `images.txt`——缺图的把完整的盖掉。"""
        self._archive("新存档", keep=16, total=16)
        self._archive("旧存档", keep=3, total=16)
        output = self._run(["images", "--root", str(self.root), "--ocr",
                            "--text-dir", str(self.text_dir)])
        self.assertIn("跳过 OCR", output)
        text = (self.text_dir / f"{NOTE_ID}.images.txt").read_text(encoding="utf-8")
        self.assertIn("已绑全", text, "产出必须是完整那份的结果")
        self.assertIn("## 图 16", text, "16 张都要有段落")


if __name__ == "__main__":
    unittest.main()
