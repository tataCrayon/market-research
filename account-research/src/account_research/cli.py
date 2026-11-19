"""CLI 入口：probe / parse / validate / stats / selftest。

设计原则（方向卡「确定性优先」）：能用代码算的都放在这里，模型只负责拆解推理。
任何命令都不访问网络、不读写 account/ 以外的地方。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from . import alerts as alert_rules
from . import schema, store
from .board import render_board
from .counts import parse_count, to_count
from .debrief import (
    TARGET_TABLES,
    DebriefError,
    audit_ratios,
    build_runtime_patch,
    build_task,
    check_payload,
    collect_date_overrides,
    collect_snapshot,
    drop_empty_rows,
    extract_payload,
    find_source_patch,
    normalize_rows,
    payload_summary,
    run_headless,
)
from .detail import extract_detail
from .images import (
    OcrUnavailable,
    body_chars,
    body_text,
    collect as collect_images,
    image_dominance,
    image_text_path,
    layer_relation,
    load_engine,
    ocr_file,
    render_text,
)
from . import attempts as attempts_module
from . import close as close_rules
from . import host as host_module
from . import hostsetup as hostsetup_module
from . import matrix, mhtml as mhtml_module, report
from . import find as find_module
from .attempts import describe as describe_attempt
from .attempts import attempts_by_note, missing_columns, summarize as summarize_attempts
from .initialstate import (
    REJECT_REASONS,
    ArchiveError,
    iter_note_cards,
    open_archive,
    state_sidecar_path,
)
from . import profile_dom
from .parser import parse_account, parse_blocks, parse_notes
from .stats import summarize
from . import tokens as tokens_module
from .tokens import build_index_with_source as build_token_index, note_url, token_age_days
from .validate import validate_dir, validate_tables

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = REPO_ROOT / "account-research" / "data"
DEFAULT_INBOX = REPO_ROOT / "account" / "inbox"
DEFAULT_TEXT_DIR = REPO_ROOT / "account" / "notes-text"
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

    close_parser = sub.add_parser(
        "close", help="结案一条假设（到期未验 / 已证伪 / 已证实）——追加一行，不改历史"
    )
    close_parser.add_argument("table", choices=["hypothesis"], help="目前只支持 hypothesis")
    close_parser.add_argument("hypothesis_id", help="要结案的那条假设，如 H-KZK-01")
    close_parser.add_argument("--status", required=True,
                              choices=list(schema.HYPOTHESIS_CLOSED),
                              help="结案状态；超期未验必须配 --note")
    close_parser.add_argument("--note", default="", help="为什么结案（超期未验必填）")
    close_parser.add_argument("--on", default=today(), help="结案日期（写入 recorded_on）")
    close_parser.add_argument("--data", default=str(DEFAULT_DATA))
    close_parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    close_parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的行")
    close_parser.set_defaults(func=cmd_close)

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
    detail.add_argument("--no-new-authors", action="store_true",
                        help="作者不在库里时不建占位账号、直接拒收（默认会建，见 --help 里的说明）")
    detail.add_argument("--data", default=str(DEFAULT_DATA))
    detail.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    detail.add_argument("--text-dir", default="",
                        help="正文归档目录；默认 account/notes-text（测试用临时目录，别碰真实证据）")
    detail.set_defaults(func=cmd_parse_detail)

    images = sub.add_parser("images", help="图片通道：列出存档里的正文图，并把图上的文字 OCR 成文件")
    images.add_argument("files", nargs="*", help="详情页 HTML；省略=扫描 --root 下所有存档")
    images.add_argument("--root", default=str(REPO_ROOT / "account"),
                        help="扫描根目录（也是救回孤立 _files 的搜索范围）")
    images.add_argument("--ocr", action="store_true", help="对本地图片跑 OCR（需 rapidocr-onnxruntime）")
    images.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（0=全部）")
    images.add_argument("--text-dir", default="", help="OCR 文本输出目录；默认 account/notes-text")
    images.add_argument("--data", default=str(DEFAULT_DATA))
    images.set_defaults(func=cmd_images)

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
    links.add_argument("--topic", default="", help="只列某个母题（对应 notes.topic）")
    links.add_argument("--limit", type=int, default=0)
    links.add_argument("--no-tokens", action="store_true",
                       help="不读存档里的 xsec_token（生成的链接很可能 404）")
    links.add_argument("--text-dir", default="",
                       help="正文归档目录；默认 account/notes-text（测试用临时目录）")
    links.add_argument("--on", default=today(),
                       help="判断 token 新鲜度用的日期；默认今天")
    links.add_argument("--out", default="", help="写成 markdown 文件；默认只打印")
    links.set_defaults(func=cmd_links)

    ingest = sub.add_parser("ingest", help="采件箱：自动识别 HTML 与粘贴块，入库并归档")
    ingest.add_argument("path", nargs="*", default=[], help="目录或单个文件；省略=account/inbox")
    ingest.add_argument("--on", default=today())
    ingest.add_argument("--no-new-authors", action="store_true",
                        help="作者不在库里时不建占位账号、直接拒收（默认会建，见 --help 里的说明）")
    ingest.add_argument("--data", default=str(DEFAULT_DATA))
    ingest.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ingest.add_argument("--text-dir", default="",
                        help="正文归档目录；默认 account/notes-text（测试用临时目录）")
    ingest.add_argument("--no-ocr", action="store_true",
                        help="跳过图上文字通道（默认入库后自动跑本地 OCR，图不进模型）")
    ingest.set_defaults(func=cmd_ingest)

    status = sub.add_parser("status", help="进度总览：采了多少、还差多少、卡在哪")
    status.add_argument("--data", default=str(DEFAULT_DATA))
    status.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    status.add_argument("--account", default="")
    status.set_defaults(func=cmd_status)

    quota = sub.add_parser("quota", help="今天的采集额度：采了几个号、几篇详情，还剩多少")
    quota.add_argument("--data", default=str(DEFAULT_DATA))
    quota.add_argument("--on", default=today())
    quota.add_argument("--accounts", type=int, default=3, help="单日账号上限（默认 3）")
    quota.add_argument("--notes", type=int, default=30, help="单日详情页上限（默认 30）")
    quota.set_defaults(func=cmd_quota)

    watch = sub.add_parser("watch", help="守着采件箱：文件一落进来就自动入库（Ctrl+C 停）")
    watch.add_argument("--path", default=str(DEFAULT_INBOX))
    watch.add_argument("--interval", type=float, default=3.0, help="轮询间隔秒（默认 3）")
    # 默认 None = **入库那一刻**才取日期。原来默认值在这里就固定了，
    # 开着过夜会把第二天的采集全记成第一天（采集日期是所有对账的基准）。
    watch.add_argument("--on", default=None,
                       help="采集日期；省略=入库那一刻的当天（推荐，开着过夜也不会记错）")
    watch.add_argument("--settle", type=float, default=1.0,
                       help="文件修改后等这么多秒再动它，避免读到下载了一半的文件（默认 1）")
    watch.add_argument("--pattern", action="append", default=None, metavar="GLOB",
                       help="只认匹配这个通配符的文件（可重复，或用逗号分隔）。"
                            "例：`--pattern \"ar-page-*\" --pattern \"* - 小红书.html\"`")
    watch.add_argument("--once", action="store_true",
                       help="扫一遍就退出（放进定时任务 / 测试用）")
    watch.add_argument("--no-new-authors", action="store_true",
                       help="作者不在库里时不建占位账号、直接拒收（默认会建）")
    watch.add_argument("--data", default=str(DEFAULT_DATA))
    watch.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    watch.add_argument("--text-dir", default="",
                       help="正文归档目录；默认 account/notes-text（测试用临时目录）")
    watch.set_defaults(func=cmd_watch)

    host = sub.add_parser(
        "host",
        help="本地宿主：注册一次，之后扩展点一下就直接入库（不用再开终端）",
    )
    host.add_argument("--register", action="store_true",
                      help="注册到 Chrome（写注册表 + 生成启动脚本与清单）")
    host.add_argument("--unregister", action="store_true",
                      help="从 Chrome 注销，并删掉生成的两个文件")
    host.add_argument("--doctor", action="store_true",
                      help="逐项体检 + 真启一次宿主握手，指出哪一环断了")
    host.add_argument("--run", action="store_true",
                      help="直接跑协议循环（Chrome 会通过生成的 .bat 调它，一般不用手敲）")
    host.add_argument("--data", default=str(DEFAULT_DATA),
                      help="入库写到哪个数据目录（默认 data/）")
    host.set_defaults(func=cmd_host)

    ext = sub.add_parser("extension", help="扩展的安装状态与安装说明")
    ext.add_argument("--status", action="store_true", help="列出扩展目录与宿主注册状态")
    ext.set_defaults(func=cmd_extension)

    debrief = sub.add_parser("debrief", help="一键拆解：拼任务 → 交给 dsh headless → 产物收回本地待审")
    debrief.add_argument("--account", default="", help="account_id；省略=库里只有唯一账号时自动选")
    debrief.add_argument("--data", default=str(DEFAULT_DATA))
    debrief.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    debrief.add_argument("--text-dir", default="", help="正文/图上文字目录；默认 account/notes-text")
    debrief.add_argument("--dsh", default="dsh", help="dsh 启动器路径（默认从 PATH 找）")
    debrief.add_argument("--patch", default="auto",
                         help="模型路由覆盖层来源档案：auto=依次找 desktop/web 档案；也可直接给文件")
    debrief.add_argument("--timeout", type=int, default=1800, help="单次运行的秒数上限（默认 1800）")
    debrief.add_argument("--on", default=today(), help="拆解日期（写入 decided_on / sampled_on）")
    debrief.add_argument("--out", default="", help="运行产物目录；默认 account-research/data/debrief/<账号>-<时间>")
    debrief.add_argument("--dry-run", action="store_true", help="只拼任务并打印，不调模型")
    debrief.add_argument("--commit", action="store_true",
                         help="产出先过 V1–V8，通过才落库（默认只留 payload.json 待人复核）")
    debrief.add_argument("--replay", default="",
                         help="不调模型，直接拿已有的 payload.json 重走检查与落库")
    debrief.add_argument("--k", default="3,5", help="爆文率 K 值，逗号分隔")
    debrief.set_defaults(func=cmd_debrief)

    report_parser = sub.add_parser(
        "report", help="把四张结论表渲染成一份人能从头读的 markdown 报告"
    )
    report_parser.add_argument("--account", default="",
                               help="account_id；省略=库里只有唯一账号时自动选")
    report_parser.add_argument("--data", default=str(DEFAULT_DATA))
    report_parser.add_argument("--text-dir", default="", help="正文目录；默认 account/notes-text")
    report_parser.add_argument("--out", default="", help="输出路径；省略=打印到终端")
    report_parser.add_argument("--notes-limit", type=int, default=0,
                               help="逐篇表最多显示几行（0=全显示，不影响任何统计）")
    report_parser.add_argument("--on", default=today(), help="渲染日期（写进报告头）")
    report_parser.add_argument("--k", default="3,5", help="爆文率 K 值，逗号分隔")
    report_parser.set_defaults(func=cmd_report)

    matrix_parser = sub.add_parser(
        "matrix", help="跨账号矩阵视图 → 单文件 HTML（无脚本、无外链、断网可开）"
    )
    matrix_parser.add_argument("--account", action="append", default=[],
                                help="只渲染指定账号（可重复）；省略=库里全部账号")
    matrix_parser.add_argument("--data", default=str(DEFAULT_DATA))
    matrix_parser.add_argument("--text-dir", default="", help="正文目录；默认 account/notes-text")
    matrix_parser.add_argument("--out", default="", help="输出 HTML 路径；省略=打印到终端")
    matrix_parser.add_argument("--on", default=today(), help="数据截至日（写进页头）")
    matrix_parser.add_argument("--k", default="3,5", help="爆文率 K 值，逗号分隔")
    matrix_parser.set_defaults(func=cmd_matrix)

    find_parser = sub.add_parser(
        "find", help="跨表检索：按关键字与字段筛，每条命中带可回查编号（查不到就说「库内无证据」）"
    )
    find_parser.add_argument("keywords", nargs="*",
                             help="关键字（空格分隔 = AND）；只给筛选条件时可为空")
    find_parser.add_argument("--table", action="append", default=[], choices=list(find_module.TABLES),
                             help="只搜这几张表（可重复）；省略=全部")
    find_parser.add_argument("--layer", default="",
                             help="只看某一层（定位/选题/结构/变现/其它）；只有 findings 有这个字段")
    find_parser.add_argument("--form", default="", choices=["图文", "视频", "未知"],
                             help="只看某种内容形式；只有 notes 有这个字段")
    find_parser.add_argument("--status", default="",
                             choices=list(schema.HYPOTHESIS_STATUS),
                             help="只看某种假设状态；只有 hypotheses 有这个字段")
    find_parser.add_argument("--grade", default="",
                             help="只看某个证据等级（②/③ 这样的前缀也算：③ 同时含 ③-a 与 ③-b）")
    find_parser.add_argument("--account", default="", help="只看某个账号的行")
    find_parser.add_argument("--min-support", type=int, default=None, metavar="N",
                             help="只列支撑账号数 ≥ N 的打法（N=2 即「跨账号验证过」）")
    find_parser.add_argument("--text", action="store_true",
                             help="连正文 / 图上文字 / 评论文件一起搜（默认只搜表）")
    find_parser.add_argument("--limit", type=int, default=0, help="最多显示几条（0=全部，不影响计数）")
    find_parser.add_argument("--json", action="store_true", help="输出 JSON（给下游消费）")
    find_parser.add_argument("--data", default=str(DEFAULT_DATA))
    find_parser.add_argument("--text-dir", default="", help="正文目录；默认 account/notes-text")
    find_parser.set_defaults(func=cmd_find)
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
        if archive.kind == "profile_dom":
            # DOM 腿没有 state，`note_items` 恒空。报 0 会让人以为「这页没笔记」，
            # 而实际上卡片都在 DOM 里——用真正会被入库的那个解析器数。
            cards = profile_dom.slices(archive.html)
            print(f"  笔记数：{len(cards)}（DOM 腿；发布日与置顶缺）")
        else:
            print(f"  笔记数：{len(archive.note_items)} | 作者数：{len(owners)} {sorted(owners)}")
        print(f"  目标 userId：{list(archive.target_user_ids)} | 采集者 userId：{archive.viewer_user_id or '未登录'}")
        if archive.viewer_user_id and archive.viewer_user_id in archive.target_user_ids:
            print("  ⚠️ 采集者就是目标账号本人——数据可用，但『对标』语义不成立")
        if len(owners) > 1:
            print("  ⚠️ 同一页出现多个作者：疑似搜索页/推荐流误采，parse 会按目标 id 丢弃")
        print("  结论：✅ 可解析" if archive.usable else f"  结论：⏭ {REJECT_REASONS.get(archive.kind, '无法识别')}")
    return 0


def _profile_rows(archive, collected_on: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[str]]:
    """按存档类型选主页解析腿：`profile` 走 state，`profile_dom` 走 DOM。

    **两条腿必须给出同形状的产物**（account 行 + notes 行 + 丢弃原因 + 缺陷清单），
    否则下游每一处都要各写一遍分支，而分支迟早会漏。

    为什么不在 `parser.parse_account` 里内部分支：那个函数读的是
    `archive.state["user"]["userPageData"]`，签名里没有 html——
    为了兼顾 DOM 腿而把 html 也塞进去，会让「这个字段到底从哪来」重新变模糊。
    两个来源就写两个函数，在**这里**合流。

    返回的第四项 `defects` 是「这条腿给不了什么」。state 腿恒为空（它什么都有）；
    DOM 腿会说「发布日与置顶缺」。**不静默留空**——那是「解析器坏了」和
    「通道给不了」之间唯一的分界线。
    """
    if archive.kind == "profile_dom":
        account = profile_dom.parse_account(
            archive.html, collected_on, source_file=archive.path.name,
            account_id=(archive.target_user_ids[0] if archive.target_user_ids else ""),
        )
        notes, defects = profile_dom.parse_notes(
            archive.html, account["account_id"], collected_on, source_file=archive.path.name)
        return account, notes, [], defects
    account = parse_account(archive, collected_on)
    notes, dropped = parse_notes(archive, account["account_id"], collected_on)
    return account, notes, dropped, []


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
        account, notes, dropped, defects = _profile_rows(archive, args.on)
        added_a, _ = store.append_rows(args.data, "accounts", [account])
        added_n, skipped_n = store.append_rows(args.data, "notes", notes)
        print(f"✅ {name}：{account['nickname']}（{account['account_id']}）笔记 {len(notes)} 篇"
              f" | 新增账号行 {added_a} / 新增笔记行 {added_n} / 去重跳过 {skipped_n}")
        for reason in dropped[:5]:
            print(f"   ⚠️ 归属不符已丢弃：{reason}")
        for defect in defects:
            print(f"   ⚠️ {defect}")
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

    `accounts` 有一处例外：同键的**占位账号**（由详情页顺手建的、没有任何主页字段）
    会被真实主页存档整行替换。原因见 `store.is_placeholder_account`——
    留着一个空壳行会永久挡住真数据（实测踩过：粉丝 16821 被静默跳过）。
    """
    tables = {name: store.read_table(args.data, name) for name in schema.TABLE_COLUMNS}
    fresh_map: dict[str, list[dict[str, Any]]] = {}
    upgraded_total = 0
    for table, rows in updates.items():
        upgraded, replaced = store.upgrade_placeholders(tables[table], table, rows)
        if replaced:
            tables[table] = upgraded
            upgraded_total += replaced
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
    if upgraded_total:
        store.replace_table(args.data, "accounts", tables["accounts"])
        print(f"accounts：{upgraded_total} 个占位账号被主页存档替换（仍是 {len(tables['accounts'])} 行）")
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

    归属靠 author_id，不靠"库里只有一个账号"**（2026-10-28 起）**：
    详情页里能读到作者主页链接（选择器抄自「小红书-同步开启博主首页」扩展，实测成立）。
    有了它就能做到：**一个窗口里混着 3 个账号的笔记，一条命令各归各位**。

    外来作者**默认收下**，建成一条待补主页的占位账号（粉丝/简介 ⬜）。
    2026-11-18 改的默认值——原来默认拒收，理由是「防止 author_id 抄错时悄悄造一个垃圾账号」。
    那理由仍然成立，但代价算错了：

    - **采集不该有限制**。`accounts` 是「我已经建档的目标」，不是「世界的白名单」。
      归属的真凭据是详情页里的作者链接，不是那个号在不在我的清单上。
    - 旧的默认值造成了**死锁**：详情页被拒 ⇒ 因为作者不在库里 ⇒ 而把作者放进库的唯一
      路径是采主页 ⇒ 而扩展采不了主页（`pageCapture` 剥掉 `<script>`）。什么都采不了。

    拒绝是**静默丢数据**，占位行是**可见、可清理**的（补一次主页就被 `upgrade_placeholders`
    整行替换）。两者选，留痕的那个更安全。要回到旧行为用 `--no-new-authors`。
    """
    notes = store.read_table(args.data, "notes")
    latest = {str(row.get("note_id")): row for row in store.latest_per_note(notes)}
    accounts = {str(row.get("account_id")) for row in store.read_table(args.data, "accounts")}
    fallback = str(getattr(args, "account", "") or (next(iter(accounts)) if len(accounts) == 1 else ""))
    allow_new_authors = not bool(getattr(args, "no_new_authors", False))
    rows: list[dict[str, Any]] = []
    account_rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    foreign: list[str] = []
    routed: dict[str, int] = {}
    for item in updates:
        note_id = str(item.get("note_id") or "").strip()
        author_id = str(item.get("author_id") or "").strip()
        author_name = str(item.get("author_name") or "").strip()
        if author_id and author_id not in accounts:
            if allow_new_authors:
                accounts.add(author_id)
                account_rows.append({
                    "account_id": author_id,
                    "nickname": author_name,
                    "platform": schema.PLATFORM,
                    "source_kind": "复制文本",
                    "collected_on": args.on,
                    "grade": "②",
                    "note": "由详情页/采集块自动建号；**缺主页存档**，粉丝/简介等为 ⬜，需要补一次主页 Ctrl+S",
                })
            elif accounts:  # 外来的，且没开 --new-authors → 拒收
                foreign.append(f"{note_id}(作者 {author_id})")
                continue
        account_id = author_id if author_id in accounts else fallback
        base = latest.get(note_id)
        if base is None:
            if not (allow_new and account_id):
                unknown.append(note_id or "(空)")
                continue
            base = {column: "" for column in schema.NOTE_COLUMNS}
            base.update({
                "account_id": account_id, "note_id": note_id, "pinned": "0",
                "title": str(item.get("title") or ""), "form": str(item.get("form") or ""),
                # 🔧 2026-11-03：这里原来无条件写「发布时间未取证」。
                # detail 通道现在能从 state 拿到绝对时间戳，硬写那句话就成了假话。
                # 拿不到才写 ⬜，拿得到就留空（下面的字段循环会填）。
                "human_note": "" if str(item.get("published_at") or "").strip()
                else "来源=详情页 HTML；发布时间未取证（页面只显示相对时间）",
            })
        else:
            account_id = str(base.get("account_id") or account_id)
        routed[account_id] = routed.get(account_id, 0) + 1
        row = dict(base)
        # `published_at` 也进来了：只在**新行**且详情页给得出绝对时间时才写。
        # 已有行的发布时间以主页存档为准——那是列表页服务端渲染的原值，
        # 而且详情页读到的时分秒已经被我们截断成日期，不该回头覆盖更早的观测。
        fill_fields = ["likes", "collects", "comments", "shares", "duration", "human_note"]
        if base is None or not str(base.get("published_at") or "").strip():
            fill_fields.append("published_at")
        for field in fill_fields:
            if str(item.get(field) or "").strip():
                row[field] = item[field]
        row.update({
            "collected_on": args.on,
            "source_kind": "复制文本",
            "source_file": str(item.get("source_file") or ""),
            "grade": "②",
        })
        if author_id:
            row["author_id"] = author_id
        rows.append(row)
    if foreign:
        print(f"⏭  拒收 {len(foreign)} 个外来作者的笔记（`--no-new-authors` 说了不收）：{foreign[:5]}")
        print("   去掉 `--no-new-authors` 就会按作者名建占位账号（粉丝/简介 ⬜，需补主页存档）。")
    if unknown:
        print(f"❌ 这些 note_id 在 notes 表里找不到（先 parse 主页存档）：{unknown}")
    if not rows and not account_rows:
        print("❌ 没有一个文件入库成功——上面每条 ⏭ / ❌ 都写明了原因。")
        return 1
    if len(routed) > 1:
        detail = "、".join(f"{acc[-6:]}:{n} 篇" for acc, n in routed.items())
        print(f"🔀 按作者归属分流到 {len(routed)} 个账号 → {detail}")
    if account_rows:
        print(f"🆕 新建 {len(account_rows)} 个占位账号（缺主页）："
              f"{[row['account_id'][-6:] for row in account_rows]}")
        return _commit_tables(args, {"accounts": account_rows, "notes": rows})
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
    if getattr(args, "topic", ""):
        notes = [row for row in notes if str(row.get("topic") or "") == args.topic]
    return store.latest_per_note(notes)


def _missing_fields(row: dict[str, Any], include_shares: bool = False) -> list[str]:
    """缺口只算「藏 / 评」。

    分享数在多数笔记页根本拿不到（DOM 里只有图标）。把它算进缺口，
    清单会永远清不完——**永远清不完的清单等于没有清单**。
    （2026-11-03 起分享数能从 state 拿到，但它仍是「有更好、没有不阻塞」的字段，
    所以仍默认不计入缺口；要补时用 `ar fill` 多打一个数字即可。）

    ⚠️ `0` 不是缺口。零评论的笔记现在会被判成 `"0"`（页面有「这是一片荒地」空态占位，
    见 `detail._resolve_zero_comments`）——用 `str(row.get(col) or "")` 判空时，
    `"0"` 是真值不会被误判，但**空字符串与 None 才该算缺**。这里显式写清楚，
    免得后来人顺手改成 `int(...) or ...` 把 0 变成假值。
    """
    labels = {"collects": "藏", "comments": "评"}
    if include_shares:
        labels["shares"] = "分享"
    return [label for col, label in labels.items() if not _has_count(row.get(col))]


def _has_count(value: Any) -> bool:
    """单元格里有没有一个真实计数。`0` 有，`⬜` / 空 / None 没有。"""
    return str(value if value is not None else "").strip() not in ("", "⬜")


def _like_key(row: dict[str, Any]) -> int:
    """按点赞降序排——但**绝不让一个解析不了的计数把整条命令崩掉**。

    实测：`1.2万` 曾经让 `ar gaps` / `ar links` 直接
    `ValueError: invalid literal for int()`。清单类命令的全部价值是「告诉人去点哪几篇」，
    它不该因为一个单元格的写法而彻底不可用——排不出来的沉到最后，其余照常输出。
    """
    return to_count(row.get("likes")) or 0


def _show_count(value: Any) -> str:
    """清单里展示计数：圆整值带 `≈`，空值 `?`。

    为什么不直接打印原串 `1.2万`：清单是给人决定「先点哪一篇」的，
    要能一眼比大小；但也不能把 ≈12,000 写成 12,000 —— 那是在替平台编精度。
    """
    parsed = parse_count(value)
    return parsed.text() if parsed else "?"


def cmd_gaps(args: argparse.Namespace) -> int:
    """告诉人「该去补哪几篇」——采集效率的一半在减少盲目点击。

    **每条缺口都带「上次是怎么试的」**（2026-11-04 起）：只有「缺什么」，
    「从没试过」和「试过但平台不给」在屏幕上长得一模一样，而这两者的处理完全相反
    ——前者去采，后者别浪费时间。这个区别曾经被固化错过一次：
    三份文档写着「评论一条都采不到」，而 11 篇存档里 95 条评论原文一直都在，
    错误来源只是一次 `grep comment-item` 没命中。见 `attempts.py` 的模块说明。
    """
    merged = _merged_notes(args)
    rows = [row for row in merged if _missing_fields(row)]
    rows.sort(key=lambda row: -_like_key(row))
    if not rows:
        print("没有缺口：所有篇目的藏/评/分享都已入库。")
        return 0
    attempts = attempts_by_note(store.read_table(args.data, "notes"))
    shown = rows[: args.limit] if args.limit else rows
    print(f"缺藏/评的篇目：{len(rows)} 篇（按点赞降序，先补最能解释量级的）")
    print("注：分享数多数拿不到，不计入缺口；要一并补用 `ar fill` 时多打一个数字即可。")
    missing_by_note = {str(row.get("note_id")): missing_columns(row) for row in rows}
    print(summarize_attempts(attempts, missing_by_note) + "。\n")
    for index, row in enumerate(shown, start=1):
        note_id = str(row.get("note_id"))
        print(f"{index:>2}. 赞 {_show_count(row.get('likes')):>8} | 缺 {'/'.join(_missing_fields(row))}"
              f" | {row.get('published_at') or '?'} | {row.get('title') or '(无标题)'}")
        print(f"    note_id={note_id}")
        print(f"    {describe_attempt(attempts.get(note_id), missing_columns(row), today())}")
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
    rows.sort(key=lambda row: -_like_key(row))
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


def cmd_links(args: argparse.Namespace) -> int:
    """把「该采哪几篇」变成一串可直接点开的链接。

    为什么有用：Ctrl+S 一次只能存一页，这个绕不过去（绕过就是自动化 = R2）。
    但「找到下一篇 → 复制链接 → 打开」这几步可以省掉——一次生成清单，从上往下点。

    ⚠️ 2026-10-28 实测纠错：**裸 `/explore/<id>` 链接现在打不开**，会跳
    `/404?...error_code=300031 当前笔记暂时无法浏览`，平台还会要求扫码。
    原因是链接缺 `xsec_token`。而你的主页存档里每张 noteCard 都自带这个 token，
    所以这里从存档建一份 note_id → token 索引，把链接拼成能打开的形态。

    但 token 是**会话级、会过期**的：它是从你某一天的存档里读出来的。
    打不开时最稳的路仍然是**回主页点进那一篇**——那条路径永远带新鲜 token。
    """
    rows = _merged_notes(args)
    text_dir = _image_text_root(args)
    index = build_token_index([REPO_ROOT / "account"]) if not args.no_tokens else {}
    tokens = {note_id: entry.token for note_id, entry in index.items()}
    pending: list[tuple[dict[str, Any], bool, str]] = []
    for row in rows:
        note_id = str(row.get("note_id") or "")
        if not note_id:
            continue
        has_text = (text_dir / f"{note_id}.txt").is_file()
        # 🔧 2026-10-29 改：原来「数字齐全就不再列出来」，理由是「页面只多给正文」。
        # 那个理由现在反了——正文（尤其写在图上的那部分）才是要分析的东西，
        # 数字齐了但没正文的篇目**恰恰最该补**。实测漏了 2 篇藏/评齐全却没有正文的。
        if not _missing_fields(row) and has_text:
            continue
        pending.append((row, has_text, tokens.get(note_id, "")))
    pending.sort(key=lambda triple: -_like_key(triple[0]))
    if args.limit:
        pending = pending[: args.limit]
    if not pending:
        print("没有待采的详情页。")
        return 0

    with_token = sum(1 for _, _, token in pending if token)
    need_body = sum(1 for _, has_text, _ in pending if not has_text)
    numbers_done_no_body = sum(
        1 for row, has_text, _ in pending if not has_text and not _missing_fields(row)
    )
    # token 的**年龄**决定它还能不能打开。只报「带 token 54/56」是不够的：
    # 那个数字把「有 token」说成了「能打开」，而实测拿着它点开是 404（2026-11-12）。
    ages = [
        token_age_days(entry.collected_on, args.on)
        for entry in index.values() if entry.collected_on
    ]
    fresh_days = min(ages) if ages else None
    stale = fresh_days is not None and fresh_days > tokens_module.TOKEN_STALE_DAYS
    lines = [
        "# 待采详情页清单",
        "",
        "用法：**右键链接 → 在新标签页中打开**（不要从主页点开弹层）→ 等加载完 →"
        " Ctrl+C 或 Ctrl+S 两种存法任选（见下）",
        "",
        "**存法 A（推荐，最省事）**：装 `extension/` 里的浏览器扩展，点一下就把当前页存进"
        " `account/inbox/`——它只读你**已经打开的页面**，不发任何额外请求。",
        "**存法 B（手动）**：Ctrl+S 选「**网页，全部**」→ 存进 `account/inbox/`。"
        "⚠️ 别选「仅 HTML」：图文笔记的正文常常只排版在图上。",
        "",
        "存完跑：`python account-research/ar.py ingest`（解析 + 归档正文 + 图上文字一次做完）",
        "",
        f"共 {len(pending)} 篇。其中 {need_body} 篇还没有正文"
        + (f"（含 {numbers_done_no_body} 篇赞/藏/评已齐、只缺正文的——"
           f"旧逻辑会把这些漏掉，理由是「数字齐了就不用打开」，而正文恰恰是要分析的东西）"
           if numbers_done_no_body else "")
        + f"。链接带 token：{with_token}/{len(pending)}。",
        "",
    ]
    if stale:
        lines += [
            f"> 🚫 **这批链接大概率打不开**：token 是从 {fresh_days} 天前的存档里读出来的，"
            f"平台按会话发 token，超过 {tokens_module.TOKEN_STALE_DAYS} 天基本失效。",
            "> **别从这里点**。正确做法：打开该账号主页 → 点进那一篇 →"
            " 用扩展或 Ctrl+S 存。**主页路径永远带新鲜 token。**",
            f"> 要重新生成能打的链接：先按 Ctrl+S 存一份主页，再跑一次 `ar links`。",
            "",
        ]
    lines += [
        "> ⚠️ token 是从你已保存的存档里读出来的，**会过期**。若某篇打开后跳到 404"
        "（error_code=300031），说明 token 失效——回该账号主页点进那篇即可（永远带新 token）。",
        "",
        "| # | 赞 | 标题 | 状态 | 链接 |",
        "|---|---|---|---|---|",
    ]
    attempts = attempts_by_note(store.read_table(args.data, "notes"))
    tried_before = sum(
        1 for row, _, _ in pending if str(row.get("note_id")) in attempts
    )
    if tried_before:
        # 插在「⚠️ token 会过期」之前（索引 11 是那条说明的起始位置）。
        # 早先写死 11 时清单短一行就会插到表格中间，把 markdown 表撑坏——用插入点定位。
        anchor = next(
            index for index, line in enumerate(lines) if line.startswith("> ⚠️ token")
        )
        lines.insert(
            anchor,
            f"> 🔁 其中 **{tried_before} 篇以前采过详情页**但没拿到全部数字。"
            f"那说明「试过」不等于「拿得到」——也说明**别默认它拿不到**："
            f"超过 {attempts_module.RETRY_AFTER_DAYS} 天的结论要重新验证（平台会改版）。"
            f"逐条历史用 `ar gaps` 看。",
        )
    for number, (row, has_text, token) in enumerate(pending, start=1):
        note_id = str(row.get("note_id"))
        title = str(row.get("title") or "(无标题)").replace("|", "／")
        lacks = _missing_fields(row)
        if has_text and lacks:
            status = "已有正文，缺数字"
        elif has_text:
            status = "已齐"
        elif lacks:
            status = "未采" + ("（无 token）" if not token else "")
            if note_id in attempts:
                status = "未采（上次试过）" + ("（无 token）" if not token else "")
        else:
            status = "数字齐，**缺正文**"
        likes = _show_count(row.get("likes"))
        lines.append(
            f"| {number} | {likes} | {title} | {status} | {note_url(note_id, token)} |"
        )

    text = "\n".join(lines) + "\n"
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        board_path = target.with_suffix(".html")
        board_rows = [(row, has_text) for row, has_text, _ in pending]
        board_path.write_text(
            render_board(board_rows, NOTE_URL, _account_label(args), tokens=tokens),
            encoding="utf-8",
        )
        print(f"已写入 {target} 与看板 {board_path}（{len(pending)} 篇，带 token {with_token} 篇）")
        if with_token < len(pending):
            print(f"⚠️ {len(pending) - with_token} 篇没有 token：打开可能 404，回主页点进去最稳。")
        if stale:
            print(f"🚫 token 来自 {fresh_days} 天前的存档，大概率已失效——"
                  f"别从清单里点，回主页点进那篇再存。")
        print(f"打开看板：start {board_path}    或双击该文件")
        return 0
    print(text)
    return 0


def _account_label(args: argparse.Namespace) -> str:
    accounts = store.read_table(args.data, "accounts")
    names = sorted({str(row.get("nickname") or "") for row in accounts} - {""})
    return " / ".join(names)


INGEST_SUFFIXES = (".html", ".htm", ".txt", ".md", ".mhtml", ".mht")


def _collect_candidates(paths: list[str]) -> tuple[list[Path], list[Path]]:
    """展开输入：目录取里面待处理的文件，文件直接用。返回 (候选文件, 新建的目录)。"""
    candidates: list[Path] = []
    created: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates.extend(
                sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in INGEST_SUFFIXES)
            )
        elif path.is_file():
            candidates.append(path)
        else:
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    return candidates, created


def _expand_mhtml(candidates: list[Path]) -> tuple[list[Path], int]:
    """把候选里的 MHTML 就地拆成 `HTML + _files/`，再用 HTML 继续走原管道。

    返回 (可继续处理的候选, 拆包失败数)。**失败数必须单独回传**：拆不出来的文件
    也会从 candidates 里消失，若不区分，「三份全拆失败」与「采件箱本来就空」
    在屏幕上完全一样——两者都让 `ingest` 返回 0，宿主于是回「成功」，
    而扩展显示「已入库 0 篇，失败 0 篇」。这是最糟的一种：**什么都没做，却报成功**。

    为什么在**入口处**拆而不让下游认 MHTML：`images.py` / `detail.py` / OCR 全部
    按「HTML + `_files/` 同目录」工作，那条路上已经有图与笔记的绑定（含 note_id 校验）、
    平台声明张数对账、孤立目录反查救援。**新格式在边界被翻译成旧形状**，
    比让旧管道长出第二条腿安全得多——两份逻辑迟早对不上，而对不上时不会报错。

    拆完删掉源 `.mhtml`：否则 inbox 里同时躺着 `x.mhtml` 和 `x.html`，
    下次 ingest 会把它当两份不同的存档各入库一次（图表里出现重复行）。
    """
    result: list[Path] = []
    broken = 0
    for path in candidates:
        if path.suffix.lower() not in mhtml_module.MHTML_SUFFIXES and not mhtml_module.looks_like_mhtml(path):
            result.append(path)
            continue
        try:
            html_path, parts = mhtml_module.convert(path)
        except (ValueError, OSError) as exc:
            print(f"❌ {path.name}：MHTML 拆不开——{exc}")
            print("   这份文件会被跳过，其余照常处理。")
            broken += 1
            continue
        print(f"📦 {path.name} → {html_path.name} + {html_path.stem}_files/（{parts} 个子资源）")
        if parts == 0:
            print("   ⚠️ 这份 MHTML 里**一个子资源都没有**：图很可能没被打包进去，"
                  "这篇的图上文字会缺（正文仍然入库）。")
        try:
            path.unlink()
        except OSError:
            pass
        result.append(html_path)
    return result, broken


def cmd_ingest(args: argparse.Namespace) -> int:
    """采件箱：把「存下来的 HTML」和「书签复制出的块」一起丢进来，一条命令入库。

    人的动作压到三步：存/点 → 丢进同一个文件夹 → 跑一条命令。
    文件类型靠内容判别，不靠扩展名（文件名是假信号，见 case-001 §2 F1）。
    也可以直接给单文件（`ar ingest 某篇.html`），不必凑齐一批。
    """
    roots = [Path(p) for p in (args.path or [str(DEFAULT_INBOX)])]
    candidates, created = _collect_candidates([str(p) for p in roots])
    for path in created:
        print(f"采件箱目录已创建：{path}\n把存档 HTML / 书签复制的 txt 丢进来，再跑一次。")
    # MHTML 在**入口处**就被翻译成「HTML + `_files/`」——下游一个字都不用改。
    # 为什么不让下游认 MHTML：图与笔记的绑定（含 note_id 校验）、平台声明张数对账、
    # 孤立目录反查救援全都长在那条路上了，重写一遍等于把踩平的坑再踩一次。
    candidates, broken = _expand_mhtml(candidates)
    if not candidates:
        if broken:
            # 拆包失败**不是「采件箱是空的」**。两者都返回 0 的话，宿主会把
            # 「一份都没入库」当成成功回给扩展，人看到的是「已入库 0 篇」。
            print(f"❌ {broken} 份 MHTML 都拆不开，没有可入库的文件。")
            return 1
        if not created:
            print("采件箱是空的：%s" % "、".join(str(p) for p in roots))
        return 0

    account_rows: list[dict[str, Any]] = []
    note_rows: list[dict[str, Any]] = []
    block_items: list[dict[str, Any]] = []
    handled: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    detail_ready: list[Path] = []

    for path in candidates:
        if path.suffix.lower() in (".html", ".htm"):
            try:
                archive = open_archive(path)
            except ArchiveError as exc:
                skipped.append((path, str(exc)))
                continue
            if not archive.usable:
                if archive.kind == "note_detail":
                    # detail 通道现在两条腿走路：state 给分享数/发布日/视频秒数，DOM 给正文。
                    # 只传 html 会静默丢掉那三样——所以 archive.state 必须一起给。
                    fields = extract_detail(archive.html, archive.state)
                    if fields.note_id:
                        block_items.append(fields.as_row_updates())
                        handled.append(path)
                        print(f"✅ {path.name}：详情页 note_id={fields.note_id}"
                              f"（赞 {fields.count_text('likes')} / 藏 {fields.count_text('collects')}"
                              f" / 评 {fields.count_text('comments')}"
                              f" / 分享 {fields.count_text('shares')}）")
                        _report_conflicts(fields)
                        _archive_body(fields, _image_text_root(args))
                        detail_ready.append(path)
                        continue
                skipped.append((path, REJECT_REASONS.get(archive.kind, "无法识别")))
                continue
            account, notes, dropped, defects = _profile_rows(archive, args.on)
            account_rows.append(account)
            note_rows.extend(notes)
            handled.append(path)
            extra = f"（丢弃非目标作者 {len(dropped)} 篇）" if dropped else ""
            print(f"✅ {path.name}：{account['nickname']} 笔记 {len(notes)} 篇{extra}")
            for defect in defects:
                print(f"   ⚠️ {defect}")
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
        code = _detail_rows(args, block_items, allow_new=True)
        if code:
            return code
    for path, reason in skipped:
        print(f"⏭  {path.name}：{reason}")
    if not handled:
        print("\n没有可入库的文件。")
        return 1

    # ⚠️ 用 `getattr` 而不是 `args.no_ocr`：`cmd_watch` 会自己拼一个 Namespace
    # 传给本函数，它没有 `no_ocr` 这个字段。直接取属性会抛 `AttributeError`——
    # 而 `if detail_ready and ...` 的短路求值让这个 bug **只在有详情页时才触发**：
    # 主页存档（detail_ready 为空）一切正常，所以原有测试全绿，`ar watch` 却在
    # 每篇详情页上崩一次。2026-11-13 端到端跑 `ar watch` 才发现。
    if detail_ready and not getattr(args, "no_ocr", False):
        _ingest_ocr(detail_ready, roots[0], _image_text_root(args))

    archive_dir = (roots[0] if roots[0].is_dir() else roots[0].parent) / "processed" / args.on
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved_dirs = 0
    moved_states = 0
    for path in handled:
        target = archive_dir / path.name
        if target.exists():
            target = archive_dir / f"{path.stem}-{_stamp()}{path.suffix}"
        # ⚠️ 必须把同名 `<名字>_files/` 一起搬走。
        # Chrome 的「网页，全部」= HTML + `_files/` 两件套；只搬 HTML 会把图留成孤儿
        # （实测：Kimi 那篇的 HTML 进了 processed/，26 张图一直躺在 account/ 根目录，
        #  靠 locate_files_dir 反查才救回来——能救，但那是补丁，不该是常态）。
        files_dir = path.with_name(f"{path.stem}_files")
        # state 旁车同理：留在 inbox 里就等于把主页的数据源丢了，
        # 而归档后的 HTML **不含**那份 data（没有 `<script>`）。
        sidecar = state_sidecar_path(path)
        path.replace(target)
        if files_dir.is_dir():
            files_target = target.with_name(f"{target.stem}_files")
            if files_target.exists():
                files_target = target.with_name(f"{target.stem}-{_stamp()}_files")
            files_dir.replace(files_target)
            moved_dirs += 1
        if sidecar.is_file():
            sidecar.replace(state_sidecar_path(target))
            moved_states += 1
    print(f"\n已归档 {len(handled)} 个文件 → {archive_dir}")
    if moved_dirs:
        print(f"连同 {moved_dirs} 个 `_files/` 目录一起搬走（图跟着 HTML 走，不留孤儿）")
    elif any(path.suffix.lower() in (".html", ".htm") for path in handled):
        print("⚠️ 这次没有 `_files/` 跟着搬：如果这些是详情页，图可能没存下来"
              "（Ctrl+S 要选「网页，全部」，不是「仅 HTML」）")
    if moved_states:
        print(f"连同 {moved_states} 份 state 旁车一起搬走（主页的粉丝/简介全靠它）")
    return 0


def _stamp() -> str:
    return datetime.now().strftime("%H%M%S")


def cmd_parse_detail(args: argparse.Namespace) -> int:
    """详情页 HTML → 笔记级字段。前提：og:url 指向 /explore/<id>（否则是主页快照）。

    `--text-dir` 让单测把正文写到临时目录：真实 `account/notes-text/` 是证据，不能被测试碰
    （实测踩过——fixture 的 note_id 恰好等于真实笔记 id，6 字假正文覆盖了 167 字真正文）。
    """
    text_root = _image_text_root(args)
    updates: list[dict[str, Any]] = []
    for raw in args.files:
        path = Path(raw)
        if not path.is_file():
            print(f"❌ 找不到 {path}")
            continue
        # 必须走 open_archive 拿 state：分享数 / 发布日 / 视频秒数只在 state 里。
        try:
            archive = open_archive(path)
        except ArchiveError as exc:
            print(f"⏭  {path.name}：{exc}")
            continue
        html = archive.html
        fields = extract_detail(html, archive.state)
        if not fields.note_id:
            if '<h1' in html and 'detail-title' in html:
                print(f"⏭  {path.name}：{REJECT_REASONS['profile_saved_as_detail']}")
            else:
                print(f"⏭  {path.name}：og:url 里没有 /explore/<id> —— 这不是整页加载的详情页"
                      f"（多半是从主页点开弹层后存的，存下来的是主页）。")
            continue
        print(f"✅ {path.name}")
        print(f"   note_id={fields.note_id} | 赞 {fields.count_text('likes')}"
              f" / 藏 {fields.count_text('collects')} / 评 {fields.count_text('comments')}"
              f" / 分享 {fields.count_text('shares')} | 形式 {fields.form or '?'}"
              + (f" | 时长 {fields.duration}" if fields.duration else "")
              + (f" | 发布 {fields.published_at}" if fields.published_at else ""))
        print(f"   标题：{fields.title or '⬜'}")
        print(f"   标签：{' '.join(fields.tags) or '⬜'}")
        print(f"   正文前 60 字：{fields.body[:60] or '⬜'}")
        _report_conflicts(fields)
        if fields.missing:
            print(f"   ⚠️ 未取到：{fields.missing}（保持 ⬜，不估算）")
        archived = _archive_body(fields, text_root)
        if archived:
            print(f"   正文已归档：{archived}")
        updates.append(fields.as_row_updates())
    if not updates:
        return 1
    return _detail_rows(args, updates, allow_new=True)


def _report_conflicts(fields: Any) -> None:
    """两条通道给出不同数字时**必须喊出来**。

    静默取一个的后果不是报错，是「同一篇笔记在报告和表里显示不同的赞数」——
    到那一步已经分不清哪个是对的，而两个数字都长得一样合理。
    """
    conflicts = getattr(fields, "conflicts", None) or {}
    for name, detail in conflicts.items():
        print(f"   ⚠️ {name} 两条通道不一致（{detail}）：取 state，DOM 可能是改版后的新结构")


def _archive_body(fields: Any, text_root: Path) -> str:
    """正文落本地文件（长文本不进 CSV）。返回写入的相对路径；未写返回空串。

    **必须保留分段**：小红书的正文用裸换行分段，压平成一整块之后，
    「他分了几段 / 钩子在第几段 / 有没有空行隔断」这些结构证据就没了——
    而那正是爆款拆解要看的东西。段落之间用空行写，读起来也像原文。

    `text_root` 必须由调用方给（默认 `account/notes-text`），**不能用模块常量**：
    实测踩过——单测拿 fixture 跑 `parse-detail`，fixture 的 note_id 恰好是真实笔记 id，
    于是 6 个字的假正文把 167 字的真实正文静默覆盖了。测试污染真实证据，比测试失败危险得多。

    并加一道「不许静默变短」的闸：现有文件明显更长时保留原样，新内容另存 `.new.txt` 并报出来。
    """
    if not fields.note_id or not fields.body:
        return ""
    target_dir = text_root
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{fields.note_id}.txt"
    paragraphs = getattr(fields, "paragraphs", ()) or (fields.body,)
    body_text = "\n\n".join(paragraphs)
    # 头部用**换算后的展示形式**：`1.2万` 这种圆整值要带 ≈，否则下游读 txt 时
    # 会把它当精确数字引用出去。标题行之外的行都以 `#` 开头，字数统计会自动跳过。
    counts = " / ".join(
        getattr(fields, "count_text", lambda _name: "⬜")(name)
        for name in ("likes", "collects", "comments")
    )
    header = (
        f"# {fields.title}\n"
        f"# 标签：{' '.join(fields.tags)}\n"
        f"# 赞/藏/评：{counts}\n"
        f"# 段落数：{len(paragraphs)}\n\n"
    )
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    existing_body = sum(len(line) for line in existing.splitlines() if not line.startswith("#"))
    if existing_body > max(len(body_text) * 2, len(body_text) + 80):
        shrunken = target.with_suffix(".new.txt")
        shrunken.write_text(header + body_text, encoding="utf-8")
        print(f"   ⚠️ 已有一份更长的正文（{existing_body} 字 > 新读到的 {len(body_text)} 字），"
              f"**保留原样**；新内容另存 {shrunken.name}")
        return ""
    target.write_text(header + body_text, encoding="utf-8")
    _write_comments(fields, target_dir)
    return str(target)


def _write_comments(fields: Any, text_root: Path) -> str:
    """把**首屏**评论另存 `<note_id>.comments.txt`，一条一行、不摘要。

    为什么单独一个文件而不塞进正文 txt：正文是「他写了什么」，评论是「读者回了什么」，
    两个证据层混在一个文件里，下游读正文时会被评论区带偏（字数、段落数全错）。

    为什么保存原文而不再做摘要：摘要是转述，转述进不了 ② 级证据。
    真正做人群画像时要的是「原话里的身份词」，摘要恰恰会把它抹平。

    ⚠️ 文件名带 `comments` 后缀，`ar gaps` 的「有正文」判定只认 `<id>.txt`，不受影响。
    """
    comments = getattr(fields, "comments_list", ()) or ()
    zeros = getattr(fields, "zeros", {}) or {}
    if not comments:
        # 确认零评论的也要落一个文件：否则「这篇真没人评」和「这篇没采」
        # 在磁盘上长得一模一样，`ar gaps` 会把前者永远列进待补清单（E19 同源）。
        if "comments" not in zeros:
            return ""
        target = text_root / f"{fields.note_id}.comments.txt"
        target.write_text(
            f"# {fields.title}\n"
            f"# 声明评论数：0 | 本文件是**确认零评论**，不是没采到\n"
            f"# 判据：{zeros['comments']}\n",
            encoding="utf-8",
        )
        return str(target)
    target = text_root / f"{fields.note_id}.comments.txt"
    declared = getattr(fields, "count_text", lambda _n: "⬜")("comments")
    lines = [
        f"# {fields.title}",
        f"# 声明评论数：{declared} | 本文件是**首屏**抓到的 {len(comments)} 条，不是全部",
        f"# 抓取口径：详情页存档 DOM（人工 Ctrl+S），未点「加载更多」",
        "",
    ]
    for index, comment in enumerate(comments, 1):
        meta = " ".join(part for part in (comment.meta, comment.location) if part)
        lines.append(f"{index}. [{comment.author or '匿名'}{'｜' + meta if meta else ''}] {comment.text}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


TEXT_DIR = "notes-text"


def _image_text_root(args: argparse.Namespace) -> Path:
    """图上文字的落盘目录。单测通过 --text-dir 指到临时目录，不碰真实证据。"""
    return Path(args.text_dir) if getattr(args, "text_dir", "") else DEFAULT_TEXT_DIR


def _detail_archives(files: list[str], root: Path) -> list[Path]:
    """列出要处理的详情页存档：显式给的文件优先，否则扫 root 下所有 HTML（跳过 saved_resource）。"""
    if files:
        return [Path(raw) for raw in files if Path(raw).is_file()]
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*.html")
        if not path.name.startswith("saved_resource")
    )


def _detail_note(path: Path, html: str, root: Path) -> Any:
    """详情页存档 → 图片序列（不跑 OCR）。非详情页返回 None。"""
    try:
        archive = open_archive(path)
    except ArchiveError:
        archive = None
    if archive is not None and archive.kind != "note_detail":
        return None
    note = collect_images(path, archive.state if archive else {}, html, search_root=root)
    return note if note.note_id else None


def _ocr_note(path: Path, html: str, text_root: Path, engine: Any, root: Path) -> dict[str, Any] | None:
    """给一篇详情页存档跑图上文字通道，写 `<note_id>.images.txt`，返回摘要。

    抽出来的原因：`ar ingest` 与 `ar images --ocr` 必须是**同一段代码**。
    两处各写一份，迟早在「篇幅行怎么写 / 关系怎么判」上漂开，
    而这两行正是模型读结构结论时唯一的路标。
    """
    note = _detail_note(path, html, root)
    if note is None or note.local == 0:
        return None
    results = {image.index: ocr_file(engine, image.local) for image in note.images if image.local}
    if not results:
        return None
    target = image_text_path(text_root, note.note_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = body_text(html)
    text_chars = body_chars(html)
    target.write_text(
        render_text(note, results, {"title": extract_detail(html).title,
                                    "body_chars": text_chars, "body_text": body}),
        encoding="utf-8",
    )
    image_body = "\n".join("\n".join(result.lines) for result in results.values())
    image_chars = sum(result.chars for result in results.values())
    return {
        "target": target,
        "ocr_images": len(results),
        "image_chars": image_chars,
        "body_chars": text_chars,
        "dominance": image_dominance(image_chars, text_chars),
        "relation": layer_relation(body, image_body),
        "low": [index for index, result in results.items() if result.low_quality],
    }


def _report_ocr(summary: dict[str, Any]) -> None:
    shown = summary["target"]
    shown = shown.relative_to(REPO_ROOT) if shown.is_relative_to(REPO_ROOT) else shown
    print(f"   ✅ 图上文字 {summary['ocr_images']} 张 / {summary['image_chars']} 字 → {shown}")
    print(f"   篇幅：{summary['dominance']}")
    print(f"   关系：{summary['relation']}")
    if summary["low"]:
        print(f"   ⚠️ 低质量图 {summary['low']}：引用前必须看原图（OCR 置信 <0.80 或字数 <10）")


def _ingest_ocr(paths: list[Path], root: Path, text_root: Path, engine: Any = None) -> int:
    """`ar ingest` 收下详情页后顺手把图上文字取出来——这一步不该由人再记一次命令。

    为什么放在 ingest：`_archive_body` 已经把正文落成 `.txt`，图上文字是**同一篇笔记的另一层**，
    分两次命令做同一件事，人一定会漏掉后一半（实测：7 篇有正文、只有 4 篇有图上文字，
    漏的那 3 篇里就有 26 图那篇长文——它的结构结论全在图里）。
    OCR 引擎缺失时不当错误：这台机器没装 rapidocr 不该让入库失败，只提示一句。
    """
    if engine is None:
        try:
            engine = load_engine()
        except OcrUnavailable:
            print("\n⚠️ 没有 OCR 引擎，图上文字这一层跳过（`python -m pip install rapidocr-onnxruntime`）")
            print("   正文已经入库；只缺图片层，不阻塞。补装后跑 `ar images --ocr` 即可。")
            return 0
    done = 0
    print("\n图上文字通道（本地 OCR，图不进模型上下文）：")
    for path in paths:
        html = path.read_text(encoding="utf-8", errors="replace")
        summary = _ocr_note(path, html, text_root, engine, root)
        if summary is None:
            continue
        print(f"   {path.name[:40]}")
        _report_ocr(summary)
        done += 1
    if done == 0:
        print("   （这批详情页没有可绑定的本地图，可能只存了「仅 HTML」）")
    return done


def cmd_images(args: argparse.Namespace) -> int:
    """图片通道：正文写在图里的笔记，把字取出来落成文件。

    回答的是那句「小红书很多内容在图片里，保存 HTML 有解析图片内容吗」：
    **图默认已经存在本机了**（Chrome「网页，全部」会把图写进 `_files/`），
    缺的不是数据，是一条把图和笔记对上、并把字取出来的通道。

    两条硬边界（违反就是 R2 / 错数据）：
    1. 只读本地文件，**一个网络请求都不发**——图是人的保存动作带下来的；
    2. **图片绝不进模型上下文**：一张 1440×2400 的笔记图约 4k tokens，
       一篇 24 张就是 10 万级，那正是上下文 400 的直接来源。这里只产出 txt。
    """
    root = Path(args.root)
    archives = _detail_archives(args.files, root)
    if not archives:
        print(f"没有可处理的 HTML：{root}")
        return 1
    text_root = _image_text_root(args)
    engine = None
    if args.ocr:
        try:
            engine = load_engine()
        except OcrUnavailable as exc:
            print(f"❌ {exc}")
            return 1

    rows: list[dict[str, Any]] = []
    for path in archives:
        html = path.read_text(encoding="utf-8", errors="replace")
        note = _detail_note(path, html, root)
        if note is None:
            continue
        rows.append({
            "path": path, "note": note, "html": html,
            "text_chars": body_chars(html),
        })
    if not rows:
        print("没有识别到详情页存档（og:url 里要有 /explore/<id>）。")
        return 1
    # 排序：张数多的在前；同一篇有多份存档时，**本地图多的那份排前面**。
    # 这样「最全的那份」总是先被 OCR，缺图的旧存档自然落到「跳过」分支——
    # 少跑一次无用的 OCR，也避免 images.txt 先被缺图的写一遍再被覆盖。
    rows.sort(key=lambda row: (-row["note"].total, -row["note"].local))
    if args.limit:
        rows = rows[: args.limit]

    print(f"=== 图片通道：{len(rows)} 篇详情 ===")
    print("图片只从本地 `_files/` 读，不发任何网络请求；图不会进模型上下文。\n")
    # 同一篇笔记可能有好几份存档（重存一次就多一份）。**必须说清哪份是完整的那份**，
    # 否则人只能靠猜，而猜的结果是拿旧的、缺图的去拆——这是 2026-11-13 实测遇到的。
    seen: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        seen.setdefault(row["note"].note_id, []).append(row)
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    for row in rows:
        note = row["note"]
        declared = "" if note.declared is None else f" / 平台声明 {note.declared}"
        # 同一篇有多份存档时，标出「这份是不是最全的」——重存一次就会多一份，
        # 而旧的那份可能是缺图的。不标，人只能靠文件名猜。
        tag = ""
        group = duplicates.get(note.note_id)
        if group:
            best = max(group, key=lambda item: (item["note"].local, item["path"].stat().st_mtime))
            if best["path"] == row["path"] and note.local > 0:
                tag = "  ← 这份最全，用它"
            elif note.local < best["note"].local:
                tag = f"  ← 旧存档（少 {best['note'].local - note.local} 张图），别用"
        print(f"{note.note_id} | 图 {note.total}（本地 {note.local}{declared}）"
              f"| 正文 {row['text_chars']} 字 | {row['path'].name[:36]}{tag}")
        if note.incomplete:
            # 判据是 **local** 对 declared，不是 total——见 `NoteImages.incomplete`。
            # 说「16 < 16」会让人以为代码算错了，其实是「枚举到 16 张、只有 3 张有文件」。
            print(f"   ⚠️ **落到本地的**少于平台声明（本地 {note.local} < 声明 {note.declared}）："
                  f"上面这些图里有一部分没存下来，别把这份当成完整正文")
        if note.missing():
            print(f"   ⬜ 未落到本地：图 {note.missing()[:8]}"
                  f"{'…' if len(note.missing()) > 8 else ''}"
                  f"（懒加载的图要滚动到那一张才会被存进存档）")
        if note.local == 0:
            print("   ⬜ 这批一张图都没有：重新 Ctrl+S 时选「网页，全部」才带图")
            continue
        if engine is None:
            continue
        # 同一篇有多份存档时**只给最全的那份跑 OCR**：后跑的会覆盖 `images.txt`，
        # 让缺图的旧存档覆盖掉完整的，正是这轮踩的坑。
        if group and note.local < max(item["note"].local for item in group):
            print("   ⏭ 跳过 OCR：这篇有更全的存档，用它跑出来的才作数")
            continue
        summary = _ocr_note(row["path"], row["html"], text_root, engine, root)
        if summary:
            _report_ocr(summary)
    if engine is None:
        print("\n提示：加 `--ocr` 才会把图上的字取出来（需 `python -m pip install rapidocr-onnxruntime`）。")
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    """今天的采集额度（节流表，不是执行器）。

    **它只告诉人还能采多少，不会自己去采。** 这就是「控制规模」的落地形态：
    规模限制的意义是「降低单日请求量」，所以它必须约束**人的动作**，
    不可能用一段会自己发请求的代码去实现——那等于用违规手段做合规保护。

    额度怎么定：见 docs/采集节流-额度与依据.md。默认 3 号/日、30 详情/日。
    """
    accounts = store.read_table(args.data, "accounts")
    notes = store.read_table(args.data, "notes")
    # 只算「人真的打开过的页面」：主页存档 = 公开页通道；详情 = 复制文本/截图通道。
    # 一次主页解析会产出 30+ 行笔记，但那是**一个**页面动作，不能算成 30 次详情采集。
    profile_accounts = sorted({
        str(r.get("account_id")) for r in accounts
        if str(r.get("collected_on")) == args.on and str(r.get("source_kind")) == "公开页"
    })
    detail_notes = sorted({
        str(r.get("note_id")) for r in notes
        if str(r.get("collected_on")) == args.on and str(r.get("source_kind")) in ("复制文本", "截图")
    })
    left_a = max(0, args.accounts - len(profile_accounts))
    left_n = max(0, args.notes - len(detail_notes))

    print(f"=== {args.on} 采集额度 ===")
    print(f"主页：已采 {len(profile_accounts)} / {args.accounts}    剩余 {left_a}")
    print(f"详情：已采 {len(detail_notes)} / {args.notes}    剩余 {left_n}")
    print()
    over = len(profile_accounts) > args.accounts or len(detail_notes) > args.notes
    if over:
        print("⚠️ 已超额度。**今天停手**——超额本身就是风险信号，不是成就。")
        return 1
    if left_a == 0 or left_n == 0:
        print("今日额度用尽，明天再继续。中断不是失败：分天取样本来就是 SOP 的一部分。")
        return 0
    print(f"今天还能采：{left_a} 个账号主页、{left_n} 篇详情。")
    print(f"下一步：`ar links --limit {min(left_n, 5)}` 拿清单 → 看板里中键连开 → Ctrl+S → `ar ingest`")
    return 0


def _match_pattern(name: str, patterns: Sequence[str]) -> bool:
    """文件名通配符匹配（大小写不敏感）。

    为什么不用 `Path.glob`：这里要按**单个文件名**过滤，不是遍历；
    而且 Windows 上用户很可能写成 `AR-PAGE-*`，大小写不该成为坑。

    多个 pattern 是「或」的关系：一次采集里大概率同时有扩展存的 `.mhtml`
    和手动 Ctrl+S 的 `… - 小红书.html`，只给一个白名单必然漏掉一类。
    """
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns)


def _split_patterns(raw: Sequence[str] | None) -> list[str]:
    """`--pattern` 可重复、也可逗号分隔——两种写法都收，避免人猜。"""
    patterns: list[str] = []
    for item in raw or ():
        for piece in str(item).split(","):
            piece = piece.strip()
            if piece:
                patterns.append(piece)
    return patterns


def _print_watch_setup(inbox: Path, patterns: Sequence[str]) -> None:
    """打印「怎么让它全自动」——这段是给人照着抄的，不是日志。"""
    print("   ── 怎么让它全自动 ──")
    print()
    print("   扩展存的文件名固定是 `ar-page-<note_id>.mhtml`，所以最稳的做法是")
    print("   盯下载目录 + 只认这个前缀，Chrome 设置一个字都不用改：")
    print()
    print(r'     python ar.py watch --path "%USERPROFILE%\Downloads"'
          r' --pattern "ar-page-*"')
    print()
    print("   这样你其他的正常下载一个都不会被收走。")
    print("   只按后缀过滤是不够的：别人的 `xxx.html` 也是 `.html`。")
    print()
    print(f"   当前监视：{inbox}")
    if patterns:
        print(f"   白名单：{'、'.join(patterns)}（大小写不敏感）")
    else:
        print("   白名单：没设 —— 该目录下所有 HTML/TXT 都会被收（下载目录请务必设）")
    print(f"   认的格式：{'/'.join(x.lstrip('.') for x in INGEST_SUFFIXES)}；"
          "其余文件不删、不挪、不报错。")
    print()


def cmd_host(args: argparse.Namespace) -> int:
    """本地宿主：注册 / 注销 / 体检 / 跑协议循环。

    ## 为什么要有这个命令

    在此之前，「点扩展图标」只是把 MHTML 存到下载目录，然后**必须另开一个终端**
    跑 `ar watch` 才算入库。用户的原话：

    > 我们应该是一整个插件包含采集能力，现在听起来是多个进程分散了

    宿主就是那条把两边缝起来的线：扩展通过原生消息把字节直接交给它，
    它再走 `ar ingest` 的同一条代码路径。于是「点一下」就是全部动作。

    ## 注册只做两件事，但两件事都不能省

    1. 写 `HKCU\\Software\\Google\\Chrome\\NativeMessagingHosts\\<name>`（不用管理员权限）；
    2. 生成一个 `.bat` 与一份清单 JSON。

    `.bat` 不是多余的：Chrome 启动宿主时**参数表为空**，把脚本路径写进 `path` 字符串
    是不会被解析的（整串会被当文件名）。所以必须有一个可执行文件自己知道要跑哪个脚本。
    """
    paths = hostsetup_module.HostPaths.under(REPO_ROOT / "account-research")

    if getattr(args, "run", False):
        # Chrome 通过生成的 .bat 走这条路。手敲也能用：它从 stdin 读帧。
        return host_module.main()

    if getattr(args, "register", False):
        written = hostsetup_module.register(paths)
        print("✅ 已注册本地宿主")
        print(f"   注册表：{paths.registry_key}")
        print(f"   清单  ：{paths.manifest}")
        print(f"   启动  ：{paths.launcher}")
        print(f"   扩展 ID：{paths.extension_id}")
        print()
        print("接下来两步（只做一次）：")
        print("   1. Chrome → chrome://extensions → 打开「开发者模式」→「加载已解压的扩展程序」")
        print(f"      → 选中 {REPO_ROOT / 'account-research' / 'extension'}")
        print("   2. 打开几篇笔记 → 点扩展图标。badge 显示 ✓N 就是入库了。")
        print()
        print("验证：`python ar.py host --doctor`")
        return 0 if written else 1

    if getattr(args, "unregister", False):
        removed = hostsetup_module.unregister(paths)
        if not removed:
            print("本来就没注册过（注册表项与两个文件都不在）。")
            return 0
        print("✅ 已注销，删掉了：")
        for item in removed:
            print(f"   {item}")
        return 0

    # 默认动作是体检：人跑到这里，十次有九次是「连不上」。
    checks = hostsetup_module.diagnose(paths)
    print("=== 本地宿主体检 ===")
    for passed, detail in checks:
        print(f"   {'✅' if passed else '❌'} {detail}")
    print()
    ok, detail = hostsetup_module.roundtrip(paths)
    print(f"   {'✅' if ok else '❌'} {detail}")
    print()
    if all(passed for passed, _ in checks) and ok:
        print("全部通过：扩展点一下就会直接入库，不需要再开终端。")
        return 0
    print("有项目没过。按上面的 ❌ 逐条修，然后重跑本条命令。")
    if not any(passed for passed, _ in checks):
        print("从这一条开始：`python ar.py host --register`")
    return 1


def cmd_extension(args: argparse.Namespace) -> int:
    """扩展的安装状态：目录在哪、宿主注册了没、Chrome 里加载了没。"""
    extension_dir = REPO_ROOT / "account-research" / "extension"
    manifest_path = extension_dir / "manifest.json"
    paths = hostsetup_module.HostPaths.under(REPO_ROOT / "account-research")
    print("=== 扩展 ===")
    if not manifest_path.is_file():
        print(f"❌ 找不到 {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"   目录：{extension_dir}")
    print(f"   版本：{manifest.get('version')}")
    print(f"   权限：{'、'.join(manifest.get('permissions', []))}")
    print(f"   ID  ：{paths.extension_id}")
    print()
    registered = hostsetup_module.current_registration(paths)
    print("=== 宿主 ===")
    print(f"   {'✅ 已注册 → ' + registered if registered else '❌ 没注册（跑 `python ar.py host --register`）'}")
    print()
    print("=== Chrome 里加载它 ===")
    print("   chrome://extensions → 开发者模式 → 加载已解压的扩展程序 → 选上面那个目录")
    print("   （已经加载过的话，改完代码点一次「重新加载」）")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """守着采件箱，文件落地即入库。

    **它监视的是本地文件夹，不是任何网页。** 浏览器下载完成的那一刻文件才出现，
    本命令只对已经落盘的文件做事——边界没有移动：页面依然由你打开，请求依然由你产生。

    为什么值得做：逐篇采时最烦的不是点图标，是**每采一篇要回终端跑一次命令**。
    有了它，你可以一次采 10 篇，全程不看终端。

    ## 三个让它「真的能一直开着」的细节（2026-11-13 补）

    1. **日期在入库那一刻才取**，不在启动时取。原来 `--on` 的默认值在
       `build_parser()` 里算好，于是**开着过夜，第二天采的全被记成昨天**——
       采集日期是本项目所有对账的基准，错一天会让「同日额度」「D1 到期」全部算错。
    2. **等文件写完再动它**（`--settle`，默认 1 秒）。浏览器下载大文件时，
       文件会先出现再慢慢写完；这时去读会读到**半份 HTML**，
       而半份 HTML 往往还能被解析成「合法但残缺」的笔记——**静默错数据**。
       判据用 mtime：文件的修改时间距今不足 `settle` 秒就先不碰。
    3. **`--once` 扫一遍就退出**。有它才能把这件事放进定时任务，
       也才能在测试里确定性地验证「落地即入库」。
    """
    inbox = Path(args.path)
    inbox.mkdir(parents=True, exist_ok=True)
    settle = float(getattr(args, "settle", 1.0))
    once = bool(getattr(args, "once", False))
    # `--pattern` 是为「直接把 Chrome 下载目录设成采件箱」准备的：
    # 那个目录里全是用户的正常下载，只按后缀过滤太容易误收（一份别人发来的 .html 也算）。
    patterns = _split_patterns(getattr(args, "pattern", None))
    seen: set[str] = set()
    ingested = 0
    failed = 0

    def scan() -> list[Path]:
        """挑出「看起来已经写完」的候选文件。"""
        now = time.time()
        ready: list[Path] = []
        for path in sorted(inbox.iterdir()):
            if not path.is_file() or str(path) in seen:
                continue
            if path.suffix.lower() not in INGEST_SUFFIXES:
                continue  # 下载目录里常有别的文件，一律不碰（不删、不挪、不报错）
            if patterns and not _match_pattern(path.name, patterns):
                continue
            try:
                if now - path.stat().st_mtime < settle:
                    continue  # 还在写，下一轮再说
            except OSError:
                continue
            ready.append(path)
        return ready

    print(f"👀 正在监视 {inbox}")
    print(f"   每 {args.interval:g} 秒扫描一次；文件落进来就自动入库。"
          + ("扫一遍就退出。" if once else "Ctrl+C 停止。"))
    print()
    _print_watch_setup(inbox, patterns)
    try:
        while True:
            for path in scan():
                seen.add(str(path))
                if not path.is_file():
                    continue
                print(f"── 发现 {path.name}")
                sub_args = argparse.Namespace(
                    # 日期在这里才取：开着过夜也不会把第二天的采集记成第一天。
                    path=[str(path)], on=args.on or today(), data=args.data,
                    ledger=args.ledger,
                    # 字段名必须与 `ingest` 的 argparse 完全一致（`no_new_authors`）。
                    # 这里曾经拼过一个 `new_authors`，而 `_detail_rows` 读的是另一个名字——
                    # 少字段的 Namespace 只在「恰好有详情页」时才炸（E: 靠约定维持的隐式契约）。
                    no_new_authors=getattr(args, "no_new_authors", False),
                    text_dir=getattr(args, "text_dir", ""),
                )
                try:
                    code = cmd_ingest(sub_args)
                except Exception as exc:  # 单个坏文件不该让监视线程死掉
                    print(f"⚠️ 入库失败：{exc}")
                    code = 1
                if code == 0:
                    ingested += 1
                    print()
                else:
                    failed += 1
                    print("（文件留在原地，修好后再跑）\n")
            if once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
    print(f"本次自动入库 {ingested} 个文件"
          + (f"，{failed} 个失败" if failed else "")
          + "。跑 `ar status` 看当前进度。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """进度总览：这一步该干什么，不用翻文档。

    采集是长跑活动，最贵的不是单次操作，是**每次坐下都要重新搞清楚进度**。
    """
    accounts = store.read_table(args.data, "accounts")
    notes = store.read_table(args.data, "notes")
    if not accounts:
        print("库里还没有账号。")
        print("下一步：把主页存档丢进 account/inbox/ → `ar ingest`")
        return 0
    merged = _merged_notes(args)
    text_dir = REPO_ROOT / "account" / TEXT_DIR
    with_text = sum(1 for row in merged if (text_dir / f"{row.get('note_id')}.txt").is_file())
    with_images_text = sum(1 for row in merged if image_text_path(text_dir, str(row.get("note_id"))).is_file())
    missing = [row for row in merged if _missing_fields(row)]
    # 🔧 「数字齐」不等于「能分析」：正文才是要读的东西。这 2 类缺口分开报，
    # 否则 status 会显示「待补 60」而实际要打开的是 62 篇（差在 2 篇有数字没正文的）。
    no_body = [row for row in merged if not (text_dir / f"{row.get('note_id')}.txt").is_file()]
    numbers_done_no_body = [row for row in no_body if not _missing_fields(row)]
    need_open = len({row.get("note_id") for row in missing} | {row.get("note_id") for row in no_body})
    print(f"账号 {len(accounts)} 个 | 笔记 {len(merged)} 篇（原始 {len(notes)} 行，含多渠道观测）")
    # 占位账号（只有详情页、没有主页存档）单独报一档。
    #
    # 为什么必须单列：采集不再拒收外来作者之后，随手点开的别人的笔记会在 `accounts`
    # 里留下一行空壳——粉丝/简介/笔记总数全是 ⬜。**不报出来，它就是看不见的噪音**；
    # 报出来，它是一份「还需补哪几个号的主页」的清单。
    placeholders = [row for row in store.latest_per_account(accounts)
                    if store.is_placeholder_account(row)]
    if placeholders:
        names = "、".join(str(row.get("nickname") or row.get("account_id"))[:12]
                          for row in placeholders)
        print(f"⚠️ {len(placeholders)} 个账号只有详情页、缺主页存档（粉丝/简介 ⬜）：{names}")
        print("   补法：整页打开那个号的主页 → 点扩展图标。采到后占位行会被整行替换。")
    print(f"有正文归档 {with_text} 篇 | 有图上文字 {with_images_text} 篇"
          f" | 藏/评齐全 {len(merged) - len(missing)} 篇 | 缺藏/评 {len(missing)} 篇"
          f" | 缺正文 {len(no_body)} 篇（其中 {len(numbers_done_no_body)} 篇数字已齐）")
    print(f"→ 实际还要打开 {need_open} 篇详情页（`ar links` 会把这批都列出来）")
    if with_images_text < with_text:
        # 两个数字并排放着还不够：实测 7 vs 4 摆了一整天没人看出来。
        # 正文有、图上文字没有 = 这篇只读了一半，必须明说。
        print(f"⚠️ 有 {with_text - with_images_text} 篇是「有正文、没图上文字」："
              f"正文层可能只是导语，完整内容在图上。跑 `ar images --ocr` 补齐。")
    print()
    inbox = DEFAULT_INBOX
    pending_files = [p for p in inbox.iterdir() if p.is_file()] if inbox.is_dir() else []
    print(f"采件箱 {inbox}：{'有 ' + str(len(pending_files)) + ' 个文件待入库' if pending_files else '空'}")
    print()
    issues = validate_dir(args.data, DEFAULT_LEDGER)
    errors = [issue for issue in issues if issue.level == "ERROR"]
    print(f"校验：{len(errors)} ERROR / {len(issues) - len(errors)} WARN"
          + ("（有错误，先修再继续）" if errors else " ✅"))
    print()
    _report_alerts(args)
    quota = _quota_line(args.data)
    if quota:
        print(quota)
    # 采集端的路怎么走，取决于宿主注册了没。**这是「下一步」该说的话**，
    # 而不是让人去翻文档猜「到底要不要开个终端」。
    host_ready = bool(hostsetup_module.current_registration(
        hostsetup_module.HostPaths.under(REPO_ROOT / "account-research")))
    if pending_files:
        print("下一步：`ar ingest` 把采件箱里的文件入库。")
    elif host_ready:
        print("下一步：打开几篇笔记（图片栏滑到底）→ 点扩展图标。**会自己入库，不用开终端**。")
    elif missing:
        print("下一步：`ar links --limit 5 --out account-research/data/todo-detail.md` 拿待采清单。")
        print("       采集方式二选一：")
        print("       · 点扩展图标（推荐）—— 但要先跑一次 `python ar.py host --register`")
        print("       · 逐篇 Ctrl+S（「网页，全部」）→ `ar ingest`")
    else:
        print("下一步：数据齐了，可以跑拆解（`ar add verdicts` 等四张表）。")
    return 0


def _report_alerts(args: argparse.Namespace) -> None:
    """打印纪律告警（到期未复核 / 假设逾期 / 台账断流）。

    为什么放进 `status`：这是唯一一个「每次坐下都会跑」的命令。
    单独做个 `ar alerts` 的话，它会变成「知道有、但从不跑」的第二条命令——
    E11 的教训：一件事要两条命令，第二条就永远不会跑。
    """
    alerts = alert_rules.collect(
        store.read_table(args.data, "verdicts"),
        store.read_table(args.data, "hypotheses"),
        args.ledger or DEFAULT_LEDGER,
        today(),
    )
    if not alerts:
        return
    print(f"⚠️ 纪律告警 {len(alerts)} 条：")
    for alert in alerts:
        print(f"  [{alert.code}] {alert.message}")
    print()


def _quota_line(data_dir: str | Path) -> str:
    """把剩余额度并进 status 一行，省掉一次单独命令——但额度仍是只读。"""
    on = today()
    accounts = store.read_table(data_dir, "accounts")
    notes = store.read_table(data_dir, "notes")
    done_a = len({str(r.get("account_id")) for r in accounts
                  if str(r.get("collected_on")) == on and str(r.get("source_kind")) == "公开页"})
    done_n = len({str(r.get("note_id")) for r in notes
                  if str(r.get("collected_on")) == on and str(r.get("source_kind")) in ("复制文本", "截图")})
    return f"今日额度：主页 {done_a}/3 | 详情 {done_n}/30（`ar quota` 看详情）"


def cmd_validate(args: argparse.Namespace) -> int:
    issues = validate_dir(args.data, args.ledger)
    errors = [issue for issue in issues if issue.level == "ERROR"]
    warns = [issue for issue in issues if issue.level != "ERROR"]
    for issue in errors + warns:
        mark = "❌" if issue.level == "ERROR" else "⚠️"
        print(f"{mark} [{issue.code}] {issue.message}")
    print(f"\n校验结果：{len(errors)} ERROR / {len(warns)} WARN")
    return 1 if errors else 0


def cmd_report(args: argparse.Namespace) -> int:
    """把四张结论表渲染成 markdown——**报告不再手写**。

    为什么必须有这条命令：前两份报告（卡兹克、知新）都是手抄数字、手排版式的，
    每拆一个新号就要重抄一遍，而手抄会漂（实测把 27/31 写成 29/31，读起来完全合理）。
    命令化之后「改表即改报告」，版式与口径只维护一处。

    默认**打印到终端**而不是直接写文件：先让人看一眼，`--out` 才落盘。
    与 `ar debrief` 默认不落库同理——自动产物要先过人的眼睛。
    """
    text_dir = _image_text_root(args)
    account_id = args.account or _only_account(Path(args.data))
    k_values = tuple(int(part) for part in str(args.k).split(",") if part.strip())
    try:
        data = report.collect(Path(args.data), account_id, text_dir, today=args.on, k_values=k_values)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1
    text = report.render(data, notes_limit=args.notes_limit)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"已写入 {target}（{len(text)} 字）")
        print(f"  findings {len(data.findings)} | playbook {len(data.playbook)}"
              f" | hypotheses {len(data.hypotheses)} | verdicts {len(data.verdicts)}")
        return 0
    print(text)
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    """跨账号矩阵视图（S4）。

    为什么要一条独立命令而不是塞进 `ar report`：两者回答的问题不同——
    `report` 是**单账号纵深**（这个号能不能抄），`matrix` 是**跨账号横切**
    （这个领域谁在做什么、哪条打法被验证过几次）。硬合成一页会让
    「单账号结论」和「跨账号规律」混在一起，而后者恰恰是样本不够时最容易被编出来的东西。

    所以本命令**拒绝在 <2 个账号时给跨账号结论**：页面照出，但 §4 会明写「未达成」。
    """
    text_dir = _image_text_root(args)
    k_values = tuple(int(part) for part in str(args.k).split(",") if part.strip())
    accounts = args.account or [
        str(row.get("account_id")) for row in store.read_table(args.data, "accounts")
        if str(row.get("account_id") or "").strip()
    ]
    if not accounts:
        print("❌ accounts 表里没有账号——先 `ar parse` 或 `ar ingest`。")
        return 1
    reports = []
    for account_id in accounts:
        try:
            reports.append(report.collect(Path(args.data), account_id, text_dir,
                                          today=args.on, k_values=k_values))
        except ValueError as exc:
            print(f"❌ {exc}")
            return 1
    data = matrix.collect(reports, args.data, today=args.on)
    text = matrix.render(data)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        crossed = sum(
            1 for row in data.playbook
            if matrix.support_count(row.get("supporting_accounts")) >= matrix.CROSS_ACCOUNT_THRESHOLD
        )
        print(f"已写入 {target}（{len(text)} 字，{len(data.accounts)} 个账号）")
        print(f"  打法 {len(data.playbook)} 条，其中跨号验证 {crossed} 条")
        if not crossed:
            print("  ⚠️ Q2 未达成：没有一条打法在 ≥2 个账号上验证过")
        print(f"打开：start {target}    或双击该文件")
        return 0
    print(text)
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    """跨表检索。**查不到就写「库内无证据」，不给近似结论。**

    PRD §1 把「不能问答」的根因归到「没有可检索单元」，§5 F7 又规定
    「回答必须带 ≥1 条 finding 编号；查不到就答『库内无证据』」。
    这条命令就是那个执行面：它只做筛选与列行，不做归纳——
    归纳那一步属于模型，而模型必须**先拿到带编号的行**才允许开口。

    为什么文本通道要显式开关（`--text`）：正文/图上文字/评论是长文本，留在文件里，
    不在表里。默认把它们混进检索会让「命中」的含义变得不确定
    （一会儿是一行结论，一会儿是一段正文），而检索的价值全在含义确定。
    """
    try:
        result = find_module.search(
            args.data,
            args.keywords,
            tables=args.table or None,
            text_dir=_image_text_root(args),
            text=args.text,
            layer=args.layer,
            form=args.form,
            status=args.status,
            grade=args.grade,
            account=args.account,
            min_support=args.min_support,
        )
    except find_module.FilterError as exc:
        print(f"❌ {exc}")
        return 1
    if args.json:
        print(json.dumps(find_module.as_payload(result), ensure_ascii=False, indent=2))
    else:
        print(find_module.render(result, limit=args.limit))
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    """结案一条假设。**追加一行新状态，不改任何历史行。**

    为什么必须有这条命令：D2 告警连喊 10 天「证伪或证实都算结案」，
    而 `ar add hypotheses` 会因键已存在静默跳过（实测「新增 0 行」）——
    系统要求人做一件命令行做不到的事，守纪律的唯一方式是手改 CSV，而那被明令禁止。

    这条命令只是把「结构上已经允许的事」（`recorded_on` 进键）暴露成一个入口。
    """
    rows = store.read_table(args.data, "hypotheses")
    try:
        closed = close_rules.close_hypothesis(
            rows, args.hypothesis_id, args.status, note=args.note, on=args.on
        )
    except close_rules.CloseError as exc:
        print(f"❌ {exc}")
        return 1

    previous = next(
        (str(row.get("status") or "") for row in store.latest_hypotheses(rows)
         if str(row.get("hypothesis_id")) == str(args.hypothesis_id)),
        "",
    )
    print(f"结案 {args.hypothesis_id}：{previous} → {closed['status']}"
          f"（recorded_on {closed['recorded_on']}）")
    if closed.get("close_note"):
        print(f"   理由：{closed['close_note']}")
    if args.dry_run:
        print("\n--dry-run：未写入。去掉该参数才落库。")
        return 0
    print()
    return _commit_rows(args, "hypotheses", [closed])


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
    _report_approx(result)
    return 0


def _report_approx(result: Any) -> None:
    """把「哪些数字不是精确值」说出来。

    平台上万之后只渲染 `1.2万`，真实值可能是 11,500 也可能是 12,499。这些行参与统计没问题，
    但报告里逐篇引用时**必须标 ≈**——不标就等于用三位有效数字冒充四位，而且看不出来。
    解析不了的计数（既不是数字也不是万/K）单独报，因为它真的没被算进样本。
    """
    if result.approx_rows:
        print(f"≈ 其中 {result.approx_rows} 篇的点赞是圆整值（形如「1.2万」，平台只给 3 位有效数字）："
              f"参与统计，但逐篇引用须标 ≈，不得当精确值写进报告")
    if result.unparsable:
        print(f"⚠️ {result.unparsable} 篇的点赞既不是数字也不是「万/K」形态，**已排除出样本**"
              f"——这不是正常现象，去看这几篇的原始存档")


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


def cmd_debrief(args: argparse.Namespace) -> int:
    """一键拆解：拼任务 → `dsh headless` 跑模型 → 产物收回本地 → （可选）校验后落库。

    这是方向卡 S2 的那条缝：确定性的活全在 `ar` 里，只有判断交给模型。
    三件事必须由代码保证，不能交给提示词：
    1. 任务文本由 `schema.py` 现取列名拼出——手抄的列名会漂，漂了是静默错数据；
    2. 运行时关掉 web / shell 工具——红线是「不访问任何平台域名」，靠代码关才成立；
    3. 默认**不落库**：产物先落 `payload.json` 等人看一眼，`--commit` 才过 V1–V8 写表。
       append-only 表没有回滚，模型的自评不能替代校验。
    """
    try:
        return _debrief(args)
    except DebriefError as exc:
        print(f"❌ {exc}")
        return 1


def _debrief(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    account_id = args.account or _only_account(data_dir)
    text_dir = _image_text_root(args)
    k_values = tuple(int(part) for part in str(args.k).split(",") if part.strip())
    snapshot = collect_snapshot(data_dir, account_id, text_dir, k_values)
    nickname = str(snapshot.account.get("nickname") or account_id)
    task = build_task(snapshot, REPO_ROOT)
    out_dir = Path(args.out) if args.out else (
        data_dir / "debrief" / f"{nickname}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    print(f"账号 {nickname}（{account_id}）：样本 {snapshot.stats.n_sample} 篇"
          f"｜正文 {len(snapshot.body_ready)} 篇｜图上文字 {len(snapshot.images_ready)} 篇")
    if args.dry_run:
        # 不落盘：dry-run 只是「让我看看任务长什么样」，写进 data/debrief 会留一堆
        # 只有 task.md 的空运行目录，事后分不清哪次真跑过。
        print(f"\n--dry-run：不调模型、不落盘（任务 {len(task)} 字）。任务全文如下：\n")
        print(task)
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "task.md").write_text(task, encoding="utf-8")
    print(f"任务已写入 {out_dir / 'task.md'}（{len(task)} 字）")
    if args.replay:
        return _replay(args, out_dir, snapshot, account_id)
    patch_file = _runtime_patch(args, out_dir)
    print(f"运行时覆盖层：{patch_file}")
    print(f"调用 dsh headless（超时 {args.timeout}s）…")
    result = run_headless(task, patch_file, args.timeout, args.dsh, REPO_ROOT)
    (out_dir / "final.md").write_text(result.final_text, encoding="utf-8")
    (out_dir / "events.json").write_text(
        json.dumps({"session": result.session_id, "exit": result.exit_code, "turn": result.turn_reason,
                    "tool_calls": result.tool_calls, "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens, "events": result.events},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"模型返回：exit {result.exit_code} | turn {result.turn_reason or '?'} | 工具调用 {result.tool_calls} 次"
          f" | tokens {result.input_tokens}→{result.output_tokens}")
    payload = extract_payload(result.final_text)
    return _finish(payload, out_dir, snapshot, account_id, args)


def _replay(args: argparse.Namespace, out_dir: Path, snapshot: Any, account_id: str) -> int:
    """拿已有 payload.json 重走检查与落库，不再花一次模型调用。

    为什么需要它：模型一轮 60 万 input tokens。闸门判错时要能改 JSON 重跑后半段，
    否则「修一个数字」的代价是重跑整轮，人就会去绕过闸门。
    """
    source = Path(args.replay)
    if source.is_dir():
        source = source / "payload.json"
    payload = _load_json(source)
    if not isinstance(payload, dict):
        print(f"❌ {source} 不是一个四表对象。")
        return 1
    unknown = sorted(set(payload) - set(TARGET_TABLES))
    if unknown:
        print(f"❌ 有不认识的表：{unknown}（只收 {' / '.join(TARGET_TABLES)}）")
        return 1
    payload = {table: payload.get(table) or [] for table in TARGET_TABLES}
    return _finish(payload, out_dir, snapshot, account_id, args)


def _finish(payload: Any, out_dir: Path, snapshot: Any, account_id: str, args: argparse.Namespace) -> int:
    """落库前的公共收尾：丢空行 → 补归属与日期 → 过闸门 → 写 payload.json → 可选落库。"""
    payload, dropped = drop_empty_rows(payload)
    if dropped:
        print(f"丢弃整行空值 {dropped} 行（模板占位被原样带回来的，不是结论）")
    payload = normalize_rows(payload, account_id, args.on)
    overrides = collect_date_overrides(payload)
    if overrides:
        # 一条一行会刷屏（实测 12 行 findings 就是 12 条一样的警告），
        # 反而让人不看内容。按「表.列 → 原值」聚合，一眼看得出模型错在哪。
        grouped: dict[str, set[str]] = {}
        for table, column, written in overrides:
            grouped.setdefault(f"{table}.{column}", set()).add(written or "(空)")
        print(f"⚠️ 日期已按本次拆解日 {args.on} 覆盖（日期是确定性的：它该是「拆解发生在哪天」，"
              f"不是数据窗口的末日）：")
        for key, values in grouped.items():
            print(f"   {key}：模型写成 {'、'.join(sorted(values))}")
    problems = check_payload(payload) + audit_ratios(snapshot, payload)
    if problems:
        rejected = out_dir / "payload.rejected.json"
        rejected.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for problem in problems:
            print(f"❌ {problem}")
        print(f"\n未落库。有问题的产出留在 {rejected}，改完 JSON 后 `ar add` 或重跑。")
        return 1
    payload_file = out_dir / "payload.json"
    payload_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"产出：{payload_summary(payload)} → {payload_file}")
    if not args.commit:
        print("\n未落库（默认）。看一眼 payload.json，确认后加 --commit 重跑，或逐表 `ar add`。")
        return 0
    updates = {table: rows for table, rows in payload.items() if rows}
    if not updates:
        print("四张表都是空的——没有可落库的内容。")
        return 1
    code = _commit_tables(args, updates)
    if code == 0:
        print(f"\n已落库，产物目录：{out_dir}")
    return code


def _only_account(data_dir: Path) -> str:
    accounts = store.read_table(data_dir, "accounts")
    known = sorted({str(row.get("account_id") or "") for row in accounts} - {""})
    if len(known) == 1:
        return known[0]
    raise DebriefError(
        f"库里{'有多个账号' if known else '还没有账号'}，请用 --account 指定：{'、'.join(known) or '（空）'}"
    )


def _runtime_patch(args: argparse.Namespace, out_dir: Path) -> Path:
    """生成并落盘运行时覆盖层。写进产物目录，事后可复现这次用了什么模型、关了哪些工具。"""
    if args.patch and args.patch != "auto":
        source = Path(args.patch)
    else:
        source = find_source_patch(Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh"))
        if source is None:
            raise DebriefError("找不到可借模型路由的档案（desktop / web 都没有 cordis.patch.yml）。"
                               "用 --patch <文件> 指定一份含 llm-pi-ai 的覆盖层。")
    target = out_dir / "runtime.patch.yml"
    target.write_text(build_runtime_patch(source), encoding="utf-8")
    return target


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


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    return int(args.func(args))
