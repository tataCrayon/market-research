"""图片通道：把「正文在图片里」的那部分内容从本地存档里取出来。

**只处理本地文件**：Chrome「网页，全部」存盘时把图片一起写进 `<存档>_files/`，
本模块只在这些文件上来回走——不下载、不访问任何平台域名（AGENTS.md §2 纪律 1）。

三条实测事实（2026-10-29，见 docs/图片内容通道.md）：
1. 图片**默认已经存在本地**了：`_files/` 里那串 `1040g0k0…!nd_dft_wlteh_webp_3`
   就是正文图，一篇 5–24 张，10–14 MB。
2. 图片与笔记的**唯一可靠 join key 是 URL 末段**：Chrome 存盘取 URL 最后一段做文件名，
   而 `imageList[].fileId` 是另一套 id（`spectrum/…`、`notes_pre_post/…`），
   按 fileId 前缀匹配会漏——实测「果然马斯克」8 张全漏、「普通人」5 张全中，所以只认 URL。
3. 文字在图片里的笔记，**正文层是空的**：只读 `#detail-desc` 会以为这篇没内容。

纪律：OCR 结果只写文件，**绝不把图片塞进模型上下文**（那是一条 400 的直接来源）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .detail import DESC_RE, TAG_RE, body_paragraphs, note_id_from_url, og_url

IMAGE_EXT_SKIP = {".js", ".css", ".html", ".htm", ".svg", ".json", ".下载"}
LIST_START_RE = re.compile(r'"imageList"\s*:\s*\[')
NOTE_ID_BEFORE_RE = re.compile(r'"noteId"\s*:\s*"([0-9a-f]{24})"')
SRC_RE = re.compile(r'src="(\./[^"]*_files/[^"]+)"')
DOM_NOTE_START = 'id="noteContainer"'
DOM_NOTE_END = 'class="interact-container"'
DOM_SLIDER_START = "note-slider"
DOM_SLIDER_END = "note-scroller"
SLIDE_INDEX_RE = re.compile(r'data-swiper-slide-index="(\d+)"')
SLIDE_IMG_RE = re.compile(r'src="(\./[^"]*_files/[^"]+)"')
# 平台自己渲染的「第几张/共几张」计数器：`<div class="fraction">1/26</div>`
# 这是唯一一个**由平台给出**的总数，用来校验我们到底有没有把图绑全。
FRACTION_RE = re.compile(r'class="[^"]*\bfraction\b[^"]*"[^>]*>\s*\d+\s*/\s*(\d+)\s*<')
URL_KEYS = ("urlDefault", "url", "urlPre")
# 比重叠时只留中文与字母数字：OCR 会插换行、也会把标点认成别的符号
NON_WORD_RE = re.compile(r"[^\u4e00-\u9fff\w]")


@dataclass(frozen=True)
class NoteImage:
    index: int
    url: str
    file_id: str
    width: int | None = None
    height: int | None = None
    local: Path | None = None

    @property
    def resolved(self) -> bool:
        return self.local is not None


@dataclass
class NoteImages:
    note_id: str
    source: str
    images: list[NoteImage] = field(default_factory=list)
    declared: int | None = None   # 平台计数器给的总张数；拿不到就是 None

    @property
    def total(self) -> int:
        return len(self.images)

    @property
    def local(self) -> int:
        return sum(1 for image in self.images if image.resolved)

    def missing(self) -> list[int]:
        return [image.index for image in self.images if not image.resolved]

    @property
    def incomplete(self) -> bool:
        """绑到的张数少于平台声明的张数。

        **为什么需要它**：`source` 说「绑上了」不等于「绑全了」。平台自己在
        `<div class="fraction">1/26</div>` 里给了总张数，那是唯一可信的外部口径。
        少了就说明我们漏了图（要修解析），不少就说明原始数据本身就只有这些
        （要记 ⬜，别去改代码）。
        """
        return self.declared is not None and self.total < self.declared


def images_dir(archive: Path) -> Path:
    """Chrome 的伴随目录命名固定：`<同名>_files`。"""
    return archive.with_name(f"{archive.stem}_files")


def referenced_basenames(html: str) -> set[str]:
    """HTML 里 `src="./…_files/<名字>"` 引用到的文件名集合。

    用途：存档 HTML 被 `ar ingest` 搬走、`_files` 目录落在原来位置时，
    靠它把两边重新认回来（实测踩过：Kimi 那篇的图至今还在 account/ 根目录）。
    """
    return {url_basename(src) for src in SRC_RE.findall(html)}


HASH_NAME_MIN = 24


def _distinctive(names: Iterable[str]) -> set[str]:
    """只留「内容文件名」：长哈希、无常见静态资源后缀。

    为什么必须过滤：每份存档都引用同一批 `index.a9f61821.js`、`standardSearchIcon.svg`，
    拿它们做匹配会让**任何两份存档都「匹配成功」**——实测就错配到了别人的 `_files`。
    """
    return {
        name for name in names
        if len(name) >= HASH_NAME_MIN and Path(name).suffix.lower() not in IMAGE_EXT_SKIP
    }


def locate_files_dir(
    archive: Path,
    html: str,
    search_root: Path | None = None,
    extra_names: Iterable[str] = (),
) -> Path | None:
    """找到该存档真正的 `_files` 目录：先看同名兄弟目录，再按内容文件名反查。

    `extra_names` 是 `imageList` 里那些 URL 的末段——只用 `<img src>` 反查会漏：
    结构化数据里的图不一定已经在 DOM 里渲染成 `<img>`（实测详情页首屏只渲染 3 张）。

    反查的三道闸（错配一张图就是错数据，所以宁可返回 None）：
    1. 只比内容文件名，不比静态资源（见 `_distinctive`）；
    2. 候选目录若有**同名的兄弟 HTML**，说明它是另一份存档的家 → 跳过；
    3. 命中数必须 ≥2 或覆盖全部引用，否则视为巧合。
    """
    sibling = images_dir(archive)
    if sibling.is_dir():
        return sibling
    if search_root is None or not search_root.is_dir():
        return None
    wanted = _distinctive(set(referenced_basenames(html)) | set(extra_names))
    if not wanted:
        return None
    best: tuple[int, Path] | None = None
    for candidate in sorted(search_root.rglob("*_files")):
        if not candidate.is_dir():
            continue
        owner = candidate.with_name(candidate.name[: -len("_files")] + ".html")
        if owner.is_file() and owner.resolve() != archive.resolve():
            continue
        names = _distinctive(unquote(path.name) for path in candidate.iterdir() if path.is_file())
        hits = len(wanted & names)
        if (hits >= 2 or (hits and hits == len(wanted))) and (best is None or hits > best[0]):
            best = (hits, candidate)
    return best[1] if best else None


def local_files(files_dir: Path) -> dict[str, Path]:
    """文件名（URL 末段）→ 本地路径。不做模糊匹配：错配一张图就是错数据。"""
    if not files_dir.is_dir():
        return {}
    index: dict[str, Path] = {}
    for path in files_dir.iterdir():
        if path.is_file() and path.suffix.lower() not in IMAGE_EXT_SKIP:
            index.setdefault(unquote(path.name), path)
    return index


def url_basename(url: str) -> str:
    """取 URL 最后一段（= Chrome 存盘时的文件名）。查询串与协议头都不参与。"""
    if not url:
        return ""
    path = urlparse(url).path or url.split("?")[0]
    return unquote(path.rsplit("/", 1)[-1])


def _candidate_urls(entry: dict[str, Any]) -> Iterable[str]:
    for key in URL_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value:
            yield value
    for info in entry.get("infoList") or []:
        if isinstance(info, dict) and isinstance(info.get("url"), str):
            yield info["url"]


def resolve(entry: dict[str, Any], index: int, files: dict[str, Path]) -> NoteImage:
    """一张图 → NoteImage。命中即绑定；一个都不中就是 ⬜（不猜、不取相邻图）。"""
    local: Path | None = None
    chosen = ""
    for url in _candidate_urls(entry):
        name = url_basename(url)
        if name in files:
            local, chosen = files[name], url
            break
    if not chosen:
        chosen = next(iter(_candidate_urls(entry)), "")
    return NoteImage(
        index=index,
        url=chosen,
        file_id=str(entry.get("fileId") or ""),
        width=_as_int(entry.get("width")),
        height=_as_int(entry.get("height")),
        local=local,
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bracket_array(text: str, start: int) -> str:
    """从 `[` 起做方括号配对，跳过字符串内的括号。截断的数组返回空串。"""
    depth, in_string, escaped = 0, False, False
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
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return ""


def raw_image_lists(html: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """直接从 HTML 文本抠 imageList（`__INITIAL_STATE__` 解析失败时的兜底）。

    实测：`noteDetailMap` 为空**不代表** HTML 里没有 imageList——
    详情页的 `feed.undertakeNote.items[].noteCard` 里还留着一份。
    note_id 取数组前最近的一个 `"noteId"`；取不到就用空串（调用方再按 og:url 补）。
    """
    found: list[tuple[str, list[dict[str, Any]]]] = []
    for match in LIST_START_RE.finditer(html):
        literal = _bracket_array(html, match.end() - 1)
        try:
            array = json.loads(literal)
        except json.JSONDecodeError:
            continue
        if not isinstance(array, list) or not array:
            continue
        entries = [item for item in array if isinstance(item, dict)]
        if not entries:
            continue
        before = NOTE_ID_BEFORE_RE.findall(html[: match.start()])
        found.append((before[-1] if before else "", entries))
    return found


def image_lists_from_state(state: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """`note.noteDetailMap.<id>.note.imageList` —— 结构最干净的一份。"""
    note_map = ((state.get("note") or {}).get("noteDetailMap") or {})
    found: list[tuple[str, list[dict[str, Any]]]] = []
    for note_id, wrapper in note_map.items():
        note = (wrapper or {}).get("note") if isinstance(wrapper, dict) else None
        images = (note or {}).get("imageList") if isinstance(note, dict) else None
        if isinstance(images, list) and images:
            found.append((str(note_id), [item for item in images if isinstance(item, dict)]))
    return found


def _slider_region(html: str) -> str:
    start = html.find(DOM_SLIDER_START)
    if start < 0:
        return ""
    end = html.find(DOM_SLIDER_END, start)
    return html[start:end] if end > start else html[start:]


def dom_slides(html: str, files: dict[str, Path]) -> list[NoteImage]:
    """按 swiper 的 `data-swiper-slide-index` 取图：**这是顺序与张数都正确的读法**。

    实测（Kimi 那篇）：swiper 会把首尾 slide 各复制一份，直接扫 `<img>` 会多出重复；
    而 slide-index 是平台自己给的序号（0..N-1），去重后顺序也天然正确。
    头像、推荐流封面都不在 slider 里，因此不会被误算——实测整段扫描会多出十几张别人的封面。
    """
    region = _slider_region(html)
    if not region:
        return []
    indexed: dict[int, str] = {}
    for match in SLIDE_INDEX_RE.finditer(region):
        index = int(match.group(1))
        if index in indexed:
            continue
        img = SLIDE_IMG_RE.search(region, match.end())
        if not img:
            continue
        name = url_basename(img.group(1))
        if name in files:
            indexed[index] = name
    return [
        NoteImage(index=order, url="", file_id="", local=files[name])
        for order, (_, name) in enumerate(sorted(indexed.items()), start=1)
    ]


def dom_images(html: str, files: dict[str, Path]) -> list[NoteImage]:
    """最后兜底：`#noteContainer` 到互动栏之间已经渲染出来的 `<img>`，按出现顺序去重。

    两个实测坑（2026-10-29）：
    1. **必须切到互动栏为止**。笔记容器之后是推荐流，整段扫下去 Kimi 那篇会从 26 张变 45 张——
       多出来的全是别人的封面（静默错数据）。
    2. swiper 会把首尾 slide 复制一份（`swiper-slide-duplicate`），所以按文件名去重。
    这条通道拿不到宽高（写 ⬜），只在没有结构化 imageList / slide 序号时才用。
    """
    start = html.find(DOM_NOTE_START)
    if start < 0:
        return []
    end = html.find(DOM_NOTE_END, start)
    region = html[start:end] if end > start else html[start:]
    seen: set[str] = set()
    images: list[NoteImage] = []
    for src in SRC_RE.findall(region):
        name = url_basename(src)
        if not name or name in seen or name not in files:
            continue
        seen.add(name)
        images.append(NoteImage(index=len(images) + 1, url="", file_id="", local=files[name]))
    return images


def declared_total(html: str) -> int | None:
    """平台自己渲染的「共几张」。取不到返回 None —— **不猜、不用我们自己的张数顶替**。

    实测 5 篇详情页全部带这个计数器（`1/26`、`2/24`、`1/5`、`2/6`、`3/8`），
    分母与我们从 `imageList`/DOM 取到的张数完全一致——所以它能当外部校验基准。
    """
    match = FRACTION_RE.search(html)
    return int(match.group(1)) if match else None


def collect(
    archive_path: Path,
    state: dict[str, Any] | None = None,
    html: str = "",
    search_root: Path | None = None,
) -> NoteImages:
    """一个详情页存档 → 该笔记的图片序列（带本地绑定）。

    `search_root` 用于救回「HTML 被搬走、_files 落在原地」的存档（见 locate_files_dir）。
    """
    text = html or archive_path.read_text(encoding="utf-8", errors="replace")
    declared = declared_total(text)
    from_state = image_lists_from_state(state or {})
    candidates = from_state or raw_image_lists(text)
    entries_all = [entry for _, entries in candidates for entry in entries]
    wanted_names = [
        url_basename(url) for entry in entries_all for url in _candidate_urls(entry)
    ]
    found_dir = locate_files_dir(archive_path, text, search_root, extra_names=wanted_names)
    files = local_files(found_dir) if found_dir else {}
    note_id = note_id_from_url(og_url(text))
    if candidates:
        list_note_id, entries = max(candidates, key=lambda pair: len(pair[1]))
        resolved = [resolve(entry, idx + 1, files) for idx, entry in enumerate(entries)]
        source = "noteDetailMap" if from_state else "raw_imageList"
        if not any(image.resolved for image in resolved):
            # 一张都没绑定成功 → 这份存档只存了 HTML，没有伴随目录，如实标注
            source = f"{source}(未存图)"
        return NoteImages(note_id=note_id or list_note_id, source=source,
                          images=resolved, declared=declared)
    slides = dom_slides(text, files)
    if slides:
        return NoteImages(note_id=note_id, source="dom_slider", images=slides, declared=declared)
    return NoteImages(note_id=note_id, source="dom",
                      images=dom_images(text, files), declared=declared)


# ---- OCR：本地引擎，按需加载；缺依赖时明确报错，不静默降级 ----


class OcrUnavailable(RuntimeError):
    """本地 OCR 引擎不可用。消息直接面向人，写清装什么。"""


def load_engine() -> Any:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 取决于本机环境
        raise OcrUnavailable(
            "本地 OCR 未安装：`python -m pip install rapidocr-onnxruntime`（离线可跑，不联网）。"
            "不装也能用 `ar images`：只出图片清单与缺口，不做字。"
        ) from exc
    return RapidOCR()


@dataclass(frozen=True)
class OcrResult:
    lines: tuple[str, ...]
    chars: int
    mean_score: float

    @property
    def low_quality(self) -> bool:
        """字太少或置信太低 → 必须人工看图，不得直接当正文用。"""
        return self.chars < 10 or (self.mean_score and self.mean_score < 0.80)


def ocr_file(engine: Any, path: Path) -> OcrResult:
    raw, _ = engine(str(path))
    rows = [item for item in (raw or []) if len(item) >= 3]
    lines = tuple(str(item[1]).strip() for item in rows if str(item[1]).strip())
    scores = [float(item[2]) for item in rows if isinstance(item[2], (int, float))]
    chars = sum(len(line) for line in lines)
    mean = round(sum(scores) / len(scores), 3) if scores else 0.0
    return OcrResult(lines=lines, chars=chars, mean_score=mean)


def image_text_path(text_root: Path, note_id: str) -> Path:
    return text_root / f"{note_id}.images.txt"


def render_text(
    note: NoteImages,
    results: dict[int, OcrResult],
    header: dict[str, Any],
) -> str:
    """OCR 结果落文件的样式：每张图一段，段首是图片文件名 + 质量标记。

    为什么保留「一张图一段」而不是拼成一整篇：**图片之间的顺序就是作者的表达顺序**，
    压平之后「第几张图讲了什么」这个结构证据会消失（与正文分段同理）。
    """
    image_chars = sum(result.chars for result in results.values())
    text_chars = int(header.get("body_chars") or 0)
    image_body = "\n".join("\n".join(result.lines) for result in results.values())
    relation = layer_relation(str(header.get("body_text") or ""), image_body)
    blocks = [
        f"# {header.get('title') or '(无标题)'}",
        f"# note_id：{note.note_id}",
        f"# 图片数：{note.total}（本地命中 {note.local}）"
        + (f" · 平台声明 {note.declared}" + ("，**不全**" if note.incomplete else "，已绑全")
           if note.declared is not None else " · 平台未声明张数"),
        f"# 来源：{note.source} · OCR 引擎：RapidOCR（本机 onnxruntime）",
        f"# 篇幅：{image_dominance(image_chars, text_chars)}",
        f"# 与正文层的关系：{relation}",
        "# 等级：图上文字 ②（原图在本地可复核）/ 段落与版式 ③-a（OCR 换行 ≠ 原作者分段）",
        "# ⚠️ OCR 有误差：置信 <0.80 或字数 <10 的图标了『低质量』，引用前必须看原图",
        "# ⚠️ 正文层（notes-text/<note_id>.txt）与图上文字可能不是同一版：两版都要留，不得互相覆盖",
        "# ⚠️ 末图可能本身就是半句：图上文字不等于作者全文，尾部残缺要对照原图确认再引用",
        "",
    ]
    for image in note.images:
        name = image.local.name if image.local else f"(未存图) {image.file_id or image.url[-40:]}"
        result = results.get(image.index)
        blocks.append(f"## 图 {image.index}  {name}")
        if image.width and image.height:
            blocks.append(f"# 尺寸 {image.width}x{image.height}")
        if result is None:
            blocks.append("(未 OCR：缺本地图片文件)")
        else:
            mark = " ⚠️ 低质量" if result.low_quality else ""
            blocks.append(f"# OCR 字数 {result.chars} · 平均置信 {result.mean_score}{mark}")
            blocks.append("\n".join(result.lines) or "(空)")
        blocks.append("")
    return "\n".join(blocks)


def body_is_thin(html: str, threshold: int = 40) -> bool:
    """正文层是不是「薄到可以判定内容在图片里」。用来在清单里标出必须走图片通道的篇目。"""
    match = DESC_RE.search(html)
    if not match:
        return True
    paragraphs = body_paragraphs(TAG_RE.sub(" ", match.group(1)))
    return sum(len(part) for part in paragraphs) < threshold


def body_chars(html: str) -> int:
    match = DESC_RE.search(html)
    if not match:
        return 0
    return sum(len(part) for part in body_paragraphs(TAG_RE.sub(" ", match.group(1))))


def body_text(html: str) -> str:
    """正文层的纯文本（段落用换行连）。用于和图上文字比重叠。"""
    match = DESC_RE.search(html)
    if not match:
        return ""
    return "\n".join(body_paragraphs(TAG_RE.sub(" ", match.group(1))))


def normalize(text: str) -> str:
    """只留中文与字母数字：OCR 会插换行、标点也会被识别成别的符号，比之前先把这些抹平。"""
    return NON_WORD_RE.sub("", text)


def overlap_ratio(body: str, image_text: str, size: int = 8) -> float:
    """正文层的 `size` 字滑窗里，有多大比例能在图上文字中找到。

    为什么需要这个：只看「字数倍数」会得出错误结论——实测五种情况并存：
    卡兹克 4581 是 44 倍但重叠只有 2.7%（**另写的一篇导语**），
    知新 3259 只有 2.1 倍却是 **95.6%**（**同一篇**，图上只是重新排版带了小标题）。
    **倍数说明篇幅差异，重叠才说明是不是同一篇文章。**
    """
    plain_body, plain_image = normalize(body), normalize(image_text)
    if len(plain_body) < size or len(plain_image) < size:
        return 0.0
    body_grams = {plain_body[i:i + size] for i in range(len(plain_body) - size + 1)}
    image_grams = {plain_image[i:i + size] for i in range(len(plain_image) - size + 1)}
    return len(body_grams & image_grams) / len(body_grams)


def layer_relation(body: str, image_text: str) -> str:
    """两份文本的关系。三种情形要分开对待，不能一律说「正文层被砍短了」。"""
    if not normalize(image_text):
        return "图上无可读文字（只有正文层）"
    if not normalize(body):
        return "正文层空 → 内容全在图里"
    ratio = overlap_ratio(body, image_text)
    if ratio < 0.10:
        return f"另写一篇（重叠 {ratio:.1%}）：两份都是原文，都要读"
    if ratio > 0.90:
        return f"同一篇（重叠 {ratio:.1%}）：图上只是重新排版，读正文层即可，标题去图上补"
    return f"部分重叠（{ratio:.1%}）：正文层可能是增补版，两份都要读"


def image_dominance(image_chars: int, text_chars: int, ratio: float = 1.5) -> str:
    """OCR 出的字 vs 正文层的字 → 篇幅谁大。

    ⚠️ 它只说篇幅，**不说内容是否同一篇**——那要用 layer_relation 算重叠。
    实测同一批数据里两者完全脱钩：卡兹克 44 倍但重叠 2.7%，知新 3259 只有 2.1 倍却重叠 95.6%。
    """
    if image_chars <= 0:
        return "图片无可读文字"
    if text_chars <= 0:
        return "正文层空 → 内容全在图里"
    if image_chars > text_chars * ratio:
        return f"图多于文（图 {image_chars} 字 / 正文 {text_chars} 字）"
    return f"正文为主（图 {image_chars} 字 / 正文 {text_chars} 字）"
