"""把扩展的真实代码放进 VM 沙箱跑一次完整采集——验证 v7.2「读得到存档字节」。

## 为什么必须这么测

v7.1 第一次真机点击就失败：

    转换存档✗ The requested file could not be read, typically due to permission
    problems that occur after a reference to a file has been acquired

那是 `DOMException: NotReadableError`，来自 `blob.arrayBuffer()`。
`pageCapture.saveAsMHTML` 返回的**不是内存里的 Blob**，而是一个指向 profile
blob storage 的**句柄**——`size` 读得出，字节取不到。

**如果桩返回一个正常的内存 Blob，这个 bug 会被永久掩盖，而测试照样全绿。**
所以桩里所有 `pageCapture` 返回的都是「有 size、读不出来」的 FileBackedBlob。
这正是「桩的保真度决定你能测出什么」这条教训的应用。

## 五条必须保持的仿真

1. `pageCapture` 返回文件-backed blob（`arrayBuffer()` 抛 NotReadableError）；
2. `FileReader` **只在渲染进程（面板）里有**，service worker 里没有；
3. 宿主对单条消息有上限，超了端口断（逼出分块）；
4. 宿主是独立进程，回包只能靠 id 配对（桩用随机延时打乱顺序）；
5. `lastError` 只在断开那一刻可读。

第 2 条守的是一个**架构决定**：采集属于面板，不属于 background。
哪天有人把它挪回去，`test_capture_fails_loudly_in_a_service_worker` 会当场红。
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "harness" / "extension_click.js"


@unittest.skipUnless(shutil.which("node"), "没装 node：跳过（它只用于这个沙箱回归）")
class TestRealCaptureFlow(unittest.TestCase):
    """端到端：真实 capture.js，桩掉的只有 chrome.* 与 FileReader。"""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ["node", str(HARNESS), str(EXT / "capture.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        cls.result = result

    def test_click_uploads_every_collectable_page(self):
        """一次点击要把窗口里**所有**可采页面送进宿主：主页 + 笔记页。

        v8 起主页也收。原来只认 `/explore/<id>`，于是「账号从哪来」这一步只能靠人
        Ctrl+S 手动存——而扩展存在的全部意义就是替代那次手动。
        """
        self.assertEqual(self.result.returncode, 0,
                         self.result.stdout + self.result.stderr)
        self.assertIn("完整流程通过", self.result.stdout)
        # 分块真的被执行了，而不是「一条消息发完」的运气
        self.assertRegex(self.result.stdout, r"分 \d+ 块")
        self.assertNotRegex(self.result.stdout, r"分 1 块")
        self.assertIn('"saved": 3', self.result.stdout)
        self.assertIn('"failed": 0', self.result.stdout)

    def test_profile_upload_carries_the_page_state(self):
        """主页必须把 `__INITIAL_STATE__` 的子树一起传上来。

        这条守的是 v8 的核心：`pageCapture` 只序列化渲染后的 DOM，**把 `<script>` 剥掉**，
        而主页的粉丝/简介/笔记列表全在脚本里。不带 state 的主页存档能「成功入库」，
        然后变成一个**没有任何数据的账号**——成功和失败在界面上长得一模一样。
        """
        self.assertIn("profile 62c98736000000001501e075", self.result.stdout)
        self.assertRegex(self.result.stdout, r"profile \S+  \d+ B  分 \d+ 块  state \d+ 字符")

    def test_file_backed_blob_is_read_by_filereader(self):
        """这是本次修复的**唯一实质断言**。

        文件-backed 的 blob 上 `arrayBuffer()` 会失败；
        救回来的那条路必须是 `FileReader`（渲染进程专用）。
        如果哪天 `readMethod` 变成 `arrayBuffer`，说明桩不再仿真那个错误，测试就等于没测。
        """
        self.assertIn('"readMethod": "FileReader"', self.result.stdout)

    def test_a_broken_handle_is_re_saved_not_just_re_read(self):
        """读失败要**重新存一次**，不是重读同一个句柄。

        坏的是那个句柄（它背后的数据此刻还不在），不是那一次调用。
        只重读会一直失败——而失败信息看起来会像「读法全试过了还是不行」。
        沙箱里 tab1 第一次存档读不出来，所以 3 篇的存档次数必须是 4 而不是 3。
        """
        self.assertIn("共 4 次", self.result.stdout)

    def test_capture_fails_loudly_in_a_service_worker(self):
        """同一段代码在 service worker 里必须**明确失败**，而且说清原因。

        这条不是「顺便测一下」，它守的是架构：采集跑在面板里，因为
        service worker 没有 `FileReader`、且跨进程 blob 句柄在那里读不稳。
        如果这条测试失败（即「居然成功了」），说明桩的仿真坏了，修复也就没被验证。
        """
        stdout = self.result.stdout
        self.assertIn("在 service worker 里跑（应当失败）", stdout)
        self.assertIn("saved= 0", stdout)
        self.assertIn("没有 FileReader", stdout)


class TestHarnessFidelity(unittest.TestCase):
    """保护测试本身：桩被「简化」之后，上面的测试会全绿但什么都不测。"""

    def setUp(self):
        self.source = HARNESS.read_text(encoding="utf-8")

    def test_reproduces_the_not_readable_error(self):
        self.assertIn("NotReadableError", self.source)
        self.assertIn("could not be read", self.source)

    def test_filereader_exists_only_in_the_popup_context(self):
        self.assertIn("installFileReader", self.source)
        self.assertIn("context !== 'popup'", self.source)

    def test_object_url_also_only_exists_in_the_popup_context(self):
        """`URL.createObjectURL` 在 service worker 里也没有——v6 当年就是为了这个造了
        一个 offscreen 文档。桩里两条渲染进程专用的能力必须一起缺。"""
        self.assertIn("installUrl", self.source)

    def test_enforces_a_small_message_cap(self):
        self.assertIn("MAX_MESSAGE_BYTES", self.source)
        self.assertIn("Native host has exited.", self.source)

    def test_replies_out_of_order(self):
        self.assertIn("Math.random()", self.source)
        self.assertIn("id: message.id", self.source)

    def test_serialises_messages_like_chrome(self):
        """Chrome 的消息通道是 JSON 序列化（v4 那个「Blob 变成 {}」的 bug 就在这里）。

        桩必须走一遍，否则「只能传 JSON 能表示的东西」这条约束会被悄悄绕过。
        """
        self.assertIn("JSON.parse(JSON.stringify(", self.source)

    def test_exposes_last_error_like_chrome(self):
        self.assertIn("get lastError()", self.source)


@unittest.skipUnless(shutil.which("node"), "没装 node：跳过（它只用于这个沙箱回归）")
class TestProbePage(unittest.TestCase):
    """`probePage` 的**真实执行**——它在主流程桩里是被替换掉的。

    `extension_click.js` 把 `chrome.scripting.executeScript` 打桩了，所以
    `probePage` 一行都没跑过。而 v8 的全部新逻辑（主页/笔记的区分、
    `__INITIAL_STATE__` 的摘取与上限、弹层判据）都在它里面。
    桩掉它，等于把「这次改动最可能出错的地方」排除在测试之外。

    这条 harness 用一个只实现必要接口的假 DOM 真跑它七次（主页 / 未渲染主页 /
    整页笔记 / 弹层 / 超大 state / 不可序列化的子树 / 搜索页）。
    """

    HARNESS = Path(__file__).resolve().parent / "harness" / "probe_page.js"

    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run(
            ["node", str(cls.HARNESS), str(EXT / "capture.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )

    def test_all_scenarios_hold(self):
        self.assertEqual(self.result.returncode, 0,
                         self.result.stdout + self.result.stderr)
        self.assertIn("八个场景全部符合预期", self.result.stdout)

    def test_profile_is_recognised_and_carries_state(self):
        line = next((row for row in self.result.stdout.splitlines()
                     if row.startswith("主页：")), "")
        self.assertIn('"kind":"profile"', line)
        self.assertIn('"stateKeys":["user"]', line,
                      "主页应当只摘 user 子树；摘多了是白占带宽，摘少了解析不了")

    def test_a_csr_profile_with_an_empty_state_is_still_collectable(self):
        """**2026-11-19 真机那次的回归**。

        小红书是 CSR：页面挂载后头部由接口填进 DOM，而首屏的
        `__INITIAL_STATE__.user.userPageData` 是空的。v8.0 拿 state 里的昵称当
        「渲染好了没」的门槛，于是**屏幕上明明完整的页面被判「还没渲染」**。

        正确的判据是 `.user-name` 在不在 DOM 里。这条钉住：state 空 + DOM 齐 → 可采。
        """
        line = next((row for row in self.result.stdout.splitlines()
                     if row.startswith("CSR 主页")), "")
        self.assertIn('"isFullPage":true', line)
        self.assertIn('"hasState":false', line,
                      "state 里确实没有昵称时必须如实报 false（发布日会缺）")
        self.assertIn('"nickname":"测试号"', line,
                      "要带出 DOM 里的昵称，面板才能显示「在采哪个号」")

    def test_an_oversized_state_is_reported_not_silently_dropped(self):
        line = next((row for row in self.result.stdout.splitlines()
                     if row.startswith("超大 state：")), "")
        self.assertIn('"tooLarge":true', line)
        self.assertIn('"chars"', line, "只报「太大」而不报多大，人无法判断差多少")

    def test_a_modal_is_told_apart_from_a_full_page(self):
        line = next((row for row in self.result.stdout.splitlines()
                     if row.startswith("弹层：")), "")
        self.assertIn('"reason":"modal"', line)

    def test_harness_does_not_use_a_real_dom_library(self):
        """不引 jsdom：多一个依赖就多一处「桩与真浏览器不一样」的未知，
        而这里验的是**我们自己写的取值逻辑**，不是选择器在某个 DOM 实现下的行为。"""
        source = self.HARNESS.read_text(encoding="utf-8")
        self.assertNotIn("require('jsdom')", source)
        self.assertNotIn("require(\"jsdom\")", source)


class TestArchitectureSplit(unittest.TestCase):
    """v7.2 的架构：采集在面板，background 只管 badge。

    **为什么把这条写成测试**：这两个文件的职责边界是最容易被「顺手挪回去」的东西。
    挪回去不会报错，只会在真机上再失败一次——而那次失败的样子
    （`NotReadableError`）和这次的修复长得完全不一样，很难联想。
    """

    def setUp(self):
        self.capture = (EXT / "capture.js").read_text(encoding="utf-8")
        self.background = (EXT / "background.js").read_text(encoding="utf-8")
        self.background_code = _strip_comments(self.background)

    def test_capture_lives_in_the_popup_bundle(self):
        html = (EXT / "popup.html").read_text(encoding="utf-8")
        self.assertIn('src="capture.js"', html)

    def test_background_does_not_capture_anything(self):
        """background 里不许出现 pageCapture / scripting —— 它们属于渲染进程那边。"""
        for banned in ("pageCapture", "executeScript", "connectNative"):
            self.assertNotIn(banned, self.background_code,
                             f"background 里出现 {banned}：它没有渲染进程，会再次读到 NotReadableError")

    def test_capture_has_three_read_paths(self):
        self.assertIn("readViaFileReader", self.capture)
        self.assertIn("readViaArrayBuffer", self.capture)
        self.assertIn("readViaObjectUrl", self.capture)

    def test_retries_the_save_not_only_the_read(self):
        """只重试「读」是没用的：坏的是句柄。

        这条守住的是一个**很容易写错的方向**——看到「读取失败」的第一反应
        是「再读一次」，而真正需要的是「让 Chrome 重新存一次」。
        """
        self.assertIn("saveAsMHTML", self.capture)
        # 存档与读字节必须在同一个循环里（重试覆盖到存档）
        self.assertRegex(self.capture, r"for \(let attempt = 0; attempt < 2")

    def test_capture_reports_which_read_path_worked(self):
        """失败时最需要的信息是「哪条路通、哪条不通」。

        只报一句「读不到」的话，下次还是只能猜——而猜正是 v5→v6 反复两轮的原因。
        """
        self.assertIn("method", self.capture)
        self.assertIn("errors", self.capture)

    def test_no_fake_fallbacks(self):
        """兜底必须是真的另一条路，不能只是换了个 API 名字。

        我一度加过 `new Response(blob).arrayBuffer()`，理由是「走不同的内部路径」。
        那是假多样性：它读 blob 用的是同一套逻辑，前一条失败它同样失败。
        代价是真实的——桩里它把「读不出来」变成了「读到一堆垃圾」，测试显示成功。

        `createObjectURL` + `fetch` **不是**这一类：它走的是网络栈，
        在「扩展注册表里没有、但 blob storage 里还在」时表现不同。
        """
        code = _strip_comments(self.capture)
        self.assertNotIn("new Response(", code)
        self.assertIn("createObjectURL", code)


def _strip_comments(text: str) -> str:
    import re

    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


if __name__ == "__main__":
    unittest.main()
