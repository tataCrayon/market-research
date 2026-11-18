"""详情页 HTML → 笔记级字段（赞 / 藏 / 评 / 分享 / 发布日 / 时长 / 正文 / 标签）。

**这条通道成立的前提是 og:url 指向 /explore/<note_id>**（2026-10-27 实测）。
之前判「详情页 HTML 是空壳」是因为那三份的 og:url 仍指向 /user/profile/——
那是「从主页点开弹层后按 Ctrl+S」，SPA 内部跳转没有重新加载文档，
存下来的仍是上一次整页加载的主页。整页加载 /explore/<id> 时，服务端会回正文。

**两条取证通道，优先级由「有没有」决定**（2026-11-03 重排）：

| 通道 | 位置 | 能给什么 |
|---|---|---|
| JSON（state） | `note.noteDetailMap.<id>.note` | 赞/藏/评/**分享**、**绝对发布日**、**视频秒数**、desc、tagList |
| DOM（正则） | `#detail-desc` / `interact-container` | 赞/藏/评、正文 HTML、标签 |

state 优先：它是平台给结构数据的原样副本，不受渲染影响；DOM 只在 state 缺这一项时兜底。
但 state 不是总在——实测 7 篇详情页里 **1 篇（Kimi）的 `noteDetailMap` 是空的**，
另 5 篇则因 `new Map([])` 连整份 state 都解析不出来（已在 initialstate 修掉）。
所以两条通道必须互相兜底，**并且交叉校验**：两边给得出数字却对不上时，宁可报冲突也不静默取一个。

不做的事：
- 不猜发布时间——DOM 只有「3天前」这种相对值，**绝对时间只从 state 的毫秒时间戳来**；
- 不编分享数——DOM 里 `share-wrapper` 只有图标没有数字，那个数字只在 state 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import schema
from .counts import consistent, parse_count

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
    "likes": re.compile(r'class="like-wrapper[^"]*".{0,900}?class="count"[^>]*>([\d.,]+[万亿KkMm]?)<', re.S),
    "collects": re.compile(r'class="collect-wrapper[^"]*".{0,900}?class="count"[^>]*>([\d.,]+[万亿KkMm]?)<', re.S),
    "comments": re.compile(r'class="chat-wrapper[^"]*".{0,900}?class="count"[^>]*>([\d.,]+[万亿KkMm]?)<', re.S),
}
# 视频时长：`og:videotime` 是平台给的标准 meta（秒级，形如 00:15）。
# 早先这条正则是 `(?:时长|duration)[^0-9]{0,10}(\d{1,2}:\d{2})`，靠碰运气命中 mp4 的
# `"duration":"00:15"`——同一份存档里还有 `video.capa.duration=14`（播放器精度），
# 两个值差 1 秒。只认 meta 就不会在两个数之间漂。
DURATION_META_RE = re.compile(r'og:videotime"[^>]*content="(\d{1,2}:\d{2})"', re.I)
DURATION_FALLBACK_RE = re.compile(r'"duration"\s*:\s*"(\d{1,2}:\d{2})"', re.I)
# state 的 `desc` 把话题标签内联在正文尾部：`…狂喜。\n\t\n#AI[话题]# #AI新手村[话题]# …`。
# 不剥掉它，正文末段就会混进一串标签、字数虚高、段落数也会多出一段「只有标签」的假段。
# DOM 通道没有这个问题（标签是独立 `<a id="hash-tag">`），所以这条只用于 state。
STATE_TAG_RE = re.compile(r"#[^#\s\[\]]{1,40}\[话题\]#")
# 零评论的**证据**：页面上没有一条 `comment-item`，却渲染了这个空态占位
# （「这是一片荒地 点击评论」）。两者同时成立才能判 0。
# 为什么需要它：`commentCount` 为空不等于 0——它也可能只是没采到。
# 实测 9 篇详情页里，`commentCount` 为空的 2 篇**都**带这个占位、且 `comment-item` 为 0；
# 其余 7 篇 `commentCount` 有值的一个都没有。所以它能把「真 0 条」和「没采到」分开。
NO_COMMENTS_MARK = 'class="no-comments"'
COMMENT_ITEM_MARK = "comment-item"
# 评论正文一直在存档里（2026-11-04 更正）：`comment-item` 内的
# `<div class="content"><span class="note-text">…</span></div>` 就是评论原话，
# 作者在 `<a class="name">`，时间与属地在 `<div class="date">`。
# 之前断言「评论内容一条都没采」是错的——**只数了节点没解析文本**。
COMMENT_ITEM_RE = re.compile(r'<div[^>]*class="comment-item"')
COMMENT_AUTHOR_RE = re.compile(r'<a[^>]*class="name"[^>]*>(.*?)</a>', re.S)
COMMENT_CONTENT_RE = re.compile(r'<div[^>]*class="content"[^>]*>(.*?)</div>', re.S)
COMMENT_TEXT_RE = re.compile(r'<span[^>]*class="note-text"[^>]*>(.*?)</span>', re.S)
COMMENT_DATE_RE = re.compile(r'<div[^>]*class="date"[^>]*>(.*?)</div>', re.S)
# `<div class="date"><span>3天前</span><span class="location">河北</span></div>`
COMMENT_LOCATION_RE = re.compile(r'<span[^>]*class="location"[^>]*>(.*?)</span>', re.S)
_TZ = timezone(timedelta(hours=schema.TZ_OFFSET_HOURS))


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


@dataclass(frozen=True)
class Comment:
    """一条评论。`text` 是原话，不改写、不摘要——摘要就把证据变成了转述。"""

    author: str = ""
    author_id: str = ""
    text: str = ""
    meta: str = ""          # 原始相对时间（`3天前` / `10-09`）
    location: str = ""      # 属地（`上海` / `加拿大`）


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
    shares: str = ""
    published_at: str = ""
    duration: str = ""
    body: str = ""
    paragraphs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    # 每个字段是从哪条通道来的：`state` / `dom` / `state+dom`（两处一致）。
    # 不加这个，事后没人能回答「这个分享数是真读到的还是某一版猜的」。
    sources: dict[str, str] = field(default_factory=dict)
    # 同一字段两条通道给出不同数字时记在这里。**不静默取一个**——取舍该由人做。
    conflicts: dict[str, str] = field(default_factory=dict)
    # 「这里确实是 0」的直接证据（如零评论的空态占位）。它和 `missing` 是两件事：
    # `0` 是观测到的数，⬜ 是没观测到。把 0 写成 ⬜ 是丢数据，把 ⬜ 写成 0 是编数据。
    zeros: dict[str, str] = field(default_factory=dict)
    # 首屏评论（原文）。**这是首屏，不是全部**——存档只含页面渲染出来的那几条，
    # 剩余的要人点「加载更多」。所以引用时必须同时说清 `declared` 与 `captured`。
    comments_list: tuple[Comment, ...] = ()
    missing: list[str] = field(default_factory=list)

    def as_row_updates(self) -> dict[str, Any]:
        """回传详情页独有的字段。

        title / form 也一起带：详情页可能读到主页存档未覆盖的新笔记（主页只滚了几屏），
        没有标题的新行是废行。已有行的标题不会被它们覆盖——`_detail_rows` 只在
        新建行时才用这两个字段。

        `published_at` 是 2026-11-03 才进来的：state 里有毫秒时间戳，是**绝对时间**，
        比主页列表页更直接。早先这条通道只给相对时间，所以它一直留空。

        `author_name` 是 2026-11-18 补的：外来作者现在会被建成**占位账号**，
        而占位账号唯一能有的信息就是这个昵称。不带它，`accounts` 里会多出一行
        名字空白的账号——`ar status` / `matrix` 里就变成「⬜ 一个号」，
        分不清是解析失败还是真的没名字。
        """
        return {
            "note_id": self.note_id,
            "title": self.title,
            "form": self.form,
            "likes": self.likes,
            "collects": self.collects,
            "comments": self.comments,
            "shares": self.shares,
            "published_at": self.published_at,
            "duration": self.duration,
            "author_id": self.author_id,
            "author_name": self.author,
        }

    def count_text(self, name: str) -> str:
        """人看的计数：圆整值前面带 `≈`（`1.2万` 不是 12000，别当精确值引用）。"""
        raw = getattr(self, name, "")
        parsed = parse_count(raw)
        return parsed.text() if parsed else "⬜"


def og_url(html: str) -> str:
    match = OG_URL_RE.search(html)
    return match.group(1) if match else ""


def note_id_from_url(url: str) -> str:
    match = NOTE_ID_RE.search(url or "")
    return match.group(1) if match else ""


def _text(raw: str) -> str:
    return WS_RE.sub(" ", TAG_STRIP_RE.sub(" ", raw or "")).strip()


def extract_comments(html: str) -> tuple[Comment, ...]:
    """从详情页 DOM 抽**首屏**评论。抽不到返回空元组，不猜。

    为什么按 `comment-item` 切片、再在片内找：评论块里还嵌着「回复」与头像链接，
    全局正则会把别人的头像 URL、菜单项当成评论。切片是最省事又最不容易串的办法。

    ⚠️ **这是首屏，不是全部**：存档只含页面已渲染的那几条（实测 10 条左右），
    剩余的要人点「加载更多」。所以调用方必须把「声明总数 vs 实际抓到」一起报出来，
    只报抓到的那几条会被读成「这篇只有 10 条评论」。
    """
    starts = [match.start() for match in COMMENT_ITEM_RE.finditer(html)]
    found: list[Comment] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else min(len(html), start + 8000)
        block = html[start:end]
        content = COMMENT_CONTENT_RE.search(block)
        if not content:
            continue
        body_match = COMMENT_TEXT_RE.search(content.group(1))
        body = _text(body_match.group(1)) if body_match else ""
        if not body:
            continue                      # 有容器没正文 → 是骨架没渲染，不算一条评论
        author = COMMENT_AUTHOR_RE.search(block)
        date = COMMENT_DATE_RE.search(block)
        location = COMMENT_LOCATION_RE.search(block)
        author_id = re.search(r'data-user-id="([0-9a-f]{24})"', block)
        # `date` 容器里同时装着时间与属地（`<span>10-06</span><span class="location">山东</span>`）。
        # 直接 _text 会得到「10-06 山东」，属地被写两遍。先把 location 那段剪掉再取时间。
        meta_html = date.group(1) if date else ""
        if location:
            meta_html = meta_html.replace(location.group(0), "")
        found.append(Comment(
            author=_text(author.group(1)) if author else "",
            author_id=author_id.group(1) if author_id else "",
            text=body,
            meta=_text(meta_html),
            location=_text(location.group(1)) if location else "",
        ))
    return tuple(found)


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


def ms_to_date(value: Any) -> str:
    """毫秒时间戳 → 北京时间日期。不依赖机器时区，避免跨机器解析结果漂移。"""
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000, _TZ).strftime("%Y-%m-%d")


def note_from_state(state: dict[str, Any] | None, note_id: str) -> dict[str, Any]:
    """从 `__INITIAL_STATE__` 里取本笔记的对象；取不到返回 `{}`。

    **必须按 note_id 取，不能取第一个**：`noteDetailMap` 里可能混着别的笔记
    （推荐流点进来的路径会这样）。取错就是把别人的赞记成这一篇的——
    与 `interact_bar_slice` 那条坑同源，一个在 JSON 层、一个在 DOM 层。
    """
    if not isinstance(state, dict) or not note_id:
        return {}
    note_map = ((state.get("note") or {}).get("noteDetailMap") or {})
    if not isinstance(note_map, dict):
        return {}
    wrapper = note_map.get(note_id)
    if isinstance(wrapper, dict):
        note = wrapper.get("note")
        if isinstance(note, dict):
            return note
    return {}


def _state_counts(note: dict[str, Any]) -> dict[str, str]:
    """state 的 interactInfo → 四个计数（原样字符串，换算交给 counts 模块）。"""
    info = note.get("interactInfo") or {}
    if not isinstance(info, dict):
        return {}
    mapping = {
        "likes": "likedCount",
        "collects": "collectedCount",
        "comments": "commentCount",
        # 🔧 2026-11-03：分享数**不是**「页面上没有」——DOM 里没有，state 里有。
        # 这一条推翻了 docs/详情页采集-结论.md §3 的旧结论。
        "shares": "shareCount",
    }
    return {
        key: str(info.get(source) or "").strip()
        for key, source in mapping.items()
        if str(info.get(source) or "").strip()
    }


def _state_duration(note: dict[str, Any], form: str) -> str:
    """视频秒数 → `mm:ss`。只对视频有意义，图文篇即使有字段也不编。"""
    if form != "视频":
        return ""
    video = note.get("video")
    if not isinstance(video, dict):
        return ""
    seconds = (video.get("capa") or {}).get("duration") if isinstance(video.get("capa"), dict) else None
    if isinstance(seconds, (int, float)) and 0 < seconds < 3600:
        whole = int(round(seconds))
        return f"{whole // 60:02d}:{whole % 60:02d}"
    return ""


def _pick(
    fields: DetailFields, name: str, dom_value: str, state_value: str,
) -> str:
    """两条通道合一个值，并记下它来自哪、有没有冲突。

    规则（顺序即优先级）：
    1. 两边都有且**可能区间重叠** → 取 state 的原样字符串，标 `state+dom`（两个独立来源互证）；
       重叠而不是相等，是因为平台过万后一边可能是 `1.2万`、另一边是 `12345`——
       那不是冲突，是同一篇笔记的两条通道各给了一个精度；
    2. 两边都有但区间也不重叠 → 取 state，但把两个原值都记进 `conflicts`。
       不静默取一个：数字打起来时，错的可能是我这条正则，人必须看得见。
    3. 只有一边有 → 用那一边，标它的来源。
    """
    dom_text = str(dom_value or "").strip()
    state_text = str(state_value or "").strip()
    if dom_text and state_text:
        dom_count, state_count = parse_count(dom_text), parse_count(state_text)
        if consistent(dom_count, state_count):
            fields.sources[name] = "state+dom"
            return state_text
        fields.conflicts[name] = f"DOM={dom_text} / state={state_text}"
        fields.sources[name] = "state"
        return state_text
    if state_text:
        fields.sources[name] = "state"
        return state_text
    if dom_text:
        fields.sources[name] = "dom"
        return dom_text
    return ""


def _resolve_zero_comments(fields: DetailFields, html: str) -> None:
    """评论数拿不到时，判它是「真 0 条」还是「没采到」——**这两件事不能混**。

    小红书的 `interactInfo.commentCount` 在零评论时是**空串**，不是 `"0"`；
    而页面上同时会渲染一个空态占位「这是一片荒地 点击评论」。

    实测 9 篇详情页：`commentCount` 为空的 2 篇都带这个占位、且 `comment-item` 计数为 0；
    其余 7 篇有值的一个都没有。所以「有占位 + 没有一条评论项」是可信的零证据。

    为什么值得单独做：把 0 写成 ⬜ 是**丢数据**（`ar gaps` 会把这 2 篇永远列进待补清单，
    人白点两次）；把 ⬜ 写成 0 是**编数据**。两种都错，而屏幕上看不出区别。
    """
    if str(fields.comments).strip():
        return
    if NO_COMMENTS_MARK not in html:
        return
    if html.count(COMMENT_ITEM_MARK):
        return                     # 有评论项却说没数 → 是没采到，不是 0
    fields.comments = "0"
    fields.sources["comments"] = "dom-zero"
    fields.zeros["comments"] = "页面空态占位「这是一片荒地」，且无任何 comment-item"
    if "comments" in fields.missing:
        fields.missing.remove("comments")


def extract_detail(html: str, state: dict[str, Any] | None = None) -> DetailFields:
    """详情页 HTML（+ 可选 state）→ 笔记级字段。

    `state` 有就传进来：它是分享数/发布日/视频秒数的**唯一**来源。
    不传也能跑——DOM 通道够给赞/藏/评/正文/标签，只是那三样会缺。
    """
    url = og_url(html)
    fields = DetailFields(note_id=note_id_from_url(url), og_url=url)
    note = note_from_state(state, fields.note_id)

    title_match = TITLE_RE.search(html)
    fields.title = _text(title_match.group(1)) if title_match else ""
    if not fields.title:
        # DOM 没渲染出标题时，state 的 title 是干净的原值（无「 - 小红书」后缀）
        fields.title = str(note.get("title") or "").strip()

    desc_match = DESC_RE.search(html)
    raw_desc = desc_match.group(1) if desc_match else ""
    if raw_desc:
        fields.tags = tuple(_text(tag) for tag in TAG_RE.findall(raw_desc) if _text(tag))
        # 🔧 2026-11-03：`TAG_RE.sub(" ")` 会把标签替换成**空格**——
        # 于是正文末尾那串 `#AI #AI新手村 …` 变成 8 个空格，最后一个正文段凭空多出
        # 8 个字符（实测末段 99 → 59 字，差的正好是标签数）。改成替换成空串，
        # 段数与字数才和 state 给的原值对得上。分段规则本身没动。
        fields.paragraphs = body_paragraphs(TAG_RE.sub("", raw_desc))
        fields.body = " ".join(fields.paragraphs)
        fields.sources["body"] = "dom"
    if not fields.body:
        # DOM 里没有正文容器（部分页面结构改版）时，state 的 desc 是原值：
        # 它用 `\n\t\n` 分段，正好喂同一套分段函数。但**标签内联在尾部**，必须先剥。
        desc = STATE_TAG_RE.sub("", str(note.get("desc") or ""))
        if desc.strip():
            fields.paragraphs = body_paragraphs(desc)
            fields.body = " ".join(fields.paragraphs)
            fields.sources["body"] = "state"
    if not fields.tags:
        fields.tags = tuple(
            _text(str(item.get("name") or ""))
            for item in (note.get("tagList") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )

    author_match = AUTHOR_RE.search(html)
    fields.author = _text(author_match.group(1)) if author_match else ""
    if not fields.author:
        fields.author = str((note.get("user") or {}).get("nickname") or "").strip()
    link_match = AUTHOR_LINK_RE.search(html)
    fields.author_id = link_match.group(1) if link_match else ""
    if not fields.author_id:
        fields.author_id = str((note.get("user") or {}).get("userId") or "").strip()

    bar = interact_bar_slice(html)
    dom_counts: dict[str, str] = {}
    for name, pattern in COUNT_IN_BAR_RES.items():
        match = pattern.search(bar)
        dom_counts[name] = match.group(1) if match else ""
    state_counts = _state_counts(note)
    for name in ("likes", "collects", "comments", "shares"):
        setattr(fields, name, _pick(fields, name, dom_counts.get(name, ""), state_counts.get(name, "")))
    _resolve_zero_comments(fields, html)
    fields.comments_list = extract_comments(html)

    note_type = str(note.get("type") or "")
    if note_type in ("video",):
        fields.form = "视频"
    elif note_type in ("normal",):
        fields.form = "图文"
    else:
        fields.form = "视频" if "<video" in html else "图文"

    duration_meta = DURATION_META_RE.search(html)
    if duration_meta:
        fields.duration = duration_meta.group(1)
        fields.sources["duration"] = "dom"
    elif fields.form == "视频":
        # 兜底优先级：state 的秒数（播放器精度）> HTML 里第一个 mm:ss 字面量
        state_duration = _state_duration(note, fields.form)
        if state_duration:
            fields.duration = state_duration
            fields.sources["duration"] = "state"
        else:
            duration_fallback = DURATION_FALLBACK_RE.search(html)
            fields.duration = duration_fallback.group(1) if duration_fallback else ""
            if fields.duration:
                fields.sources["duration"] = "dom"

    published = ms_to_date(note.get("time"))
    if published:
        fields.published_at = published
        fields.sources["published_at"] = "state"

    for name in ("note_id", "title", "body", "likes", "collects", "comments"):
        if not getattr(fields, name):
            fields.missing.append(name)
    return fields


def looks_like_detail_html(html: str) -> bool:
    return bool(note_id_from_url(og_url(html)))
