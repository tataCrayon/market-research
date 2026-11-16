"""MHTML 拆分器：把「单文件网页存档」还原成 `HTML + _files/`。

## 为什么这条能力决定插件有没有用

2026-11-12 用户一句判断把方向定下来了：

> 「不能和 Ctrl+S 一样保存图片和我之前采集的 HTML+文件夹，那就没什么用才对」

数据支持这句话：实测 11 篇图文笔记，文字层占全文比例**中位数只有 28.1%**，
6 篇低于 30%，最极端两篇 **2.2% / 1.4%**（正文 299 字 / 图上 13,200 字）。
不带图的存档，对要深拆的那批篇目**等于一篇空笔记**。

MHTML 是浏览器自己的「单文件网页」格式：图、CSS、字体全打包在一个文件里。
`chrome.pageCapture.saveAsMHTML` 拿到的就是它，而它**不产生任何新请求**——
打包的是渲染这个页面时已经下载进内存的那份字节。

## 测试策略

不手拼「看起来像 MHTML」的字符串。真 Chrome 的 MHTML 有 `quoted-printable`、
RFC 5322 折行、`cid:` 与 `Content-Location` 两套引用。手拼样本测出来的东西不对。

所以用 Python 标准库 `email` **造**一份真实 MIME 结构的样本——和 Chrome 用的是同一套格式。
"""

from __future__ import annotations

import email.message
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import mhtml  # noqa: E402

NOTE_ID = "6ac48e16000000001203ed6e"
OG_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
CID_IMAGE_URL = "https://sns-img.xhscdn.com/cidimage"
ABS_IMAGE_URL = "https://sns-img.xhscdn.com/1040g2sg31mp21tlvmgkg5q3027tmsejk7i4pe9o"

PAGE = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta property="og:url" content="{OG_URL}">
<title>测试笔记</title></head>
<body>
<div id="detail-desc">这是正文第一段。

这是正文第二段。</div>
<img src="cid:img-a@mhtml.blink">
<img src="{ABS_IMAGE_URL}">
</body></html>"""


def build_mhtml(html: str = PAGE, with_parts: bool = True) -> bytes:
    """造一份与 Chrome 同构的 MHTML（multipart/related + 两种引用方式）。"""
    message = email.message.EmailMessage()
    message["MIME-Version"] = "1.0"
    message["Snapshot-Content-Location"] = OG_URL
    message.add_alternative(html, subtype="html", charset="utf-8")
    if not with_parts:
        return message.as_bytes()

    cid_part = email.message.EmailMessage()
    cid_part.set_content(b"\x89PNG\r\n\x1a\nFAKE-CID-IMAGE", maintype="image", subtype="png")
    cid_part["Content-ID"] = "<img-a@mhtml.blink>"
    cid_part["Content-Location"] = CID_IMAGE_URL
    message.attach(cid_part)

    abs_part = email.message.EmailMessage()
    abs_part.set_content(b"\x89PNG\r\n\x1a\nFAKE-ABS-IMAGE", maintype="image", subtype="png")
    abs_part["Content-Location"] = ABS_IMAGE_URL
    message.attach(abs_part)
    return message.as_bytes()


class TestLooksLikeMhtml(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_suffix_is_enough(self):
        path = self.dir / "x.mhtml"
        path.write_bytes(b"anything")
        self.assertTrue(mhtml.looks_like_mhtml(path))

    def test_content_detected_even_with_wrong_suffix(self):
        """被改名成 `.html` 的 MHTML 必须能被认出来。

        否则 `ingest` 会拿它当普通存档解析，报一堆「找不到 __INITIAL_STATE__」——
        真正的原因是格式不认，那个错误信息会把人带偏。
        """
        path = self.dir / "misnamed.html"
        path.write_bytes(build_mhtml())
        self.assertTrue(mhtml.looks_like_mhtml(path))

    def test_plain_html_is_not_mhtml(self):
        path = self.dir / "plain.html"
        path.write_bytes(b"<html><body>hi</body></html>")
        self.assertFalse(mhtml.looks_like_mhtml(path))


class TestParse(unittest.TestCase):
    def setUp(self):
        self.doc = mhtml.parse(build_mhtml())

    def test_html_segment_extracted(self):
        self.assertIn("这是正文第一段", self.doc.html)
        self.assertIn(OG_URL, self.doc.html)

    def test_sub_resources_extracted(self):
        self.assertEqual(len(self.doc.parts), 2)

    def test_images_recognised_by_mime_type_not_by_suffix(self):
        """小红书的图文件名是纯哈希（`1040g2sg31mp21…`），没有扩展名。

        靠后缀判「是不是图」会把它们全算成非图——而那正好是本项目最需要的那批。
        """
        self.assertTrue(self.doc.has_images)

    def test_source_url_captured(self):
        self.assertEqual(self.doc.source_url, OG_URL)

    def test_no_sub_resources_is_not_an_error(self):
        """没有子资源说明图没被打包，正文仍然可用——不该让整篇失败。"""
        doc = mhtml.parse(build_mhtml(with_parts=False))
        self.assertEqual(doc.parts, [])
        self.assertFalse(doc.has_images)
        self.assertIn("这是正文第一段", doc.html)


class TestHeaderFolding(unittest.TestCase):
    """RFC 5322 会把长头部折行，读回来带 `\\n `。不还原就永远匹配不上。

    这是实测踩到的真坑：绝对 URL 那条图**静默丢链接**（不报错，只是正文变短）。
    """

    def test_long_content_location_is_unfolded(self):
        doc = mhtml.parse(build_mhtml())
        data = build_mhtml()
        rewrote = mhtml.rewrite_references(doc.html, doc, data, "x_files")
        self.assertIn(f"./x_files/", rewrote)
        self.assertNotIn(ABS_IMAGE_URL, rewrote, "绝对 URL 没被改成本地路径")


class TestRewriteReferences(unittest.TestCase):
    def setUp(self):
        self.data = build_mhtml()
        self.doc = mhtml.parse(self.data)
        self.html = mhtml.rewrite_references(self.doc.html, self.doc, self.data, "x_files")

    def test_cid_reference_rewritten(self):
        self.assertNotIn("cid:", self.html)
        self.assertIn("./x_files/", self.html)

    def test_absolute_url_rewritten(self):
        self.assertNotIn(ABS_IMAGE_URL, self.html)

    def test_body_is_preserved_verbatim(self):
        """正文一个字符都不许动——它是证据。"""
        self.assertIn("这是正文第一段。", self.html)
        self.assertIn("这是正文第二段。", self.html)

    def test_paragraph_breaks_survive(self):
        """段落结构是拆解要看的证据（钩子在第几段、有没有空行隔断）。"""
        self.assertIn("\n\n", self.html)


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, **kwargs) -> Path:
        path = self.dir / "ar-page-test.mhtml"
        path.write_bytes(build_mhtml(**kwargs))
        return path

    def test_produces_html_plus_files_dir(self):
        html_path, count = mhtml.convert(self._write())
        self.assertEqual(html_path.name, "ar-page-test.html")
        self.assertEqual(count, 2)
        files_dir = self.dir / "ar-page-test_files"
        self.assertTrue(files_dir.is_dir())
        self.assertEqual(len(list(files_dir.iterdir())), 2)

    def test_matches_chrome_layout(self):
        """产物必须与 Chrome「网页，全部」**完全同构**。

        同构才可能「下游一个字都不用改」——`images.images_dir()` 找的就是
        `<stem>_files`，OCR / 图与 note_id 的绑定 / 孤立目录反查全挂在这条约定上。
        """
        html_path, _ = mhtml.convert(self._write())
        self.assertEqual(html_path.stem + "_files", "ar-page-test_files")
        self.assertTrue((self.dir / "ar-page-test_files").is_dir())

    def test_src_points_into_the_files_dir(self):
        html_path, _ = mhtml.convert(self._write())
        text = html_path.read_text(encoding="utf-8")
        self.assertIn('./ar-page-test_files/', text)
        self.assertEqual(text.count("cid:"), 0)

    def test_referenced_files_really_exist(self):
        """`src` 指的文件必须真的在磁盘上——否则 OCR 那一步会静默少几张图。"""
        html_path, _ = mhtml.convert(self._write())
        text = html_path.read_text(encoding="utf-8")
        import re

        for name in re.findall(r'\./ar-page-test_files/([^"\']+)', text):
            self.assertTrue((self.dir / "ar-page-test_files" / name).is_file(), f"{name} 不存在")

    def test_empty_mhtml_raises_with_actionable_message(self):
        path = self.dir / "broken.mhtml"
        path.write_bytes(b"not a mhtml at all")
        with self.assertRaises(ValueError) as ctx:
            mhtml.convert(path)
        self.assertIn("没有 text/html 段", str(ctx.exception))

    def test_duplicate_names_do_not_overwrite(self):
        """两张图写进同一个名字 = 静默丢一张。重名必须加序号。"""
        message = email.message.EmailMessage()
        message["MIME-Version"] = "1.0"
        message.add_alternative(PAGE, subtype="html", charset="utf-8")
        for payload in (b"ONE", b"TWO"):
            part = email.message.EmailMessage()
            part.set_content(payload, maintype="image", subtype="png")
            part["Content-Location"] = "https://x.invalid/same.png"
            message.attach(part)
        path = self.dir / "dup.mhtml"
        path.write_bytes(message.as_bytes())
        _, count = mhtml.convert(path)
        self.assertEqual(count, 2)
        names = sorted(p.name for p in (self.dir / "dup_files").iterdir())
        self.assertEqual(len(set(names)), 2, f"重名被覆盖：{names}")


if __name__ == "__main__":
    unittest.main()
