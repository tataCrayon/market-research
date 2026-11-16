#!/usr/bin/env python3
"""原生消息宿主的启动器：给 Chrome 用的入口（`ar host --register` 会指向这里）。

**为什么单独一个文件，而不是让 `ar.py` 兼任**：
Chrome 启动宿主时**不传任何参数**、cwd 也不在项目里。`ar.py` 需要一个子命令才知道做什么，
而这里什么都不需要——它唯一的职责就是「进入协议循环」。

另外它刻意**不捕获异常**：宿主崩了必须留下 traceback（Chrome 会把 stderr 写进
`chrome://extensions` 的日志），静默退出会让「点了没反应」再次发生。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from account_research import host  # noqa: E402  (路径注入必须先执行)

if __name__ == "__main__":
    sys.exit(host.main())
