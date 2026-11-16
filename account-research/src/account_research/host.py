"""原生消息宿主：Chrome 扩展 ⇄ 本地 Python 的**唯一**通道。

## 它解决的问题

在这之前，「点扩展图标」只是把 MHTML 存到下载目录，然后**必须另开一个终端**
跑 `ar watch` 才算入库。用户的原话：

> 我们应该是一整个插件包含采集能力，现在听起来是多个进程分散了

分散的不是进程数，而是**人必须记住的两件事**。装好宿主之后，
点一下图标 = 存档 + 拆包 + 解析 + 写表 + OCR + 归档，一次做完，终端的窗口可以永远关着。

## 边界没有动，而且更窄了

原生消息听起来像「扩展能调本地程序」，很容易被误读成「扩展能随便操作本机」。
实际约束是：

1. **宿主只会做一件事**：把收到的字节交给 `ar ingest` 的同一条代码路径。
   没有「通用执行」入口，没有 shell，没有文件浏览。
2. **请求仍然由人在浏览器里产生**。宿主收到的是**已经渲染好的页面的序列化结果**
   （`pageCapture` 的输出），不是任何「帮我抓一下这个 URL」的指令。
   宿主里没有任何网络调用——它连 `urllib` 都不 import。
3. **消息里带 note_id 但不带 URL**：宿主不需要知道页面在哪，也就不可能自己去取。

## 协议

Chrome 规定：每条消息 = 4 字节小端长度 + UTF-8 JSON。**双向都是这个格式**。

⚠️ Windows 上的两个必踩的坑（不处理就必然出问题）：

- **stdout 必须切成二进制模式**。`sys.stdout` 在 Windows 默认把 `\n` 翻译成 `\r\n`，
  而长度前缀是按字节算的——多出来的 `\r` 会让对端从错误的位置开始解析，
  表现为「能收到一条消息，之后全乱」。用 `msvcrt.setmode(O_BINARY)` 关掉翻译。
- **任何一行业务代码的 `print` 都会毒化协议流**。宿主里 `cmd_ingest` 会打印一大段
  人看的进度；这些东西必须进 stderr。做法是开局就把 `sys.stdout` 换成 `sys.stderr`，
  只保留一份指向真实二进制流的私有引用。

## 为什么是分块传输

一条 16 图笔记的 MHTML 约 20 MB，base64 后 27 MB。Chrome 对「发给宿主」的单条消息
虽然文档上写的是 4 GB，但**各处实现与版本差异很大，历史上只有 1 MB**。
与其赌一个自己控制不了的限额，不如自己分块。

**块大小由宿主在 `hello` 里声明，扩展照着用。** 这一条不是多余的间接：
如果两边各自硬编码一个数字，改了一边就会在生产上断开连接，
而错误信息只有一句 `Native host has exited.`——查起来毫无线索。
声明 + 照用，让「限额」只有一个来源。
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

# 宿主名与扩展 ID 是**跨端契约**：注册表项名由它推导，清单的 allowed_origins 也由它推导。
# 写在宿主模块里而不是注册模块里，是为了让「协议」和「身份」待在同一个地方，
# 免得两处各写一份、改了一处另一处没跟上（Chrome 只会说「origin 不允许」）。
HOST_NAME = "com.account_research.capture"
EXTENSION_ID = "onabfkpgiapeffmjogkdnhllamejdehm"
# 协议版本。扩展与宿主各自声明，不一致时扩展会明说「版本不匹配」而不是静默乱跑。
PROTOCOL_VERSION = 1

# 单条消息的硬上限。分块后实际远小于它；这个值只用来挡掉明显坏掉的长度前缀。
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# 每块 base64 的字符数。
#
# **按字符算而不是按字节算**，因为扩展切的是 base64 字符串。
# 96 KB 字符 → 约 72 KB 原始字节 → JSON 包起来约 97 KB。
# 这个值要**小于 Chrome 那条 1 MB 的历史限额一个数量级**：
# Chrome 的限额不归我们控制，也不能靠「按理说应该够」来赌，
# 赌输的表现是宿主进程直接消失，报错只有一句 `Native host has exited.`
CHUNK_CHARS = 96 * 1024
# 一条 chunk 消息的 JSON 开销（引号、type、session、id）留 2 KB 余量。
JSON_OVERHEAD_BYTES = 2048
# 入库写入哪个数据目录。默认走 `cli.DEFAULT_DATA`（项目内的 data/）。
#
# 为什么允许环境变量覆盖：测试必须能把结果写进**临时目录**，
# 绝不能碰到采集者的真实证据库。一条测试如果会污染 data/，它就不该存在于这个仓库。
DATA_DIR_ENV = "AR_HOST_DATA"


class ProtocolError(RuntimeError):
    """消息格式不对。宿主一旦读到它就退出——继续读只会读到垃圾。"""


# ---- 帧读写 ----


def _binary_stdio() -> tuple[BinaryIO, BinaryIO]:
    """把 stdin/stdout 切到二进制，并把 `sys.stdout` 让给 stderr。

    返回值是**真实**的输入输出流；之后任何 `print` 都到 stderr，不会污染协议流。
    """
    if os.name == "nt":  # pragma: no cover - 只在 Windows 生效
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    # 关掉文本包装：谁再 print 谁就写到 stderr 去。
    sys.stdout = sys.stderr
    return reader, writer


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """读一条消息。流结束时返回 None（Chrome 关闭端口就是这样表现的）。

    ⚠️ 「流结束了」和「这条消息坏了」必须分开：
    `read(4)` 拿到空 = 对端正常关闭 → 返回 None，安静退出；
    `read(4)` 拿到 1–3 字节 = 帧被截断 → **抛错**。
    把后者也当成「结束」的话，坏掉的帧会被当成「用户关闭了端口」，
    真正的协议错误就此消失（实测：写了一段坏字节，宿主只打印「输入已关闭」）。
    """
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        raise ProtocolError(f"长度前缀只有 {len(header)} 字节，帧被截断了")
    (length,) = struct.unpack("<I", header)
    if length == 0:
        raise ProtocolError("长度前缀为 0：对端发了一个空帧")
    if length > MAX_MESSAGE_BYTES:
        raise ProtocolError(f"消息长度 {length} 超过上限 {MAX_MESSAGE_BYTES}")
    body = stream.read(length)
    if len(body) < length:
        raise ProtocolError(f"声明 {length} 字节，只读到 {len(body)} 字节，帧被截断了")
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"消息不是合法 UTF-8 JSON：{exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("消息必须是 JSON 对象")
    return message


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """写一条消息并立刻 flush——不 flush 的话对端会一直等，看起来像卡死。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(body)))
    stream.write(body)
    stream.flush()


# ---- 业务：把收到的存档交给 ingest ----


class Upload:
    """一次分块上传的累积状态。

    只在内存里拼字节，**不落临时文件**直到收完——半途断开时不该在磁盘上留下垃圾。
    """

    def __init__(self, note_id: str = "", collected_on: str = "") -> None:
        self.note_id = note_id
        self.collected_on = collected_on
        self.parts: list[bytes] = []
        self.chunks = 0

    def add(self, data: str) -> None:
        try:
            self.parts.append(base64.b64decode(data, validate=True))
        except (ValueError, TypeError) as exc:
            raise ProtocolError(f"第 {self.chunks + 1} 块不是合法 base64：{exc}") from exc
        self.chunks += 1

    @property
    def size(self) -> int:
        return sum(len(part) for part in self.parts)

    def bytes(self) -> bytes:
        return b"".join(self.parts)


def ingest_payload(
    payload: bytes,
    note_id: str = "",
    collected_on: str = "",
    *,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """把一份 MHTML 字节送进 `ar ingest` 的同一条代码路径，返回结果摘要。

    **这里刻意复用 `cli.main`，不另写一套解析。** 两条路径迟早会漂开：
    一条会记得校验、另一条会忘——而忘了校验的那条正是「静默错数据」的来路
    （实测教训 E53/E57：多个数据源并存而没有统一入口时，坏的那份会被留下）。

    `cli.main` 会打印一大段面向人的进度。那些文字**不能进协议流**，
    所以在这里捕获，作为 `log` 字段随响应返回——用户在扩展的提示里能看到它。
    """
    from . import cli

    if data_dir is None:
        data_dir = cli.DEFAULT_DATA
    with tempfile.TemporaryDirectory(prefix="ar-host-") as tmp:
        name = f"ar-page-{note_id or 'unknown'}.mhtml"
        source = Path(tmp) / name
        source.write_bytes(payload)
        argv = [
            "ingest", str(source),
            "--data", str(data_dir),
            "--on", collected_on or cli.today(),
            # 上传通道不该顺手往采集者的证据库里建号：作者不在库里就该拒收。
            # 需要建号时人会在命令行显式加 `--new-authors`。
        ]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                code = cli.main(argv)
            except SystemExit as exc:  # argparse 在参数不对时走这里
                code = int(exc.code or 1)
        log = buffer.getvalue()
    return {
        "ok": code == 0,
        "exit_code": code,
        "note_id": note_id,
        "bytes": len(payload),
        "log": log[-4000:],  # 只回传尾部：完整日志留在 stderr，避免消息过大
    }


# ---- 分发 ----


def handle(message: dict[str, Any], uploads: dict[str, Upload],
           data_dir: str | Path | None = None) -> dict[str, Any]:
    """处理一条消息，返回要回给扩展的对象。

    `uploads` 按 `session` 分桶：扩展可能同时开多个上传（一次点击可能连续处理
    多个标签页），用会话号隔离，互不覆盖。
    """
    kind = str(message.get("type") or "")
    session = str(message.get("session") or "default")
    request_id = message.get("id")

    def reply(**extra: Any) -> dict[str, Any]:
        return {"id": request_id, "session": session, **extra}

    if kind == "hello":
        return reply(ok=True, protocol=PROTOCOL_VERSION,
                     chunk_chars=CHUNK_CHARS, chunk_bytes=CHUNK_CHARS // 4 * 3,
                     overhead=JSON_OVERHEAD_BYTES, host=HOST_NAME)

    if kind == "begin":
        uploads[session] = Upload(
            note_id=str(message.get("note_id") or ""),
            collected_on=str(message.get("collected_on") or ""),
        )
        return reply(ok=True, chunks=0)

    if kind == "chunk":
        upload = uploads.get(session)
        if upload is None:
            return reply(ok=False, error="还没 begin 就发 chunk")
        try:
            upload.add(str(message.get("data") or ""))
        except ProtocolError as exc:
            uploads.pop(session, None)
            return reply(ok=False, error=str(exc))
        return reply(ok=True, chunks=upload.chunks, bytes=upload.size)

    if kind == "end":
        upload = uploads.pop(session, None)
        if upload is None:
            return reply(ok=False, error="还没 begin 就发 end")
        if not upload.size:
            return reply(ok=False, error="这份存档是 0 字节，页面可能还没渲染完")
        try:
            result = ingest_payload(upload.bytes(), upload.note_id,
                                    upload.collected_on, data_dir=data_dir)
        except Exception as exc:  # 任何失败都要回一句人看得懂的话，不能静默
            return reply(ok=False, error=f"入库失败：{type(exc).__name__}: {exc}")
        return reply(**result)

    if kind == "status":
        # 扩展的 popup 用它显示「库里现在什么样」。**只读**，不改任何表。
        return reply(**status_payload(data_dir))

    if kind == "abort":
        uploads.pop(session, None)
        return reply(ok=True)

    return reply(ok=False, error=f"不认识的消息类型：{kind!r}")


def status_payload(data_dir: str | Path | None = None) -> dict[str, Any]:
    """库的只读摘要，给扩展的 popup 用。

    **只做统计，不做判断**：popup 里显示「今天已采 N 篇」是陈述事实；
    显示「你今天采得不够」就是评价，那不属于工具该说的话（PRD N5：不评分不排名）。
    """
    from . import cli, store

    root = Path(data_dir) if data_dir else cli.DEFAULT_DATA
    note_rows = store.read_table(root, "notes")
    accounts = store.read_table(root, "accounts")
    today = cli.today()
    # 同一个 note_id 可能有多行（多渠道观测），篇数按 note_id 去重。
    note_ids = {str(row.get("note_id")) for row in note_rows}
    today_ids = {
        str(row.get("note_id")) for row in note_rows
        if str(row.get("collected_on")) == today
    }
    text_dir = cli.REPO_ROOT / "account" / cli.TEXT_DIR
    with_text = sum(1 for note_id in note_ids if (text_dir / f"{note_id}.txt").is_file())
    return {
        "ok": True,
        "data_dir": str(root),
        "today": today,
        "accounts": len(accounts),
        "notes": len(note_ids),
        "collected_today": len(today_ids),
        "with_body": with_text,
        # 主页额度与详情额度是两回事，分开报——它们的「一次动作」定义不同。
        "quota_accounts": [len({str(r.get("account_id")) for r in accounts
                                if str(r.get("collected_on")) == today
                                and str(r.get("source_kind")) == "公开页"}), 3],
        "quota_notes": [len(today_ids), 30],
    }


def main(argv: list[str] | None = None) -> int:
    """宿主入口：读一条、处理、写一条，直到 stdin 关闭。"""
    reader, writer = _binary_stdio()
    uploads: dict[str, Upload] = {}
    # 数据目录在这里解析一次：环境变量优先（测试/多库场景），否则交给 `cli.DEFAULT_DATA`。
    data_dir: str | None = os.environ.get(DATA_DIR_ENV) or None
    print(f"[AR宿主] 启动，协议 v{PROTOCOL_VERSION}，数据目录 {data_dir or '（默认 data/）'}",
          file=sys.stderr)
    while True:
        try:
            message = read_message(reader)
        except ProtocolError as exc:
            # **协议错误必须回一句再退出**，否则扩展侧只会看到「端口断开了」，
            # 而真正的原因（帧被污染 / 长度前缀坏掉）会彻底丢失。
            try:
                write_message(writer, {"ok": False, "error": f"协议错误：{exc}"})
            except OSError:
                pass
            print(f"[AR宿主] 协议错误，退出：{exc}", file=sys.stderr)
            return 1
        if message is None:
            print("[AR宿主] 输入已关闭，退出", file=sys.stderr)
            return 0
        response = handle(message, uploads, data_dir=data_dir)
        write_message(writer, response)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
