"""把 MHTML（单文件网页存档）拆回「HTML + `_files/` 目录」——Ctrl+S 的那个形状。

## 为什么需要这一步

2026-11-12：浏览器扩展存下来的只有 DOM 序列化，**拿不到图**。而实测 11 篇图文笔记里，
文字层占全文的比例中位数只有 **28.1%**，6 篇低于 30%，最极端两篇是 **2.2%** 与 **1.4%**
（正文 299 字 / 图上 13,200 字）。也就是说：不带图的存档，对本项目要深拆的那批篇目
**等于一篇空笔记**。

而「带图的完整存档」原本只有一条路——人按 Ctrl+S 选「网页，全部」，
代价是**每次都弹保存对话框**，还产出 HTML + `_files/` 两件套要人挪。

MHTML 把这两件事一起解决：Chrome 的「另存为 MHTML」是**单文件**且**把图一起打包**。
扩展用 `chrome.pageCapture.saveAsMHTML` 拿到它，下载到固定目录，
再由本模块拆成现有管道认得的形状——**下游一个字都不用改**。

## 为什么拆而不是让下游认 MHTML

`images.py` / `detail.py` / `ingest` 全部按「HTML + `_files/` 同目录」工作，
而且那条路径上已经有：图与笔记的绑定（含 `note_id` 校验）、平台声明张数对账、
本地 OCR、孤立 `_files/` 反查救援。为一个新输入格式重写一遍这些，
等于把已经踩平的坑再踩一次。

**所以：新格式在边界处被翻译成旧形状，而不是让旧管道长出第二条腿。**

## 关键实现选择

### 用标准库 `email` 解析，不手写 MIME

MHTML 就是 `multipart/related` 的 MIME。`quoted-printable` / `base64` 解码、
CRLF 折叠、编码处理全都是坑，而 `email` 模块已经把它们都解决了。
手写一个只支持「Chrome 这一种写法」的解析器，是在给自己造一个更脆的东西。

### URL 与 `cid:` 两种引用都要改写

Chrome 生成的 MHTML 里，子资源既可能以**原始绝对 URL** 出现（靠 `Content-Location` 对上），
也可能被改写成 `cid:<content-id>`。**两种都实测见过**，所以两种都改。
只处理一种的话，另一批笔记的图会静默全部丢失——而丢图不会报错，只会让正文变短。

### 文件名沿用 `images.url_basename`

`_files/` 里的文件名必须和 HTML 里 `src` 指的名字一致，而下游 `images.py` 用的正是
「URL 末段」。所以这里**复用同一个函数**，不另写一份——两份取名规则一旦漂移，
图就对不上笔记，而且同样不报错。
"""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from .images import url_basename

MHTML_SUFFIXES = (".mhtml", ".mht")
FILES_SUFFIX = "_files"

# Windows 文件名里非法的字符。MHTML 的 Content-Location 末段可能是任意 URL 片段，
# 直接落到磁盘会在某些篇目上抛 OSError——那时人会以为「这篇没图」。
ILLEGAL_IN_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# `cid:` 引用：`cid:frame-ABC@mhtml.blink` 或 `cid:xxxx`
CID_REF_RE = re.compile(r"cid:([^\"')\s>]+)", re.I)


@dataclass(frozen=True)
class Part:
    """一个被拆出来的子资源（图 / 样式 / 字体）。"""

    name: str
    content: bytes
    content_type: str = ""

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")


@dataclass
class MhtmlDoc:
    html: str
    parts: list[Part]
    source_url: str = ""

    @property
    def has_images(self) -> bool:
        return any(part.is_image for part in self.parts)


def looks_like_mhtml(path: Path) -> bool:
    """先看后缀，再看**内容开头**。

    只认后缀不够：从浏览器拷来或改名后可能变成 `.html`，而 `ingest` 会把它当普通存档
    解析，然后报一堆「找不到 __INITIAL_STATE__」——真正的原因是格式不认。

    判据是 `MIME-Version` 与 `multipart/` **同时**出现：
    MHTML 一定是 MIME 多段（Chrome 用 `multipart/related`），
    普通 HTML 里可能有 `multipart/` 字样（正文里的代码示例），但不会有 MIME 头。
    """
    if path.suffix.lower() in MHTML_SUFFIXES:
        return True
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    text = head.decode("latin-1", errors="replace").lower()
    return "mime-version" in text and "multipart/" in text


def _decode_part(part: email.message.Message) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload
    raw = part.get_payload()
    if isinstance(raw, str):
        charset = part.get_content_charset() or "utf-8"
        try:
            return raw.encode(charset, errors="replace")
        except LookupError:
            return raw.encode("utf-8", errors="replace")
    return b""


def _safe_name(raw: str, fallback: str) -> str:
    name = unquote(raw or "").strip()
    name = ILLEGAL_IN_NAME.sub("_", name)
    return name or fallback


def _header(part: email.message.Message, name: str) -> str:
    """读一个头部值并**折行还原**。

    MIME 头按 RFC 5322 会在 78 字符附近折行，读回来是 `"\\n https://…"`。
    这不是极端情况——长 URL 必然触发。`compat32` 策略下 `get()` 不做折叠还原，
    于是 `'\\n https://…'` 与 HTML 里那个干净的 URL **永远不相等**，
    替换表静默失配、图全部丢链接。实测就踩了这一个（2026-11-12）。
    """
    raw = part.get(name) or ""
    return " ".join(str(raw).split())


def _part_name(part: email.message.Message, index: int, used: set[str]) -> str:
    """给子资源定一个本地文件名：优先取 `Content-Location` 的末段。

    取不到（只有 `cid:`）时退回 `content-<序号>` 加原扩展名——**不猜来源 URL**。
    重名时加序号，绝覆盖：两张图写进同一个名字，等于静默丢一张。
    """
    location = _header(part, "Content-Location")
    name = _safe_name(url_basename(location), "")
    if not name:
        guessed = part.get_content_type().split("/")[-1]
        ext = {"jpeg": "jpg", "svg+xml": "svg", "x-icon": "ico"}.get(guessed, guessed)
        name = f"content-{index}.{ext}"
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    candidate, counter = name, 1
    while candidate in used:
        counter += 1
        candidate = f"{stem}-{counter}.{suffix}" if suffix else f"{stem}-{counter}"
    used.add(candidate)
    return candidate


def parse(data: bytes) -> MhtmlDoc:
    """拆开 MHTML。返回 (主 HTML, 子资源列表)。主 HTML 是**第一段 text/html**。"""
    message = email.message_from_bytes(data, policy=email.policy.compat32)
    if not message.is_multipart():
        # 没有 MIME 头 = 根本不是 MHTML。返回空 HTML，让 `convert` 报一条能看懂的错，
        # 而不是把一段乱码当网页存下去（那会让下游报「找不到 __INITIAL_STATE__」，
        # 把人带到完全错误的方向）。
        if not _header(message, "MIME-Version"):
            return MhtmlDoc(html="", parts=[])
        # 有 MIME 头但只有一段：整份内容就是 HTML。当成普通存档原文处理。
        return MhtmlDoc(html=data.decode("utf-8", errors="replace"), parts=[])

    source_url = _header(message, "Snapshot-Content-Location")
    html = ""
    parts: list[Part] = []
    used: set[str] = set()
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if not html and ctype == "text/html":
            charset = part.get_content_charset() or "utf-8"
            try:
                html = _decode_part(part).decode(charset, errors="replace")
            except LookupError:
                html = _decode_part(part).decode("utf-8", errors="replace")
            continue
        if ctype in ("text/html",):
            continue  # 主文档之外的 HTML 段（极少见）不当作资源
        payload = _decode_part(part)
        if not payload:
            continue
        parts.append(Part(
            name=_part_name(part, index, used),
            content=payload,
            # 按 **MIME 类型**判是不是图，不看文件名后缀：
            # `Content-Location` 的末段常常没有扩展名（小红书的图就是
            # `1040g2sg31mp21…` 这种纯哈希），靠后缀判会把它们全算成「非图」。
            content_type=ctype,
        ))
    return MhtmlDoc(html=html, parts=parts, source_url=source_url)


def rewrite_references(html: str, doc: MhtmlDoc, message: bytes, folder: str) -> str:
    """把 `cid:` 引用与原始绝对 URL 都改成本地相对路径 `./<folder>/<name>`。

    为什么必须两种都改：Chrome 生成的 MHTML 里，子资源既可能以原始绝对 URL 出现
    （靠 `Content-Location` 对上），也可能被改写成 `cid:`。**只改一种，
    另一批笔记的图会全部丢失，而且不会报错**——正文只是悄悄变短。
    """
    result = html
    mapping: dict[str, str] = {}
    mime = email.message_from_bytes(message, policy=email.policy.compat32)
    names = iter([part.name for part in doc.parts])
    for part in mime.walk():
        if part.is_multipart() or part.get_content_type() == "text/html":
            continue
        try:
            name = next(names)
        except StopIteration:
            break
        target = f"./{folder}/{name}"
        location = _header(part, "Content-Location")
        if location:
            mapping[location] = target
        content_id = _header(part, "Content-ID").strip().strip("<>")
        if content_id:
            mapping[content_id] = target
            mapping[f"cid:{content_id}"] = target

    # 长键优先：短 URL 可能是长 URL 的前缀，先替换短的会把长的改坏。
    for key in sorted(mapping, key=len, reverse=True):
        if key:
            result = result.replace(key, mapping[key])
    # 兜底：仍以 `cid:` 开头但没在表里的引用，改成一个不会 404 的相对路径，
    # 让 `images.py` 的对账把它算作「平台声明 N 张、绑到 M 张」——缺口可见，而不是静默。
    return CID_REF_RE.sub(lambda m: f"./{folder}/{_safe_name(m.group(1), 'unknown')}", result)


def convert(source: Path, out_dir: Path | None = None) -> tuple[Path, int]:
    """MHTML → `<stem>.html` + `<stem>_files/`。返回 (HTML 路径, 子资源数)。

    产物目录与 Chrome「网页，全部」完全同构，所以 `ar ingest` / `ar images` /
    OCR / 孤立目录反查**全部无需改动**。
    """
    target_dir = Path(out_dir) if out_dir else source.parent
    data = source.read_bytes()
    doc = parse(data)
    if not doc.html.strip():
        raise ValueError(
            f"{source.name} 里没有 text/html 段——这不是有效的 MHTML"
            f"（可能是文件不完整，或把普通 HTML 改名成了 .mhtml）。"
        )
    html_path = target_dir / f"{source.stem}.html"
    folder = f"{source.stem}{FILES_SUFFIX}"
    files_dir = target_dir / folder
    files_dir.mkdir(parents=True, exist_ok=True)
    for part in doc.parts:
        (files_dir / part.name).write_bytes(part.content)
    html_path.write_text(
        rewrite_references(doc.html, doc, data, folder), encoding="utf-8"
    )
    return html_path, len(doc.parts)
