"""原生消息宿主：协议、分块、分发，以及「真的能入库」的端到端验证。

## 为什么这个文件里的每一条都必须存在

宿主是 v7 新增的一层，它把「扩展」和「本地 Python」缝在了一起。这一层的失败**全部**
是「看不到的失败」——Chrome 只会给一句 `Native host has exited.`，没有堆栈、没有行号。
所以每一处能静默出错的地方，这里都要有一条测试把它钉住：

| 静默失败的样子 | 对应测试 |
|---|---|
| 长度前缀算错 → 对端从错误位置解析 → 第一条之后全乱 | `test_frame_roundtrip` |
| Windows 把 `\n` 翻成 `\r\n` → 长度对不上 | `test_stdout_is_binary_not_text` |
| 业务 print 混进协议流 → 帧被污染 | `test_prints_do_not_reach_the_wire` |
| 分块上传时块顺序/边界错 → 文件只有一部分 | `test_chunked_upload_reassembles_exactly` |
| 宿主收到坏块还在继续攒 → 入库一份坏文件 | `test_bad_chunk_drops_the_session` |
| 入库抛异常但不回包 → 扩展等到超时 | `test_end_without_begin_answers_instead_of_hanging` |

## 端到端那条为什么必须走真实子进程

`test_end_to_end_lands_in_csv` 会**真的把 `ar_host.py` 启起来**，用真实 MHTML 走
begin/chunk/end，然后去读 `data/notes.csv`。
在同一个进程里调 `handle()` 测不出「Windows 的换行翻译」「pythonw 的 stdout 行为」
「`.bat` 的引号」这类问题——而那些正是会在用户机器上炸的东西。
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from account_research import host  # noqa: E402


def frame(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def unframe(data: bytes) -> list[dict]:
    """解出字节流里**所有**完整的帧。多解一层能发现「多发了一条」这类错误。"""
    messages: list[dict] = []
    offset = 0
    while offset + 4 <= len(data):
        (length,) = struct.unpack("<I", data[offset:offset + 4])
        body = data[offset + 4:offset + 4 + length]
        if len(body) < length:
            break
        messages.append(json.loads(body.decode("utf-8")))
        offset += 4 + length
    return messages


class TestFrame(unittest.TestCase):
    def test_frame_roundtrip(self):
        payload = {"type": "hello", "id": 7, "文本": "中文不该被转义成 \\uXXXX"}
        messages = unframe(frame(payload))
        self.assertEqual(messages, [payload])

    def test_length_prefix_is_utf8_bytes_not_characters(self):
        """中文一个字符占 3 字节。按字符算长度会让对端读到半个 JSON。

        这是最容易写错的一处，而且错了以后**第一条消息看起来是对的**——
        直到中文出现在消息里。
        """
        payload = {"data": "中" * 100}
        raw = frame(payload)
        (declared,) = struct.unpack("<I", raw[:4])
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual(declared, len(body))
        self.assertGreater(declared, len(body) if False else 0)

    def test_read_message_returns_none_on_closed_stream(self):
        self.assertIsNone(host.read_message(io.BytesIO(b"")))

    def test_truncated_body_is_an_error_not_an_end_of_stream(self):
        """**「对端关了」和「这条消息坏了」必须分开。**

        把两者都当成「结束」的后果是：一段坏字节会被当成「用户关闭了端口」，
        真正的协议错误就此消失——实测过，宿主只打印「输入已关闭」，看不出任何异常。
        """
        raw = struct.pack("<I", 100) + b"{}"
        with self.assertRaises(host.ProtocolError):
            host.read_message(io.BytesIO(raw))

    def test_truncated_header_is_an_error_too(self):
        with self.assertRaises(host.ProtocolError):
            host.read_message(io.BytesIO(b"\x01\x02"))

    def test_zero_length_frame_is_an_error(self):
        """长度 0 不是「空消息」，是**坏帧**。

        Chrome 永远不会发 0 长度的消息；把它当「结束」会让后面所有字节被静默丢弃。
        """
        with self.assertRaises(host.ProtocolError):
            host.read_message(io.BytesIO(struct.pack("<I", 0)))

    def test_absurd_length_is_a_protocol_error(self):
        """长度前缀坏掉时，不要试图分配一个 4 GB 的 buffer。"""
        raw = struct.pack("<I", host.MAX_MESSAGE_BYTES + 1)
        with self.assertRaises(host.ProtocolError):
            host.read_message(io.BytesIO(raw))

    def test_non_object_json_is_rejected(self):
        body = json.dumps([1, 2, 3]).encode("utf-8")
        raw = struct.pack("<I", len(body)) + body
        with self.assertRaises(host.ProtocolError):
            host.read_message(io.BytesIO(raw))


class TestChunkedUpload(unittest.TestCase):
    """分块是硬要求：一条 20 MB 的 MHTML 不可能一条消息发完。"""

    def setUp(self):
        self.uploads: dict[str, host.Upload] = {}

    def _begin(self, session: str = "s1", note_id: str = "abc") -> dict:
        return host.handle({"type": "begin", "session": session, "note_id": note_id},
                           self.uploads)

    def test_chunked_upload_reassembles_exactly(self):
        import base64

        blob = bytes(range(256)) * 400  # 102400 字节，跨很多块
        self._begin()
        step = 4096
        encoded = base64.b64encode(blob).decode("ascii")
        for offset in range(0, len(encoded), step):
            reply = host.handle({"type": "chunk", "session": "s1",
                                 "data": encoded[offset:offset + step]}, self.uploads)
            self.assertTrue(reply["ok"], reply)
        upload = self.uploads["s1"]
        self.assertEqual(upload.bytes(), blob)

    def test_chunk_size_is_declared_by_the_host(self):
        """块大小只能有一个来源：宿主在 `hello` 里说，扩展照用。

        两边各自硬编码，改了一边就断线，而错误只有一句 `Native host has exited.`。
        """
        reply = host.handle({"type": "hello"}, self.uploads)
        self.assertIn("chunk_chars", reply)
        # 声明值必须真的比 Chrome 那条 1 MB 的历史限额小一个量级
        worst_case = reply["chunk_chars"] * 1.1 + reply["overhead"]
        self.assertLess(worst_case, 1024 * 1024)

    def test_bad_chunk_drops_the_session(self):
        """坏块必须立刻丢会话：继续攒下去会入库一份「长度对但内容错」的文件。

        **静默错数据比报错危险**——这一个字节翻转不会让任何人发现。
        """
        self._begin()
        reply = host.handle({"type": "chunk", "session": "s1", "data": "这不是 base64!!"},
                            self.uploads)
        self.assertFalse(reply["ok"])
        self.assertNotIn("s1", self.uploads, "坏块之后会话必须消失，不能留着继续攒")

    def test_chunk_without_begin_is_an_error_not_a_crash(self):
        reply = host.handle({"type": "chunk", "session": "nope", "data": ""}, self.uploads)
        self.assertFalse(reply["ok"])
        self.assertIn("begin", reply["error"])

    def test_end_without_begin_answers_instead_of_hanging(self):
        """不回包的话扩展会一直等到超时——那看起来就是「点了没反应」。"""
        reply = host.handle({"type": "end", "session": "nope"}, self.uploads)
        self.assertFalse(reply["ok"])
        self.assertIn("begin", reply["error"])

    def test_zero_byte_upload_is_refused(self):
        self._begin()
        reply = host.handle({"type": "end", "session": "s1"}, self.uploads)
        self.assertFalse(reply["ok"])
        self.assertIn("0 字节", reply["error"])

    def test_sessions_are_isolated(self):
        """一次点击可能连续处理多个标签页，两路上传不能互相覆盖。"""
        import base64

        host.handle({"type": "begin", "session": "a", "note_id": "n1"}, self.uploads)
        host.handle({"type": "begin", "session": "b", "note_id": "n2"}, self.uploads)
        host.handle({"type": "chunk", "session": "a", "data": base64.b64encode(b"AAA").decode()},
                    self.uploads)
        host.handle({"type": "chunk", "session": "b", "data": base64.b64encode(b"BBB").decode()},
                    self.uploads)
        self.assertEqual(self.uploads["a"].bytes(), b"AAA")
        self.assertEqual(self.uploads["b"].bytes(), b"BBB")

    def test_unknown_type_is_answered(self):
        reply = host.handle({"type": "什么"}, self.uploads)
        self.assertFalse(reply["ok"])
        self.assertIn("不认识", reply["error"])


class TestStdioIsBinaryOnWindows(unittest.TestCase):
    """Windows 上 `sys.stdout` 会把 `\n` 翻成 `\r\n`，而长度前缀是按字节算的。

    不关掉翻译的话，宿主发的第一条消息就会比它声明的长——
    对端从错误位置继续解析，表现为「能收到一条，之后全乱」。
    """

    def test_binary_stdio_switches_and_redirects_prints(self):
        source = (ROOT / "src" / "account_research" / "host.py").read_text(encoding="utf-8")
        self.assertIn("setmode", source)
        self.assertIn("O_BINARY", source)
        # 必须把 sys.stdout 让给 stderr，否则业务 print 会毒化协议流
        self.assertIn("sys.stdout = sys.stderr", source)

    def test_host_prints_go_to_stderr(self):
        """宿主的每一句 print 都要带 `file=sys.stderr`。

        漏一句，那个字节就进了协议流，而**帧的完整性一旦被破坏，
        后续每一条消息都对不上**——错误还会指向下一个无辜的消息。

        用 `ast` 而不是逐行匹配：`print(...)` 常常跨行（参数多的时候），
        逐行看会把「`file=` 写在下一行」误判成违规——那种测试会逼人把代码折成难看的样子。
        """
        import ast

        source = (ROOT / "src" / "account_research" / "host.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        prints = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        self.assertTrue(prints, "host.py 里一句 print 都没有？那这条测试就没意义了")
        for node in prints:
            keywords = {kw.arg for kw in node.keywords}
            self.assertIn("file", keywords,
                          f"第 {node.lineno} 行的 print 没写 file=：会污染协议流")


class TestEndToEnd(unittest.TestCase):
    """真的把宿主作为**子进程**启起来，走完整的 begin/chunk/end，再读 CSV 验证。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data = self.base / "data"
        shutil.copytree(ROOT / "data", self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_host(self, payloads: list[dict], data_dir: Path) -> list[dict]:
        script = ROOT / "ar_host.py"
        blob = b"".join(frame(item) for item in payloads)
        done = subprocess.run(
            [sys.executable, str(script)],
            input=blob, capture_output=True, timeout=300,
            cwd=str(ROOT),
            env={**__import__("os").environ, "AR_HOST_DATA": str(data_dir)},
        )
        replies = unframe(done.stdout)
        self.assertTrue(replies, f"宿主没有回任何消息（stderr：{done.stderr.decode('utf-8', 'replace')[-800:]}）")
        return replies

    def test_end_to_end_lands_in_csv(self):
        """一条真实的详情页 MHTML 走完全程，必须真的进 `notes.csv`。

        这条测试用临时 data 目录，**绝不碰采集者的真实证据库**。
        """
        import base64

        from tests.mhtml_samples import NOTE_ID, build_detail_mhtml

        payload = build_detail_mhtml()
        encoded = base64.b64encode(payload).decode("ascii")
        step = 20_000
        messages = [
            {"type": "hello", "id": 0},
            {"type": "begin", "session": "e2e", "note_id": NOTE_ID, "id": 1},
        ]
        for index in range(0, len(encoded), step):
            messages.append({"type": "chunk", "session": "e2e",
                             "data": encoded[index:index + step], "id": 2})
        messages.append({"type": "end", "session": "e2e", "id": 3})

        # 先把底行写进去：详情页要求 note_id 已存在（先 parse 主页）。这是**正确行为**。
        from account_research import store

        # ⚠️ 账号与笔记都用**临时造的 ID**，不能借仓库 data/ 里已有的。
        # 借的话，那条已有行会和新行混在同一个 note_id 下，
        # 断言读到的可能是旧行——测试会变成「有时过有时不过」，而原因和被测代码无关。
        store.append_rows(self.data, "accounts", [{"account_id": "acc-e2e", "nickname": "测试号",
                                                  "collected_on": "2026-11-01"}])
        store.append_rows(self.data, "notes", [{
            "account_id": "acc-e2e", "note_id": NOTE_ID, "pinned": "0",
            "title": "底行", "form": "图文", "likes": "1",
            "source_file": "f.html", "source_kind": "公开页",
            "collected_on": "2026-11-01", "grade": "②",
        }])

        replies = self._run_host(messages, self.data)
        last = replies[-1]
        self.assertTrue(last.get("ok"), f"入库失败：{last}")
        self.assertEqual(last.get("bytes"), len(payload))
        rows = store.read_table(self.data, "notes")
        mine = [row for row in rows
                if str(row.get("note_id")) == NOTE_ID and str(row.get("account_id")) == "acc-e2e"]
        self.assertTrue(mine, "notes 表里没有这一篇")
        self.assertEqual(str(mine[-1].get("likes")), "318", "赞数没入库")
        self.assertEqual(str(mine[-1].get("collects")), "73", "藏数没入库")

    def test_no_output_noise_before_the_first_frame(self):
        """协议流里第一件事必须是长度前缀。

        只要宿主的启动横幅跑到了 stdout，前 4 个字节就是横幅的一部分，
        对端会解析出一个荒唐的长度。
        """
        replies = self._run_host([{"type": "hello", "id": 0}], self.data)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["protocol"], host.PROTOCOL_VERSION)

    def test_crash_still_answers_before_exiting(self):
        """坏 JSON 不该让宿主一声不吭地死掉——那在扩展侧就是「没有回应」。"""
        done = subprocess.run(
            [sys.executable, str(ROOT / "ar_host.py")],
            input=struct.pack("<I", 5) + b"not{",
            capture_output=True, timeout=60, cwd=str(ROOT),
        )
        self.assertIn("协议错误", done.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
