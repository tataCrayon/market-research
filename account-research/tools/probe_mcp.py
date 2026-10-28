"""最小 MCP 客户端：验证 agent-browser 的 mcp server 能正常握手并列出工具。

只发 initialize / tools/list 两个请求，不调用任何工具、不发动任何页面。
"""
from __future__ import annotations

import json
import subprocess
import sys

NODE = r"D:\DevEnv\node-v24.18.0-win-x64\node.exe"
BIN = r"D:\DevEnv\node-v24.18.0-win-x64\node_global\node_modules\agent-browser\bin\agent-browser.js"


def main() -> int:
    proc = subprocess.Popen(
        [NODE, BIN, "mcp", "--tools", "core"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )
    assert proc.stdin and proc.stdout

    def send(payload: dict) -> None:
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def read_line() -> dict:
        while True:
            line = proc.stdout.readline()
            if not line:
                raise SystemExit("server closed stdout")
            line = line.strip()
            if line:
                return json.loads(line)

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "ar-probe", "version": "0.1"},
    }})
    init = read_line()
    server = init.get("result", {}).get("serverInfo", {})
    print("serverInfo:", json.dumps(server, ensure_ascii=False))

    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = read_line()
    names = [t["name"] for t in tools.get("result", {}).get("tools", [])]
    print(f"tools ({len(names)}):")
    for name in names:
        print("  -", name)

    proc.stdin.close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
