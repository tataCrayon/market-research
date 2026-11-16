"""造一份「真 Chrome 形态」的笔记 MHTML 样本，给多个测试共用。

不手拼字符串：真 Chrome 的 MHTML 有 `quoted-printable`/`base64`、
RFC 5322 折行、`cid:` 与 `Content-Location` 两套引用。
手拼样本测出来的东西不对——这是 `tests/test_mhtml.py` 当时就踩过的结论。

这里用 Python 标准库 `email` **造**一份真实 MIME 结构，和 Chrome 用的是同一套格式。
"""

from __future__ import annotations

import email.message

NOTE_ID = "6ac48e16000000001203ed6e"
OG_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"

PAGE = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta property="og:url" content="{OG_URL}">
<meta property="og:xhs:note_comment" content="14">
<meta property="og:xhs:note_like" content="318">
<meta property="og:xhs:note_collect" content="73">
<title>测试笔记</title></head>
<body>
<div id="detail-title">测试笔记</div>
<div id="detail-desc" class="desc"><span class="note-text">这是正文第一段。

这是正文第二段。</span></div>
<!-- 平台真实形态：互动栏的容器 class 就叫 `interact-container`，
     解析器按它切片（不切的话会命中推荐流里别人的数字）。 -->
<div class="interact-container">
  <span class="like-wrapper"><span class="count">318</span></span>
  <span class="collect-wrapper"><span class="count">73</span></span>
  <span class="chat-wrapper"><span class="count">14</span></span>
</div>
</body></html>"""


def build_detail_mhtml(html: str = PAGE, image_parts: int = 2) -> bytes:
    """造一份与 Chrome 同构的详情页 MHTML（multipart/related + 两套引用方式）。"""
    message = email.message.EmailMessage()
    message["MIME-Version"] = "1.0"
    message["Snapshot-Content-Location"] = OG_URL
    message.add_alternative(html, subtype="html", charset="utf-8")
    for index in range(image_parts):
        # 图片部分用 Content-Location 绝对地址 —— 与 Chrome 的另一种引用方式一致。
        part = email.message.EmailMessage()
        part["Content-Type"] = "image/webp"
        part["Content-Transfer-Encoding"] = "base64"
        part["Content-Location"] = f"https://sns-webpic-qc.xhscdn.com/x/{index}.webp"
        part.set_payload("A" * 64)  # 只要合法 base64，内容不重要
        message.attach(part)
    return message.as_bytes()
