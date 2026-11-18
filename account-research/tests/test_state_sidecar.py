"""state 旁车：`pageCapture` 剥掉 `<script>` 之后，state 靠这条路回来。

## 它解决的问题

扩展用 `chrome.pageCapture.saveAsMHTML` 存的不是「Ctrl+S 那份 HTML」，而是**渲染后的 DOM
序列化**——`<script>` 整段被剥掉，`__INITIAL_STATE__` 随之消失（
[采集端-v6-实测复盘](../../docs/采集端-v6-实测复盘-20261113.md) §5 实测记录）。

后果是分叉的，而且**只有一半会报错**：

| 页面 | 解析要什么 | 没有 state 的后果 |
|---|---|---|
| 详情页 | DOM 里的标题/正文/互动栏 | ✅ 照常解析（分享数/精确发布日缺） |
| 主页 | **全部**来自 `userPageData` + `noteCards` | ❌ 抛「找不到 __INITIAL_STATE__」 |

于是「用插件采主页」这件事物理上做不到，只能回去 Ctrl+S 手动存。
而主页是**账号的入口**——采不了主页就没有账号，没有账号详情页也会被归属校验拒收。

## 为什么是旁车，而不是把 state 塞回 HTML

塞回 HTML 等于让「一份存档」有两种形状，而 MHTML 拆包那条路已经踩平了。
旁车是**可选增强**：没有它，详情页的行为与今天完全一致（一个字段都不变）。
这条性质很重要——它让这个改动**不可能**弄坏已有的 13 篇正文、12 篇图上文字。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import initialstate  # noqa: E402
from account_research.initialstate import (  # noqa: E402
    STATE_ABSENT,
    STATE_FROM_HTML,
    STATE_FROM_SIDECAR,
    ArchiveError,
    open_archive,
    state_sidecar_path,
    read_state_sidecar,
    write_state_sidecar,
)

ROOT = Path(__file__).resolve().parent
PROFILE_STATE = {
    "user": {
        "userInfo": {"userId": "viewer1", "nickname": "采集者"},
        "userPageData": {
            "basicInfo": {"nickname": "旁车测试号", "redId": "sidecar01",
                          "desc": "一句话简介", "ipLocation": "上海"},
            "interactions": [{"type": "fans", "count": "1,000"},
                             {"type": "follows", "count": "20"},
                             {"type": "interaction", "count": "5000"}],
        },
        "noteQueries": [{"userId": "target-sidecar"}],
        "notes": [[
            {"noteCard": {"noteId": "s1", "displayTitle": "第一篇",
                          "interactInfo": {"likedCount": "100"}, "time": 1761000000000,
                          "type": "normal", "user": {"userId": "target-sidecar"}}},
            {"noteCard": {"noteId": "s2", "displayTitle": "第二篇",
                          "interactInfo": {"likedCount": "50", "sticky": True},
                          "time": 1761100000000, "type": "video",
                          "user": {"userId": "target-sidecar"}}},
        ]],
    },
}

# 一份「`pageCapture` 存出来的」主页 HTML：DOM 齐全，但**没有 `<script>`**。
PROFILE_HTML_NO_SCRIPT = """<!DOCTYPE html>
<html><head><title>旁车测试号 - 小红书</title></head>
<body>
<section class="note-item" data-note-id="s1"><a class="title"><span>第一篇</span></a>
  <span class="like-wrapper"><span class="count">100</span></span></section>
<section class="note-item" data-note-id="s2"><a class="title"><span>第二篇</span></a>
  <span class="like-wrapper"><span class="count">50</span></span></section>
</body></html>"""


class SidecarFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str = "ar-page-test.html", html: str = PROFILE_HTML_NO_SCRIPT) -> Path:
        path = self.dir / name
        path.write_text(html, encoding="utf-8")
        return path


class TestSidecarPath(SidecarFixture):
    def test_maps_html_to_state_json(self):
        self.assertEqual(state_sidecar_path(self.dir / "x.html").name, "x.state.json")

    def test_keeps_the_full_stem(self):
        """`ar-page-6aed…-141455.html` → `ar-page-6aed…-141455.state.json`。

        用 `Path.with_suffix(".state.json")` 就会踩这里：它按**单段后缀**处理，
        多段后缀在 3.14 起直接被拒；而在老版本上它会把 `.json` 当成唯一后缀。
        """
        path = state_sidecar_path(self.dir / "ar-page-abc-141455.html")
        self.assertEqual(path.name, "ar-page-abc-141455.state.json")
        # 反向也是：从旁车推出存档名，得到的必须是原来那个 HTML。
        self.assertEqual(path.name.replace(".state.json", ".html"), "ar-page-abc-141455.html")

    def test_mhtml_and_html_of_the_same_stem_share_a_sidecar(self):
        """MHTML 拆包后只剩 `<stem>.html`，所以旁车按 **stem** 对齐才接得上。"""
        self.assertEqual(
            state_sidecar_path(self.dir / "x.mhtml").name,
            state_sidecar_path(self.dir / "x.html").name,
        )


class TestReadWriteSidecar(SidecarFixture):
    def test_write_then_read_roundtrips(self):
        path = self.write()
        write_state_sidecar(path, PROFILE_STATE)
        self.assertEqual(read_state_sidecar(path), PROFILE_STATE)

    def test_absent_sidecar_is_none_not_an_error(self):
        """缺席是正常状态：Ctrl+S 的 state 本来就在 HTML 里，详情页也不需要它。

        把「没旁车」当错误，会让每一条老通道都开始报错。
        """
        self.assertIsNone(read_state_sidecar(self.write()))

    def test_write_does_not_overwrite(self):
        """和所有证据一样：只增不改。

        覆盖的后果很具体：同一天采两次同一个号，第二次的 state 会盖掉第一次，
        而两者的笔记列表可能不同——被盖掉的那份就永久消失。
        """
        path = self.write()
        write_state_sidecar(path, PROFILE_STATE)
        write_state_sidecar(path, {"user": {"userPageData": {"basicInfo": {"nickname": "后写的"}}}})
        self.assertEqual(read_state_sidecar(path), PROFILE_STATE)

    def test_truncated_sidecar_says_what_to_do(self):
        path = self.write()
        state_sidecar_path(path).write_text('{"user": {', encoding="utf-8")
        with self.assertRaises(ArchiveError) as ctx:
            read_state_sidecar(path)
        self.assertIn("重采", str(ctx.exception))

    def test_non_object_sidecar_is_rejected(self):
        path = self.write()
        state_sidecar_path(path).write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ArchiveError):
            read_state_sidecar(path)


class TestOpenArchiveUsesSidecar(SidecarFixture):
    def test_profile_without_script_fails_without_a_sidecar(self):
        """先钉住「不加旁车会怎样」——这是本模块存在的理由。"""
        with self.assertRaises(ArchiveError) as ctx:
            open_archive(self.write())
        self.assertIn("__INITIAL_STATE__", str(ctx.exception))

    def test_profile_with_a_sidecar_parses(self):
        path = self.write()
        write_state_sidecar(path, PROFILE_STATE)
        archive = open_archive(path)
        self.assertEqual(archive.kind, "profile")
        self.assertTrue(archive.usable)
        self.assertEqual(len(archive.note_items), 2)
        self.assertEqual(archive.target_user_ids, ("target-sidecar",))

    def test_state_source_is_recorded(self):
        """**必须能回答「这个粉丝数是从哪来的」。**

        三个值分开：`html`（Ctrl+S 自带）/ `sidecar`（扩展旁车）/ `none`（只有 DOM）。
        只记「有没有 state」是不够的——`html` 与 `sidecar` 的可信度不是一回事：
        前者是页面自己吐的字面量，后者是扩展按子树挑出来又 JSON 往返过一遍的。
        """
        path = self.write()
        write_state_sidecar(path, PROFILE_STATE)
        self.assertEqual(open_archive(path).state_source, STATE_FROM_SIDECAR)

    def test_html_state_wins_over_a_sidecar(self):
        """HTML 里自带 state 时**不去读旁车**——那是这份存档的原件。

        反过来的话，一份陈旧旁车会盖掉页面里更新的数据，而且不报错。
        """
        html = PROFILE_HTML_NO_SCRIPT.replace(
            "</body>",
            "<script>window.__INITIAL_STATE__="
            + json.dumps(PROFILE_STATE, ensure_ascii=False)
            + ";</script></body>",
        )
        path = self.write("html-state.html", html)
        write_state_sidecar(path, {"user": {"userPageData": {"basicInfo": {"nickname": "旁车旧数据"}}}})
        archive = open_archive(path)
        self.assertEqual(archive.state_source, STATE_FROM_HTML)
        self.assertEqual(archive.state, PROFILE_STATE)

    def test_detail_page_still_works_without_any_state(self):
        """**这条是最重要的回归**：详情页的行为一个字段都不许变。

        摘要里说过：旁车是「可选增强」而不是「新的必需品」。
        如果哪天 open_archive 开始要求每份存档都有 state，这份测试会红——
        而那时受影响的正是已经采好的 13 篇正文。
        """
        fixture = ROOT / "fixtures" / "minimal_detail.html"
        archive = open_archive(fixture)
        self.assertEqual(archive.kind, "note_detail")
        self.assertEqual(archive.state_source, STATE_ABSENT)

    def test_detail_page_reads_state_from_a_sidecar_too(self):
        """详情页也能用旁车：`shareCount` / 精确发布日只在 state 里。

        扩展通道以前这两样永远是 ⬜。旁车让它们第一次有值。
        """
        html = (ROOT / "fixtures" / "minimal_detail.html").read_text(encoding="utf-8")
        path = self.dir / "detail.html"
        path.write_text(html, encoding="utf-8")
        note_id = (html.split("og:url")[1].split("/explore/")[1].split('"')[0])
        write_state_sidecar(path, {"note": {"noteDetailMap": {note_id: {"note": {
            "noteId": note_id, "shareCount": "77",
        }}}}})
        archive = open_archive(path)
        self.assertEqual(archive.state_source, STATE_FROM_SIDECAR)
        self.assertEqual(archive.state["note"]["noteDetailMap"][note_id]["note"]["shareCount"], "77")

    def test_error_names_the_sidecar_it_looked_for(self):
        """报错必须指出**它找过哪个文件**。

        「找不到 __INITIAL_STATE__」对扩展通道是常态，人需要知道旁车本该叫什么名字，
        否则下一步只能靠猜。
        """
        with self.assertRaises(ArchiveError) as ctx:
            open_archive(self.write("ar-page-x.html"))
        message = str(ctx.exception)
        self.assertIn("ar-page-x.state.json", message)
        self.assertIn("Ctrl+S", message)


class TestSidecarIsMovedWithTheArchive(SidecarFixture):
    def test_ingest_moves_the_sidecar_into_processed(self):
        """`ar ingest` 归档时必须把旁车一起搬走。

        留在 inbox 里 = 把主页的数据源丢了：归档后的 HTML 不含 `<script>`，
        而 `processed/` 下的那份才是长期证据。
        """
        from account_research import cli

        data = self.dir / "data"
        data.mkdir()
        inbox = self.dir / "inbox"
        inbox.mkdir()
        path = inbox / "ar-page-target-sidecar.html"
        path.write_text(PROFILE_HTML_NO_SCRIPT, encoding="utf-8")
        write_state_sidecar(path, PROFILE_STATE)

        code = cli.main([
            "ingest", str(path), "--data", str(data), "--on", "2026-11-18",
            "--text-dir", str(self.dir / "notes-text"),
        ])
        self.assertEqual(code, 0)

        processed = list((inbox / "processed" / "2026-11-18").glob("*.html"))
        self.assertEqual(len(processed), 1, "HTML 没归档")
        moved = state_sidecar_path(processed[0])
        self.assertTrue(moved.is_file(), f"旁车没跟着搬（{moved.name}）")
        # 搬过去之后仍然能解析——这才是「搬对了」的证据，而不只是「文件在」。
        self.assertEqual(open_archive(processed[0]).kind, "profile")

    def test_sidecar_does_not_become_an_ingest_candidate(self):
        """`.json` 不在 `INGEST_SUFFIXES` 里，所以它不会被当成独立的存档再收一次。"""
        from account_research.cli import INGEST_SUFFIXES

        self.assertNotIn(".json", INGEST_SUFFIXES)


if __name__ == "__main__":
    unittest.main()
