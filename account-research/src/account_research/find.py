"""`find`：跨表检索——「不能检索」的确定性答案。

## 它解决什么

PRD §1 把「不能问答」的根因写成「**没有可检索单元**：结论散落在 markdown 段落里」。
六张表落地之后，可检索单元已经有了，但**入口还没有**：想找「哪条结论说过公共性」
只能翻 CSV 或让模型读全库。

`ar find` 就是那个入口。它只做一件事：**按关键字与字段筛，把命中的行原样列出来，
每条带可回查的编号**。不做归纳、不做推断、不排序（N5 不评分不排名）。

## 三条口径

1. **查不到就写「库内无证据」**（PRD §5 F7：回答必须带 ≥1 条 finding 编号；
   查不到就答「库内无证据」）。绝不用相近结论凑答案——那正是「段落式问答」的老毛病。
2. **筛选项不许被静默忽略**。`--layer` 只有 findings 有这个字段，
   这时范围必须收窄到 findings **并在输出里说明**；两个互斥的筛选条件同时给 = 报错，
   因为悄悄丢掉其中一个，等于让人以为自己筛过两件事。
3. **正文 / 图上文字 / 评论留在文件里，不进表**——它们是长文本，塞进 CSV 会让
   表失去「一行一个可比较单元」的性质。所以文本检索是**显式开关**（`--text`），
   默认只搜表：命中范围一旦含糊，结论就会含糊。

## 为什么命中一定要带编号

`F-2`、`A-1`、`H-KZK-03`、`note_id` 是这套系统的**引用单元**。
一条搜出来的结论如果不带编号，读者既不能复核它，也不能在报告里引用它——
那和「散落在 markdown 段落里」没有区别。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import schema, store
from .images import image_text_path
from .matrix import CROSS_ACCOUNT_THRESHOLD, support_count

# 默认搜索范围。顺序即输出顺序：笔记在前，结论在后——
# 人搜东西时十次有九次是在找「哪一篇 / 哪一条说过这句话」。
TABLES = ("notes", "findings", "playbook", "hypotheses", "verdicts", "accounts", "fields")

# 每张表里参与匹配的列。**只列文本列**：把 `likes` 之类拉进来，
# 搜「500」会命中半个库，而那不是检索，是列出全部。
SEARCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "notes": ("title", "topic", "human_note"),
    "findings": ("statement", "applicability", "sample_basis", "evidence_ref"),
    "playbook": ("action", "mechanism", "requires", "failure_condition",
                 "counterexample", "evidence_ids"),
    "hypotheses": ("statement", "support", "counter", "experiment", "close_note"),
    "verdicts": ("verdict", "reason", "next_step", "cheapest_test", "abandon_signal"),
    "accounts": ("nickname", "red_id", "desc", "ip_location", "note"),
    # 口径登记表也要能搜：「涨粉到底能不能拿」这类问题的答案是**一条登记行**，
    # 不让它进检索，人就会去翻 CSV 或者问模型——后者正是 PRD 要消灭的东西。
    "fields": ("metric", "use_for", "note"),
}

# 命中之后「这一行说的是什么」——给输出用，不参与匹配。
DISPLAY_COLUMNS: dict[str, str] = {
    "notes": "title",
    "findings": "statement",
    "playbook": "action",
    "hypotheses": "statement",
    "verdicts": "reason",
    "accounts": "nickname",
    "fields": "use_for",
}

# 主键列。行没有主键就不该被检索出来——它无法被引用。
KEY_COLUMNS: dict[str, str] = {
    "notes": "note_id",
    "findings": "finding_id",
    "playbook": "action_id",
    "hypotheses": "hypothesis_id",
    "verdicts": "account_id",
    "accounts": "account_id",
    "fields": "metric",
}

# 每个筛选条件需要哪张表有这个字段。
# 这一张表就是「不许静默忽略」的实现：条件之间有交集才算能一起用。
FILTER_TABLES: dict[str, tuple[str, ...]] = {
    "layer": ("findings",),
    "form": ("notes",),
    "status": ("hypotheses",),
    "grade": ("notes", "findings"),
    "account": ("notes", "findings", "verdicts", "accounts"),
    "min_support": ("playbook",),
}

FILTER_COLUMNS: dict[str, str] = {
    "layer": "layer",
    "form": "form",
    "status": "status",
    "grade": "grade",
}

# 筛选条件给人看的名字。输错名字的提示里必须用它，不能把 `min_support` 直接抛给人。
FILTER_LABELS: dict[str, str] = {
    "layer": "--layer",
    "form": "--form",
    "status": "--status",
    "grade": "--grade",
    "account": "--account",
    "min_support": "--min-support",
}

NO_EVIDENCE = "库内无证据"

# 文本通道：三个文件各是一层，**分开标**——
# 「他写什么」（正文）、「图上印什么」（图上文字）、「读者说什么」（评论）
# 是三种证据，混成一个层就分不清引用的是哪一种。
TEXT_CHANNELS = (
    (".txt", "正文"),
    (".images.txt", "图上文字"),
    (".comments.txt", "评论"),
)


class FilterError(ValueError):
    """筛选条件组合不成立（不是运行时故障，是调用方式错）。"""


@dataclass(frozen=True)
class Hit:
    """一条命中。`where` 是可回查的位置，`key` 是要写进报告的引用编号。"""

    table: str
    key: str
    text: str
    where: str
    row: dict[str, Any] = field(default_factory=dict)
    layer: str = ""
    line: int = 0
    flags: tuple[str, ...] = ()


@dataclass
class SearchResult:
    keywords: list[str] = field(default_factory=list)
    tables: tuple[str, ...] = TABLES
    hits: list[Hit] = field(default_factory=list)
    scope_note: str = ""

    @property
    def empty(self) -> bool:
        return not self.hits


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _matches(text: str, keywords: Sequence[str]) -> bool:
    """多个关键字是 AND。OR 会把「AI」搜成半个库——检索的价值在缩小范围。"""
    haystack = text.lower()
    return all(word.lower() in haystack for word in keywords)


def _flags(table: str, row: dict[str, Any]) -> tuple[str, ...]:
    """命中当场要标出来的纪律问题。它们都是校验器会拒、但检索时容易被忽略的项。"""
    flags: list[str] = []
    if table == "findings":
        grade = _text(row.get("grade"))
        used = _text(row.get("used_in_verdict")) in ("1", "true", "是")
        if grade.startswith("③") and used:
            # V4：③ 级（二手）当结论用 = 二手来源被写成了证据。
            flags.append("V4：③ 级结论进了判决（二手不得当结论）")
    if table == "playbook":
        if not _text(row.get("failure_condition")):
            flags.append("V5：缺失效条件（等于「建议全抄」）")
        support = support_count(row.get("supporting_accounts"))
        if support < CROSS_ACCOUNT_THRESHOLD:
            flags.append(
                f"支撑账号 {support} < {CROSS_ACCOUNT_THRESHOLD}："
                f"≥{CROSS_ACCOUNT_THRESHOLD} 才算跨账号验证过的路径，这条是个案"
            )
    if table == "notes":
        missing = [label for column, label in
                   (("collects", "藏"), ("comments", "评"))
                   if not _text(row.get(column))]
        if missing:
            flags.append(f"⬜ 缺 {'/'.join(missing)}（没采到，不是 0）")
    return tuple(flags)


def _scope(
    requested_tables: Sequence[str] | None,
    filters: dict[str, Any],
    layer: str,
) -> tuple[tuple[str, ...], str]:
    """算出实际范围，并返回一句「为什么收窄」。

    互斥的筛选条件在这里被拒：`--layer` 属于 findings、`--status` 属于 hypotheses，
    同时给意味着范围为空——静默丢掉一个，用户会以为两件事都筛过了。
    """
    allowed = {name for name, value in filters.items() if value not in ("", None)}
    if not allowed:
        return tuple(requested_tables or TABLES), ""

    needed: set[str] | None = None
    for name in allowed:
        tables = set(FILTER_TABLES[name])
        needed = tables if needed is None else needed & tables
        if not needed:
            break

    if not needed:
        parts = "、".join(
            f"{FILTER_LABELS[name]} 只在 {'/'.join(FILTER_TABLES[name])}" for name in sorted(allowed)
        )
        raise FilterError(
            f"这些筛选条件不能同时用：{parts}。"
            f"同时用意味着没有任何一张表同时满足它们——范围是空的，不是「筛出 0 条」。"
        )

    chosen = tuple(requested_tables or TABLES)
    outside = [name for name in chosen if name not in needed]
    scope = tuple(name for name in chosen if name in needed)
    if not scope:
        raise FilterError(
            f"--table {'/'.join(chosen)} 里没有 {('、'.join(FILTER_LABELS[name] for name in sorted(allowed)))}"
            f" 能用的字段；它们只在 {'/'.join(sorted(needed))}。"
        )
    note = ""
    if outside:
        note = (
            f"范围收窄到 {'/'.join(scope)}："
            f"{'、'.join(FILTER_LABELS[name] for name in sorted(allowed))} 只在这些表里有字段，"
            f"其余表（{'/'.join(outside)}）没有该字段，**不是没有命中**。"
        )
    return scope, note


def _row_matches(table: str, row: dict[str, Any], keywords: Sequence[str],
                 filters: dict[str, Any], layer: str) -> bool:
    if not keywords:
        pass  # 纯筛选：不写关键字就是列子集
    else:
        haystack = "\n".join(_text(row.get(column)) for column in SEARCH_COLUMNS[table])
        if not _matches(haystack, keywords):
            return False
    if layer and table == "findings" and _text(row.get("layer")) != layer:
        return False
    for name, column in FILTER_COLUMNS.items():
        wanted = filters.get(name)
        if not wanted:
            continue
        if name == "grade":
            # 等级有三个三级值（③-a / ③-b / ③），人不会记全，所以按前缀匹配。
            if not _text(row.get(column)).startswith(str(wanted)):
                return False
        elif _text(row.get(column)) != str(wanted):
            return False
    account = filters.get("account")
    if account and table not in ("notes", "findings", "verdicts", "accounts"):
        return False
    if account and _text(row.get("account_id")) != str(account):
        return False
    return True


def _rows(data_dir: Path, table: str) -> list[dict[str, Any]]:
    rows = store.read_table(data_dir, table)
    if table == "notes":
        # 同一篇有多条通道时先合并：否则一个命中被数两遍，还会读到旧观测的数字。
        return store.latest_per_note(rows)
    if table == "hypotheses":
        # 结案是追加行：旧状态是历史，检索要的是「现在是什么」。
        return store.latest_hypotheses(rows)
    return rows


# 这几张表检索的是**折叠后的视图**（按 note_id / hypothesis_id 合并过），
# 所以行号对不上 CSV 里的物理行。位置只写表名，回查靠主键——
# 写一个错的行号比不写更坏：人会照着去翻，然后发现那里是另一条记录。
MERGED_TABLES = ("notes", "hypotheses")


def _text_hits(text_dir: Path, note_ids: Iterable[str], keywords: Sequence[str],
               account_of: dict[str, str], filters: dict[str, Any],
               limit_per_file: int = 5) -> list[Hit]:
    hits: list[Hit] = []
    account = filters.get("account")
    for note_id in sorted(note_ids):
        if account and account_of.get(note_id) != str(account):
            continue
        for suffix, label in TEXT_CHANNELS:
            path = image_text_path(text_dir, note_id) if suffix == ".images.txt" \
                else text_dir / f"{note_id}{suffix}"
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            if not keywords:
                hits.append(Hit(
                    table="text", key=note_id, text=f"（{label}，{len(lines)} 行）",
                    where=f"{path.name}", layer=label, row={"note_id": note_id},
                ))
                continue
            found = 0
            for line_no, line in enumerate(lines, start=1):
                if not line.strip() or not _matches(line, keywords):
                    continue
                hits.append(Hit(
                    table="text", key=note_id, text=line.strip()[:300],
                    where=f"{path.name}:{line_no}", layer=label, line=line_no,
                    row={"note_id": note_id},
                ))
                found += 1
                if found >= limit_per_file:
                    break
    return hits


def search(
    data_dir: str | Path,
    keywords: Sequence[str],
    *,
    tables: Sequence[str] | None = None,
    text_dir: str | Path | None = None,
    text: bool = False,
    layer: str = "",
    form: str = "",
    status: str = "",
    grade: str = "",
    account: str = "",
    min_support: int | None = None,
) -> SearchResult:
    """检索哪些行命中。**纯函数**：同库同参数永远得到同一结果。"""
    data_dir = Path(data_dir)
    words = [str(word).strip() for word in keywords if str(word).strip()]
    filters = {
        "layer": layer, "form": form, "status": status,
        "grade": grade, "account": account, "min_support": min_support,
    }
    any_filter = any(value not in ("", None) for value in filters.values())
    if not words and not any_filter and not text:
        raise FilterError(
            "至少给一个关键字，或用筛选条件（--layer / --form / --status / --grade / "
            "--account / --min-support）收窄范围。列出整库不是检索。"
        )

    scope, note = _scope(tables, filters, layer)
    if not words and not any_filter:
        # 只给了 `--text`：那就只列文本文件。让整库的表行一起涌出来，
        # 输出会变成「全库清单」，而那不是这次查询要的东西。
        scope = ()
    hits: list[Hit] = []
    for table in scope:
        for index, row in enumerate(_rows(data_dir, table), start=2):  # 2 = 第一个数据行
            if table == "playbook" and min_support is not None:
                # 支撑账号数是**文本**（`1（知新的AI笔记）`），解析与 matrix 共用同一个函数。
                if support_count(row.get("supporting_accounts")) < int(min_support):
                    continue
            if not _row_matches(table, row, words, filters, layer):
                continue
            key = _text(row.get(KEY_COLUMNS[table]))
            if not key:
                continue                       # 无主键的行无法被引用，不出现在结果里
            hits.append(Hit(
                table=table,
                key=key,
                text=_text(row.get(DISPLAY_COLUMNS[table]))[:300],
                where=(f"{table}.csv" if table in MERGED_TABLES else f"{table}.csv:{index}"),
                row=dict(row),
                layer=_text(row.get("layer")),
                flags=_flags(table, row),
            ))

    hits.sort(key=lambda hit: (TABLES.index(hit.table) if hit.table in TABLES else 99, hit.key))

    if text:
        if text_dir is None:
            raise FilterError("--text 需要正文目录（--text-dir 或默认 account/notes-text）。")
        note_rows = _rows(data_dir, "notes")
        if form:
            note_rows = [row for row in note_rows if _text(row.get("form")) == str(form)]
        account_of = {_text(row.get("note_id")): _text(row.get("account_id"))
                      for row in note_rows}
        hits.extend(_text_hits(
            Path(text_dir), list(account_of), words, account_of, filters,
        ))

    return SearchResult(keywords=words, tables=scope, hits=hits, scope_note=note)


def render(result: SearchResult, limit: int = 0) -> str:
    """给人看的输出。**只陈述命中**，不加任何评价或推测。"""
    lines: list[str] = []
    query = " ".join(result.keywords) or "（无关键字，按筛选列出）"
    lines.append(f"检索：{query}")
    lines.append(f"范围：{'、'.join(result.tables)}" + ("（+ 正文文件）" if any(
        hit.table == "text" for hit in result.hits) else ""))
    if result.scope_note:
        lines.append(f"⚠️ {result.scope_note}")
    lines.append("")

    if result.empty:
        lines.append(f"{NO_EVIDENCE}。")
        lines.append("")
        lines.append("这不是「大概没有」，是检索范围内的表里没有匹配行。下一步只能是：")
        lines.append("  · `ar status` 看库里到底有什么")
        lines.append("  · 换关键字，或用 `--layer/--form/--status` 按字段筛")
        lines.append("  · 结论还不存在时先跑 `ar debrief --account <id>`")
        return "\n".join(lines)

    shown = result.hits[:limit] if limit else result.hits
    current = ""
    for hit in shown:
        if hit.table != current:
            current = hit.table
            lines.append(f"── {_label(current)} ──")
        layer = f"[{hit.layer}] " if hit.layer else ""
        line = f"  {hit.key}  {layer}{hit.text}   ← {hit.where}"
        lines.append(line)
        for flag in hit.flags:
            lines.append(f"      ⚠️ {flag}")
    lines.append("")
    if limit and len(result.hits) > limit:
        lines.append(f"（共 {len(result.hits)} 条，只显示前 {limit} 条；--limit 0 看全部）")
    lines.append(f"共 {len(result.hits)} 条。引用时用表内编号（findings 的 F-*、playbook 的 A-*、"
                 f"假设的 H-*、笔记的 note_id）——它们能回查到具体行。")
    return "\n".join(lines)


def _label(table: str) -> str:
    return {
        "notes": "笔记（notes）",
        "findings": "结论（findings）",
        "playbook": "打法（playbook）",
        "hypotheses": "假设（hypotheses）",
        "verdicts": "判决（verdicts）",
        "accounts": "账号（accounts）",
        "text": "正文文件",
    }.get(table, table)


def as_payload(result: SearchResult) -> dict[str, Any]:
    """`--json`：给下游（模型 / 页面）消费。字段与 `Hit` 一一对应，不加工。"""
    return {
        "query": result.keywords,
        "tables": list(result.tables),
        "scope_note": result.scope_note,
        "count": len(result.hits),
        "evidence": bool(result.hits),
        "hits": [
            {
                "table": hit.table,
                "key": hit.key,
                "layer": hit.layer,
                "text": hit.text,
                "where": hit.where,
                "line": hit.line,
                "flags": list(hit.flags),
            }
            for hit in result.hits
        ],
    }


def schema_tables() -> tuple[str, ...]:
    """可检索的表。与 `schema.TABLE_COLUMNS` 对齐，防止有人加表忘了加检索。"""
    return tuple(name for name in TABLES if name in schema.TABLE_COLUMNS)
