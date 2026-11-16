"""注册链路：注册表、清单、启动脚本，以及「注册完真的能握手吗」。

## 为什么这些测试一条都不能少

注册这一步的失败**全都不可见**。Chrome 只会给一句

    Specified native messaging host not found.

而它至少有五种原因：没注册、注册表指向的文件不存在、清单里没允许这个扩展 ID、
`.bat` 里的路径有空格没加引号、`.bat` 用了 `python` 而不是 `pythonw`。
**五种原因、一句提示**——所以每一环都要单独钉住，并且 `--doctor` 必须逐项报告。

## 测试不会碰真实注册表

`HostPaths` 允许传入自定义 `registry_key`。测试用一个临时键
（`Software\\account_research_tests\\<随机>`），跑完就删。
**一条会污染真实 Chrome 配置的测试，不该存在于这个仓库**——和「测试不许碰 data/」同一条规矩。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from account_research import hostsetup  # noqa: E402


def temp_paths(base: Path) -> hostsetup.HostPaths:
    """造一组「完全在临时目录里」的路径，注册表键也带随机后缀。"""
    paths = hostsetup.HostPaths.under(base)
    return hostsetup.HostPaths(
        project_root=paths.project_root,
        launcher=paths.launcher,
        manifest=paths.manifest,
        registry_key=rf"Software\account_research_tests\{uuid.uuid4().hex}",
        extension_id=paths.extension_id,
    )


class TestLauncherScript(unittest.TestCase):
    """`.bat` 是 Chrome 唯一能启动的东西——它错了，整条链路就断了。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = temp_paths(Path(self.tmp.name))
        # 脚本内容里会写到 ar_host.py 的绝对路径，所以先把它造出来
        (self.paths.project_root / "ar_host.py").write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_with_echo_off(self):
        """`@echo off` 不是整洁癖：不加的话每条命令都会回显到 **stdout**，
        而 stdout 是协议流——那些字符会把长度前缀冲掉。"""
        text = hostsetup.launcher_script(self.paths)
        self.assertTrue(text.startswith("@echo off"), text[:80])

    def test_changes_directory_absolutely(self):
        """Chrome 的 cwd 是 `C:\\Windows\\System32` 之类，相对路径必然找不到脚本。"""
        text = hostsetup.launcher_script(self.paths)
        self.assertIn("cd /d", text)
        self.assertIn(str(self.paths.project_root), text)

    def test_uses_pythonw_so_no_console_window_flashes(self):
        """每次采集闪一个黑框没人能忍。`pythonw` 没有控制台，管道照常工作。"""
        text = hostsetup.launcher_script(self.paths)
        self.assertIn("pythonw", text.lower())

    def test_quotes_every_path(self):
        """路径里有空格时不加引号 → 命令被拆成两段 → 宿主起不来，
        而错误只会在 Chrome 里表现成「没回应」。"""
        text = hostsetup.launcher_script(self.paths)
        for line in text.splitlines():
            if "python" in line.lower():
                self.assertEqual(line.count('"'), 4,
                                 f"引号数量不对（应为 4 个，包住解释器和脚本）：{line}")

    def test_has_crlf_line_endings(self):
        """`.bat` 用 LF 换行时，某些 Windows 版本的 cmd 会把参数吃掉。"""
        text = hostsetup.launcher_script(self.paths)
        self.assertIn("\r\n", text)


class TestManifestPayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = temp_paths(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_shape(self):
        payload = hostsetup.manifest_payload(self.paths)
        self.assertEqual(payload["type"], "stdio")
        self.assertEqual(payload["name"], hostsetup.HOST_NAME)
        self.assertEqual(payload["path"], str(self.paths.launcher))

    def test_allowed_origins_is_exactly_this_extension(self):
        """白名单只能有我们自己——多一个 origin 就是让别的扩展也能调本地程序。"""
        payload = hostsetup.manifest_payload(self.paths)
        self.assertEqual(payload["allowed_origins"],
                         [f"chrome-extension://{self.paths.extension_id}/"])


class TestRegisterRoundtrip(unittest.TestCase):
    """真的写注册表、真的删掉。用的键带随机后缀，不碰 Chrome 的。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = temp_paths(Path(self.tmp.name))
        (self.paths.project_root / "ar_host.py").write_text("", encoding="utf-8")

    def tearDown(self):
        hostsetup.unregister(self.paths)  # 失败也不该影响其它测试
        self.tmp.cleanup()

    def test_register_then_unregister_leaves_nothing_behind(self):
        hostsetup.register(self.paths)
        self.assertTrue(self.paths.launcher.is_file())
        self.assertTrue(self.paths.manifest.is_file())
        self.assertEqual(hostsetup.current_registration(self.paths), str(self.paths.manifest))

        removed = hostsetup.unregister(self.paths)
        self.assertTrue(removed, "注销时说「什么都没删掉」，但刚注册过")
        self.assertFalse(self.paths.launcher.exists())
        self.assertFalse(self.paths.manifest.exists())
        self.assertEqual(hostsetup.current_registration(self.paths), "")

    def test_unregister_on_a_clean_machine_is_not_an_error(self):
        self.assertEqual(hostsetup.unregister(self.paths), [])

    def test_register_overwrites_a_stale_registration(self):
        """重复注册是**正常操作**（换了 Python、换了目录都要重跑一次）。"""
        hostsetup.register(self.paths)
        old = self.paths.manifest.read_text(encoding="utf-8")
        hostsetup.register(self.paths)
        self.assertEqual(self.paths.manifest.read_text(encoding="utf-8"), old)


class TestDiagnose(unittest.TestCase):
    """`--doctor` 是这套东西**唯一的可观测面**，所以它必须能区分不同原因。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = temp_paths(Path(self.tmp.name))
        (self.paths.project_root / "ar_host.py").write_text("", encoding="utf-8")
        (self.paths.project_root / "extension").mkdir(exist_ok=True)
        (self.paths.project_root / "extension" / "manifest.json").write_text(
            json.dumps({"key": "x" * 400, "permissions": ["nativeMessaging"]}),
            encoding="utf-8")

    def tearDown(self):
        hostsetup.unregister(self.paths)
        self.tmp.cleanup()

    def _detail(self, checks):
        return " | ".join(detail for _, detail in checks)

    def test_reports_not_registered_before_anything_is_done(self):
        checks = hostsetup.diagnose(self.paths)
        self.assertFalse(checks[0][0])
        self.assertIn("注册表没有", checks[0][1])

    def test_reports_missing_manifest_separately(self):
        """「没注册」和「注册了但文件没了」是两种不同的修法，不能都报成一句。"""
        checks = hostsetup.diagnose(self.paths)
        self.assertIn("宿主清单不存在", self._detail(checks))

    def test_reports_a_manifest_that_allows_the_wrong_origin(self):
        """扩展 ID 变过（换了 key）→ 必须重新注册。这条要在体检里说清楚。"""
        hostsetup.register(self.paths)
        payload = json.loads(self.paths.manifest.read_text(encoding="utf-8"))
        payload["allowed_origins"] = ["chrome-extension://someoneelse/"]
        self.paths.manifest.write_text(json.dumps(payload), encoding="utf-8")
        checks = hostsetup.diagnose(self.paths)
        self.assertIn("没允许", self._detail(checks))

    def test_reports_a_launcher_that_would_print_to_stdout(self):
        """手改坏的 `.bat`（少了 `@echo off`）必须被查出来——
        它会让协议流里多出几行命令回显，症状是「第一条之后全乱」。"""
        hostsetup.register(self.paths)
        self.paths.launcher.write_text(
            f'cd /d "{self.paths.project_root}"\r\npython ar_host.py\r\n', encoding="utf-8")
        checks = hostsetup.diagnose(self.paths)
        self.assertIn("启动脚本缺", self._detail(checks))

    def test_all_green_after_a_real_register(self):
        hostsetup.register(self.paths)
        checks = hostsetup.diagnose(self.paths)
        failed = [detail for passed, detail in checks if not passed]
        # 扩展 manifest 那条会过（上面造了 key + nativeMessaging），其余四条也应全过
        self.assertEqual(failed, [], f"体检没过：{failed}")


class TestExtensionIdIsDerivedFromTheKey(unittest.TestCase):
    """扩展 ID 与 manifest 的 `key` 必须**同源**，否则注册了也连不上。

    算法不是猜的：ID = SHA-256(DER 公钥) 取前 16 字节，
    每字节拆成两个 hex 数字，0–15 映射成 a–p。
    """

    def test_matches_the_real_manifest(self):
        import base64
        import hashlib

        manifest = json.loads(
            (ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        der = base64.b64decode(manifest["key"])
        digest = hashlib.sha256(der).digest()
        derived = "".join(chr(ord("a") + nibble) for byte in digest[:16]
                          for nibble in (byte >> 4, byte & 0xF))
        self.assertEqual(derived, hostsetup.EXTENSION_ID)


if __name__ == "__main__":
    unittest.main()
