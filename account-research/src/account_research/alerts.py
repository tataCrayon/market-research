"""纪律告警：把「到期该复核」「台账断流」从纸面变成会响的东西。

**为什么需要这个模块**：SKILL.md 里写了两条硬性纪律，但一行代码都没有：

1. 判决与假设都带 `due_on`，「到期二判 / 降级」写进了验证清单；
2. 持续追踪写「台账 >10 天无追加 → 月报显式写『追踪中断』，禁静默断流」。

实测代价（2026-11-04 盘点）：库里两条判决一条假设早已过期，`ar status` 一个字都没提。
**纪律只活在自然语言里 = 没有纪律**——这是本项目反复付学费的那条老毛病（E10 / E11）。

## 为什么不能只判 `due_on < today`

那会把「已经被新判决替代的旧判决」也一起报出来。实测就有一条：
10-26 的判决 due 11-02 已过，但 11-01 已经有新判决替代它——它**不是欠账，是历史**。
把它们一起报，就是制造噪音；噪音多了，真欠账也一起被忽略。
这是 E12 那条教训的直接应用：**闸门的第一职责是不误杀**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# 台账漏采多久算断流。SKILL.md「持续追踪」定的是 10 天；写死在这里并随告警一起打印，
# 免得读者去猜这个阈值从哪来。
LEDGER_STALE_DAYS = 10

# 台账里「最近更新」是哪一列（0 起）。**不能用 `cells[2:6]` 这种宽窗口找日期**：
# 第 3 列是「粉丝区间（观察值+日期）」，里面写着 `14.4万（2026-11-04 公开页）`——
# 那是**观察值日期**，不是台账更新时间。宽窗口取第一个匹配就会取到它，
# 于是断流判断永远触发不了（观察值通常比台账更新时间新），自欺且静默。
# 实测就是这么踩的：`test_date_comes_from_update_column_not_fans_column` 抓到的。
# 列名优先、下标兜底：表头改了仍能对，表头没了就退回约定位置。
LEDGER_UPDATED_COLUMN = 5
LEDGER_UPDATED_NAME = "最近更新"

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _ledger_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    return set(line.replace("|", "").strip()) <= {"-", " ", ":"}


def ledger_dates(path: str | Path) -> list[str]:
    """台账里每个数据行的「最近更新」日期（按行序）。

    只认「最近更新」一列：粉丝区间里的日期是观察值日期，来源链接里的日期是查证日期，
    三者都不同，混用会让断流判断失真。
    """
    file_path = Path(path)
    if not file_path.is_file():
        return []
    column = LEDGER_UPDATED_COLUMN
    dates: list[str] = []
    header_resolved = False
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        if _is_separator(line):
            continue
        cells = _ledger_cells(line)
        if not header_resolved and cells and cells[0] == "账号名":
            # 表头：按列名定位，找不到就沿用约定下标
            if LEDGER_UPDATED_NAME in cells:
                column = cells.index(LEDGER_UPDATED_NAME)
            header_resolved = True
            continue
        if column >= len(cells):
            continue
        found = DATE_RE.search(cells[column])
        if found:
            dates.append(found.group(0))
    return dates


@dataclass(frozen=True)
class Alert:
    """一条告警。`level` 只分两档：该处理的，和只是提醒。"""

    code: str
    level: str          # "warn" | "info"
    message: str


def _days_between(start: str, end: str) -> int | None:
    """两个 YYYY-MM-DD 差几天。格式不对返回 None，**不猜**。"""
    from datetime import date

    try:
        first = date.fromisoformat(start)
        second = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    return (second - first).days


def overdue_verdicts(rows: Iterable[dict[str, Any]], today: str) -> list[Alert]:
    """到期未复核的判决。

    「已被替代」的判定：同一个 `account_id` 存在 `decided_on` 更晚的判决。
    那种行不报——它是历史，不是欠账。同理，`due_on` 为空的也不报（没约定就没有违约）。
    """
    verdicts = [row for row in rows if str(row.get("account_id") or "").strip()]
    latest_by_account: dict[str, str] = {}
    for row in verdicts:
        account = str(row["account_id"])
        decided = str(row.get("decided_on") or "")
        if decided > latest_by_account.get(account, ""):
            latest_by_account[account] = decided

    alerts: list[Alert] = []
    for row in verdicts:
        due = str(row.get("due_on") or "").strip()
        if not due:
            continue
        account = str(row["account_id"])
        decided = str(row.get("decided_on") or "")
        if decided < latest_by_account.get(account, ""):
            continue                      # 已被更新的判决替代
        days = _days_between(due, today)
        if days is None or days <= 0:
            continue
        alerts.append(Alert(
            "D1", "warn",
            f"判决到期未复核：{account}（{decided} 判「{row.get('verdict')}」）"
            f"约定 {due} 复核，已过期 {days} 天。按纪律应二判或降级 —— "
            f"要么补数据重判，要么改成 ⏸/❌ 并写清换对标方向。",
        ))
    return alerts


def overdue_hypotheses(rows: Iterable[dict[str, Any]], today: str) -> list[Alert]:
    """到期仍停在「未验证 / 验证中」的假设。

    `已证伪 / 已证实 / 超期未验` 不报：它们已经结案。假设表的用处就是逼人给实验设期限——
    期限过了还挂着「未验证」，等于这条假设从来没进过实验。

    **必须先按 `hypothesis_id` 折叠到最新一次记录**（2026-11-12 起）：
    结案是追加一行新状态，旧行仍留在表里（那是证据）。拿全表逐行判的话，
    一条已结案的假设会因为它的旧「未验证」行继续被报出来——
    **结案了却还在响**，比不结案更让人不信任告警。
    """
    from .store import latest_hypotheses

    alerts: list[Alert] = []
    for row in latest_hypotheses(list(rows)):
        status = str(row.get("status") or "").strip()
        if status not in ("未验证", "验证中"):
            continue
        due = str(row.get("due_on") or "").strip()
        days = _days_between(due, today) if due else None
        if days is None or days <= 0:
            continue
        alerts.append(Alert(
            "D2", "warn",
            f"假设到期未结案：{row.get('hypothesis_id')}（状态「{status}」）"
            f"约定 {due} 验证完，已过期 {days} 天。证伪或证实都算结案，"
            f"**唯独不能继续挂着「{status}」** —— 那和没提这条假设一样。"
            f"结案用：`ar close hypothesis {row.get('hypothesis_id')} --status 超期未验`",
        ))
    return alerts


def ledger_staleness(path: str | Path, today: str) -> list[Alert]:
    """台账多久没追加了。超过阈值就报——**断流要显式说出来，不能靠人想起来**。"""
    dates = ledger_dates(path)
    if not dates:
        return [Alert("D3", "warn", f"台账读不到任何日期：{path}（是真的空，还是路径不对？）")]
    last = max(dates)
    days = _days_between(last, today)
    if days is None or days <= LEDGER_STALE_DAYS:
        return []
    return [Alert(
        "D3", "warn",
        f"台账断流：最后一条 {last}，已 {days} 天没追加（阈值 {LEDGER_STALE_DAYS} 天）。"
        f"按纪律「追踪中断」必须**写进报告**，不许静默——"
        f"要么补一次采集，要么在报告里明写这段时间没追。",
    )]


def collect(
    verdicts: Iterable[dict[str, Any]],
    hypotheses: Iterable[dict[str, Any]],
    ledger: str | Path | None,
    today: str,
) -> list[Alert]:
    """所有纪律告警，按「欠得越久越靠前」排。"""
    alerts = overdue_verdicts(verdicts, today) + overdue_hypotheses(hypotheses, today)
    if ledger:
        alerts += ledger_staleness(ledger, today)
    return alerts
