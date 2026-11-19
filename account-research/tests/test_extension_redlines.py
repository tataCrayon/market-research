"""扩展的权限与红线检查。

扩展比书签更"能"——它能常驻、能注入脚本、能**调用本地程序**。所以红线的检查要比书签更严：
**权限清单本身就是设计**。一个 `cookies` 进来，这个扩展就从"读当前页"变成了"能读整个浏览器"。

## 历次修订

- v3.0（2026-11-12）：从「存 txt」改成「存整页 HTML」（txt 通道一篇正文都没落盘）。
- v4.0（2026-11-12）：改成存 MHTML（实测文字层只占全文 28.1%，不带图等于空笔记）。
- v6.0（2026-11-13）：下载挪进 offscreen 文档解决 blob URL 跨上下文问题。
- **v7.0（2026-11-16）：删掉 offscreen，改用原生消息直接入库。**

## v7 为什么权限清单几乎没变

从 `[scripting, pageCapture, offscreen]` 变成 `[scripting, pageCapture, nativeMessaging]`：
删一个、加一个，**净增 0**。

`nativeMessaging` 听起来比 `offscreen` 危险得多（「扩展能调本地程序」），但要让这一点变成
真正的风险，还得有「一个会执行任意命令的宿主」。所以红线检查的重心从**扩展的权限**
移到了**宿主的实现**：

- 宿主只做一件事：把收到的字节交给 `ar ingest` 的同一条代码路径；
- 没有「通用执行」入口、没有 shell、没有文件浏览；
- 宿主里**没有任何网络调用**（连 `urllib` 都不 import）——这一条由 `test_host_redlines.py` 守。

## 「文件不再落盘」也让一整类 bug 消失

v6 的三个真实事故（Blob 过不了消息通道、blob URL 跨上下文失效、offscreen 没有
`chrome.downloads`）**全部**来自「要让浏览器写文件」这一个需求。v7 不写文件了，
那三个坑连同它们的修法一起消失。这不是运气，是**换掉一个错误的需求**的结果。
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parents[1] / "extension"
SRC = Path(__file__).resolve().parents[1] / "src" / "account_research"
sys.path.insert(0, str(SRC.parent))
ALLOWED_PERMISSIONS = {"scripting", "pageCapture", "nativeMessaging"}
ALLOWED_HOSTS = {"https://www.xiaohongshu.com/*"}
BANNED_IN_CODE = ("XMLHttpRequest", "WebSocket", "sendBeacon",
                  "document.cookie", "localStorage", "sessionStorage",
                  "setInterval", "location.href =")
# `fetch` 单独处理：**红线是「不许发网络请求」，不是「不许出现 fetch 这个字」**。
# 读 `pageCapture` 的 blob 有一条路必须走 `fetch(blobUrl)`——
# blob URL 由浏览器本地解析，一个字节都不出网。
# 所以判据改成「fetch 的实参必须来自 createObjectURL 拿到的那个变量」，
# 再加一条「代码里不许出现 http(s) 字面量」。
# 用一个更宽的字面量禁令替代它，等于为了洁癖把唯一能救回来的读法禁掉。


def strip_comments(text: str) -> str:
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_v3(self):
        self.assertEqual(self.manifest["manifest_version"], 3)

    def test_permissions_are_minimal(self):
        granted = set(self.manifest.get("permissions", []))
        self.assertTrue(granted <= ALLOWED_PERMISSIONS,
                        f"出现多余权限：{granted - ALLOWED_PERMISSIONS}")

    def test_host_permissions_only_target_site(self):
        """host_permissions 按站点收窄：只允许小红书域，不能是 <all_urls>。"""
        hosts = set(self.manifest.get("host_permissions", []))
        self.assertTrue(hosts <= ALLOWED_HOSTS, f"多申请了域名：{hosts - ALLOWED_HOSTS}")
        self.assertNotIn("<all_urls>", hosts)

    def test_no_cookie_or_webrequest_permission(self):
        for banned in ("cookies", "webRequest", "history", "webNavigation", "activeTab"):
            self.assertNotIn(banned, self.manifest.get("permissions", []))

    def test_needs_scripting_and_page_capture(self):
        granted = set(self.manifest.get("permissions", []))
        self.assertIn("scripting", granted)
        self.assertIn("pageCapture", granted)

    def test_offscreen_permission_is_gone(self):
        """v7 不再需要 offscreen 文档：它存在只为解决「service worker 里没有
        `URL.createObjectURL`」，而现在不写文件了。

        留着一个不用的权限，等于**为一个已经不存在的能力继续背书**。
        """
        self.assertNotIn("offscreen", self.manifest.get("permissions", []))

    def test_downloads_permission_is_gone(self):
        self.assertNotIn("downloads", self.manifest.get("permissions", []))

    def test_native_messaging_is_declared(self):
        """「一整个插件包含采集能力」靠的就是它：点一下直接把字节交给本地宿主。"""
        self.assertIn("nativeMessaging", self.manifest.get("permissions", []))

    def test_has_a_stable_key(self):
        """manifest 里必须有 `key`，扩展 ID 才是固定的。

        没有它的话，扩展 ID 由**安装路径**推导——同一份代码换个目录就换一个 ID，
        而宿主的 `allowed_origins` 是按 ID 白名单的，于是「昨天还能用，今天连不上」。
        """
        key = self.manifest.get("key")
        self.assertTrue(key, "manifest 缺 key：扩展 ID 会随安装路径变化")
        self.assertGreater(len(key), 300, "key 太短，不像一个 2048 位公钥")

    def test_key_determines_the_registered_extension_id(self):
        """扩展 ID 必须与注册用的常量一致，否则 Chrome 会以「origin 不允许」拒连。

        这个算法不是猜的：ID = SHA-256(DER 公钥) 前 16 字节，
        每个字节拆成两个 hex 数字，0–15 映射成 a–p。
        """
        import base64
        import hashlib

        from account_research.host import EXTENSION_ID

        der = base64.b64decode(self.manifest["key"])
        digest = hashlib.sha256(der).digest()
        derived = "".join(chr(ord("a") + nibble) for byte in digest[:16]
                          for nibble in (byte >> 4, byte & 0xF))
        self.assertEqual(derived, EXTENSION_ID,
                         "manifest.key 推导出的 ID 与 host.EXTENSION_ID 不一致——改了 key 就要重新注册")

    def test_version_and_description_match_what_it_saves(self):
        """清单里的话必须对得上行为。v2 的描述写「各存一个 txt」——那正是要改掉的老行为。"""
        self.assertEqual(self.manifest["version"], "8.1.0")
        self.assertIn("MHTML", self.manifest["description"])
        self.assertIn("含图", self.manifest["description"])
        # v8：描述里必须出现「主页」。它不再只是笔记采集器——
        # 而「能不能采主页」正是上一版把自己卡死的那条边界。
        self.assertIn("主页", self.manifest["description"])
        self.assertIn("MHTML", self.manifest["action"]["default_title"])
        self.assertNotIn("txt", self.manifest["description"])

    def test_name_says_what_it_does(self):
        """扩展名不能让用户猜。

        用户原话：「插件名字也改一下，AR 采集不知道是什么意思」。
        `AR` 是这个项目对 `account-research` 的缩写——**对内部人是缩写，对使用者是乱码**。
        名字里必须有**平台 + 动作**，两者缺一就还得猜一次。
        """
        name = self.manifest["name"]
        self.assertIn("小红书", name, "名字里要出现平台：不然不知道它采什么")
        self.assertIn("采集", name, "名字里要出现动作：不然不知道它干什么")
        self.assertNotIn("AR ", name, "不许再用内部缩写当门面")

    def test_has_a_popup_so_the_plugin_has_a_visible_surface(self):
        """「一整个插件」得有个看得见的地方。

        在此之前，扩展的全部可见部分就是图标上那个小 badge——
        采集结果只留一句 tooltip，而没人会去悬停一个按钮（E47）。
        """
        self.assertEqual(self.manifest["action"].get("default_popup"), "popup.html")


class TestBackgroundScript(unittest.TestCase):
    """background（service worker）现在只画 badge，不碰页面。

    v7.2 把它降级成这个角色，是因为第一次真机采集在它里面失败：
    `pageCapture` 返回的是**文件-backed 句柄**，MV3 的 worker 里读不出来
    （`NotReadableError: The requested file could not be read…`）。
    采集因此搬进了弹出面板（渲染进程），`TestCaptureEngine` 守那边。
    """

    def setUp(self):
        self.raw = (EXT / "background.js").read_text(encoding="utf-8")
        self.code = strip_comments(self.raw)

    def test_no_network_calls(self):
        for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
            self.assertNotIn(banned, self.code, f"代码里出现 {banned}：扩展会变成采集器")

    def test_no_session_access(self):
        for banned in ("document.cookie", "localStorage", "sessionStorage"):
            self.assertNotIn(banned, self.code, f"读了 {banned}：会话数据不该被采集")

    def test_no_polling(self):
        for banned in ("setInterval", "chrome.alarms"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：会自己跑起来")

    def test_does_not_capture(self):
        """**这条是架构约束，不是风格偏好。**

        service worker 里没有渲染进程那套 File API，`pageCapture` 的 blob 在那里
        读不出来。把采集挪回去不会报错——只会在真机上再失败一次，
        而那次失败的样子（`NotReadableError`）和这次的修复长得完全不一样。
        """
        for banned in ("chrome.pageCapture", "chrome.scripting", "connectNative"):
            self.assertNotIn(banned, self.code,
                             f"background 里出现 {banned}：它读不出 pageCapture 的 blob")

    def test_still_renders_the_badge(self):
        """采集搬走了，badge 还得有人画——它是面板关掉之后唯一留下的痕迹。"""
        self.assertIn("setBadgeText", self.code)
        self.assertIn("setBadgeBackgroundColor", self.code)

    def test_click_without_a_popup_is_still_visible(self):
        """面板加载失败时点图标会退回 `onClicked`。那时也必须说清楚，不能安静。"""
        self.assertIn("chrome.action.onClicked", self.code)
        self.assertIn("面板没能打开", self.code)


class TestCaptureEngine(unittest.TestCase):
    """`capture.js` 是采集的全部逻辑，跑在弹出面板（渲染进程）里。"""

    def setUp(self):
        self.raw = (EXT / "capture.js").read_text(encoding="utf-8")
        self.code = strip_comments(self.raw)

    def test_no_network_calls(self):
        for banned in ("XMLHttpRequest", "WebSocket", "sendBeacon"):
            self.assertNotIn(banned, self.code, f"代码里出现 {banned}：扩展会变成采集器")

    def test_no_session_access(self):
        for banned in ("document.cookie", "localStorage", "sessionStorage"):
            self.assertNotIn(banned, self.code, f"读了 {banned}：会话数据不该被采集")

    def test_no_polling(self):
        for banned in ("setInterval", "chrome.alarms"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：会自己跑起来")

    def test_no_navigation_or_tab_creation(self):
        """不许开新标签 / 导航——那才是程序发起请求。"""
        for banned in ("chrome.tabs.create", "chrome.tabs.update", "chrome.tabs.reload",
                       "location.assign", "location.reload"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：会造成新的页面请求")

    def test_skips_discarded_and_loading_tabs(self):
        """核心论证：对休眠标签注入会触发重新加载 = 我们制造了一次请求。必须跳过。"""
        self.assertIn("discarded", self.code)
        self.assertIn('status === "loading"', self.code)

    def test_has_hard_tab_cap(self):
        self.assertIn("MAX_TABS", self.code)
        self.assertRegex(self.code, r"const MAX_TABS = \d+")

    def test_scoped_to_current_window(self):
        self.assertIn("currentWindow: true", self.code)
        self.assertIn("https://www.xiaohongshu.com/*", self.code)

    def test_no_banned_identifiers_present(self):
        for banned in BANNED_IN_CODE:
            self.assertNotIn(banned, self.code, f"命中禁止标识：{banned}")

    def test_fetch_only_reads_the_local_blob_url(self):
        """`fetch` 只允许读 `createObjectURL` 生成的那个 blob URL。

        红线是「**不发网络请求**」，不是「不许出现 fetch 这个字」。
        读 `pageCapture` 的 blob 有一条路必须走 `fetch(blobUrl)`——
        blob URL 由浏览器本地解析，一个字节都不出网。
        所以判据落在**实参**上：它必须是 `URL.createObjectURL(...)` 的返回值，
        而且代码里不许出现任何 http(s) 字面量（那才是真的会出网的东西）。
        """
        for match in re.finditer(r"fetch\(([^)]*)\)", self.code):
            argument = match.group(1).strip()
            self.assertIn(argument, {"url", "blobUrl"},
                          f"fetch 的实参不是 blob URL 变量：{argument}")
        self.assertNotRegex(self.code, r"fetch\(\s*[\"'`]https?:")
        self.assertNotIn("http://", self.code)
        # 站内 https 只在 tabs.query 的匹配式与注释里出现；剔除它再看剩下的。
        without_site = self.code.replace("https://www.xiaohongshu.com/*", "")
        self.assertNotIn("https://", without_site, "代码里不该出现别的外网地址")

    def test_host_name_matches_the_python_side(self):
        """扩展里的宿主名与 Python 侧的 HOST_NAME 必须一字不差。

        不一致的表现是 Chrome 抛「Specified native messaging host not found.」——
        看起来像「宿主没装」，实际是名字写错了。这条断言把两者钉在一起。
        """
        from account_research.host import HOST_NAME

        self.assertIn(f'"{HOST_NAME}"', self.code)

    def test_protocol_version_matches_the_python_side(self):
        from account_research.host import PROTOCOL_VERSION

        self.assertRegex(self.code, rf"const PROTOCOL_VERSION = {PROTOCOL_VERSION};")


class TestSavesFullArchiveNotALossyTxt(unittest.TestCase):
    """v2 的核心缺陷：产出的 txt 里没有可分析内容。

    实测证据：走 txt 通道采过 2 篇（`6a0da856`、`6ac8f9c1`），
    `account/notes-text/` 里**一篇都没有**，赞/藏/评也全空。
    原因是 `body_head` 只写正文前 40 字。

    v3 改存整页 HTML —— 但那仍然**不带图**，而实测文字层只占全文 28.1%（中位数）、
    6/11 篇低于 30%。所以 v4 再进一步：存单文件 MHTML，图一起打包。
    """

    def setUp(self):
        self.raw = (EXT / "capture.js").read_text(encoding="utf-8")
        self.code = strip_comments(self.raw)

    def test_uses_page_capture_mhtml(self):
        self.assertIn("chrome.pageCapture.saveAsMHTML", self.code)

    def test_does_not_truncate_body_to_a_head(self):
        """`body_head` 是 v2 的截断写法，必须彻底消失。

        注意别写成「不许出现 `slice(0, 40)`」——URL 截断到 40 字是错误提示里的正常写法，
        禁掉它会把测试变成一条与意图无关的格式洁癖。
        真正的判据是：**正文变量根本不再被读、被截断**。
        """
        self.assertNotIn("body_head", self.code)
        self.assertNotIn("bodyText", self.code)
        self.assertNotIn("innerText", self.code)

    def test_does_not_reimplement_the_pipeline(self):
        """不许在这里自己生成 HTML + `_files/`：那会让下游出现第二条解析腿。"""
        self.assertNotIn("outerHTML", self.code)
        self.assertNotIn('"_files"', self.code)
        self.assertNotIn("' _files'", self.code)


class TestClickGivesVisibleFeedback(unittest.TestCase):
    """「点了没反应」的第二层原因：v4 的成功/失败**只写进 tooltip**。

    `chrome.action.setTitle()` 是鼠标悬停才显示的提示。
    不悬停一个「点了没反应」的按钮，是人的正常反应——所以必须有 badge 与面板。
    """

    def setUp(self):
        self.capture = strip_comments((EXT / "capture.js").read_text(encoding="utf-8"))
        self.popup = strip_comments((EXT / "popup.js").read_text(encoding="utf-8"))
        self.background = strip_comments((EXT / "background.js").read_text(encoding="utf-8"))

    def test_uses_badge_for_immediate_feedback(self):
        self.assertIn("setBadgeText", self.background)
        self.assertIn("setBadgeBackgroundColor", self.background)

    def test_popup_pushes_the_badge_after_capturing(self):
        """采集在面板里跑，badge 得靠消息推给 background——不然图标一直是空的。"""
        self.assertIn('type: "AR_BADGE"', self.popup)
        self.assertIn("AR_BADGE", self.background)

    def test_reports_when_there_are_no_collectable_tabs(self):
        """最常见的一种「没反应」：当前窗口里根本没有可采的页面。必须明说。

        v8 起「可采」= 主页 **或** 笔记页，所以措辞跟着变了——
        仍然**不能**是静默返回一个空结果。
        """
        self.assertIn("没有小红书主页或笔记页", self.capture)

    def test_failures_are_shown_in_the_panel(self):
        """失败必须出现在面板上，而且带**层次**（识别页面/存档页面/转换存档/入库）。"""
        for step in ("识别页面", "存档页面", "转换存档", "入库"):
            self.assertIn(step, self.capture, f"失败信息没说清是哪一层：{step}")
        self.assertIn("failures", self.popup)

    def test_host_failures_are_translated_into_actions(self):
        """Chrome 的原话（`Specified native messaging host not found.`）对用户没有意义。

        「插件坏了」和「宿主没注册」在屏幕上长得一样，而后者只要跑一条命令。
        所以必须把它翻译成「你现在该做什么」。
        """
        self.assertIn("hostHint", self.capture)
        self.assertIn("ar.py host --register", self.capture)
        self.assertIn("ar.py host --doctor", self.capture)

    def test_shows_the_ingest_log(self):
        """入库是后台发生的，它的输出（含校验告警）必须回流到面板。

        否则「入库了但校验报警」这类信息只存在于一个看不见的地方——
        与 E47「不显示的错误 = 没有错误」同一条教训。
        """
        self.assertIn("reply.log", self.capture)
        self.assertIn("result.logs", self.popup)

    def test_shows_which_read_path_worked(self):
        """读存档有两条路。出问题时最需要知道的是**哪条通、哪条不通**。"""
        self.assertIn("readMethod", self.capture)
        self.assertIn("result.readMethod", self.popup)


class TestMainWorldIsReadOnly(unittest.TestCase):
    """`world: "MAIN"` 是 v3 新开的能力：只有主上下文能读 `window.__INITIAL_STATE__`。

    它同时意味着注入的代码**能改页面自己的 JS 变量**——所以必须钉死为只读。
    """

    def setUp(self):
        self.code = strip_comments((EXT / "capture.js").read_text(encoding="utf-8"))

    def test_uses_main_world(self):
        self.assertIn('world: "MAIN"', self.code)

    def test_does_not_write_page_state(self):
        """不许给 `window.__INITIAL_STATE__` 赋值，也不许调平台全局对象。"""
        self.assertNotRegex(self.code, r"window\.__INITIAL_STATE__\s*=")
        self.assertNotRegex(self.code, r"window\.\w+\s*=")

    def test_nothing_is_clicked_in_the_page(self):
        """整条链路里不该再点任何东西：不写文件了，也没有 `<a download>`。"""
        self.assertEqual(re.findall(r"\.click\(\)", self.code), [])

    def test_refuses_modal_saves(self):
        """从主页点开的弹层存下来是主页，不是笔记页。

        `parse-detail` 早就能判出这种情况（`og:url` 仍指向主页），但那要等人把它
        丢进 inbox 再报错。在采集端拦一次，省一步——而且错误信息能直接说清姿势。
        """
        self.assertIn("og:url", self.code)
        self.assertIn('reason: "modal"', self.code)

    def test_reports_lazy_images_without_scrolling(self):
        """懒加载的图没被下载过，存档里就只剩远程 URL。

        实测（`6aed6c2b`，16 图）：MHTML 里 16 个 slide 只有 3 张真的下载过，
        另外 13 张只剩远程地址、`_files/` 里没有字节。

        **只能提醒，不能替人滚**：滚动会触发浏览器下载新图，那才是「程序制造请求」。
        所以代码里不许出现 `scrollTo` / `scrollIntoView` / `element.scroll` 之类。
        """
        self.assertIn("loadedImages", self.code)
        self.assertIn("naturalWidth", self.code)
        # 提示语在面板里（引擎只负责统计，措辞属于界面）
        popup = strip_comments((EXT / "popup.js").read_text(encoding="utf-8"))
        self.assertIn("有图没加载出来", popup)
        for banned in ("scrollTo", "scrollIntoView", "scrollBy", ".scroll(", "scrollIntoViewIfNeeded"):
            self.assertNotIn(banned, self.code, f"不许出现滚动调用：{banned}")


class TestPopup(unittest.TestCase):
    """面板是这次新增的「可见面」。它自己不能变成第二个采集入口。

    为什么这条重要：面板有 `chrome.tabs` 的访问权，它**有能力**自己去找笔记页、
    自己存档、自己上传。如果它真这么做，就会出现两条采集路径——
    而两条路径迟早会漂开（E57：两份存档争着写同一个文件）。
    所以面板只能「显示 + 发指令」。
    """

    def setUp(self):
        raw_html = (EXT / "popup.html").read_text(encoding="utf-8")
        raw_js = (EXT / "popup.js").read_text(encoding="utf-8")
        # **先剥注释再断言。** 注释里解释「为什么不用内联 `<script>`」时，
        # 那个字符串本身就会出现——不剥的话，一条正确遵守规则的代码会被判违规，
        # 而修法只能是「把解释删掉」。这类假阳性会教人别写字，不会教人写对。
        self.html = re.sub(r"<!--.*?-->", "", raw_html, flags=re.S)
        self.js = re.sub(r"^\s*//.*$", "", raw_js, flags=re.M)

    def test_popup_has_no_inline_script(self):
        """MV3 的 CSP 禁止内联脚本；这同时保证面板不会被注入。"""
        self.assertNotIn("<script>", self.html)
        self.assertIn('src="popup.js"', self.html)
        self.assertIn('src="capture.js"', self.html)

    def test_popup_does_not_capture_by_itself(self):
        """面板**本身**不直接调 `pageCapture` / `scripting`。

        采集逻辑全在 `capture.js` 里，面板只调 `AR.captureAll()`。
        为什么要分文件：面板的职责是「显示 + 触发」，引擎的职责是「采 + 传」。
        混在一起之后，「这一行到底属于界面还是属于采集」就没法回答了。
        """
        for banned in ("pageCapture", "executeScript"):
            self.assertNotIn(banned, self.js, f"面板里出现 {banned}：采集逻辑该在 capture.js")

    def test_popup_uses_the_capture_engine(self):
        self.assertIn("AR.captureAll", self.js)
        self.assertIn("AR.readStatus", self.js)

    def test_popup_pushes_the_badge_to_background(self):
        """面板一关，图标上那点字是唯一留下来的痕迹。"""
        self.assertIn('type: "AR_BADGE"', self.js)

    def test_popup_shows_the_install_hint_when_the_host_is_missing(self):
        """宿主没注册是**最常见**的首次失败。面板必须直接给出那条命令。"""
        self.assertIn("host --register", self.js)
        self.assertIn("host --doctor", self.js)

    def test_popup_makes_no_network_calls(self):
        for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
            self.assertNotIn(banned, self.js, f"面板里出现 {banned}")

    def test_popup_shows_facts_without_judging(self):
        """PRD N5：不评分不排名。面板显示数字，不显示「你今天采得不够」。"""
        for banned in ("评分", "排名", "得分", "建议"):
            self.assertNotIn(banned, self.js, f"面板里出现评价性措辞：{banned}")


class TestReadmeStatesBoundaries(unittest.TestCase):
    def setUp(self):
        self.text = (EXT / "README.md").read_text(encoding="utf-8")

    def test_readme_lists_permissions_and_limits(self):
        self.assertIn("scripting", self.text)
        self.assertIn("不发任何网络请求", self.text)
        self.assertIn("开发者模式", self.text)

    def test_readme_admits_images_are_not_captured(self):
        """**必须写明拿不到图**。这是 v2 用「正文要先拿到」误导人的那类错误的反面：
        说清做不到什么，比强调做到了什么更重要。"""
        self.assertIn("拿不到图", self.text)
        self.assertIn("Ctrl+S", self.text)

    def test_readme_does_not_claim_it_replaces_ctrl_s(self):
        self.assertNotIn("完全替代 Ctrl+S", self.text)


if __name__ == "__main__":
    unittest.main()
