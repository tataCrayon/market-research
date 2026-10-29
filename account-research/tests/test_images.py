"""图片通道：URL 绑定、两种存档形态、swiper 顺序、OCR 质量标记。

全部用合成 fixture，**不依赖 OCR 引擎**（引擎用假对象注入）——
真跑 OCR 的是 `ar images --ocr` 对真实存档，见 tests/README.md 的两套测试分工。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import images as img

NOTE_ID = "0000000000000000000000f1"


def detail_html(image_list: list[dict] | None = None, *, slide_names: list[str] | None = None,
                body: str = "第一段正文。", declared: int | None = None) -> str:
    """造一份最小详情页：og:url + 正文 + 可选 imageList + 可选 swiper + 可选张数计数器。"""
    parts = [
        "<!DOCTYPE html><html><head>",
        f'<meta property="og:url" content="https://www.xiaohongshu.com/explore/{NOTE_ID}" />',
        "</head><body>",
        f'<div id="detail-desc" class="desc"><span class="note-text">{body}</span></div>',
    ]
    if declared is not None:
        # 平台真实形态：<div class="fraction">1/26</div>，分母就是总张数
        parts.append(f'<div data-v-49df7ae2="" class="fraction">1/{declared}</div>')
    if image_list is not None:
        # 平台真实形态：`/` 在 JSON 里被转义成 \u002F
        raw = json.dumps(image_list, ensure_ascii=False).replace("/", "\\u002F")
        parts.append(f'<script>window.__INITIAL_STATE__={{"noteDetail":{{"imageList":{raw}}}}};</script>')
    if slide_names is not None:
        slides = "".join(
            f'<div class="swiper-slide" data-swiper-slide-index="{index}">'
            f'<div class="img-container"><img src="./测试 - 小红书_files/{name}"></div></div>'
            for index, name in enumerate(slide_names)
        )
        # swiper 首尾各复制一份：index 与首张/末张相同
        dup_head = (
            f'<div class="swiper-slide swiper-slide-duplicate" data-swiper-slide-index="0">'
            f'<div class="img-container"><img src="./测试 - 小红书_files/{slide_names[0]}"></div></div>'
        )
        dup_tail = (
            f'<div class="swiper-slide swiper-slide-duplicate" '
            f'data-swiper-slide-index="{len(slide_names) - 1}">'
            f'<div class="img-container"><img src="./测试 - 小红书_files/{slide_names[-1]}"></div></div>'
        )
        parts.append(
            '<div id="noteContainer" class="note-container">'
            f'<div class="note-slider"><div class="swiper-wrapper">{dup_head}{slides}{dup_tail}</div></div>'
            "<div class='note-scroller'></div>"
            # 推荐流：别人的封面（不切区间就会被算进来）
            '<div class="interact-container"></div>'
            '<div class="recommend"><img src="./测试 - 小红书_files/别人的封面.jpg"></div>'
            "</div>"
        )
    parts.append("</body></html>")
    return "\n".join(parts)


class ImageBindingTest(unittest.TestCase):
    def test_url_basename_strips_query_and_decodes(self):
        url = "http://sns-webpic-qc.xhscdn.com/20261029/abc/spectrum/1040g0k0abc!nd_dft_webp_3?imageView2/2/w/1080"
        self.assertEqual(img.url_basename(url), "1040g0k0abc!nd_dft_webp_3")

    def test_bind_by_url_tail_not_file_id(self):
        """实测纪律：fileId 是 `spectrum/…` 前缀的另一套 id，按它匹配会全漏。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "测试 - 小红书.html"
            files_dir = root / "测试 - 小红书_files"
            files_dir.mkdir()
            (files_dir / "1040g0k0abc!nd_dft_wlteh_webp_3").write_bytes(b"x")
            entry = {
                "fileId": "spectrum/1040g0k0abc",
                "width": 1440,
                "height": 2400,
                "urlDefault": "http://sns-webpic.xhscdn.com/20261029/hash/spectrum/1040g0k0abc!nd_dft_wlteh_webp_3",
            }
            archive.write_text(detail_html([entry]), encoding="utf-8")

            note = img.collect(archive, {}, archive.read_text(encoding="utf-8"), search_root=root)
            self.assertEqual(note.note_id, NOTE_ID)
            self.assertEqual(note.total, 1)
            self.assertEqual(note.local, 1)
            self.assertEqual(note.source, "raw_imageList")
            self.assertEqual(note.images[0].width, 1440)
            self.assertTrue(note.images[0].local.name.startswith("1040g0k0abc"))

    def test_html_only_save_is_marked_not_guessed(self):
        """只选了「仅 HTML」：图一个都没落地 → 标 (未存图)，不许拿相邻图凑。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "测试 - 小红书.html"
            archive.write_text(
                detail_html([{"fileId": "spectrum/abc", "urlDefault": "http://x/spectrum/abc!webp_3"}]),
                encoding="utf-8",
            )
            note = img.collect(archive, {}, archive.read_text(encoding="utf-8"), search_root=root)
            self.assertEqual(note.total, 1)
            self.assertEqual(note.local, 0)
            self.assertIn("未存图", note.source)
            self.assertEqual(note.missing(), [1])

    def test_static_assets_must_not_rebind_to_another_archive(self):
        """实测踩过的错配：每份存档都引用同名 JS/SVG，拿它们做反查会认错家。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "别人的"
            other.mkdir()
            other_files = other / "别人的_files"
            other_files.mkdir()
            (other_files / "index.a9f61821.js").write_bytes(b"x")
            (other_files / "1040g0k0other!nd_dft_wlteh_webp_3").write_bytes(b"x")
            # 别人的存档存在 → 这个目录有主
            (other / "别人的.html").write_text("<html></html>", encoding="utf-8")

            archive = root / "测试 - 小红书.html"
            archive.write_text(
                '<html><head><script src="./测试 - 小红书_files/index.a9f61821.js"></script></head>'
                '<body><div id="detail-desc">正文</div></body></html>',
                encoding="utf-8",
            )
            self.assertIsNone(img.locate_files_dir(archive, archive.read_text(encoding="utf-8"), root))

    def test_orphan_files_dir_is_recovered_by_content_names(self):
        """存档 HTML 被 ingest 搬走、_files 留在原地：靠内容文件名认回来。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files_dir = root / "旧位置_files"
            files_dir.mkdir()
            (files_dir / "1040g0k0abc!nd_dft_wlteh_webp_3").write_bytes(b"x")
            (files_dir / "1040g0k0def!nd_dft_wlteh_webp_3").write_bytes(b"x")
            archive = root / "sub" / "测试 - 小红书.html"
            archive.parent.mkdir()
            html = detail_html([
                {"fileId": "a", "urlDefault": "http://x/1040g0k0abc!nd_dft_wlteh_webp_3"},
                {"fileId": "b", "urlDefault": "http://x/1040g0k0def!nd_dft_wlteh_webp_3"},
            ])
            archive.write_text(html, encoding="utf-8")
            note = img.collect(archive, {}, html, search_root=root)
            self.assertEqual(note.local, 2)


class SliderOrderTest(unittest.TestCase):
    def test_slide_index_gives_order_and_dedupes_duplicates(self):
        names = [f"1040g0k0pic{i}!nd_dft_wlteh_webp_3" for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files_dir = root / "测试 - 小红书_files"
            files_dir.mkdir()
            for name in names:
                (files_dir / name).write_bytes(b"x")
            (files_dir / "别人的封面.jpg").write_bytes(b"x")
            archive = root / "测试 - 小红书.html"
            html = detail_html(slide_names=names)
            archive.write_text(html, encoding="utf-8")

            note = img.collect(archive, {}, html, search_root=root)
            self.assertEqual(note.source, "dom_slider")
            self.assertEqual(note.total, 4)  # duplicate 两张没被算进去
            self.assertEqual([image.local.name for image in note.images], names)
            self.assertNotIn("别人的封面.jpg", [image.local.name for image in note.images])


class OcrQualityTest(unittest.TestCase):
    def test_low_quality_when_too_short_or_low_score(self):
        self.assertTrue(img.OcrResult(lines=("A",), chars=1, mean_score=0.99).low_quality)
        self.assertTrue(img.OcrResult(lines=("一" * 30,), chars=30, mean_score=0.55).low_quality)
        self.assertFalse(img.OcrResult(lines=("一" * 30,), chars=30, mean_score=0.95).low_quality)

    def test_ocr_file_handles_engine_returning_none(self):
        class FakeEngine:
            def __call__(self, _path):
                return None, None

        result = img.ocr_file(FakeEngine(), Path("x.png"))
        self.assertEqual(result.chars, 0)
        self.assertTrue(result.low_quality)

    def test_render_text_keeps_one_block_per_image_and_flags_quality(self):
        note = img.NoteImages(
            note_id=NOTE_ID,
            source="dom_slider",
            images=[
                img.NoteImage(index=1, url="", file_id="", local=Path("a!webp_3"), width=1440, height=2400),
                img.NoteImage(index=2, url="", file_id="", local=Path("b!webp_3")),
            ],
        )
        results = {
            1: img.OcrResult(lines=("内容一" * 10,), chars=30, mean_score=0.99),
            2: img.OcrResult(lines=("内容二",), chars=3, mean_score=0.40),
        }
        text = img.render_text(note, results, {"title": "标题", "body_chars": 300})
        self.assertIn("## 图 1", text)
        self.assertIn("## 图 2", text)
        self.assertEqual(text.count("⚠️ 低质量"), 1)        # 只有图 2 被标
        self.assertIn("正文为主", text)                    # 图 33 字 / 正文 300 字

    def test_image_dominance_thresholds(self):
        self.assertEqual(img.image_dominance(0, 100), "图片无可读文字")
        self.assertIn("正文层空", img.image_dominance(100, 0))
        self.assertIn("图多于文", img.image_dominance(1000, 300))
        self.assertIn("正文为主", img.image_dominance(300, 1000))

    def test_missing_engine_names_the_install_command(self):
        """缺依赖要报「装什么」，不许静默降级成空文本。"""
        original = img.load_engine
        try:
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name.startswith("rapidocr"):
                    raise ImportError(name)
                return real_import(name, *args, **kwargs)

            builtins.__import__ = fake_import
            with self.assertRaises(img.OcrUnavailable) as ctx:
                img.load_engine()
            self.assertIn("rapidocr-onnxruntime", str(ctx.exception))
        finally:
            builtins.__import__ = real_import
            img.load_engine = original


class LayerRelationTest(unittest.TestCase):
    """两份文本的关系要按重叠判，不能按字数倍数猜。

    实测数据把这四种情形都撞出来了：
    - 卡兹克 4581：46 倍字数差，重叠 2.7% → 另写的导语
    - 知新 3259：2.1 倍，重叠 95.6% → 同一篇（图上只是重新排版带小标题）
    - 知新 510：1.4 倍，重叠 61% → 部分重叠
    **只看倍数会把 3259 误判成「压缩版」，把 4581 误判成「删减版」——两个都错。**
    """

    def test_same_article_detected_by_high_overlap(self):
        body = "看完马斯克最新的那场访谈就一个感受他想得太通透了" * 4
        image = "1 " + body + "\n2 " + body
        self.assertGreater(img.overlap_ratio(body, image), 0.9)
        self.assertIn("同一篇", img.layer_relation(body, image))

    def test_rewritten_teaser_detected_by_low_overlap(self):
        body = "这篇文章我从昨天写到了今天凌晨六点十五分" * 3
        image = "昨天早上可能会是人类时代的一个分水岭" * 6
        self.assertLess(img.overlap_ratio(body, image), 0.1)
        self.assertIn("另写一篇", img.layer_relation(body, image))

    def test_big_char_ratio_does_not_imply_rewrite(self):
        """字数差 46 倍、内容却完全相同的极端情形：倍数不能当判据。"""
        body = "同一段正文内容" * 20
        image = body * 46
        self.assertGreater(img.overlap_ratio(body, image), 0.99)
        self.assertIn("同一篇", img.layer_relation(body, image))

    def test_whitespace_and_punctuation_are_ignored(self):
        body = "普通人如何应对AI时代的变化"
        image = "普 通 人 如 何 应 对 A I 时 代 的 变 化"
        self.assertGreater(img.overlap_ratio(body, image), 0.99)

    def test_empty_side_reports_which_layer_is_empty(self):
        self.assertIn("图上无可读文字", img.layer_relation("有正文", ""))
        self.assertIn("正文层空", img.layer_relation("", "有图上文字"))

    def test_render_text_states_relation_not_just_ratio(self):
        note = img.NoteImages(note_id=NOTE_ID, source="dom_slider", images=[
            img.NoteImage(index=1, url="", file_id="", local=Path("a!webp_3")),
        ])
        results = {1: img.OcrResult(lines=("完全不同的另一段话内容",), chars=10, mean_score=0.99)}
        text = img.render_text(note, results, {"title": "标题", "body_chars": 300,
                                               "body_text": "这一段正文字跟图上完全不一样" * 3})
        self.assertIn("篇幅：", text)
        self.assertIn("与正文层的关系：", text)
        self.assertIn("另写一篇", text)


class DeclaredTotalTest(unittest.TestCase):
    """平台自己给的「共几张」，用来区分「我们漏了」和「原始数据就这么多」。

    实测 5 篇详情页全部带 `<div class="fraction">1/26</div>`，分母与我们绑到的张数一致。
    这个字段的价值只在**不一致**时体现——那时它是唯一的失败信号。
    """

    def test_declared_total_is_read_from_page_not_from_our_count(self):
        html = detail_html([{"fileId": "spectrum/a", "urlDefault": "http://x/spectrum/a!webp_3"}],
                           declared=9)
        self.assertEqual(img.declared_total(html), 9)

    def test_missing_counter_yields_none_not_a_guess(self):
        """没有计数器就写 None：不许用我们自己绑到的张数冒充「平台说就这么多」。"""
        self.assertIsNone(img.declared_total(detail_html([])))

    def test_bound_fewer_than_declared_is_flagged_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "测试 - 小红书.html"
            files_dir = root / "测试 - 小红书_files"
            files_dir.mkdir()
            (files_dir / "1040g0k0abc!nd_dlt_wlteh_webp_3").write_bytes(b"x")
            archive.write_text(
                detail_html([{"fileId": "spectrum/abc",
                              "urlDefault": "http://x/spectrum/1040g0k0abc!nd_dlt_wlteh_webp_3"}],
                            declared=8),
                encoding="utf-8",
            )
            note = img.collect(archive, {}, archive.read_text(encoding="utf-8"), search_root=root)
            self.assertEqual(note.total, 1)
            self.assertEqual(note.declared, 8)
            self.assertTrue(note.incomplete)

    def test_bound_all_declared_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "测试 - 小红书.html"
            files_dir = root / "测试 - 小红书_files"
            files_dir.mkdir()
            (files_dir / "1040g0k0abc!nd_dlt_wlteh_webp_3").write_bytes(b"x")
            archive.write_text(
                detail_html([{"fileId": "spectrum/abc",
                              "urlDefault": "http://x/spectrum/1040g0k0abc!nd_dlt_wlteh_webp_3"}],
                            declared=1),
                encoding="utf-8",
            )
            note = img.collect(archive, {}, archive.read_text(encoding="utf-8"), search_root=root)
            self.assertFalse(note.incomplete)

    def test_render_text_states_completeness(self):
        note = img.NoteImages(note_id=NOTE_ID, source="raw_imageList", declared=9,
                              images=[img.NoteImage(index=1, url="", file_id="", local=Path("a"))])
        results = {1: img.OcrResult(lines=("内容" * 10,), chars=20, mean_score=0.99)}
        text = img.render_text(note, results, {"title": "标题", "body_chars": 100, "body_text": "正文" * 20})
        self.assertIn("平台声明 9", text)
        self.assertIn("不全", text)


class BodyThinTest(unittest.TestCase):
    def test_body_chars_counts_only_desc_paragraphs(self):
        html = detail_html(body="短")
        self.assertEqual(img.body_chars(html), 1)
        self.assertTrue(img.body_is_thin(html, threshold=40))
        self.assertFalse(img.body_is_thin(detail_html(body="长" * 80), threshold=40))

    def test_note_without_desc_counts_as_thin(self):
        self.assertTrue(img.body_is_thin("<html><body>无正文</body></html>"))


if __name__ == "__main__":
    unittest.main()
