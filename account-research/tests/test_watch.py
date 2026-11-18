"""watch：守着采件箱自动入库。

它监视的是**本地文件夹**，不是网页。测试要钉住这几件事：
① 文件落地即入库并归档；② 已处理过的不重复入库；③ 坏的不能让它死掉；
④ 下载目录里别人的文件一个都不许动；⑤ 日期在入库那一刻才取；
⑥ **详情页要能走完整条路**（含图上文字那一步）。

⑥ 是 2026-11-13 补的，因为它的缺席让一个崩溃藏了一整天：
`cmd_ingest` 里写的是 `if detail_ready and not args.no_ocr`，而 `cmd_watch`
自己拼的 Namespace 没有 `no_ocr` 字段。**短路求值让这个 bug 只在有详情页时才触发**，
原来的测试只丢主页存档进去，所以全绿——而真实用法里丢的正是详情页。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AR = ROOT / "ar.py"

sys.path.insert(0, str(ROOT / "src"))

from account_research import cli, store  # noqa: E402


class TestWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data = base / "data"
        self.inbox = base / "inbox"
        self.inbox.mkdir()
        self.text_dir = base / "notes-text"   # 正文归档只写临时目录，不碰真实证据
        shutil.copytree(ROOT / "data", self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _spawn(self, *extra: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-u", str(AR), "watch",
             "--path", str(self.inbox), "--interval", "0.5",
             "--on", "2026-10-28", "--data", str(self.data), "--ledger", "",
             "--text-dir", str(self.text_dir), *extra],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )

    def test_dropped_file_is_ingested_and_archived(self):
        before = len((self.data / "notes.csv").read_text(encoding="utf-8").splitlines())
        proc = self._spawn()
        try:
            time.sleep(1.2)
            shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "一篇.html")
            deadline = time.time() + 15
            archived = False
            while time.time() < deadline:
                if (self.inbox / "processed").exists() and any((self.inbox / "processed").rglob("*.html")):
                    archived = True
                    break
                time.sleep(0.4)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.assertTrue(archived, "文件没有被自动入库归档")
        after = len((self.data / "notes.csv").read_text(encoding="utf-8").splitlines())
        self.assertGreater(after, before, "notes 表没有新增行")

    def test_bad_file_does_not_kill_the_watcher(self):
        proc = self._spawn()
        try:
            time.sleep(1.2)
            (self.inbox / "垃圾.html").write_text("<html>none</html>", encoding="utf-8")
            time.sleep(1.5)
            self.assertIsNone(proc.poll(), "监视线程被坏文件搞死了")
            self.assertTrue((self.inbox / "垃圾.html").exists(), "坏文件应留在原地待排查")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_unrelated_files_are_left_alone(self):
        """下载目录里必然有别的东西（安装包、图片）。**不碰、不报错、不挪走**。

        这条看着像废话，但「把 Chrome 下载目录直接设成采件箱」是推荐用法，
        那时这个目录里全是用户的正常下载——误删一个文件比漏采一篇严重得多。
        """
        noise = self.inbox / "别动我.zip"
        noise.write_bytes(b"PK\x03\x04")
        proc = self._spawn("--once")
        try:
            proc.wait(timeout=20)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertTrue(noise.exists(), "非采集文件被动了")
        self.assertEqual(noise.read_bytes(), b"PK\x03\x04", "非采集文件内容被改了")

    def test_foreign_html_in_downloads_is_not_swallowed(self):
        """别人的 `.html` 也是 `.html`——只按后缀过滤，会把用户的正常下载误收。

        把 Chrome 下载目录设成采件箱是推荐用法，那时目录里全是他自己下载的东西。
        `--pattern ar-page-*` 就是给这个场景的白名单。
        """
        foreign = self.inbox / "同事发来的报告.html"
        foreign.write_text("<html><body>别人的东西</body></html>", encoding="utf-8")
        time.sleep(1.2)
        proc = self._spawn("--once", "--pattern", "ar-page-*")
        try:
            proc.wait(timeout=20)
        finally:
            if proc.poll() is None:
                proc.kill()
        self.assertTrue(foreign.exists(), "别人的 HTML 被收走了")

    def test_prefix_match_is_case_insensitive(self):
        self.assertTrue(cli._match_pattern("AR-PAGE-abc.mhtml", ["ar-page-*"]))
        self.assertFalse(cli._match_pattern("别的东西.mhtml", ["ar-page-*"]))

    def test_multiple_patterns_are_an_or(self):
        """一次采集里大概率同时有扩展存的 `.mhtml` 和手动 Ctrl+S 的 `… - 小红书.html`。

        只给一个白名单必然漏掉一类，而漏采**不会报错**——只会少几篇。
        """
        patterns = cli._split_patterns(["ar-page-*,* - 小红书.html"])
        self.assertEqual(patterns, ["ar-page-*", "* - 小红书.html"])
        self.assertTrue(cli._match_pattern("ar-page-abc.mhtml", patterns))
        self.assertTrue(cli._match_pattern("某篇 - 小红书.html", patterns))
        self.assertFalse(cli._match_pattern("同事发的报告.html", patterns))

    def test_repeated_flag_is_accepted(self):
        args = cli.build_parser().parse_args(
            ["watch", "--pattern", "a-*", "--pattern", "b-*"])
        self.assertEqual(cli._split_patterns(args.pattern), ["a-*", "b-*"])


class TestWatchOnce(unittest.TestCase):
    """`--once`：扫一遍就退出。有它才能把自动入库放进定时任务，也才能确定性测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data = base / "data"
        self.inbox = base / "inbox"
        self.inbox.mkdir()
        self.text_dir = base / "notes-text"
        shutil.copytree(ROOT / "data", self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_once(self, *extra: str) -> str:
        done = subprocess.run(
            [sys.executable, "-u", str(AR), "watch",
             "--path", str(self.inbox), "--interval", "0.1", "--once",
             "--data", str(self.data), "--ledger", "", "--text-dir", str(self.text_dir),
             *extra],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        return done.stdout + done.stderr

    def test_exits_by_itself_after_one_pass(self):
        shutil.copy(FIXTURES / "minimal_profile.html", self.inbox / "一篇.html")
        time.sleep(1.2)  # 让 mtime 老于 settle，模拟「下载已完成」
        output = self._run_once("--on", "2026-10-28")
        self.assertIn("本次自动入库 1 个文件", output)
        self.assertTrue(any((self.inbox / "processed").rglob("*.html")), "没有归档")

    def test_skips_a_file_that_is_still_being_written(self):
        """正在写的文件不能读——半份 HTML 往往还能被解析成「合法但残缺」的笔记。

        这是**静默错数据**：不报错，只是少了内容。判据用 mtime。
        """
        target = self.inbox / "一篇.html"
        shutil.copy(FIXTURES / "minimal_profile.html", target)
        output = self._run_once("--on", "2026-10-28", "--settle", "600")
        self.assertIn("本次自动入库 0 个文件", output)
        self.assertTrue(target.exists(), "还没写完就被处理了")
        self.assertFalse((self.inbox / "processed").exists())

    def test_detail_page_goes_all_the_way_through(self):
        """详情页要能走完整条路：识别 → 写表 → 图上文字 → 归档。

        **这条测试本身就是回归防线。** 它缺席的那一天，
        `cmd_watch` 传给 `cmd_ingest` 的 Namespace 少了 `no_ocr` 字段，
        而 `if detail_ready and not args.no_ocr` 的短路求值让它只在有详情页时才抛
        `AttributeError`——主页存档一切正常，所以原有测试全绿。
        真实用法里丢进去的恰恰是详情页，于是 `ar watch` 每篇都崩，
        还被包装成「入库失败：'Namespace' object has no attribute 'no_ocr'」。
        """
        store.append_rows(self.data, "accounts", [{"account_id": "acc1",
                                                  "nickname": "测试号",
                                                  "collected_on": "2026-10-27"}])
        # 详情页要求底行已存在（先 parse 主页）。这一步是**正确行为**：
        # note_id 抄错 / 账号混了必须被拒，不该为了让内容进来就放宽。
        store.append_rows(self.data, "notes", [{
            "account_id": "acc1", "note_id": "0000000000000000000000f1", "pinned": "0",
            "title": "底行", "form": "图文", "likes": "1",
            "source_file": "f.html", "source_kind": "公开页",
            "collected_on": "2026-10-27", "grade": "②",
        }])
        shutil.copy(FIXTURES / "minimal_detail.html", self.inbox / "详情 - 小红书.html")
        time.sleep(1.2)
        output = self._run_once("--on", "2026-10-28")
        self.assertIn("本次自动入库 1 个文件", output, f"详情页没走通：\n{output}")
        self.assertNotIn("入库失败", output, f"详情页入库报错了：\n{output}")
        self.assertNotIn("no_ocr", output, "Namespace 少了字段")
        self.assertNotIn("no_new_authors", output, "Namespace 又少了一个字段")
        rows = store.read_table(self.data, "notes")
        self.assertTrue(any(str(r.get("note_id")) == "0000000000000000000000f1" for r in rows),
                        "详情页没有写进 notes 表")

    def test_watch_namespace_carries_the_author_gate(self):
        """`cmd_watch` 拼的 Namespace 必须带 `no_new_authors`。

        这一条与上面 ⑥ 同源，只是换了个字段：`cmd_watch` 手搓 Namespace 交给
        `cmd_ingest`，而 argparse 那侧的字段叫 `no_new_authors`。名字对不上时
        `_detail_rows` 读 `getattr(args, "no_new_authors", False)`——**默认值把错误吞掉了**，
        于是「拒收外观作者」这个开关在 watch 通道里静默失效。

        用 `getattr` 默认值兜底是合理的（它让旧 Namespace 不至于崩），
        但「默认值」不能变成「永久默认」——所以这里直接查源码里传的是哪个名字。
        """
        source = (ROOT / "src" / "account_research" / "cli.py").read_text(encoding="utf-8")
        watch_body = source.split("def cmd_watch", 1)[1]
        self.assertIn("no_new_authors=getattr(args, \"no_new_authors\"", watch_body,
                      "cmd_watch 传给 cmd_ingest 的字段名与 argparse 不一致")
        self.assertNotIn("new_authors=not getattr", watch_body,
                         "又用回了旧字段名：argparse 那侧叫 no_new_authors")


class TestWatchDateIsTakenAtIngestTime(unittest.TestCase):
    """`--on` 省略时，日期必须在**入库那一刻**取，不在启动那一刻取。

    为什么单独立一条：采集日期是本项目所有对账的基准（同日额度、D1 到期、
    报告里的「截至日」）。开着过夜把第二天的记成第一天，会让这些全部算错，
    而且**没有任何一处会报错**——只能靠人翻 CSV 时发现日期不对。
    """

    def test_parser_default_is_none_so_date_is_resolved_later(self):
        parser = cli.build_parser()
        # 从子命令里把 watch 的 parser 找出来，看 --on 的默认值
        subparsers = [action for action in parser._actions
                      if action.__class__.__name__ == "_SubParsersAction"]
        watch = next(sp for name, sp in subparsers[0].choices.items() if name == "watch")
        on_action = next(a for a in watch._actions if a.dest == "on")
        self.assertIsNone(on_action.default,
                          "默认值不能是固定的日期字符串，否则跨天运行会记错")

    def test_missing_on_means_today_at_ingest(self):
        args = cli.build_parser().parse_args(["watch", "--once"])
        self.assertIsNone(args.on)
        # `cmd_watch` 里用的是 `args.on or today()` —— 上面那条 None 就是它的前提
        self.assertFalse(args.on, "省略 --on 时必须落到 None 分支")


if __name__ == "__main__":
    unittest.main()
