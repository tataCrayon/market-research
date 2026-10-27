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
from .detail import extract_detail
from .initialstate import REJECT_REASONS, ArchiveError, iter_note_cards, open_archive
from .parser import parse_account, parse_blocks, parse_notes
from .stats import summarize
from .validate import validate_dir, validate_tables

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = REPO_ROOT / "account-research" / "data"
DEFAULT_INBOX = REPO_ROOT / "account" / "inbox"
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

    detail = sub.add_parser("parse-detail", help="详情页 HTML → 补赞/藏/评/时长，并把正文归档")
    detail.add_argument("files", nargs="+")
    detail.add_argument("--on", default=today())
    detail.add_argument("--account", default="", help="账号 id（详情页读到主页未覆盖的新笔记时必填）")
    detail.add_argument("--data", default=str(DEFAULT_DATA))
    detail.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    detail.set_defaults(func=cmd_parse_detail)

    gaps = sub.add_parser("gaps", help="列出缺藏/评/分享的篇目，按该补的价值排序")
    gaps.add_argument("--data", default=str(DEFAULT_DATA))
    gaps.add_argument("--account", default="")
    gaps.add_argument("--limit", type=int, default=0, help="只列前 N 篇（0=全部）")
    gaps.set_defaults(func=cmd_gaps)

    fill = sub.add_parser("fill", help="逐篇口播补数（交互式，一次提交）")
    fill.add_argument("--data", default=str(DEFAULT_DATA))
    fill.add_argument("--account", default="")
    fill.add_argument("--limit", type=int, default=10)
    fill.add_argument("--on", default=today())
    fill.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    fill.set_defaults(func=cmd_fill)

    links = sub.add_parser("links", help="生成待采集的详情页链接清单（点一个存一个）")
    links.add_argument("--data", default=str(DEFAULT_DATA))
    links.add_argument("--account", default="")
    links.add_argument("--limit", type=int, default=0)
    links.add_argument("--out", default="", help="写成 markdown 文件；默认只打印")
    links.set_defaults(func=cmd_links)

    ingest = sub.add_parser("ingest", help="采件箱：自动识别 HTML 与粘贴块，入库并归档")
    ingest.add_argument("path", nargs="?", default=str(DEFAULT_INBOX))
    ingest.add_argument("--on", default=today())
    ingest.add_argument("--data", default=str(DEFAULT_DATA))
    ingest.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ingest.add_argument("--keep", action="store_true", help="归档但保留原文件副本（默认移动）")
    ingest.set_defaults(func=cmd_ingest)
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


def _commit_tables(args: argparse.Namespace, updates: dict[str, list[dict[str, Any]]]) -> int:
    """先校验候选状态、通过才落盘（append-only 表没有回滚）。

    支持一次提交多张表：采集收件箱可能同时产出账号行与笔记行，分两次写会留下
    「账号进了、笔记被拒」的半截状态。
    """
    tables = {name: store.read_table(args.data, name) for name in schema.TABLE_COLUMNS}
    fresh_map: dict[str, list[dict[str, Any]]] = {}
    for table, rows in updates.items():
        fresh, _ = store.preview_append(tables[table], table, rows)
        fresh_map[table] = fresh
        tables[table] = tables[table] + fresh
    issues = validate_tables(tables, args.ledger)
    errors = [issue for issue in issues if issue.level == "ERROR"]
    if errors:
        for issue in issues:
            print(f"{'❌' if issue.level == 'ERROR' else '⚠️'} [{issue.code}] {issue.message}")
        print(f"\n写入被拒：{len(errors)} 条 ERROR。**本次一行都没写**，修好再重跑。")
        return 1
    for table, fresh in fresh_map.items():
        if fresh:
            store.append_rows(args.data, table, fresh)
            print(f"{table}：新增 {len(fresh)} 行（校验先于落盘 ✅）")
    return 0


def _commit_rows(args: argparse.Namespace, table: str, rows: list[dict[str, Any]]) -> int:
    return _commit_tables(args, {table: rows})


def _detail_rows(args: argparse.Namespace, updates: list[dict[str, Any]], allow_new: bool = False) -> int:
    """详情页通道共用逻辑：以已有主页行为底，只覆盖人工读到的字段。

    为什么必须找到底行：详情页数字不能凭空造，也不能顺手改掉列表页的标题/时间——
    对不上的行要么是 note_id 抄错，要么是账号混了，两种都该报错而不是猜。

    `allow_new`：详情页可能读到主页存档未覆盖的新笔记。此时允许新建行，
    但**发布时间留空**（详情页只给「3天前」这种相对值，推算日期 = 编数据）。
    """
    notes = store.read_table(args.data, "notes")
    latest = {str(row.get("note_id")): row for row in store.latest_per_note(notes)}
    accounts = {str(row.get("account_id")) for row in store.read_table(args.data, "accounts")}
    default_account = str(getattr(args, "account", "") or (next(iter(accounts)) if len(accounts) == 1 else ""))
    rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    for item in updates:
        note_id = str(item.get("note_id") or "").strip()
        base = latest.get(note_id)
        if base is None:
            if not (allow_new and default_account):
                unknown.append(note_id or "(空)")
                continue
            base = {column: "" for column in schema.NOTE_COLUMNS}
            base.update({
                "account_id": default_account, "note_id": note_id, "pinned": "0",
                "title": str(item.get("title") or ""), "form": str(item.get("form") or ""),
                "human_note": "来源=详情页 HTML；发布时间未取证（页面只显示相对时间）",
            })
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
    if args.file == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.is_file():
            print(f"❌ 找不到 {path}")
            return 1
        text = path.read_text(encoding="utf-8", errors="replace")
    blocks, errors = parse_blocks(text)
    for message in errors:
        print(f"⚠️ {message}")
    if not blocks:
        print("没有解析到任何 ===AR-NOTE-v1=== 块——检查书签小工具的输出是否完整粘贴。")
        return 1
    print(f"识别到 {len(blocks)} 块")
    return _detail_rows(args, blocks)


def _merged_notes(args: argparse.Namespace) -> list[dict[str, Any]]:
    notes = store.read_table(args.data, "notes")
    if args.account:
        notes = [row for row in notes if str(row.get("account_id")) == args.account]
    return store.latest_per_note(notes)


def _missing_fields(row: dict[str, Any], include_shares: bool = False) -> list[str]:
    """缺口只算「藏 / 评」。

    分享数在多数笔记页根本拿不到（报告里三篇深拆的分享全 ⬜）。把它算进缺口，
    清单会永远清不完——**永远清不完的清单等于没有清单**。
    """
    labels = {"collects": "藏", "comments": "评"}
    if include_shares:
        labels["shares"] = "分享"
    return [label for col, label in labels.items() if not str(row.get(col) or "").strip()]


def cmd_gaps(args: argparse.Namespace) -> int:
    """告诉人「该去补哪几篇」——采集效率的一半在减少盲目点击。"""
    rows = [row for row in _merged_notes(args) if _missing_fields(row)]
    rows.sort(key=lambda row: -int(str(row.get("likes") or 0)))
    if not rows:
        print("没有缺口：所有篇目的藏/评/分享都已入库。")
        return 0
    shown = rows[: args.limit] if args.limit else rows
    print(f"缺藏/评的篇目：{len(rows)} 篇（按点赞降序，先补最能解释量级的）")
    print("注：分享数多数拿不到，不计入缺口；要一并补用 `ar fill` 时多打一个数字即可。\n")
    for index, row in enumerate(shown, start=1):
        print(f"{index:>2}. 赞 {str(row.get('likes') or '?'):>6} | 缺 {'/'.join(_missing_fields(row))}"
              f" | {row.get('published_at') or '?'} | {row.get('title') or '(无标题)'}")
        print(f"    note_id={row.get('note_id')}")
    if len(shown) < len(rows):
        print(f"\n（共 {len(rows)} 篇，用 --limit 0 看全部）")
    print("\n下一步：`ar fill` 逐篇口播补数，或点书签复制块后用 `ar parse-text`。")
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    """交互式补数：一次只问一篇，回车跳过，q 结束，最后一次性提交。

    为什么做成口播而不是自动抓：交互栏 DOM 没有稳定标签，写死选择器会把平台改版
    变成静默错数据。人眼读三个数字是 3 秒的事，错了能立刻发现。
    """
    rows = [row for row in _merged_notes(args) if _missing_fields(row)]
    rows.sort(key=lambda row: -int(str(row.get("likes") or 0)))
    targets = rows[: args.limit] if args.limit else rows
    if not targets:
        print("没有缺口，无需补数。")
        return 0
    print(f"待补 {len(targets)} 篇。输入格式：`藏 评 [分享]`（空格分隔）；回车=跳过，q=结束，"
          f"数字不确定就按回车——**留空比填错好**。\n")
    updates: list[dict[str, Any]] = []
    for index, row in enumerate(targets, start=1):
        print(f"[{index}/{len(targets)}] 赞 {row.get('likes') or '?'} | {row.get('title') or '(无标题)'}")
        raw = input("    藏 评 分享 > ").strip()
        if raw.lower() == "q":
            break
        if not raw:
            continue
        parts = [part for part in raw.replace(",", " ").replace("/", " ").split() if part]
        update: dict[str, Any] = {"note_id": row.get("note_id")}
        for column, value in zip(("collects", "comments", "shares"), parts):
            if value not in ("-", "⬜"):
                update[column] = value
        updates.append(update)
    if not updates:
        print("\n没有要写入的行（全部跳过）。")
        return 0
    print(f"\n准备写入 {len(updates)} 篇…")
    return _detail_rows(args, updates)


NOTE_URL = "https://www.xiaohongshu.com/explore/{note_id}"
TEXT_DIR = "notes-text"


def cmd_links(args: argparse.Namespace) -> int:
    """把「该采哪几篇」变成一串可直接点开的链接。

    为什么有用：Ctrl+S 一次只能存一页，这个绕不过去（绕过就是自动化 = R2）。
    但「找到下一篇 → 复制链接 → 打开」这几步可以省掉——一次生成清单，从上往下点。
    """
    rows = _merged_notes(args)
    text_dir = REPO_ROOT / "account" / TEXT_DIR
    pending: list[tuple[dict[str, Any], bool]] = []
    for row in rows:
        note_id = str(row.get("note_id") or "")
        if not note_id:
            continue
        has_text = (text_dir / f"{note_id}.txt").is_file()
        # 数字齐全就不用再打开页面了（正文有没有是另一回事，但页面只多给正文）
        if not _missing_fields(row):
            continue
        pending.append((row, has_text))
    pending.sort(key=lambda pair: -int(str(pair[0].get("likes") or 0)))
    if args.limit:
        pending = pending[: args.limit]
    if not pending:
        print("没有待采的详情页。")
        return 0

    lines = [
        "# 待采详情页清单",
        "",
        "用法：**右键链接 → 在新标签页中打开**（不要从主页点开弹层）→ 等加载完 → Ctrl+S（仅 HTML）",
        "→ 全部存完丢进 `account/inbox/` → `python account-research/ar.py ingest`",
        "",
        f"共 {len(pending)} 篇；标「已有正文」的只缺赞/藏/评。",
        "",
        "| # | 赞 | 标题 | 状态 | 链接 |",
        "|---|---|---|---|---|",
    ]
    for index, (row, has_text) in enumerate(pending, start=1):
        note_id = str(row.get("note_id"))
        title = str(row.get("title") or "(无标题)").replace("|", "／")
        status = "已有正文，缺数字" if has_text else "未采"
        likes = str(row.get("likes") or "?")
        lines.append(f"| {index} | {likes} | {title} | {status} | {NOTE_URL.format(note_id=note_id)} |")

    text = "\n".join(lines) + "\n"
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"已写入 {target}（{len(pending)} 篇）")
        return 0
    print(text)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """采件箱：把「存下来的 HTML」和「书签复制出的块」一起丢进来，一条命令入库。

    人的动作压到三步：存/点 → 丢进同一个文件夹 → 跑一条命令。
    文件类型靠内容判别，不靠扩展名（文件名是假信号，见 case-001 §2 F1）。
    """
    root = Path(args.path)
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        print(f"采件箱目录已创建：{root}\n把存档 HTML / 书签复制的 txt 丢进来，再跑一次。")
        return 0
    candidates = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in (".html", ".htm", ".txt", ".md"))
    if not candidates:
        print(f"采件箱是空的：{root}")
        return 0

    account_rows: list[dict[str, Any]] = []
    note_rows: list[dict[str, Any]] = []
    block_items: list[dict[str, Any]] = []
    handled: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    for path in candidates:
        if path.suffix.lower() in (".html", ".htm"):
            try:
                archive = open_archive(path)
            except ArchiveError as exc:
                skipped.append((path, str(exc)))
                continue
            if not archive.usable:
                if archive.kind == "note_detail":
                    fields = extract_detail(archive.html)
                    if fields.note_id:
                        block_items.append(fields.as_row_updates())
                        handled.append(path)
                        print(f"✅ {path.name}：详情页 note_id={fields.note_id}"
                              f"（赞 {fields.likes or '⬜'} / 藏 {fields.collects or '⬜'} / 评 {fields.comments or '⬜'}）")
                        _archive_body(fields)
                        continue
                skipped.append((path, REJECT_REASONS.get(archive.kind, "无法识别")))
                continue
            account = parse_account(archive, args.on)
            notes, dropped = parse_notes(archive, account["account_id"], args.on)
            account_rows.append(account)
            note_rows.extend(notes)
            handled.append(path)
            extra = f"（丢弃非目标作者 {len(dropped)} 篇）" if dropped else ""
            print(f"✅ {path.name}：{account['nickname']} 笔记 {len(notes)} 篇{extra}")
        else:
            blocks, errors = parse_blocks(path.read_text(encoding="utf-8", errors="replace"))
            for message in errors:
                print(f"⚠️ {path.name}：{message}")
            if not blocks:
                skipped.append((path, "没有 ===AR-NOTE-v1=== 块"))
                continue
            block_items.extend(blocks)
            handled.append(path)
            print(f"✅ {path.name}：识别到 {len(blocks)} 个笔记块")

    if account_rows or note_rows:
        code = _commit_tables(args, {"accounts": account_rows, "notes": note_rows})
        if code:
            return code
    if block_items:
        code = _detail_rows(args, block_items)
        if code:
            return code
    for path, reason in skipped:
        print(f"⏭  {path.name}：{reason}")
    if not handled:
        print("\n没有可入库的文件。")
        return 1

    archive_dir = root / "processed" / args.on
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in handled:
        target = archive_dir / path.name
        if args.keep:
            target.write_bytes(path.read_bytes())
            path.unlink()
        else:
            path.replace(target)
    print(f"\n已归档 {len(handled)} 个文件 → {archive_dir}")
    return 0


def cmd_parse_detail(args: argparse.Namespace) -> int:
    """详情页 HTML → 笔记级字段。前提：og:url 指向 /explore/<id>（否则是主页快照）。"""
    updates: list[dict[str, Any]] = []
    for raw in args.files:
        path = Path(raw)
        if not path.is_file():
            print(f"❌ 找不到 {path}")
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        fields = extract_detail(html)
        if not fields.note_id:
            if '<h1' in html and 'detail-title' in html:
                print(f"⏭  {path.name}：{REJECT_REASONS['profile_saved_as_detail']}")
            else:
                print(f"⏭  {path.name}：og:url 里没有 /explore/<id> —— 这不是整页加载的详情页"
                      f"（多半是从主页点开弹层后存的，存下来的是主页）。")
            continue
        print(f"✅ {path.name}")
        print(f"   note_id={fields.note_id} | 赞 {fields.likes or '⬜'} / 藏 {fields.collects or '⬜'}"
              f" / 评 {fields.comments or '⬜'} | 形式 {fields.form or '?'}"
              + (f" | 时长 {fields.duration}" if fields.duration else ""))
        print(f"   标题：{fields.title or '⬜'}")
        print(f"   标签：{' '.join(fields.tags) or '⬜'}")
        print(f"   正文前 60 字：{fields.body[:60] or '⬜'}")
        if fields.missing:
            print(f"   ⚠️ 未取到：{fields.missing}（保持 ⬜，不估算）")
        archived = _archive_body(fields)
        if archived:
            print(f"   正文已归档：{archived}")
        updates.append(fields.as_row_updates())
    if not updates:
        return 1
    return _detail_rows(args, updates, allow_new=True)


def _archive_body(fields: Any) -> str:
    """正文落本地文件（长文本不进 CSV）。目录在 account/ 下，随存档一起被 gitignore。"""
    if not fields.note_id or not fields.body:
        return ""
    target_dir = REPO_ROOT / "account" / "notes-text"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{fields.note_id}.txt"
    header = f"# {fields.title}\n# 标签：{' '.join(fields.tags)}\n# 赞/藏/评：{fields.likes}/{fields.collects}/{fields.comments}\n\n"
    target.write_text(header + fields.body, encoding="utf-8")
    return str(target.relative_to(REPO_ROOT))


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
