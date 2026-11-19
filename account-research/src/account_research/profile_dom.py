"""主页通道的 **DOM 腿**：`pageCapture` 剥掉 `<script>` 之后，只靠渲染后的 HTML 解析。

## 为什么必须有它

`chrome.pageCapture.saveAsMHTML` 产出的是**渲染后 DOM 序列化**，`<script>` 整段被剥掉，
`__INITIAL_STATE__` 随之消失。而主页的两个数据源全在脚本里：

| 字段 | state 里 | DOM 里 |
|---|---|---|
| 昵称 / 小红书号 / 简介 / IP 属地 | ✅ `userPageData.basicInfo` | ✅ `.user-name` / `.user-redId` / `.user-desc` / `.user-IP` |
| 粉丝 / 关注 / 获赞与收藏 | ✅ `interactions[]` | ✅ `.count` + `.shows` |
| 笔记卡片（id/标题/赞/形式） | ✅ `user.notes[][]` | ✅ `section.note-item[data-note-id]` |
| 发布日 | ✅ 毫秒时间戳 | ❌ 页面上是「3 天前」这类相对值 |
| 置顶标记 | ✅ `interactInfo.sticky` | ❌ **没有**（实测：DOM 里连 `置顶` 两个字都不出现） |

所以「扩展采主页」原本只有两条路：把 state 带回来（v8 的旁车），或只靠 DOM 丢两列。
**这个模块是第三条：先把能拿的都拿回来。** 它不替代旁车——发布日与置顶仍然只有
state 能给，缺了就是 ⬜（`validate` 会 WARN，`ar status` 会报），绝不猜。

## 三条不可越界的判断

1. **不猜发布日。** 卡片 DOM 上没有时间戳。按序号或按文件顺序推算 = 编数据。
2. **不猜置顶。** 置顶卡在 DOM 里与普通卡长得一模一样（实测 32 张卡片逐张比对，
   没有 `pin` / `top` / `置顶` / `sticky` 任何一种标记）。少这两篇就会让「样本 30」
   变成「样本 32」，`ar selftest` 的 AC1 会当场红——**那是对的**，它逼人补旁车。
3. **不跨卡片借字段。** 每个 `<section class="note-item">` 自己闭包解析，
   绝不「找不到就用邻居的」——`images.py` 的 `dom_slides` 当年就是这么把 16 张图
   算成 3 张还报「已绑全」的（[采集端-v6 复盘](../../docs/采集端-v6-实测复盘-20261113.md) §3）。

## 为什么不引 HTML 解析库

本项目 `src/` 是**零第三方依赖**的（只用标准库）。这里要的只是「按 class 取一段文本」，
正则足够；换成 `html.parser` 反而要自己写一棵树再遍历，代码更长、还更容易在
畸形 HTML 上出错。**约束保持不变，代价是选择器必须抄准** —— 抄自真实存档，
并在 `tests/test_profile_dom.py` 里用真实存档的片段钉住。
"""

from __future__ import annotations

import html as html_lib
import re
from typing import Any, Iterable

from . import schema
from .counts import to_count

# ── 选择器（全部抄自真实存档，见模块文档）────────────────────────────────
NOTE_SECTION = re.compile(r'<section[^>]*\bclass="[^"]*\bnote-item\b[^"]*"[^>]*>', re.I)
NOTE_ID_ATTR = re.compile(r'data-note-id="([0-9a-f]{24})"', re.I)
NICKNAME = re.compile(r'<div[^>]*\bclass="user-name"[^>]*>(.*?)</div>', re.S | re.I)
RED_ID = re.compile(r'<span[^>]*\bclass="user-redId"[^>]*>(.*?)</span>', re.S | re.I)
IP_LOCATION = re.compile(r'<span[^>]*\bclass="user-IP"[^>]*>(.*?)</span>', re.S | re.I)
DESC = re.compile(r'<div[^>]*\bclass="user-desc"[^>]*>(.*?)</div>', re.S | re.I)
INTERACTION = re.compile(
    r'<span[^>]*\bclass="count"[^>]*>(.*?)</span>\s*'
    r'<span[^>]*\bclass="shows"[^>]*>(.*?)</span>',
    re.S,
)
CARD_TITLE = re.compile(r'<a[^>]*\bclass="title"[^>]*>(.*?)</a>', re.S | re.I)
CARD_LIKES = re.compile(
    r'<span[^>]*\bclass="like-wrapper\b[^"]*"[^>]*>.*?'
    r'<span[^>]*\bclass="count"[^>]*>(.*?)</span>',
    re.S,
)
PLAY_ICON = re.compile(r'<span[^>]*\bclass="play-icon\b', re.I)
TAG_STRIP = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
# 平台在标签之间塞的 Vue 占位注释（`<!---->`）：不清掉，昵称会变成「张三<!---->」。
VUE_COMMENT = re.compile(r"<!--.*?-->", re.S)

# `shows` 文案 → 列名。平台用中文标签，映射必须显式写出来，不靠顺序猜。
INTERACTION_LABELS = {"关注": "follows", "粉丝": "fans", "获赞与收藏": "interactions"}
# 小红书号前缀：页面上是「小红书号：wzglyay2023」，只留冒号后面的部分。
RED_ID_PREFIX = re.compile(r"^小红书号[:：]\s*")
IP_PREFIX = re.compile(r"^IP\s*属地[:：]\s*")

# 卡片形式：封面里的播放角标是唯一判据。
# 为什么不用 `data-width/height`：竖图 1440×1920 与竖版视频一样，分不开。
FORM_VIDEO, FORM_IMAGE = "视频", "图文"


def text_of(fragment: str) -> str:
    """HTML 片段 → 一行纯文本。

    三件必须做的事，少一件就会把「平台的标签/占位」写进证据库：

    1. **删注释**：Vue 的 `<!---->` 会粘在昵称后面，变成「暖暖爱分享<!---->」。
    2. **剥标签**：`<br>` 不剥，简介会带换行；CSV 里的换行是**假的行分割**。
    3. **解实体**：`&amp;` 不解，知新那句简介会落库成「AI 大厂动态 &amp; 行业趋势」——
       而 state 腿读的是 JSON 里的原值，两条腿对同一个账号给出不同的字符串。
       （2026-11-19 实测踩到：真实存档的简介里就有 `&amp;`。）
    """
    cleaned = VUE_COMMENT.sub("", fragment or "")
    return WS.sub(" ", html_lib.unescape(TAG_STRIP.sub(" ", cleaned))).strip()


def looks_like_profile(html: str) -> bool:
    """这份 HTML 是不是主页的 DOM。判据取**证据最硬的那一个**：昵称容器。

    不用 `<title>`（详情页也可能带账号名），不用「有没有 note-item」
    （搜索页与推荐流也有）。`.user-name` 只在主页头部出现。
    """
    return bool(NICKNAME.search(html or ""))


def slices(html: str) -> list[str]:
    """按 `section.note-item` 切开，每张卡片一段**闭包**的 HTML。

    切法是「下一个 section 之前」，不用括号配对：卡片里没有嵌套的 `<section>`，
    而括号配对在畸形 HTML 上更容易切错。抄自真实存档的结构。

    ⚠️ 末尾那段可能是页面别的部分（滚动加载的哨兵、空态），
    所以只保留**带 `data-note-id`** 的那些——不带 id 的段落不是卡片。
    """
    starts = [match.start() for match in NOTE_SECTION.finditer(html or "")]
    cards: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(html)
        chunk = html[start:end]
        if NOTE_ID_ATTR.search(chunk):
            cards.append(chunk)
    return cards


def _interactions(html: str) -> dict[str, int | None]:
    """三个计数。页面给的是圆整值（`14.4万`），换算后是**近似值**，逐篇引用要标 ≈。"""
    counts: dict[str, int | None] = {"fans": None, "follows": None, "interactions": None}
    for raw_count, raw_label in INTERACTION.findall(html or ""):
        column = INTERACTION_LABELS.get(text_of(raw_label))
        if column:
            counts[column] = to_count(text_of(raw_count))
    return counts


def parse_account(html: str, collected_on: str, source_file: str = "",
                  account_id: str = "") -> dict[str, Any]:
    """主页 DOM → 一行 `accounts`。

    `account_id` 由调用方给（扩展从 URL 里拿，`/user/profile/<id>`）；
    给不出就留空——**不按昵称去猜**。空 id 会被 V7 拦住，那是对的：
    没有 id 的账号行不能当归属依据。
    """
    counts = _interactions(html)
    nickname = text_of(NICKNAME.search(html).group(1)) if NICKNAME.search(html) else ""
    return {
        "account_id": account_id,
        "nickname": nickname,
        "red_id": RED_ID_PREFIX.sub("", text_of(
            RED_ID.search(html).group(1) if RED_ID.search(html) else "")),
        "desc": desc_of(html),
        "ip_location": IP_PREFIX.sub("", text_of(
            IP_LOCATION.search(html).group(1) if IP_LOCATION.search(html) else "")),
        "fans": counts["fans"],
        "follows": counts["follows"],
        "interactions": counts["interactions"],
        # 这三项页面头部**没有**（只有「获赞与收藏」合计）。留空 = ⬜。
        "liked": None,
        "collected": None,
        "posted": None,
        "platform": schema.PLATFORM,
        "source_file": source_file,
        "source_kind": "公开页",
        "collected_on": collected_on,
        "grade": "②",
        "note": "主页 DOM（pageCapture 剥掉 <script> 的通道）：发布日与置顶缺，需 state 旁车",
    }


def desc_of(html: str) -> str:
    match = DESC.search(html or "")
    return text_of(match.group(1)).replace("\n", " ").strip() if match else ""


def parse_notes(html: str, account_id: str, collected_on: str,
                source_file: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """主页 DOM → `notes` 行。返回 (行, 缺陷清单)。

    缺陷清单是这个通道**必须付的代价**：发布日与置顶 DOM 里没有。
    不静默留空——调用方要能把它说出来（「N 篇缺发布日，补旁车即可」）。
    """
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    seen: set[str] = set()
    for card in slices(html):
        note_id = (NOTE_ID_ATTR.search(card).group(1) or "").lower()
        if not note_id or note_id in seen:
            continue
        seen.add(note_id)
        title_match = CARD_TITLE.search(card)
        likes_match = CARD_LIKES.search(card)
        rows.append({
            "account_id": account_id,
            "note_id": note_id,
            "published_at": "",          # DOM 里没有绝对时间 → ⬜，绝不推算
            "title": text_of(title_match.group(1)) if title_match else "",
            "form": FORM_VIDEO if PLAY_ICON.search(card) else FORM_IMAGE,
            "likes": to_count(text_of(likes_match.group(1))) if likes_match else None,
            "collects": None,            # 列表页不存在 → ⬜
            "comments": None,
            "shares": None,
            "pinned": "",                # 未知 ≠ 不是置顶：写 "0" 就是编数据
            "topic": "",
            "source_file": source_file,
            "source_kind": "公开页",
            "collected_on": collected_on,
            "grade": "②",
            "duration": "",
            "human_note": "",
        })
    if not rows:
        return [], ["一个 note-item 都没解析出来：选择器可能与平台改版脱节"]
    missing.append(
        f"{len(rows)} 篇的发布日与置顶标记在 DOM 里不存在（页面上是相对时间、置顶卡无标记）；"
        f"补 `<存档名>.state.json` 旁车可补齐"
    )
    return rows, missing


def diagnose(html: str) -> str:
    """给扩展面板用的一句话诊断：为什么这个页面采不了。

    为什么要单独一个函数：面板只能显示一句话，而「主页采不了」有三种完全不同的原因，
    处理方式也不同（等加载 / 换整页 / 换通道）。混成一句「识别失败」等于没说话。
    """
    if not (html or "").strip():
        return "页面是空的（可能还没加载完）"
    if looks_like_profile(html):
        return ""
    if "note-item" in (html or ""):
        return "看起来是主页（有笔记卡片），但账号头部还没渲染出来——往下滚一屏再试"
    if 'class="note-scroller"' in (html or "") or 'id="detail-title"' in (html or ""):
        return "这是详情页，不是主页"
    return "既不像主页也不像详情页（可能还在加载，或不是小红书页面）"


def summary(rows: Iterable[dict[str, Any]]) -> str:
    """一行摘要，给命令行打印用。"""
    rows = list(rows)
    videos = sum(1 for row in rows if row.get("form") == FORM_VIDEO)
    with_likes = sum(1 for row in rows if row.get("likes") is not None)
    return f"{len(rows)} 篇（视频 {videos} / 有赞数 {with_likes}）"
