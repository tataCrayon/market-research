"""详情页 HTML → 笔记级字段（赞 / 藏 / 评 / 时长 / 正文 / 标签）。

**这条通道成立的前提是 og:url 指向 /explore/<note_id>**（2026-10-27 实测）。
之前判「详情页 HTML 是空壳」是因为那三份的 og:url 仍指向 /user/profile/——
那是「从主页点开弹层后按 Ctrl+S」，SPA 内部跳转没有重新加载文档，
存下来的仍是上一次整页加载的主页。整页加载 /explore/<id> 时，服务端会回正文。

不做的事：不猜发布时间（详情页只给「3天前」这种相对值，绝对时间以主页存档为准）、
不编分享数（页面上只有图标没有数字）——拿不到就留空，交回去让人知道拿不到。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

OG_URL_RE = re.compile(r'og:url[^>]*content="([^"]+)"')
NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]{24})", re.I)
TITLE_RE = re.compile(r'<h1[^>]*id="detail-title"[^>]*>(.*?)</h1>', re.S)
DESC_RE = re.compile(r'<div[^>]*id="detail-desc"[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r'<a[^>]*id="hash-tag"[^>]*>(.*?)</a>', re.S)
AUTHOR_RE = re.compile(r'class="username"[^>]*>([^<]{1,40})<')
# 作者主页链接：抄「小红书 - 同步开启博主首页」扩展的选择器（.author-container .info>a），
# 实测在真实详情页里成立。这个 id 用来做**归属校验**——判断详情页里的作者是不是目标账号。
AUTHOR_LINK_RE = re.compile(
    r'class="author-container".{0,900}?href="[^"]*?/user/profile/([0-9a-f]{24})', re.S
)
TAG_STRIP_RE = re.compile(r"<[^>]+>")
# 兜底：被前面步骤切断后残留的半截标签（只剩 `<a` 这种）。
# 不清掉它就会多出一个「只含 <a 的假段落」，把段数从 3 变成 4。
RESIDUAL_TAG_RE = re.compile(r"<[a-zA-Z/][^<>\n]*$", re.M)
WS_RE = re.compile(r"[\s\u3000]+")
INTERACT_RE = re.compile(r'class="interact-container"')
COUNT_IN_BAR_RES = {
    "likes": re.compile(r'class="like-wrapper[^"]*".{0,900}?class="count"[^>]*>([\d.]+[万亿]?)<', re.S),
    "collects": re.compile(r'class="collect-wrapper[^"]*".{0,900}?class="count"[^>]*>([\d.]+[万亿]?)<', re.S),
    "comments": re.compile(r'class="chat-wrapper[^"]*".{0,900}?class="count"[^>]*>([\d.]+[万亿]?)<', re.S),
}
DURATION_RE = re.compile(r"(?:时长|duration)[^0-9]{0,10}(\d{1,2}:\d{2})", re.I)


def interact_bar_slice(html: str, span: int = 4000) -> str:
    """只取本笔记的互动栏片段。

    ⚠️ 实测踩坑（2026-10-27）：详情页 HTML 里背景推荐流有 **47 个** `like-wrapper`，
    本笔记的交互栏是最后一个（位置最靠后）。不先切片就正则会命中推荐卡片的数字，
    把别人的点赞数记成本笔记的——静默错数据，比报错危险得多。
    """
    matches = list(INTERACT_RE.finditer(html))
    if not matches:
        return ""
    return html[matches[-1].start(): matches[-1].start() + span]


@dataclass
class DetailFields:
    note_id: str = ""
    og_url: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    form: str = ""
    likes: str = ""
    collects: str = ""
    comments: str = ""
    duration: str = ""
    body: str = ""
    paragraphs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    missing: list[str] = field(default_factory=list)

    def as_row_updates(self) -> dict[str, Any]:
        """回传详情页独有的字段。

        title / form 也一起带：详情页可能读到主页存档未覆盖的新笔记（主页只滚了几屏），
        没有标题的新行是废行。已有行的标题不会被它们覆盖——`_detail_rows` 只在
        新建行时才用这两个字段。
        """
        return {
            "note_id": self.note_id,
            "title": self.title,
            "form": self.form,
            "likes": self.likes,
            "collects": self.collects,
            "comments": self.comments,
            "duration": self.duration,
            "author_id": self.author_id,
        }


def og_url(html: str) -> str:
    match = OG_URL_RE.search(html)
    return match.group(1) if match else ""


def note_id_from_url(url: str) -> str:
    match = NOTE_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _text(raw: str) -> str:
    return WS_RE.sub(" ", TAG_STRIP_RE.sub(" ", raw or "")).strip()


# 小红书的正文用**裸换行**分段（没有 <p>/<br>），存档里长这样：`…巨变。\n\t\n想想身边…`
# 所以必须先把「空行」识别成分段，再做空白折叠——否则 900 字会被压成一个巨块，
# 结构分析（看他分几段、钩子在第几段）就彻底失效。
BLANK_LINE_RE = re.compile(r"[ \t\u3000]*\n[ \t\u3000]*\n[ \t\u3000]*")
SINGLE_NL_RE = re.compile(r"[ \t\u3000]*\n[ \t\u3000]*")
# 项目符号行：`- xxx` / `• xxx` / `· xxx` / `1. xxx`。
# **它们必须各自成段**：实测那篇 110 赞的笔记，4 条项目符号被合并成了一段
# （「- 左边每个项目… - 中间玻璃圆顶… - 右边是两个 Agent… - 每个真实任务…」），
# 于是「8 段 + 4 条列表」这个结构证据丢了一半——而清单式说明正是那篇的骨架。
LIST_ITEM_RE = re.compile(r"^\s*(?:[-•·*]|\d{1,2}[.、)])\s+")


def is_list_item(paragraph: str) -> bool:
    """这一段是不是项目符号。分不开「正文段」和「清单项」就没法数他的结构。"""
    return bool(LIST_ITEM_RE.match(paragraph))


def _split_block(block: str) -> list[str]:
    """一个空行块内部：普通折行要合并，项目符号行要各自独立。"""
    out: list[str] = []
    buffer: list[str] = []
    for line in SINGLE_NL_RE.split(block):
        if LIST_ITEM_RE.match(line):
            if buffer:
                out.append(" ".join(buffer))
                buffer = []
            out.append(line.strip())
        else:
            buffer.append(line)
    if buffer:
        out.append(" ".join(buffer))
    return [WS_RE.sub(" ", part).strip() for part in out]


def body_paragraphs(raw_desc: str) -> tuple[str, ...]:
    """把正文 HTML 片段切成段落。段落结构本身就是拆解要用的证据，不能压平。"""
    text = TAG_STRIP_RE.sub("", raw_desc or "").replace("\r\n", "\n").replace("\r", "\n")
    # 半截标签：调用方可能先用别的正则摘掉 <a id="hash-tag">…</a>，只留下一个没有 '>' 的 `<a`，
    # TAG_STRIP_RE 要求成对尖括号因此漏掉它，最后会变成一个只含 `<a` 的假段落。
    text = RESIDUAL_TAG_RE.sub("", text)
    chunks: list[str] = []
    for block in BLANK_LINE_RE.split(text):
        chunks.extend(_split_block(block))
    return tuple(chunk for chunk in chunks if chunk)


def extract_detail(html: str) -> DetailFields:
    url = og_url(html)
    fields = DetailFields(note_id=note_id_from_url(url), og_url=url)

    title_match = TITLE_RE.search(html)
    fields.title = _text(title_match.group(1)) if title_match else ""

    desc_match = DESC_RE.search(html)
    if desc_match:
        raw_desc = desc_match.group(1)
        fields.tags = tuple(_text(tag) for tag in TAG_RE.findall(raw_desc) if _text(tag))
        fields.paragraphs = body_paragraphs(TAG_RE.sub(" ", raw_desc))
        fields.body = " ".join(fields.paragraphs)

    author_match = AUTHOR_RE.search(html)
    fields.author = _text(author_match.group(1)) if author_match else ""
    link_match = AUTHOR_LINK_RE.search(html)
    fields.author_id = link_match.group(1) if link_match else ""

    bar = interact_bar_slice(html)
    for name, pattern in COUNT_IN_BAR_RES.items():
        match = pattern.search(bar)
        setattr(fields, name, match.group(1) if match else "")

    fields.form = "视频" if "<video" in html else "图文"
    duration_match = DURATION_RE.search(html)
    fields.duration = duration_match.group(1) if duration_match else ""

    for name in ("note_id", "title", "body", "likes", "collects", "comments"):
        if not getattr(fields, name):
            fields.missing.append(name)
    return fields


def looks_like_detail_html(html: str) -> bool:
    return bool(note_id_from_url(og_url(html)))
