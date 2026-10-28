"""扩展的权限与红线检查。

扩展比书签更"能"——它能常驻、能注入脚本、能下载。所以红线的检查要比书签更严：
**权限清单本身就是设计**。一个 `cookies` 进来，这个扩展就从"读当前页"变成了"能读整个浏览器"。

v2.0 起扩展支持「一次保存当前窗口里所有已打开的小红书笔记页」。这个能力**不新增任何请求**：
它只读取用户自己打开过的标签页，并跳过休眠/未加载的标签（对它们注入会触发重新加载 = 制造请求）。
下面把这些约束钉成断言。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parents[1] / "extension"
ALLOWED_PERMISSIONS = {"scripting", "downloads"}
ALLOWED_HOSTS = {"https://www.xiaohongshu.com/*"}
BANNED_IN_CODE = ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon",
                  "document.cookie", "localStorage", "sessionStorage",
                  "setInterval", "location.href =")


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
        for banned in ("cookies", "webRequest", "history", "webNavigation"):
            self.assertNotIn(banned, self.manifest.get("permissions", []))

    def test_needs_downloads_and_scripting(self):
        granted = set(self.manifest.get("permissions", []))
        self.assertIn("scripting", granted)
        self.assertIn("downloads", granted)


class TestBackgroundScript(unittest.TestCase):
    def setUp(self):
        self.raw = (EXT / "background.js").read_text(encoding="utf-8")
        self.code = re.sub(r"^\s*//.*$", "", self.raw, flags=re.M)

    def test_no_network_calls(self):
        for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
            self.assertNotIn(banned, self.code, f"代码里出现 {banned}：扩展会变成采集器")

    def test_no_session_access(self):
        for banned in ("document.cookie", "localStorage", "sessionStorage"):
            self.assertNotIn(banned, self.code, f"读了 {banned}：会话数据不该被采集")

    def test_no_polling(self):
        for banned in ("setInterval", "chrome.alarms"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：会自己跑起来")

    def test_only_runs_on_click(self):
        self.assertIn("chrome.action.onClicked", self.code)
        for banned in ("chrome.tabs.onUpdated", "chrome.webNavigation"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：会在用户没点时自己跑")

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

    def test_counts_left_for_human(self):
        self.assertIn("bar_text", self.code)
        self.assertNotRegex(self.code, r'"likes: "\s*\+\s*[a-zA-Z]')

    def test_no_banned_identifiers_present(self):
        for banned in BANNED_IN_CODE:
            self.assertNotIn(banned, self.code, f"命中禁止标识：{banned}")


class TestReadmeStatesBoundaries(unittest.TestCase):
    def test_readme_lists_permissions_and_limits(self):
        text = (EXT / "README.md").read_text(encoding="utf-8")
        self.assertIn("scripting", text)
        self.assertIn("不发任何网络请求", text)
        self.assertIn("开发者模式", text)


if __name__ == "__main__":
    unittest.main()
