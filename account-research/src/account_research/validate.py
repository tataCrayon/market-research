"""把 SKILL.md 的硬信念编译成可执行校验（V1–V8）。

这是本产品唯一抄不走的部分：prompt 可以被复制，但这些拦截条件不会跟着提示词走。
每条规则都对应一条已有纪律，注释里写清出处。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import schema
from .store import read_table

# 「涨粉」类断言只能进假设表：公开数据拿不到后台涨粉，见 xiaohongshu-metrics.md §2
CAUSAL_TOKENS = ("涨粉", "关注转化")
LEDGER_DATE_COLUMN = "最近更新"


@dataclass(frozen=True)
class Issue:
    code: str
    level: str
    message: str


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "y")


def validate_dir(data_dir: str | Path, ledger: str | Path | None = None) -> list[Issue]:
    tables = {name: read_table(data_dir, name) for name in schema.TABLE_COLUMNS}
    return validate_tables(tables, ledger)


def validate_tables(tables: dict[str, list[dict[str, Any]]], ledger: str | Path | None = None) -> list[Issue]:
    """对「一张表在内存里的样子」做校验。

    为什么需要内存版：写库前要先审候选状态（校验通过才允许落盘），
    否则被拒收的行已经写进 append-only 表里，无法回滚。
    """
    issues: list[Issue] = []
    issues.extend(check_verdicts(tables.get("verdicts", [])))
    issues.extend(check_findings(tables.get("findings", [])))
    issues.extend(check_playbook(tables.get("playbook", [])))
    issues.extend(check_hypotheses(tables.get("hypotheses", [])))
    issues.extend(check_notes(tables.get("notes", []), tables.get("accounts", [])))
    if ledger:
        issues.extend(check_ledger(ledger))
    return issues


def check_notes(notes: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> list[Issue]:
    """V3 五件套完整性 + V7 归属不断裂。"""
    issues: list[Issue] = []
    known = {str(row.get("account_id") or "") for row in accounts}
    for row in notes:
        ref = f"{row.get('account_id')}/{row.get('note_id')}"
        if str(row.get("account_id") or "") not in known:
            issues.append(Issue("V7", "ERROR", f"{ref}: notes 里的 account 不在 accounts 表（归属断裂）"))
        if not str(row.get("source_kind") or "").strip() or not str(row.get("collected_on") or "").strip():
            issues.append(Issue("V3", "WARN", f"{ref}: 缺 source_kind 或 collected_on，该行不计入统计"))
        if not str(row.get("grade") or "").strip():
            issues.append(Issue("V3", "WARN", f"{ref}: 缺证据等级，该行不计入统计"))
    return issues


def check_verdicts(verdicts: list[dict[str, Any]]) -> list[Issue]:
    """V1 判决集合封闭 + V2 三件套非空 + V8 到期日不早于决定日。"""
    issues: list[Issue] = []
    for row in verdicts:
        ref = f"{row.get('account_id')}@{row.get('decided_on')}"
        verdict = str(row.get("verdict") or "").strip()
        if verdict not in schema.VERDICTS:
            issues.append(Issue("V1", "ERROR", f"{ref}: 判决「{verdict}」不在五选一集合内"))
        missing = [col for col in ("next_step", "cheapest_test", "abandon_signal") if not str(row.get(col) or "").strip()]
        if missing:
            issues.append(Issue("V2", "ERROR", f"{ref}: 缺三件套 {missing}（缺任一项 = 和稀泥判决）"))
        if str(row.get("due_on") or "") and str(row.get("decided_on") or "") > str(row.get("due_on") or ""):
            issues.append(Issue("V8", "ERROR", f"{ref}: due_on 早于 decided_on"))
    return issues


def check_findings(findings: list[dict[str, Any]]) -> list[Issue]:
    """V4 二手不得当结论 + V6 因果类断言不得进判决。"""
    issues: list[Issue] = []
    for row in findings:
        ref = str(row.get("finding_id") or "?")
        grade = str(row.get("grade") or "").strip()
        if not grade:
            issues.append(Issue("V4", "ERROR", f"{ref}: 缺证据等级"))
        elif grade not in schema.GRADES:
            issues.append(Issue("V4", "ERROR", f"{ref}: 证据等级「{grade}」不在 ①②③-a③-b⬜ 内"))
        if grade.startswith("③") and _truthy(row.get("used_in_verdict")):
            issues.append(Issue("V4", "ERROR", f"{ref}: ③ 级证据被用于判决（③ 只能当线索）"))
        statement = str(row.get("statement") or "")
        if _truthy(row.get("used_in_verdict")) and any(token in statement for token in CAUSAL_TOKENS):
            issues.append(Issue("V6", "ERROR", f"{ref}: 涨粉类断言不得进判决（公开数据不可取证，请改走 hypotheses 表）"))
    return issues


def check_playbook(rows: list[dict[str, Any]]) -> list[Issue]:
    """V5 打法必须有失效条件（死亡清单交叉纪律）。"""
    issues: list[Issue] = []
    for row in rows:
        ref = str(row.get("action_id") or "?")
        if not str(row.get("failure_condition") or "").strip():
            issues.append(Issue("V5", "ERROR", f"{ref}: 缺失效条件（等于「建议全抄」）"))
    return issues


def check_hypotheses(rows: list[dict[str, Any]]) -> list[Issue]:
    """假设表自身的完整性：状态封闭 + 实验与到期日必填。"""
    issues: list[Issue] = []
    for row in rows:
        ref = str(row.get("hypothesis_id") or "?")
        status = str(row.get("status") or "").strip()
        if status not in schema.HYPOTHESIS_STATUS:
            issues.append(Issue("V6", "ERROR", f"{ref}: 状态「{status}」不在 {schema.HYPOTHESIS_STATUS} 内"))
        for col in ("experiment", "due_on"):
            if not str(row.get(col) or "").strip():
                issues.append(Issue("V6", "ERROR", f"{ref}: 缺 {col}（不可验证的假设不许留在表里）"))
    return issues


def check_ledger(path: str | Path) -> list[Issue]:
    """V8 台账只追加：本次新行的日期不得早于已有末行日期。"""
    file_path = Path(path)
    if not file_path.is_file():
        return []
    dates: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", " ", ":"}:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("账号名",):
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2}", " ".join(cells[2:6]))
        if match:
            dates.append(match.group(0))
    if len(dates) < 2:
        return []
    issues: list[Issue] = []
    for previous, current in zip(dates, dates[1:]):
        if current < previous:
            issues.append(Issue("V8", "ERROR", f"台账日期倒挂：{previous} → {current}（只追加，不得改写历史行）"))
    return issues
