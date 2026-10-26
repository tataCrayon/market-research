#!/usr/bin/env python3
"""CLI 启动器：不安装包也能跑。

用法：
    python account-research/ar.py probe  account/.../卡兹克-主页-20261022.html
    python account-research/ar.py selftest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from account_research.cli import main  # noqa: E402  (路径注入必须先执行)

if __name__ == "__main__":
    sys.exit(main())
