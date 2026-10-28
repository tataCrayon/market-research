"""扩展的权限与红线检查。

扩展比书签更"能"——它能常驻、能注入脚本、能下载。所以红线的检查要比书签更严：
**权限清单本身就是设计**。一个 `cookies` 或 `<all_urls>` 进来，这个扩展就从"读当前页"
变成了"能读整个浏览器"。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

EXT = Path(__file__).resolve().parents[1] / "extension"
ALLOWED_PERMISSIONS = {"activeTab", "scripting", "downloads"}
BANNED_IN_CODE = ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon",
                  "document.cookie", "localStorage", "sessionStorage",
                  "setInterval", "setTimeout", "location.href =")


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_v3(self):
        self.assertEqual(self.manifest["manifest_version"], 3)

    def test_permissions_are_minimal(self):
        granted = set(self.manifest.get("permissions", []))
        self.assertTrue(granted <= ALLOWED_PERMISSIONS,
                        f"出现多余权限：{granted - ALLOWED_PERMISSIONS}")

    def test_no_host_permissions(self):
        """host_permissions 会让扩展在用户不点图标时也能读任意站点——不能要。"""
        self.assertNotIn("host_permissions", self.manifest)
        self.assertNotIn("optional_host_permissions", self.manifest)

    def test_needs_active_tab_and_downloads(self):
        granted = set(self.manifest.get("permissions", []))
        self.assertIn("activeTab", granted)   # 只在用户点图标时拿当前页
        self.assertIn("downloads", granted)   # 只用来存本地 txt
        self.assertIn("scripting", granted)


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

    def test_no_bulk_or_polling(self):
        for banned in ("setInterval", "setTimeout"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：轮询/批量加载属于自动化采集")

    def test_only_runs_on_click(self):
        """必须由 chrome.action.onClicked 触发，不能有 alarms / 定时 / 自动运行入口。"""
        self.assertIn("chrome.action.onClicked", self.code)
        for banned in ("chrome.alarms", "chrome.tabs.onUpdated", "chrome.webNavigation"):
            self.assertNotIn(banned, self.code, f"出现 {banned}：会在用户没点的时候自己跑")

    def test_single_tab_scope(self):
        self.assertIn("target: { tabId: tab.id }", self.code)
        self.assertNotIn("chrome.tabs.query", self.code)  # 不遍历标签页

    def test_counts_left_for_human(self):
        self.assertIn("bar_text", self.code)
        self.assertNotRegex(self.code, r'"likes: "\s*\+\s*[a-zA-Z]')

    def test_no_banned_identifiers_present(self):
        for banned in BANNED_IN_CODE:
            self.assertNotIn(banned, self.code, f"命中禁止标识：{banned}")


class TestReadmeStatesBoundaries(unittest.TestCase):
    def test_readme_lists_permissions_and_limits(self):
        text = (EXT / "README.md").read_text(encoding="utf-8")
        self.assertIn("activeTab", text)
        self.assertIn("不发任何网络请求", text)
        self.assertIn("开发者模式", text)


if __name__ == "__main__":
    unittest.main()
