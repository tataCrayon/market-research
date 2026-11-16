"""把原生消息宿主注册到本机——**一次性动作，之后不用管**。

## 注册做了什么（只有两件事）

1. 在 `HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<name>` 下写一个默认值，
   内容是**清单 JSON 的路径**。
2. 那份 JSON 里写清 `allowed_origins`（扩展 ID）和宿主的启动命令。

不需要管理员权限（HKCU 不是 HKLM），不需要重启 Chrome（下次启动扩展就生效）。

## 为什么要把 `path` 指向一个 `.bat`

Chrome 启动宿主的规则是：把 `path` 直接 `CreateProcess`，**参数表为空**。
于是两种常见写法都不行：

- `path` 指向 `python.exe` → 没有脚本参数，Python 进 REPL，等 stdin → 看起来像挂死；
- 把脚本路径写进 `path` 的字符串里 → Chrome **不解析**，整串被当文件名，报「找不到」。

所以必须有一个可执行**文件**，它自己知道要跑哪个脚本。`.bat` 就是这个文件。

`.bat` 里启动的是 **`pythonw.exe`** 而不是 `python.exe`：后者会弹出一个黑色控制台窗口，
而且那个窗口会一直挂着——每次采集都闪一个黑框，没人能忍。
`pythonw` 没有控制台；宿主需要的 stdin/stdout 仍然是 Chrome 给的管道，不受影响。

## 卸载要干净

注册过之后，机器上有三样东西：注册表项、清单 JSON、`.bat`。
卸载时三样都要删——只删注册表会留下两个孤儿文件，只删文件会让 Chrome 报
「清单不存在」（扩展里表现为连接失败）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .host import EXTENSION_ID, HOST_NAME, PROTOCOL_VERSION

# 注册表项由宿主名推导（`Software\Google\Chrome\NativeMessagingHosts\<name>`）。
REGISTRY_KEY = rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
SUPPORTED_MANIFESTS = ("manifest.json",)


@dataclass(frozen=True)
class HostPaths:
    """注册涉及的三样东西各自的绝对路径。"""

    project_root: Path          # account-research/（含 ar_host.py 与 src/）
    launcher: Path              # 生成的 .bat
    manifest: Path              # 生成的宿主清单 JSON
    registry_key: str = REGISTRY_KEY
    extension_id: str = EXTENSION_ID

    @classmethod
    def under(cls, project_root: Path) -> "HostPaths":
        """把三样东西放在项目内的 `.host/` 下：可随项目一起删，不污染系统目录。"""
        root = Path(project_root).resolve()
        base = root / ".host"
        return cls(
            project_root=root,
            launcher=base / "ar-host.bat",
            manifest=base / f"{HOST_NAME}.json",
        )


def find_pythonw() -> str:
    """找到与当前解释器同目录的 `pythonw.exe`，找不到就退回 `python.exe`。

    退回是安全的（只是会闪一个控制台窗口），但**不能退回 `py` 或空字符串**——
    那会让宿主根本起不来，而错误发生在 Chrome 里，用户看不到。
    """
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    if candidate.is_file():
        return str(candidate)
    return str(current)


def launcher_script(paths: HostPaths) -> str:
    """生成 `.bat` 的内容。

    ⚠️ 三处细节都是踩过才知道的：

    - `@echo off`：不加的话每条命令都回显到 stdout ——**那会把协议流的长度前缀冲掉**。
    - `cd /d "%~dp0.."`：Chrome 的 cwd 是 `C:\\Windows\\System32` 之类，
      用相对路径找 `ar_host.py` 必然失败。先切到脚本所在目录的上一级（= 项目根）。
    - 末尾 `exit /b`：别让 `.bat` 之后的命令影响退出码。
    """
    host_script = paths.project_root / "ar_host.py"
    return (
        "@echo off\r\n"
        "rem 由 `ar host --register` 生成，请勿手改；重新注册会覆盖它。\r\n"
        f'cd /d "{paths.project_root}"\r\n'
        f'"{find_pythonw()}" "{host_script}"\r\n'
        "exit /b\r\n"
    )


def manifest_payload(paths: HostPaths) -> dict:
    """宿主清单：Chrome 只认这几个字段（多写会被忽略，但也不该乱写）。"""
    return {
        "name": HOST_NAME,
        "description": "account-research 采集入库（本地文件处理，零平台请求）",
        "path": str(paths.launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{paths.extension_id}/"],
    }


def write_files(paths: HostPaths) -> list[Path]:
    """写好 `.bat` 与清单 JSON，返回写过的路径。"""
    paths.launcher.parent.mkdir(parents=True, exist_ok=True)
    paths.launcher.write_text(launcher_script(paths), encoding="utf-8", newline="")
    paths.manifest.write_text(
        json.dumps(manifest_payload(paths), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return [paths.launcher, paths.manifest]


def current_registration(paths: HostPaths | None = None) -> str:
    """读注册表里当前的清单路径；没注册返回空串。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return ""
    key_path = paths.registry_key if paths else REGISTRY_KEY
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "")
            return str(value)
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def register(paths: HostPaths) -> list[Path]:
    """写文件 + 写注册表。返回写过的文件路径。"""
    written = write_files(paths)
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, paths.registry_key, 0,
                            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(paths.manifest))
    return written


def unregister(paths: HostPaths) -> list[str]:
    """删注册表项 + 删两个文件，返回实际删掉的东西（给人看，不是猜）。"""
    removed: list[str] = []
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, paths.registry_key)
        removed.append(f"注册表 {paths.registry_key}")
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover
        removed.append(f"删注册表失败：{exc}")
    for path in (paths.manifest, paths.launcher):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def diagnose(paths: HostPaths) -> list[tuple[bool, str]]:
    """逐项体检，返回 (是否通过, 说明)。

    每一项都对应一个「一旦不对就只有一片空白」的环节：
    Chrome 侧的失败信息很薄（`Specified native messaging host not found.`），
    不自己逐层查，人会以为扩展坏了。
    """
    checks: list[tuple[bool, str]] = []

    registered = current_registration(paths)
    checks.append((
        bool(registered),
        f"注册表指向：{registered}" if registered else
        f"注册表没有 {paths.registry_key}（跑 `ar host --register`）",
    ))

    manifest_ok = paths.manifest.is_file()
    detail = "宿主清单不存在"
    if manifest_ok:
        try:
            payload = json.loads(paths.manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            manifest_ok, detail = False, f"宿主清单不是合法 JSON：{exc}"
        else:
            origin = f"chrome-extension://{paths.extension_id}/"
            allowed = payload.get("allowed_origins") or []
            listed = str(payload.get("path") or "")
            if origin not in allowed:
                manifest_ok, detail = False, f"清单里没允许 {origin}"
            elif listed != str(paths.launcher):
                manifest_ok, detail = False, f"清单里的 path 不是 {paths.launcher}"
            else:
                detail = f"清单 OK，允许 {origin}"
    checks.append((manifest_ok, detail))

    launcher_ok = paths.launcher.is_file()
    detail = "启动脚本不存在"
    if launcher_ok:
        text = paths.launcher.read_text(encoding="utf-8", errors="replace")
        missing = [part for part in ("cd /d", "ar_host.py", "@echo off") if part not in text]
        if missing:
            launcher_ok, detail = False, f"启动脚本缺：{'、'.join(missing)}"
        else:
            detail = f"启动脚本 OK（{find_pythonw()}）"
    checks.append((launcher_ok, detail))

    script_ok = (paths.project_root / "ar_host.py").is_file()
    checks.append((script_ok, "ar_host.py 存在" if script_ok else "找不到 ar_host.py"))

    manifest_json = paths.project_root / "extension" / "manifest.json"
    key_ok = False
    detail = "扩展 manifest 不存在"
    if manifest_json.is_file():
        try:
            payload = json.loads(manifest_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            detail = f"扩展 manifest 不是合法 JSON：{exc}"
        else:
            key_ok = bool(payload.get("key")) and "nativeMessaging" in payload.get("permissions", [])
            detail = ("扩展 manifest 带 key 与 nativeMessaging"
                      if key_ok else "扩展 manifest 缺 key 或 nativeMessaging 权限")
    checks.append((key_ok, detail))

    return checks


def roundtrip(paths: HostPaths) -> tuple[bool, str]:
    """真的把宿主启起来握一次手——这是唯一能证明「Chrome 也能启起来」的检查。

    下面这些错，逐项体检**都查不出来**，但这里能：

    - `.bat` 用了 `python` 而不是 `pythonw`（能跑，但会闪黑框）；
    - `.bat` 里的路径有空格却没加引号；
    - 宿主启动时 `sys.stdout` 没切成二进制（Windows 换行翻译会毁掉长度前缀）；
    - 宿主一启动就把自己的横幅 print 到 stdout（毒化协议流）。
    """
    try:
        done = subprocess.run(
            [str(paths.launcher)], input=frame({"type": "hello", "id": "doctor"}),
            capture_output=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"启不起来：{type(exc).__name__}: {exc}"
    if not done.stdout:
        tail = (done.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        return False, f"宿主没有回应（stderr：{tail or '空'}）"
    try:
        reply = unframe(done.stdout)
    except ValueError as exc:
        return False, f"回包不是合法帧：{exc}（协议流被污染了？）"
    if not reply.get("ok"):
        return False, f"宿主回了错误：{reply}"
    return True, f"握手成功（协议 v{reply.get('protocol')}，块大小 {reply.get('chunk_bytes')}）"


def frame(payload: dict) -> bytes:
    """按 Chrome 的格式打包一条消息。"""
    import struct

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def unframe(data: bytes) -> dict:
    """从字节流里解出**第一条**消息（多了不管，少了报错）。"""
    import struct

    if len(data) < 4:
        raise ValueError("不足 4 字节，连长度前缀都不完整")
    (length,) = struct.unpack("<I", data[:4])
    body = data[4:4 + length]
    if len(body) < length:
        raise ValueError(f"声明 {length} 字节，实际只有 {len(body)}")
    return json.loads(body.decode("utf-8"))
