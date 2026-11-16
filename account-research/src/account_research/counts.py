"""平台计数归一化：`1.2万` / `1.4亿` / `1.2K` / `3,567` → 整数。

**为什么单开一个模块**：这套换算有三个调用方（详情页 DOM、主页 state、统计口径），
散着写必然漂——`stats._int_or_none` 与 `parser.as_int` 已经是两份同义实现，
再加一份「万」的处理就是三份。漂开之后最坏的结果不是报错，是**同一篇笔记在两个
视图里显示不同的赞数**。

**最重要的一条纪律**：`万` 是**圆整值**，不是精确值。`1.2万` 的真实值可能是 11,500，
也可能是 12,499——把它当 12000 写进报告，等于用三位有效数字冒充四位。
所以这里返回 `(值, 是否圆整)`，调用方必须知道自己在用哪一种：
- 排序、分档、画图 → 用换算值，无所谓；
- 报告里逐篇引用的「精确赞数」→ 圆整值必须标 `≈`，或标 ⬜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 中文平台用「万 / 亿」，英文界面/JSON 里偶见 K / M / B（大小写都收）。
# `万亿` 不是「1万亿」，平台是「1.2万」这种单后缀，所以按最长后缀优先匹配。
_UNITS = (
    ("万亿", 10 ** 12),
    ("亿", 10 ** 8),
    ("万", 10 ** 4),
    ("K", 10 ** 3),
    ("k", 10 ** 3),
    ("M", 10 ** 6),
    ("m", 10 ** 6),
    ("B", 10 ** 9),
    ("b", 10 ** 9),
)
# 允许「1.2万」「1,234」「1234」「1.2 万」「3.4K」；不接受负号与正号（计数不该为负）
_NUMBER_RE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s*([万亿KkMmBb]{0,2})\s*$")


@dataclass(frozen=True)
class Count:
    """一个平台计数。`value` 是换算后的整数，`approx` 表示原文是圆整值。"""

    value: int
    approx: bool = False
    raw: str = ""
    # 圆整值的可能区间：`1.2万` 的真实值在 [12000, 13000) 里，`1.25万` 在 [12500, 12600) 里。
    # 精确值的 `low == high == value`。区间是判断「两条通道是不是在说同一件事」的依据。
    low: int = 0
    high: int = 0

    def __str__(self) -> str:
        return self.text()

    def text(self, mark: str = "≈") -> str:
        """给报告用的写法：圆整值前面加 `mark`，精确值原样。"""
        return f"{mark}{self.value:,}" if self.approx else f"{self.value:,}"

    @property
    def span(self) -> int:
        """这个数字有多少不确定性（精确值 = 0）。报告里该不该标 ≈ 由它决定。"""
        return max(0, self.high - self.low)


def parse_count(value: Any) -> Count | None:
    """解析成 `Count`；解析不了返回 None（= ⬜，绝不补齐、绝不猜）。"""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    match = _NUMBER_RE.match(raw)
    if not match:
        return None
    number, unit = match.group(1).replace(",", ""), match.group(2)
    try:
        base = float(number)
    except ValueError:
        return None
    multiplier = 1
    if unit:
        matched = next((scale for suffix, scale in _UNITS if suffix == unit), None)
        if matched is None:
            return None
        multiplier = matched
    if multiplier == 1:
        # 纯数字：`1234` 是精确值；`1200.0` 这种也当精确值处理（平台不会这么渲染）
        exact = int(base)
        return Count(value=exact, approx=False, raw=raw, low=exact, high=exact)
    value_int = int(round(base * multiplier))
    decimals = len(number.split(".", 1)[1]) if "." in number else 0
    step = max(1, int(round(multiplier / (10 ** decimals))))
    return Count(value=value_int, approx=True, raw=raw, low=value_int, high=value_int + step - 1)


def consistent(left: Count | None, right: Count | None) -> bool:
    """两个计数是不是在说同一件事——**允许一边是圆整值**。

    `1.2万` 与 `12345` 不相等，但它们的可能区间重叠，说明这是同一篇笔记的两条通道
    读数，不该报「冲突」。早先按 `value == value` 判，会把每一篇破万笔记都标成冲突，
    而冲突报多了就等于没有冲突——这条纪律在 E12 里已经付过一次学费。
    """
    if left is None or right is None:
        return False
    return left.low <= right.high and right.low <= left.high


def to_count(value: Any) -> int | None:
    """只要整数，不要圆整标记。用于排序、分档、求和。

    排序场景下「1.2万 排在哪里」不需要精确到个位——但**报告里引用**必须走
    `parse_count` 拿到 `approx`，否则 11,500 会被写成 12,000。
    """
    parsed = parse_count(value)
    return parsed.value if parsed else None


def is_approx(value: Any) -> bool:
    """这个格子的原文是不是圆整值（`1.2万` 这种）。空值/解析失败一律 False。"""
    parsed = parse_count(value)
    return bool(parsed and parsed.approx)
