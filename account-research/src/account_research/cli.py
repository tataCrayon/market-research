"""CLI 入口：probe / parse / validate / stats / selftest。

设计原则（方向卡「确定性优先」）：能用代码算的都放在这里，模型只负责拆解推理。
任何命令都不访问网络、不读写 account/ 以外的地方。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from . import schema, store
from .initialstate import REJECT_REASONS, ArchiveError, iter_note_cards, open_archive
from .parser import parse_account, parse_blocks, parse_notes
from .stats import summarize
from .validate import validate_dir, validate_tables

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = REPO_ROOT / "account-research" / "data"
DEFAULT_LEDGER = REPO_ROOT / "account-research" / "skill" / "账号库台账.md"
DEFAULT_ARCHIVE = REPO_ROOT / "account" / "数字生命卡兹克"
DEFAULT_FIXTURE = REPO_ROOT / "account-research" / "tests" / "fixtures" / "expected_kazike_20261022.json"
CANONICAL = "卡兹克-主页-20261022.html"
SEARCH = "卡兹克-搜索-20261022.html"
SCROLLS = ("主页快照-滚动位1-20261022.html", "主页快照-滚动位2-20261022.html", "主页快照-滚动位3-20261022.html")


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，打印 ✅/⬜ 会直接崩——入口处强制 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def today() -> str:
    return datetime.now(timezone(timedelta(hours=schema.TZ_OFFSET_HOURS))).strftime("%Y-%m-%d")


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ar", description="account-research CLI（本地文件处理，零平台请求）")
    sub = root.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="判定存档页面类型与归属（3 秒级探针）")
    probe.add_argument("files", nargs="+")
    probe.set_defaults(func=cmd_probe)

    parse = sub.add_parser("parse", help="主页存档 → data/ 下的 accounts + notes 表")
    parse.add_argument("files", nargs="+")
    parse.add_argument("--data", default=str(DEFAULT_DATA))
    parse.add_argument("--on", default=today(), help="采集日期（写入五件套），默认今天")
    parse.set_defaults(func=cmd_parse)

    check = sub.add_parser("validate", help="跑 V1–V8 校验")
    check.add_argument("--data", default=str(DEFAULT_DATA))
    check.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    check.set_defaults(func=cmd_validate)

    stats = sub.add_parser("stats", help="确定性统计（篇均/中位/形式/爆文率）")
    stats.add_argument("--data", default=str(DEFAULT_DATA))
    stats.add_argument("--account", default="")
    stats.add_argument("--k", default="3,5", help="爆文率 K 值，逗号分隔")
    stats.set_defaults(func=cmd_stats)

    selftest = sub.add_parser("selftest", help="对真实存档跑回归（account/ 缺失则 SKIP）")
    selftest.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    selftest.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    selftest.set_defaults(func=cmd_selftest)

    add = sub.add_parser("add", help="按表写入结论行（唯一合法写入口，写完立即校验）")
    add.add_argument("table", choices=sorted(schema.TABLE_COLUMNS))
    add.add_argument("--file", required=True, help="JSON：一个对象或对象数组")
    add.add_argument("--data", default=str(DEFAULT_DATA))
    add.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    add.set_defaults(func=cmd_add)

    record = sub.add_parser("record", help="详情页人工补数：按 note_id 补收藏/评论/分享/时长")
    record.add_argument("--file", required=True, help="JSON：一个对象或对象数组")
    record.add_argument("--on", default=today(), help="详情页采集日期")
    record.add_argument("--data", default=str(DEFAULT_DATA))
    record.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    record.set_defaults(func=cmd_record)

    paste = sub.add_parser("parse-text", help="解析书签小工具产出的 ===AR-NOTE-v1=== 块")
    paste.add_argument("--file", required=True)
    paste.add_argument("--on", default=today())
    paste.add_argument("--data", default=str(DEFAULT_DATA))
    paste.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    paste.set_defaults(func=cmd_parse_text)
    return root


def cmd_probe(args: argparse.Namespace) -> int:
    for raw in args.files:
        print(f"\n=== {Path(raw).name} ===")
        try:
            archive = open_archive(raw)
        except ArchiveError as exc:
            print(f"  不可用：{exc}")
            continue
        owners = {str((card.get("user") or {}).get("userId")) for card in iter_note_cards(archive.state)}
        owners.discard("")
        print(f"  页面类型：{archive.kind}")
        print(f"  笔记数：{len(archive.note_items)} | 作者数：{len(owners)} {sorted(owners)}")
        print(f"  目标 userId：{list(archive.target_user_ids)} | 采集者 userId：{archive.viewer_user_id or '未登录'}")
        if archive.viewer_user_id and archive.viewer_user_id in archive.target_user_ids:
            print("  ⚠️ 采集者就是目标账号本人——数据可用，但『对标』语义不成立")
        if len(owners) > 1:
            print("  ⚠️ 同一页出现多个作者：疑似搜索页/推荐流误采，parse 会按目标 id 丢弃")
        print("  结论：✅ 可解析" if archive.usable else f"  结论：⏭ {REJECT_REASONS.get(archive.kind, '无法识别')}")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    seeded = store.seed_fields(args.data, args.on)
    if seeded:
        print(f"口径登记表已初始化：fields.csv（{seeded} 个指标）")
    parsed = 0
    for raw in args.files:
        name = Path(raw).name
        try:
            archive = open_archive(raw)
        except ArchiveError as exc:
            print(f"❌ {name}：{exc}")
            continue
        if not archive.usable:
            print(f"⏭  {name}：{REJECT_REASONS.get(archive.kind, '无法识别')}")
            continue
        account = parse_account(archive, args.on)
        notes, dropped = parse_notes(archive, account["account_id"], args.on)
        added_a, _ = store.append_rows(args.data, "accounts", [account])
        added_n, skipped_n = store.append_rows(args.data, "notes", notes)
        print(f"✅ {name}：{account['nickname']}（{account['account_id']}）笔记 {len(notes)} 篇"
              f" | 新增账号行 {added_a} / 新增笔记行 {added_n} / 去重跳过 {skipped_n}")
        for reason in dropped[:5]:
            print(f"   ⚠️ 归属不符已丢弃：{reason}")
        parsed += 1
    if not parsed:
        print("没有任何文件被解析——检查文件是否为主页 SSR 存档。")
        return 1
    print(f"\n数据目录：{args.data}")
    return 0


def _commit_rows(args: argparse.Namespace, table: str, rows: list[dict[str, Any]]) -> int:
    """先校验候选状态、通过才落盘（append-only 表没有回滚）。"""
    tables = {name: store.read_table(args.data, name) for name in schema.TABLE_COLUMNS}
    fresh, skipped = store.preview_append(tables[table], table, rows)
    candidate = dict(tables)
    candidate[table] = tables[table] + fresh
    issues = validate_tables(candidate, args.ledger)
    errors = [issue for issue in issues if issue.level == "ERROR"]
    if errors:
        for issue in issues:
            print(f"{'❌' if issue.level == 'ERROR' else '⚠️'} [{issue.code}] {issue.message}")
        print(f"\n写入被拒：{len(errors)} 条 ERROR。**本次一行都没写**，修好再重跑。")
        return 1
    store.append_rows(args.data, table, fresh)
    print(f"{table}：新增 {len(fresh)} 行 / 键重复跳过 {skipped} 行（校验先于落盘 ✅）")
    return 0


def _detail_rows(args: argparse.Namespace, updates: list[dict[str, Any]]) -> int:
    """详情页通道共用逻辑：以已有主页行为底，只覆盖人工读到的字段。

    为什么必须找到底行：详情页数字不能凭空造，也不能顺手改掉列表页的标题/时间——
    对不上的行要么是 note_id 抄错，要么是账号混了，两种都该报错而不是猜。
    """
    notes = store.read_table(args.data, "notes")
    latest = {str(row.get("note_id")): row for row in store.latest_per_note(notes)}
    rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    for item in updates:
        note_id = str(item.get("note_id") or "").strip()
        base = latest.get(note_id)
        if base is None:
            unknown.append(note_id or "(空)")
            continue
        row = dict(base)
        for field in ("likes", "collects", "comments", "shares", "duration", "human_note"):
            if str(item.get(field) or "").strip():
                row[field] = item[field]
        row.update({
            "collected_on": args.on,
            "source_kind": "复制文本",
            "source_file": str(item.get("source_file") or ""),
            "grade": "②",
        })
        rows.append(row)
    if unknown:
        print(f"❌ 这些 note_id 在 notes 表里找不到（先 parse 主页存档）：{unknown}")
        if not rows:
            return 1
    return _commit_rows(args, "notes", rows)


def cmd_record(args: argparse.Namespace) -> int:
    payload = _load_json(Path(args.file))
    items = payload if isinstance(payload, list) else [payload]
    if not all(isinstance(item, dict) for item in items):
        print("❌ JSON 必须是对象或对象数组。")
        return 1
    return _detail_rows(args, items)


def cmd_parse_text(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"❌ 找不到 {path}")
        return 1
    blocks, errors = parse_blocks(path.read_text(encoding="utf-8", errors="replace"))
    for message in errors:
        print(f"⚠️ {message}")
    if not blocks:
        print("没有解析到任何 ===AR-NOTE-v1=== 块——检查书签小工具的输出是否完整粘贴。")
        return 1
    print(f"识别到 {len(blocks)} 块")
    return _detail_rows(args, blocks)


def cmd_validate(args: argparse.Namespace) -> int:
    issues = validate_dir(args.data, args.ledger)
    errors = [issue for issue in issues if issue.level == "ERROR"]
    warns = [issue for issue in issues if issue.level != "ERROR"]
    for issue in errors + warns:
        mark = "❌" if issue.level == "ERROR" else "⚠️"
        print(f"{mark} [{issue.code}] {issue.message}")
    print(f"\n校验结果：{len(errors)} ERROR / {len(warns)} WARN")
    return 1 if errors else 0


def cmd_stats(args: argparse.Namespace) -> int:
    notes = store.read_table(args.data, "notes")
    if not notes:
        print("notes 表是空的——先跑 parse。")
        return 1
    account = args.account or sorted({str(row.get("account_id")) for row in notes})[0]
    ks = tuple(int(part) for part in str(args.k).split(",") if part.strip())
    result = summarize(notes, account, ks)
    print(f"\n=== 账号 {account} ===")
    print(f"置顶 {result.n_pinned} 篇（单列，不进样本）| 样本 {result.n_sample} 篇 | 因缺来源被排除 {result.excluded} 行")
    print(f"时间窗：{result.window[0]} → {result.window[1]}")
    likes = result.likes
    print(f"点赞：篇均 {likes.mean} | 中位 {likes.median} | 最高 {likes.maximum} | 最低 {likes.minimum}"
          f" | Top20% 锚定线 {likes.top20_anchor}（内部分档，非平台爆文定义）")
    print("形式：" + " | ".join(f"{form} {data['n']} 篇（篇均 {data['mean']}）" for form, data in result.forms.items()))
    for k, data in result.burst.items():
        print(f"爆文率 K={k}：{data['rate']}%（{data['hits']}/{result.n_sample}，阈值 {data['threshold']}）"
              f"｜定义：{data['definition']}")
    print("⬜ 覆盖度：" + " | ".join(f"{key} {value}" for key, value in result.coverage.items()))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """模型产出的唯一落库通道：**先校验候选状态，通过才落盘**。

    顺序很重要。append-only 表没有回滚——先写后校验的话，一次 V 码拒收
    就把脏行永久留在 CSV 里（v0.1 的缺陷，由 agent-run-001 §4 实测发现）。
    """
    payload = _load_json(Path(args.file))
    rows = payload if isinstance(payload, list) else [payload]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        print("❌ JSON 必须是对象或对象数组。")
        return 1
    unknown = sorted({key for row in rows for key in row} - set(schema.TABLE_COLUMNS[args.table]))
    if unknown:
        print(f"❌ {args.table} 没有这些列：{unknown}（列名见 schema.py，禁止自造字段）")
        return 1
    return _commit_rows(args, args.table, rows)


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise SystemExit(f"❌ 找不到 {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_selftest(args: argparse.Namespace) -> int:
    archive_dir = Path(args.archive)
    if not archive_dir.is_dir():
        print(f"SKIP：真实存档目录不存在（{archive_dir}）。")
        print("      这不是 PASS——本机没有 account/ 存档，解析器未经真实数据检验。")
        return 0
    checks: list[tuple[str, bool, str]] = []
    fixture = _load_json(Path(args.fixture))
    canonical = archive_dir / CANONICAL
    if not canonical.is_file():
        print(f"SKIP：缺少 {canonical.name}")
        return 0
    archive = open_archive(canonical)
    account = parse_account(archive, "2026-10-22")
    notes, dropped = parse_notes(archive, account["account_id"], "2026-10-22")
    pinned = [row for row in notes if row["pinned"] == "1"]
    checks.append(("AC1 页面类型", archive.kind == "profile", archive.kind))
    checks.append(("AC1 笔记总数 32", len(notes) == 32, str(len(notes))))
    checks.append(("AC1 置顶 2 / 样本 30", len(pinned) == 2 and len(notes) - len(pinned) == 30,
                   f"{len(pinned)}/{len(notes) - len(pinned)}"))
    checks.append(("AC1 账号身份", account["nickname"] == "数字生命卡兹克" and account["red_id"] == "wzglyay2023",
                   f"{account['nickname']}/{account['red_id']}"))
    checks.append(("AC1 归属零丢弃", not dropped and archive.viewer_user_id != account["account_id"],
                   f"dropped={len(dropped)}, viewer={archive.viewer_user_id}"))
    checks.extend(_fixture_checks(notes, fixture))
    checks.extend(_negative_checks(archive_dir))
    checks.extend(_dedupe_checks(canonical, archive_dir, account["account_id"]))
    stats = summarize(notes, account["account_id"], (3, 5))
    checks.append(("AC3 统计口径", stats.n_sample == 30, f"n_sample={stats.n_sample}"))
    return _report(checks)


def _fixture_checks(notes: list[dict[str, Any]], fixture: dict[str, Any]) -> list[tuple[str, bool, str]]:
    if not fixture:
        return [("AC2 对照基线可读", False, "fixture 缺失")]
    expected = {item["title"]: item["likes"] for item in fixture.get("notes", [])}
    volatile = set(fixture.get("volatile", []))
    actual = {row["title"]: row["likes"] for row in notes if row["title"]}
    common = sorted(set(expected) & set(actual))
    regress = [t for t in common if t not in volatile and actual[t] < expected[t]]
    skipped = [t for t in common if t in volatile]
    pinned_ok = set(fixture.get("pinned", [])) <= set(actual)
    return [
        ("AC2 与报告标题交集 ≥30", len(common) >= 30, f"{len(common)} 条"),
        ("AC2 共有篇点赞数不倒退", not regress, f"倒退 {len(regress)} 条：{regress[:3]}"),
        ("AC2 口径可疑行已豁免", True, f"{len(skipped)} 条（volatile，见 fixture._volatile_note）"),
        ("AC2 两篇置顶仍在", pinned_ok, f"置顶={fixture.get('pinned')}"),
    ]


def _negative_checks(archive_dir: Path) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    search = archive_dir / SEARCH
    if search.is_file():
        kind = open_archive(search).kind
        checks.append(("AC3 搜索页被识别并拒收", kind == "search_page", kind))
    for name in SCROLLS:
        path = archive_dir / "_无效存档" / name
        if not path.is_file():
            continue
        try:
            kind = open_archive(path).kind
        except ArchiveError as exc:
            checks.append((f"AC3 {name[:9]}", False, str(exc)))
            continue
        checks.append((f"AC3 {name[:9]} 是主页(滚动位不同)", kind == "profile", kind))
    return checks


def _dedupe_checks(canonical: Path, archive_dir: Path, account_id: str) -> list[tuple[str, bool, str]]:
    base, _ = parse_notes(open_archive(canonical), account_id, "2026-10-22")
    base_ids = {row["note_id"] for row in base}
    extra: set[str] = set()
    for name in SCROLLS:
        path = archive_dir / "_无效存档" / name
        if path.is_file():
            rows, _ = parse_notes(open_archive(path), account_id, "2026-10-22")
            extra |= {row["note_id"] for row in rows}
    merged = base_ids | extra
    return [("AC4 多滚动位合并去重后不膨胀", len(merged) == len(base_ids), f"并集 {len(merged)} / 基准 {len(base_ids)}")]


def _report(checks: list[tuple[str, bool, str]]) -> int:
    failures = 0
    for name, ok, detail in checks:
        print(f"{'✅' if ok else '❌'} {name}：{detail}")
        failures += 0 if ok else 1
    print(f"\nselftest：{len(checks) - failures}/{len(checks)} 通过")
    return 1 if failures else 0


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    return int(args.func(args))
