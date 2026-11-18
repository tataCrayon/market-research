"""从存档 HTML 里取出 __INITIAL_STATE__ 并判定页面类型 / 归属。

四条实测坑（case-001 复盘 §2，写解析器前必读）：
1. `__INITIAL_STATE__` 是 JS 字面量不是严格 JSON——`undefined` 值必须清洗成 `null`；
2. 存档里混着采集者本人的会话数据（`user.userInfo` 是 viewer）——必须按目标 userId 归属校验；
3. 文件名不是采集成功的证据，只有内容能（三份「详情页」实际都是主页）；
4. **`new Map([])` 会让整份 state 解析失败**（2026-11-03 实测 7 篇详情页里挂 5 篇）。
   它藏在 `AiNoteDetailStore` 里，与正文无关，却足以把 `noteDetailMap` 一起带走——
   于是那 5 篇只能退回 DOM 正则，丢掉 `shareCount` / 精确发布时间 / 视频时长。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

MARKER = "__INITIAL_STATE__"


class ArchiveError(Exception):
    """存档不可用。message 直接面向人，写清「哪一类坏 + 怎么修」。"""


@dataclass(frozen=True)
class Archive:
    path: Path
    kind: str
    state: dict[str, Any]
    title: str = ""
    og_url: str = ""
    html: str = ""
    target_user_ids: tuple[str, ...] = ()
    viewer_user_id: str = ""
    note_items: tuple[dict[str, Any], ...] = field(default=())
    # state 是从哪来的。三个值，**必须能区分**：
    # - `html`：存档自带的 `<script>` 里就有（Ctrl+S 通道）
    # - `sidecar`：从同名 `.state.json` 旁车读到（扩展通道，`pageCapture` 把脚本剥了）
    # - `none`：没有 state，只有 DOM（详情页可以这么跑；主页不行）
    # 不记下来，事后没人能回答「这个粉丝数是从页面脚本读的，还是从哪来的」。
    state_source: str = "html"

    @property
    def usable(self) -> bool:
        return self.kind == "profile"


STATE_FROM_HTML = "html"
STATE_FROM_SIDECAR = "sidecar"
STATE_ABSENT = "none"

# 扩展通道（`pageCapture`）会把 `<script>` 整段剥掉，`__INITIAL_STATE__` 随之消失。
# 主页解析完全依赖 state（`userPageData` + `noteCards`），所以那条通道必须把 state
# 单独带回来，落成与 HTML 同名的旁车文件。
#
# **为什么是旁车而不是塞回 HTML**：MHTML 拆包（`mhtml.convert`）已经是踩平的路，
# 往 HTML 里注入脚本等于让「一份存档」有两种形状；而旁车是**可选增强**——
# 没有它，详情页照常走 DOM 解析，行为与今天完全一致。
STATE_SIDECAR_SUFFIX = ".state.json"



def _brace_match(text: str, start: int) -> str:
    """从 start（一个 '{' 的位置）开始做括号配对，跳过字符串内的括号。"""
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    raise ArchiveError("__INITIAL_STATE__ 大括号不闭合——存档可能被截断，请重新保存整页。")


def extract_literal(html: str) -> str:
    pos = html.find(MARKER)
    if pos < 0:
        raise ArchiveError(f"找不到 {MARKER}：这不是服务端渲染的页面存档（详情页是 CSR，请改用 Ctrl+A 复制文本通道）。")
    brace = html.find("{", pos)
    if brace < 0:
        raise ArchiveError(f"{MARKER} 之后没有对象字面量——存档不完整。")
    return _brace_match(html, brace)


def clean_js_literals(literal: str) -> str:
    """把字符串外的 JS 专有字面量换成 JSON 能解析的东西。

    逐字符走，不整串替换：正文里出现 `a:undefined` 这类文字时，整串替换会改坏内容。

    两类替换（都来自真实存档实测）：
    1. `undefined` / `NaN` → `null`（294 处）
    2. `new Map([])` / `new Set()` → `{}` / `[]`

    第 2 条是 2026-11-03 补的，**它决定了 detail 通道是能用 state 还是只能啃 DOM**：
    实测 7 篇详情页里有 5 篇的 `__INITIAL_STATE__` 里带
    `"AiNoteDetailStore":{"noteDetailMap":new Map([])}`，JSON 在 `new` 上直接报
    `Expecting value`。修之前这 5 篇只能退回 DOM 正则，丢掉 shareCount / 精确发布时间 /
    视频时长——而这三样 DOM 里都没有。
    """
    tokens = {"undefined", "NaN"}
    out: list[str] = []
    idx = 0
    in_string = False
    escaped = False
    length = len(literal)
    while idx < length:
        ch = literal[idx]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            idx += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            idx += 1
            continue
        if literal.startswith("new", idx) and _is_token_boundary(literal, idx, idx + 3):
            replaced = _empty_constructor_to_json(literal, idx)
            if replaced is not None:
                text, next_idx = replaced
                out.append(text)
                idx = next_idx
                continue
        matched = next((t for t in tokens if literal.startswith(t, idx)), None)
        if matched and _is_token_boundary(literal, idx, idx + len(matched)):
            out.append("null")
            idx += len(matched)
            continue
        out.append(ch)
        idx += 1
    return "".join(out)


_EMPTY_CTOR_RE = re.compile(r"new\s+(Map|Set)\s*\(")


def _empty_constructor_to_json(literal: str, start: int) -> tuple[str, int] | None:
    """`new Map([])` → `{}`，`new Set()` → `[]`。不是空构造就当没看见（让它大声失败）。

    **为什么不把非空 Map 也转换**：`new Map([["a",1]])` 里有真实数据，转成 `{}` 就是
    静默丢数据。宁可让 JSON 解析报错、人来看一眼，也不要「解析成功但少了一半内容」。
    """
    match = _EMPTY_CTOR_RE.match(literal, start)
    if not match:
        return None
    kind = match.group(1)
    depth = 1
    idx = match.end()
    in_string = False
    escaped = False
    while idx < len(literal) and depth:
        ch = literal[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        idx += 1
    if depth:
        return None
    inner = literal[match.end(): idx - 1].strip()
    if inner not in ("", "[]", "[ ]"):
        return None
    return ("{}" if kind == "Map" else "[]"), idx


def _is_token_boundary(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (before.isalnum() or before in "_$") and not (after.isalnum() or after in "_$")


def state_sidecar_path(archive_path: str | Path) -> Path:
    """`x.html` → `x.state.json`。

    ⚠️ 不用 `Path.with_suffix`：它的入参是**单段后缀**，
    传 `.state.json` 在不同 Python 版本上行为不一致（3.14 起会拒多段）。
    拼字符串在哪个版本上都对。
    """
    path = Path(archive_path)
    return path.parent / f"{path.stem}{STATE_SIDECAR_SUFFIX}"


def read_state_sidecar(archive_path: str | Path) -> dict[str, Any] | None:
    """读同名旁车 `.state.json`。没有就返回 None——**缺席不是错误**。

    为什么允许缺席：`Ctrl+S` 通道的 state 本来就在 HTML 里；详情页没有 state 也能走 DOM。
    把「没旁车」当成错误，会让每一条老通道都开始报错。
    """
    path = state_sidecar_path(archive_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ArchiveError(
            f"{path.name} 不是合法 JSON（{exc}）：旁车坏了，"
            f"删掉它重采一次即可——不要手改，它是脚本状态的原样拷贝。"
        ) from exc
    if not isinstance(payload, dict):
        raise ArchiveError(f"{path.name} 顶层不是对象：旁车应当是 {{}} 形状的 state。")
    return payload


def write_state_sidecar(archive_path: str | Path, state: dict[str, Any]) -> Path:
    """把 state 写成旁车。**不覆盖已有旁车**——和所有证据一样，只增不改。"""
    path = state_sidecar_path(archive_path)
    if not path.exists():
        path.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return path


def parse_html(html: str) -> dict[str, Any]:
    """HTML 文本 → `__INITIAL_STATE__` 对象。存档只读一次，避免大文件重复 IO。"""
    literal = clean_js_literals(extract_literal(html))
    try:
        state = json.loads(literal)
    except json.JSONDecodeError as exc:
        raise ArchiveError(
            f"__INITIAL_STATE__ 仍不是合法 JSON（{exc.msg} @ char {exc.pos}）：存档可能被其他脚本改写过。"
        ) from exc
    if not isinstance(state, dict):
        raise ArchiveError("__INITIAL_STATE__ 顶层不是对象。")
    return state


def load_state(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        raise ArchiveError(f"文件不存在：{file_path}")
    return parse_html(file_path.read_text(encoding="utf-8", errors="replace"))


def flatten_note_items(state: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """user.notes 是「分页数组的数组」；空页是 []。拍平并保留原始顺序。"""
    notes = (state.get("user") or {}).get("notes") or []
    items: list[dict[str, Any]] = []
    for page in notes:
        if isinstance(page, list):
            items.extend(item for item in page if isinstance(item, dict))
        elif isinstance(page, dict):
            items.append(page)
    return tuple(items)


def iter_note_cards(state: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for item in flatten_note_items(state):
        card = item.get("noteCard")
        if isinstance(card, dict):
            yield card


def target_user_ids(state: dict[str, Any]) -> tuple[str, ...]:
    """目标账号 owner id 的两个独立来源，取并集后再交叉校验。

    来源一：noteQueries[].userId（页面发起笔记请求时带的 id）
    来源二：noteCard.user.userId（每张卡片自带的作者 id）
    两者不一致说明存档里混入了别人的笔记（搜索页误采就是这个形状）。
    """
    ids: set[str] = set()
    for query in (state.get("user") or {}).get("noteQueries") or []:
        uid = (query or {}).get("userId")
        if uid:
            ids.add(str(uid))
    for card in iter_note_cards(state):
        uid = (card.get("user") or {}).get("userId")
        if uid:
            ids.add(str(uid))
    return tuple(sorted(ids))


def viewer_user_id(state: dict[str, Any]) -> str:
    return str(((state.get("user") or {}).get("userInfo") or {}).get("userId") or "")


def extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def classify(state: dict[str, Any], title: str = "", og_url_value: str = "") -> str:
    """按「证据强度」排序判别，不按文件名（文件名是假信号，见 case-001 §2 F1）。

    判据来源（均来自真实存档实测）：
    - 详情页：`og:url` 指向 `/explore/<id>` **或** `note.noteDetailMap` 非空。
      ⚠️ 2026-10-27 实测纠正：`noteDetailMap` 为空**不代表**是主页——整页加载 /explore/<id>
      时服务端会回正文与互动栏，而 `noteDetailMap` 仍可能是空的（脚本未跑）。
      所以 og:url 是更强、更早的判据，必须排在前面。
    - 主页：`user.userPageData.basicInfo.nickname` 非空
    - 搜索页：`<title>` 含「搜索」
    - 推荐流：`feed.feeds` 非空
    """
    if og_url_value and re.search(r"/(?:explore|discovery/item)/[0-9a-f]{24}", og_url_value, re.I):
        return "note_detail"
    if (state.get("note") or {}).get("noteDetailMap"):
        return "note_detail"
    basic_info = (((state.get("user") or {}).get("userPageData") or {}).get("basicInfo") or {})
    if str(basic_info.get("nickname") or "").strip():
        return "profile"
    if "搜索" in title:
        return "search_page"
    if (state.get("search") or {}).get("feeds") or (state.get("search") or {}).get("searchValue"):
        return "search_page"
    if (state.get("feed") or {}).get("feeds"):
        return "home_feed"
    if flatten_note_items(state):
        return "profile"
    return "empty_skeleton"


REJECT_REASONS = {
    "note_detail": "详情页：需走详情通道（`ar parse-detail` 或 Ctrl+A 复制），不是主页数据。",
    "search_page": "搜索页：内容是他人笔记，不是目标账号主页（归属校验会把它们全部丢弃）。",
    "home_feed": "推荐流 / 结果页：内容是他人笔记，不是某个账号的主页。",
    "empty_skeleton": "空壳存档：noteDetailMap 为空且无笔记列表，页面没渲染出来。",
    "unknown": "无法识别页面类型：既没有笔记列表，也没有详情或搜索结构。",
    "profile_saved_as_detail": "这份看起来是从主页点开弹层后存的——og:url 仍指向主页。"
                               "请在新标签页整页打开该笔记后再保存（见 docs/详情页采集-结论.md）。",
}


def open_archive(path: str | Path) -> Archive:
    """读一份存档。**state 有三个来源，按可靠性排序试，直到有一个能用。**

    为什么要三档而不是「HTML 里没有就报错」：`pageCapture`（扩展通道）会把
    `<script>` 整段剥掉，`__INITIAL_STATE__` 随之消失——那不是存档坏了，
    只是这条通道不带脚本。而主页解析**完全依赖** state（`userPageData` + `noteCards`），
    所以扩展必须把 state 单独带回来落成旁车。三档分别是：

    | 来源 | 什么时候有 | 谁能用 |
    |---|---|---|
    | `html` | Ctrl+S「网页，全部」 | 主页 + 详情页 |
    | `sidecar` | 扩展采集（`<name>.state.json`） | 主页 + 详情页 |
    | `none` | 只有 DOM | 仅详情页（og:url 可判） |

    **三档必须都留下痕迹**（`Archive.state_source`），否则事后没人能回答
    「这个粉丝数是从页面脚本读的，还是从别处来的」。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ArchiveError(f"文件不存在：{file_path}")
    html = file_path.read_text(encoding="utf-8", errors="replace")
    title = extract_title(html)
    match = re.search(r'og:url[^>]*content="([^"]+)"', html)
    og = match.group(1) if match else ""

    state: dict[str, Any] = {}
    source = STATE_ABSENT
    try:
        state = parse_html(html)
        source = STATE_FROM_HTML
    except ArchiveError as exc:
        # ① 先试旁车：扩展通道的 state 在这里。
        sidecar = read_state_sidecar(file_path)
        if sidecar:
            state = sidecar
            source = STATE_FROM_SIDECAR
        # ② 还是空：详情页可以只靠 DOM（主页不行，它的数据全在 state 里）。
        elif og and re.search(r"/(?:explore|discovery/item)/[0-9a-f]{24}", og, re.I):
            return Archive(path=file_path, kind="note_detail", state={}, title=title,
                           og_url=og, html=html, state_source=STATE_ABSENT)
        else:
            raise ArchiveError(
                f"{exc} 也没有 {file_path.stem}{STATE_SIDECAR_SUFFIX} 旁车——"
                f"这条通道（扩展的 pageCapture）会把 <script> 剥掉，"
                f"主页必须靠旁车里的 state 才能解析。"
                f"用扩展重采一次，或改用 Ctrl+S 选「网页，全部」。"
            ) from exc
    return Archive(
        path=file_path,
        kind=classify(state, title, og),
        state=state,
        title=title,
        og_url=og,
        html=html,
        target_user_ids=target_user_ids(state),
        viewer_user_id=viewer_user_id(state),
        note_items=flatten_note_items(state),
        state_source=source,
    )
