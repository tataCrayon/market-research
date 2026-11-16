"""假设结案：把「到期未验」从一句空话变成一条能执行的命令。

## 为什么必须单独做一个命令

2026-11-12 实测：`ar status` 的 D2 告警连喊了 10 天「证伪或证实都算结案，
唯独不能继续挂着『未验证』」，而**命令行里没有一条路能做到**：

```
$ ar add hypotheses --file h.json     # 想把 status 改成「已证伪」
hypotheses：新增 0 行（校验先于落盘 ✅）   ← 键已存在，append-only 静默跳过
$ ar --help | grep -i close
（无）
```

PRD §4.1 还写着「到期未验 → 自动降 `已证伪（超期未验）`」——那个值既不在枚举里，
V6 会直接拒。**三条路全是死路，而告警每天照响。**

这是 E25/E26 同源的第四次复发：纪律写在提示里，执行面缺一块，于是守纪律的唯一方式
是手改 CSV——而手改 CSV 是明令禁止的（「写入口唯一」）。

## 为什么用「追加一行」而不是「改原来那行」

假设表的键原先是 `hypothesis_id`，一条假设终生一行。改成 `(hypothesis_id, recorded_on)`
之后，结案 = 追加一行新状态：

- 历史状态留在表里（那是证据：它确实在 11-02 之前一直「未验证」）
- 「现在是什么状态」由 `store.latest_hypotheses` 按 `recorded_on` 折叠
- 与判决表（`account_id + decided_on` 追加二判）是同一个模式

**没有第二个模式可选**：原地改会让 append-only 失去意义，而 append-only 的存在理由
是「历史观测不能被后来的认知覆盖」。

## 为什么「超期未验」是独立状态而不是塞进「已证伪」

没做实验 ≠ 被证伪。前者是「我不知道」，后者是「我知道它不对」。
把两者写成同一个词，就是在结案的同时编了一个结论。
"""

from __future__ import annotations

from typing import Any

from . import schema

# 允许用来结案的状态。`未验证 / 验证中` 永远不在此列——
# 用它们「结案」只是把告警重新点亮一次，等于什么都没做。
CLOSING_STATUSES = schema.HYPOTHESIS_CLOSED

# 必须写理由才能结案的状态。`超期未验` 是「我没做」——
# 不写原因的话，三个月后没人记得这条假设是被放弃了还是被忘了。
NOTE_REQUIRED = ("超期未验",)


class CloseError(ValueError):
    """结案请求本身不合法（不是运行时故障）。"""


def close_hypothesis(
    rows: list[dict[str, Any]],
    hypothesis_id: str,
    status: str,
    note: str = "",
    on: str = "",
) -> dict[str, Any]:
    """生成一条**新的**结案记录（纯函数，不落盘）。

    返回的行是「原最新行 + 新状态 + recorded_on + close_note」，
    调用方负责过校验再写。**不改任何已有行**。

    拒绝的情况都显式抛出，不静默返回空行——静默是这套系统里最贵的失败模式
    （`ar add` 那次「新增 0 行」看起来像成功）。
    """
    from .store import latest_hypotheses

    hypothesis_id = str(hypothesis_id or "").strip()
    if not hypothesis_id:
        raise CloseError("必须给 hypothesis_id。")

    status = str(status or "").strip()
    if status not in CLOSING_STATUSES:
        raise CloseError(
            f"「{status}」不是结案状态。可用的只有：{' / '.join(CLOSING_STATUSES)}。"
            f"用「未验证 / 验证中」结案只是把告警重新点亮，等于没做。"
        )

    note = str(note or "").strip()
    if status in NOTE_REQUIRED and not note:
        raise CloseError(
            f"「{status}」必须写 --note：说明为什么没做实验。"
            f"不写原因的话，三个月后没人分得清这条假设是被放弃了还是被忘了。"
        )

    latest = {str(row.get("hypothesis_id")): row for row in latest_hypotheses(rows)}
    current = latest.get(hypothesis_id)
    if current is None:
        known = "、".join(sorted(latest)) or "（空）"
        raise CloseError(f"hypotheses 表里没有「{hypothesis_id}」。现有：{known}")

    old_status = str(current.get("status") or "").strip()
    if old_status == status:
        raise CloseError(
            f"「{hypothesis_id}」已经是「{status}」，无需重复结案。"
            f"（重复写会多一条无信息的新行，而表是只追加的——删不掉。）"
        )

    # 同一天第二次结案：键 `(hypothesis_id, recorded_on)` 会撞，`preview_append`
    # 会**静默跳过**——而 `close` 打印的却是「新增 1 行」。这种「看起来成功其实没写」
    # 正是本次要消灭的失败模式，所以在命令层就拦住，让人改 `--on`。
    same_day = [
        row for row in rows
        if str(row.get("hypothesis_id")) == hypothesis_id
        and str(row.get("recorded_on") or "").strip() == str(on or "").strip()
        and str(on or "").strip()
    ]
    if same_day:
        raise CloseError(
            f"「{hypothesis_id}」在 {on} 已经有一条结案记录（状态「{same_day[-1].get('status')}」）。"
            f"同一天只能结案一次——表键是 (hypothesis_id, recorded_on)，再写会被静默跳过。"
            f"要改判请换一天，或先撤销那行（需手工处理，表是只追加的）。"
        )

    closed = dict(current)
    closed["status"] = status
    closed["recorded_on"] = str(on or "").strip()
    closed["close_note"] = note
    return closed
