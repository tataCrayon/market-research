"""一键拆解：把 skill 契约交给 `dsh headless`，再把模型产出收回本地。

为什么需要这一层（方向卡 S2）：
    工作台/CLI 负责确定性工作（解析、入库、统计、校验），模型只负责**判断**。
    但「谁来拼任务、谁来关工具、谁来收产物」如果留给人，每跑一次都要人工抄一遍
    契约——那正好是 agent-run-001 里出过的那类错（先写后校验、字段自造）。

三条设计约束：
1. **提示词由代码生成**：列名从 `schema.py` 现取，不手抄。手抄的列名会漂，
   漂了就是静默错数据。
2. **运行时关网**：headless profile 默认带 web 搜索/抓取工具，而本项目红线是
   「不访问任何平台域名」。所以覆盖层必须把 `tool-web` 关掉，不能只靠提示词自觉。
3. **图片不进上下文（R7）**：任务里只出现 `notes-text/<id>.images.txt` 这一层 OCR 文本，
   显式禁止打开任何图片文件。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import schema
from .stats import summarize

# 拆解产出只落这四张表：notes/accounts 是采集产物，fields 是口径登记
TARGET_TABLES = ("verdicts", "findings", "playbook", "hypotheses")

# 从哪个 DSH 档案里借模型路由：desktop 是日常在用的那个，web 次之
PROFILE_ORDER = ("desktop", "web")
BORROW_IDS = ("llm-pi-ai", "agent-default-model")

# headless 默认把这些能力挂进来。关掉它们 = 把红线变成代码，而不是留给模型自觉：
#   tool-web  —— 搜索 / 抓取，直连平台域名的最短路径
#   tool-pwsh / tool-bash / tool-jobs —— 一条 `curl` 就能绕过上面那条，必须一起去掉
#   tool-subagent / tool-subagent-fork / tool-workflow —— 会派生更多回合，
#       预算和边界都变得不可数；一次拆解本来就该是一条单线程推理
# 拆解只需要读本地文件（tool-fs），不需要执行任何命令、也不需要派生子代理。
NETWORK_ROWS = (
    "tool-web", "tool-pwsh", "tool-bash", "tool-jobs",
    "tool-subagent", "tool-subagent-fork", "tool-workflow",
)

ENTRY_SPLIT_RE = re.compile(r"(?m)^- ")
ID_RE = re.compile(r"^\s*-\s*id:\s*([^\s#]+)", re.MULTILINE)
FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)

NO_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")


class DebriefError(RuntimeError):
    """面向人的失败：消息直接可读，不需要包一层 stack。"""


# ---- 运行时覆盖层：借模型路由 + 关网络 ----


def dsh_home() -> Path:
    raw = os.environ.get("DSH_HOME", "")
    return Path(raw) if raw else Path.home() / ".dsh"


def yaml_entries(text: str) -> list[str]:
    """把顶层 `- ` 开头的块切开（块内缩进的行跟着块走）。

    不引 YAML 依赖：这些 patch 文件是块式 YAML，顶层 `- ` 是可据的结构。
    """
    parts = ENTRY_SPLIT_RE.split(text)
    if len(parts) <= 1:
        return []
    return [f"- {part}" for part in parts[1:] if part.strip()]


def entry_id(block: str) -> str:
    match = ID_RE.search(block)
    return match.group(1) if match else ""


def borrowed_entry(block: str) -> str:
    """整理借来的行：补 `name:`，并切掉尾部夹进来的整行注释。

    为什么要切注释：源档案里常有紧跟在条目后面、缩进归零的说明注释（例如「浏览器能力…
    禁止打开平台域名」）。按 `^- ` 切块时这类注释会粘在上一块尾部，原样搬进覆盖层
    就会让「借用模型路由」的文件里混进与本次无关的说明。
    """
    lines = block.rstrip().splitlines()
    while lines and lines[-1].lstrip().startswith("#"):
        lines.pop()
    block = "\n".join(lines) + "\n"
    if re.search(r"(?m)^\s+name:", block):
        return block
    ident = entry_id(block)
    package = f"@deepseek-ai/dsh-{ident}"
    return block.replace(f"id: {ident}", f"id: {ident}\n  name: \"{package}\"", 1)


def build_runtime_patch(source_patch: Path) -> str:
    """生成 headless 的覆盖层：恢复凭据存储、借模型路由、关掉 web 工具。

    为什么必须恢复凭据存储：headless profile 出厂把 `credentials` 关掉了，
    只认环境变量与 `.env`——而日常在 GUI 里配好的 key 存在凭据库里。
    实测过：不恢复就是 `MISSING_CREDENTIAL`，且报错说的是「没有 API key」，
    很容易被误判成「模型配错了」。
    """
    if not source_patch.is_file():
        raise DebriefError(
            f"找不到可借模型路由的档案：{source_patch}\n"
            f"用 `--patch <文件>` 自己给一份覆盖层，或在 DSH 里配置一个模型。"
        )
    blocks = yaml_entries(source_patch.read_text(encoding="utf-8"))
    borrowed = [borrowed_entry(block) for block in blocks if entry_id(block) in BORROW_IDS]
    if not borrowed:
        raise DebriefError(f"{source_patch} 里没有 {' / '.join(BORROW_IDS)} 行，无法借用模型路由。")
    lines = [
        "# 由 `ar debrief` 自动生成，勿手改（每次运行会重写）",
        "# 来源档案：" + str(source_patch),
        "# 1) 恢复凭据存储（headless 出厂关掉它，只认 env / .env）",
        "- id: credentials",
        "  disabled: false",
        "- id: authorization",
        "  disabled: false",
        "- id: deepseek-account",
        "  disabled: false",
        "# 2) 借模型路由（凭据值仍在凭据库里，本文件不含任何密钥）",
        *borrowed,
        "# 3) 关掉网络与 shell：本项目红线是不访问任何平台域名（靠代码关，不靠模型自觉）",
        *[f"- id: {name}\n  disabled: true" for name in NETWORK_ROWS],
        "# 4) 只读文件系统：结论只能由 stdout 交回，再走 `ar add` 校验落库。",
        "#    留下 write/edit 等于给了模型一条直写 data/*.csv 的路——append-only 表没有回滚，",
        "#    那正是 agent-run-001 §4 踩过的坑。readImage 关掉 = R7（图片不进上下文）的代码化。",
        "- id: tool-fs",
        "  config:",
        "    read: true",
        "    write: false",
        "    edit: false",
        "    readImage: false",
        "",
    ]
    return "\n".join(lines)


def find_source_patch(home: Path) -> Path | None:
    for name in PROFILE_ORDER:
        candidate = home / "profiles" / name / "cordis.patch.yml"
        if candidate.is_file():
            return candidate
    return None


# ---- 任务拼装：列名与枚举都从 schema 现取 ----


@dataclass
class Snapshot:
    account: dict[str, Any]
    notes: list[dict[str, Any]]
    stats: Any
    body_ready: set[str] = field(default_factory=set)
    images_ready: set[str] = field(default_factory=set)
    # 有评论原文的篇目（`<note_id>.comments.txt`）。这是「读者说了什么」的证据层，
    # 与正文（「他写了什么」）分开数：混在一起就会把人群画像误当成有据可依。
    comments_ready: set[str] = field(default_factory=set)
    # 已有 id：模型直接看列表，不必自己去读 CSV（也避免重号）
    existing_ids: dict[str, list[str]] = field(default_factory=dict)


ID_COLUMN = {"findings": "finding_id", "playbook": "action_id", "hypotheses": "hypothesis_id"}


def collect_existing_ids(data_dir: Path) -> dict[str, list[str]]:
    from .store import read_table

    collected: dict[str, list[str]] = {}
    for table, column in ID_COLUMN.items():
        rows = read_table(data_dir, table)
        collected[table] = sorted({str(row.get(column) or "") for row in rows} - {""})
    return collected


def render_id_block(existing: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for table, column in ID_COLUMN.items():
        ids = existing.get(table) or []
        filled = "、".join(ids) if ids else "（表还是空的）"
        lines.append(f"- `{table}` 已占用 {column}：{filled}")
    return "\n".join(lines)


def collect_snapshot(
    data_dir: Path, account_id: str, text_dir: Path, k_values: tuple[int, ...] = (3, 5)
) -> Snapshot:
    """把「模型要看的现状」一次性取齐。

    为什么统计也在这一步算：模型算篇均/中位会漂，而且漂得看不出来。凡是能确定的
    数字都先由代码算完，任务里直接给结论——模型只剩判断可做。
    """
    from .store import latest_per_note, read_table

    accounts = read_table(data_dir, "accounts")
    account = next((row for row in accounts if str(row.get("account_id")) == account_id), None)
    if account is None:
        known = "、".join(f"{row.get('nickname')}({row.get('account_id')})" for row in accounts) or "（空）"
        raise DebriefError(f"accounts 表里没有 {account_id}。现有：{known}")
    notes = [row for row in read_table(data_dir, "notes") if str(row.get("account_id")) == account_id]
    merged = latest_per_note(notes)
    body_ready = {str(row.get("note_id")) for row in merged if (text_dir / f"{row.get('note_id')}.txt").is_file()}
    images_ready = {
        str(row.get("note_id")) for row in merged if (text_dir / f"{row.get('note_id')}.images.txt").is_file()
    }
    comments_ready = {
        str(row.get("note_id")) for row in merged if (text_dir / f"{row.get('note_id')}.comments.txt").is_file()
    }
    return Snapshot(
        account=account,
        notes=merged,
        stats=summarize(notes, account_id, k_values),
        body_ready=body_ready,
        images_ready=images_ready,
        comments_ready=comments_ready,
        existing_ids=collect_existing_ids(data_dir),
    )


def render_body_gate(snapshot: Snapshot) -> str:
    """「深拆 3 篇」这条纪律的闸门：正文不够就必须说，而且说清哪层要留 ⬜。

    为什么必须有这一段：SKILL.md 与验证清单都写着「深拆 3 篇（爆款 / 中位 / 近期）」
    且「结构结论用的必须是同一层文本」，但**代码里一个字都没有**。
    实测（2026-11-04）：知新那个号 30 篇样本里只有 1 篇有正文，`ar debrief` 首行照样
    欢快地报「样本 30 篇」，一个警告都不打——而一轮真跑要烧 60–114 万 tokens。
    这与 E14/E17 同源：规则写在文档里，执行靠人记得。

    不出声的代价不是「结果差」，是「结果看起来一样好」：
    模型会用标题 + 点赞硬写出一份结构拆解，读起来完全合理，而正文根本没读过。
    """
    notes = snapshot.notes
    sample = [
        row for row in notes
        if not _is_pinned(row) and str(row.get("likes") or "").strip()
    ]
    ready = [row for row in sample if str(row.get("note_id")) in snapshot.body_ready]
    readable = [row for row in sample if str(row.get("note_id")) in snapshot.images_ready]
    total = len(sample)
    if total == 0:
        return "⚠️ 样本为 0：没有任何一篇能进统计，拆解无从谈起。"

    body_n = len(ready)
    images_n = len(readable)
    # 🔧 「深拆 3 篇」里那 3 是 SKILL.md 定的下限，不是随手挑的数。
    required = 3
    # 正文层与图上文字层是**两批不同的篇目**：一篇也可能两层都有。
    # 判断「读得到几篇」必须按篇取并集，不能把两个数相加——那会把同一篇数两次。
    readable_ids = (
        {str(row.get("note_id")) for row in ready}
        | {str(row.get("note_id")) for row in readable}
    )
    any_n = len(readable_ids)
    if any_n >= required:
        return (f"- 可读正文 {any_n}/{total} 篇（其中正文层 {body_n} 篇、图上文字层 {images_n} 篇）："
                f"满足「深拆 3 篇」的下限，结构结论可以基于实读文本。")

    # 不足 3 篇——分档说清后果，别用一个笼统的「注意」了事
    titles = "、".join(
        f"`{note_id[:8]}`（{next((r.get('title') or '无标题' for r in ready + readable if str(r.get('note_id')) == note_id), '')}）"
        for note_id in sorted(readable_ids)
    ) or "无"
    if body_n == 0 and images_n == 0:
        head = (f"⛔ **正文一篇都读不到**（0/{total}，图上文字也是 0/{total}）："
                f"结构层（开头钩子 / 中段节奏 / 结尾引导 / 标题句式）**必须整层留 ⬜**，"
                f"不许用标题 + 点赞反推结构——那不是拆解，是编。")
    elif any_n < required:
        head = (f"⚠️ **能读到的文本只有 {any_n} 篇**（正文层 {body_n} / 图上文字层 {images_n}，"
                f"样本 {total}）：不足 SKILL.md 要求的「深拆 3 篇」。"
                f"**结构层结论必须在 sample_basis 里写明样本量，并标注仅这几篇成立**；"
                f"不许把 {any_n} 篇的观察写成「这个号的通用结构」。")
    else:
        head = (f"⚠️ **正文层只读到 {body_n}/{total} 篇**（图上文字层 {images_n}/{total} 篇）："
                f"结构层只能基于这几篇，样本量必须写进 sample_basis。")
    lines = [head, f"- 可读文本的具体篇目：{titles}"]
    if any_n < required:
        lines.append(
            "- 其余篇只能支撑**选题层**（标题 + 时间 + 点赞），"
            "**不要**对它们写开头钩子 / 段落数 / 结尾引导这类需要正文的判断。"
        )
    return "\n".join(lines)


def render_account_block(snapshot: Snapshot) -> str:
    account = snapshot.account
    keys = ("account_id", "nickname", "red_id", "fans", "follows", "interactions", "posted", "desc", "collected_on")
    lines = [f"- {key}：{account.get(key) or '⬜'}" for key in keys]
    total = len(snapshot.notes)
    lines.append(f"- 正文覆盖：{len(snapshot.body_ready)}/{total} 篇")
    lines.append(f"- 图上文字覆盖：{len(snapshot.images_ready)}/{total} 篇")
    return "\n".join(lines)


def render_notes_block(rows: Iterable[dict[str, Any]], snapshot: Snapshot) -> str:
    header = ("| note_id | 发布 | 赞 | 藏 | 评 | 形式 | 置顶 | 时长 | 正文 | 图上 | 标题 |\n"
              "|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [header]
    for row in rows:
        note_id = str(row.get("note_id") or "")
        lines.append("| " + " | ".join(str(part) for part in (
            note_id,
            row.get("published_at") or "⬜",
            row.get("likes") or "⬜",
            row.get("collects") or "⬜",
            row.get("comments") or "⬜",
            row.get("form") or "⬜",
            "是" if str(row.get("pinned") or "0") in ("1", "true") else "",
            row.get("duration") or "",
            "✅" if note_id in snapshot.body_ready else "⬜",
            "✅" if note_id in snapshot.images_ready else "⬜",
            (row.get("title") or "").replace("|", "｜"),
        )) + " |")
    return "\n".join(lines)


def render_stats_block(stats: Any) -> str:
    likes = stats.likes
    burst = " | ".join(
        f"K={k} {data['rate']}%（{data['hits']}/{stats.n_sample}，阈值 ≥{data['threshold']}）"
        for k, data in stats.burst.items()
    )
    coverage = " | ".join(f"{label} {value}" for label, value in stats.coverage.items()) or "⬜"
    forms = " | ".join(f"{form} {data['n']} 篇/篇均 {data['mean']}" for form, data in stats.forms.items()) or "⬜"
    lines = [
        f"- 样本 {stats.n_sample} 篇（置顶 {stats.n_pinned} 篇单列，不进样本；因缺来源被排除 {stats.excluded} 行）",
        f"- 点赞：篇均 {likes.mean} | 中位 {likes.median} | 最高 {likes.maximum} | 最低 {likes.minimum}",
        f"- 爆文率（相对账号自身，K 值无共识）：{burst or '⬜'}",
        f"- 分形式：{forms}",
        f"- 字段覆盖：{coverage}",
        f"- 时间窗：{stats.window[0]} → {stats.window[1]}",
        "- 互动率**不可算**：曝光量仅作者后台可见（fields 表已登记 ⬜），不得编分母",
    ]
    # 圆整值必须在**模型读到数字的地方**就说清，而不是靠它自己去看 ⬜ 纪律那一条。
    # `1.2万` 会以 `12000` 的形式出现在上面的篇均/中位里，模型无从知道那是个圆整值。
    approx = int(getattr(stats, "approx_rows", 0) or 0)
    if approx:
        # 只报个数不够用：模型知道「有 1 篇是圆整值」却不知道是哪篇，只能猜。
        # 名单直接给它——这才是「代码算好、模型只引用」该有的形态。
        names = getattr(stats, "approx_notes", ()) or ()
        listed = "、".join(names[:8]) + ("…" if len(names) > 8 else "")
        lines.append(
            f"- **⚠️ {approx} 篇的点赞是圆整值**（平台过万后渲染成「1.2万」这类 3 位有效数字）："
            f"{listed}。它们已计入上面的篇均/中位，但**逐篇引用这些篇的点赞时必须写 `≈12,000`，"
            f"不得当 12,000 用**。样本表里这些数看起来是普通整数，是因为代码做了换算——换算不等于精确。"
        )
    unparsable = int(getattr(stats, "unparsable", 0) or 0)
    if unparsable:
        lines.append(f"- **⚠️ 有 {unparsable} 篇的点赞无法解析，已排除出样本**：引用任何 a/b 时不得把它们算进分母。")
    return "\n".join(lines)


def render_coverage_block(snapshot: Snapshot) -> str:
    """把「能数出来的分母」明写出来。

    不加这一块时实测出现过把 27/31 写成 29/31 的漂移——这种错读起来完全合理，
    所以不让模型自己减，直接把条目列给它。
    """
    counts = coverage_counts(snapshot)
    sample = counts["n_sample"]
    return "\n".join((
        f"- 样本 {sample} 篇（置顶 {counts['n_pinned']} 篇单列，不进样本）",
        f"- 有正文 {counts['body']}/{sample} → 缺 {sample - counts['body']}/{sample}",
        f"- 有图上文字 {counts['images']}/{sample} → 缺 {sample - counts['images']}/{sample}",
        f"- 有收藏 {counts['collects']}/{sample} | 有评论数 {counts['comments']}/{sample}"
        f" | 有分享 {counts['shares']}/{sample}",
        f"- 有评论**原文** {counts['comments_text']}/{sample}"
        f"（`account/notes-text/<note_id>.comments.txt`，是**首屏**几条，不是全部）",
        f"- 有发布日期 {counts['dated']}/{sample}",
        "- **引用任何 `a/b` 之前先在这里找对应数**；找不到的数不许写，写成 ⬜ 并计入 sample_basis",
        "- 人群画像只能用「有评论原文」的那几篇；**「有评论数」不等于读到过评论**——"
        "只看到数字就推断人群，是把缺证据写成了证据。",
    ))


def render_output_contract() -> str:
    template = {table: [dict.fromkeys(schema.TABLE_COLUMNS[table], "")] for table in TARGET_TABLES}
    enums = "\n".join((
        f"- verdict 只能取：{' / '.join(schema.VERDICTS)}",
        f"- grade 只能取：{' / '.join(schema.GRADES)}（③ 级只能当线索，不得进判决）",
        f"- status 只能取：{' / '.join(schema.HYPOTHESIS_STATUS)}",
    ))
    return "\n".join((
        "只输出**一个** ```json 代码块，内容为一个对象，四个键固定："
        f"{', '.join(TARGET_TABLES)}。不要解释、不要前后缀。",
        "",
        enums,
        "",
        "每张表的列名与顺序（由 schema.py 生成，**不许改名、不许加列**）：",
        "",
        "```json",
        json.dumps(template, ensure_ascii=False, indent=2),
        "```",
    ))


def build_task(snapshot: Snapshot, repo_root: Path) -> str:
    """拼出交给 headless 的完整任务。所有路径都是仓库相对路径，便于人复核。"""
    ready_images = sorted(snapshot.images_ready)
    images_note = (
        f"有图上文字 {len(ready_images)} 篇：{', '.join(ready_images[:6])}{' …' if len(ready_images) > 6 else ''}"
        if ready_images else "⚠️ 本次**一篇图上文字都没有**：图文笔记的内容读不到，必须把这一点写进 sample_basis，不许假装读过"
    )
    return "\n".join((
        "# 拆解任务（由 `ar debrief` 生成，勿手改）",
        "",
        "你在执行 account-research 的「拆解」步骤：把一个已入库的小红书账号，拆成四张结论表的候选行。",
        "",
        "## 0 硬边界（违反即本次产出作废）",
        "",
        "1. **零网络**：只读本地文件。禁止访问 xiaohongshu.com 等任何平台域名；本运行时的 web 工具已被关闭，不要绕过。",
        "2. **禁止打开任何图片文件**（" + " / ".join(NO_IMAGE_SUFFIXES) + "）。",
        "   图文笔记的内容只能读 `account/notes-text/<note_id>.images.txt` 这层 OCR 文本——"
        "把图片字节读进上下文会直接把请求撑爆（本项目实测过）。",
        "3. **⬜ 不得被填充**：拿不到就写 ⬜ 并计入 sample_basis；禁止估算值、行业经验值、第三方横评。",
        "4. **相关 ≠ 因果**：不许写「涨粉 / 关注转化」这类病因断言；要提就进 hypotheses 表。",
        "5. **判决封闭**：五选一 + 三件套（最值钱的一步 / 最便宜的验证含时间预算 / 放弃信号），不许「值得考虑」。",
        "6. 不要写方法论、不要做涨粉预测、不要写 HTML 可视化。",
        "",
        "## 1 先读这两份（其余的这次都不用读）",
        "",
        "- `account-research/agent/account-analyst.md`（你的契约：七条禁止、自治边界）——全读。",
        "- `account-research/skill/SKILL.md`——**只读中间一段**：用 grep 找到 `### Step 2: POSITION` "
        "的行号，从那一行 read 到 `## 输入参数契约` 之前（四层拆解方法 + 三件套 + 落库）。"
        "该文件其余部分（版本史记、输出模板、与其他 Skill 的关系）本次不需要；整份 5 万字读下来只会稀释判断。",
        f"- 正文与图上文字目录：`{_rel(Path('account') / 'notes-text', repo_root)}`",
        "",
        "**不要读的东西**（实测上一轮读它们白花了约二十万 tokens）：",
        "",
        "- 不要读 `account-research/src/` 下的任何源码——那是产出这张任务的工具本身，不含判断依据"
        "（上一轮读了 `debrief.py` / `schema.py` / `validate.py`）。",
        "- 不要读 `data/*.csv`。你需要的数字全在 §2–§6 里：已有 id 在 §6 列全了，"
        "不要为了「避免重号」去翻 CSV；上一轮为此 glob 了整个 `data/` 并读了 5 个表。",
        "- 不要读 `skill/references/overall-workflow.md`（产品级管线，与本次单个账号的拆解无关）"
        "和 `skill/references/xiaohongshu-metrics.md`（口径已固化在 §4/§5，争议时才溯源）。",
        "",
        "## 2 本次要拆的账号",
        "",
        render_account_block(snapshot),
        "",
        "## 3 样本（按 note_id 合并多渠道观测后的最新值）",
        "",
        render_notes_block(snapshot.notes, snapshot),
        "",
        "「正文 ✅」= 有 `account/notes-text/<note_id>.txt`；「图上 ✅」= 有 `<note_id>.images.txt`。",
        images_note,
        "",
        "### 3.1 正文可读性闸门（这条决定你能下哪些结论）",
        "",
        render_body_gate(snapshot),
        "",
        "## 4 统计口径（代码已算出，直接引用，不要重算）",
        "",
        render_stats_block(snapshot.stats),
        "",
        "## 5 可核算的覆盖计数（引用 a/b 前先查这里）",
        "",
        render_coverage_block(snapshot),
        "",
        "## 6 已有 id（新行不得重号）",
        "",
        render_id_block(snapshot.existing_ids),
        "",
        "## 7 输出契约",
        "",
        render_output_contract(),
        "",
        "## 8 输出前自检",
        "",
        "- [ ] 每条 finding 都有 grade，且 ③ 级行的 used_in_verdict 为 0",
        "- [ ] 每条 playbook 都有 failure_condition（否则等于「建议全抄」）",
        "- [ ] 每条 hypothesis 都有 experiment 与 due_on，status 取前四个之一"
        "（`超期未验` 是结案状态，只能由 `ar close` 写入，不要自己填）",
        "- [ ] verdicts 每行都有 next_step / cheapest_test / abandon_signal",
        "- [ ] 全文没有 URL、没有图片路径、没有 ⬜ 被填成数字",
        "- [ ] sample_basis 写清了样本量与缺口（含图上文字缺多少）",
        "- [ ] **§3.1 若标了 ⛔ / ⚠️，结构层结论必须按那里的要求降级**（不足 3 篇不许写成通用结构；0 篇整层留 ⬜）",
        "",
    ))


def _rel(path: Path, root: Path) -> str:
    """一律给正斜杠路径：任务文本是给人看和给模型读的，反斜杠在 markdown 里还会吞字符。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# ---- 运行 headless 并收回产物 ----


@dataclass
class RunResult:
    exit_code: int
    final_text: str
    session_id: str = ""
    error: str = ""
    turn_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


def parse_events(stream: str) -> RunResult:
    """解析 `--json` 的 NDJSON 事件流。

    注意：退出码 0 + final 非空才是成功。turn_end 的 reason 是失败信号，
    一个「格式完好但内容失败」的流照样会以 exit 1 结束。
    """
    result = RunResult(exit_code=0, final_text="")
    for line in stream.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        result.events.append(event)
        kind = event.get("type")
        if kind == "session":
            result.session_id = str(event.get("sessionId") or "")
        elif kind == "final":
            result.final_text = str(event.get("text") or "")
        elif kind == "error":
            result.error = str(event.get("message") or "")
        elif kind == "tool_call":
            result.tool_calls += 1
        elif kind == "status" and event.get("phase") == "step_end":
            usage = event.get("usage") or {}
            result.input_tokens += int(usage.get("inputTokens") or 0)
            result.output_tokens += int(usage.get("outputTokens") or 0)
        elif kind == "status" and event.get("phase") == "turn_end":
            reason = event.get("reason") or {}
            result.turn_reason = str(reason.get("kind") or "")
            if reason.get("error"):
                result.error = str((reason.get("error") or {}).get("message") or "")
    return result


def resolve_launcher(dsh: str) -> Path:
    """把 `dsh` 解析成真正的可执行文件。

    实测：Windows 上 `shutil.which` 按 PATHEXT 找到的是 `dsh.cmd`，而裸名 `dsh`
    交给 CreateProcess 是不带扩展名搜索的——直接拿来跑会 FileNotFoundError，
    报错还会误导成「dsh 没装」。
    """
    candidate = Path(dsh)
    if candidate.parent != Path(".") or candidate.suffix:
        return candidate
    for suffix in ("", ".cmd", ".bat", ".exe", ".ps1", ".py"):
        found = shutil.which(dsh + suffix)
        if found:
            return Path(found)
    return candidate


def launcher_command(dsh: str) -> list[str]:
    """拼出可执行命令。`.cmd` / `.ps1` 垫片都不能被 CreateProcess 直接执行。"""
    path = resolve_launcher(dsh)
    suffix = path.suffix.lower()
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(path)]
    if suffix == ".ps1":
        return ["pwsh", "-NoProfile", "-File", str(path)]
    if suffix == ".py":
        return [sys.executable, str(path)]
    return [str(path)]


def run_headless(task: str, patch: Path, timeout: int, dsh: str = "dsh", cwd: Path | None = None) -> RunResult:
    command = launcher_command(dsh) + ["headless", "--patch", str(patch), "--json", "-"]
    try:
        completed = subprocess.run(  # noqa: S603 - 参数是固定的，任务走 stdin
            command, input=task, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise DebriefError(f"找不到 dsh 启动器（{dsh}）：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DebriefError(f"dsh headless 超时（{timeout}s）。任务文本已存档，可加 --timeout 重跑。") from exc
    result = parse_events(completed.stdout or "")
    result.exit_code = completed.returncode
    if result.error and not result.final_text:
        raise DebriefError(f"dsh headless 失败（exit {completed.returncode}）：{result.error}")
    return result


def extract_payload(text: str) -> dict[str, list[dict[str, Any]]]:
    """从模型输出里取出四表 JSON。只认闭合的 JSON，取不到就报错，不猜。"""
    candidates = [block.strip() for block in FENCE_RE.findall(text)] or [text.strip()]
    payload: Any = None
    for candidate in candidates:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            payload = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and set(payload) & set(TARGET_TABLES):
            break
        payload = None
    if not isinstance(payload, dict):
        raise DebriefError("模型输出里没有可解析的四表 JSON。原始输出已存档，看 events 文件排错。")
    unknown = sorted(set(payload) - set(TARGET_TABLES))
    if unknown:
        raise DebriefError(f"产出里有不认识的表：{unknown}（只收 {' / '.join(TARGET_TABLES)}）")
    return {table: payload.get(table) or [] for table in TARGET_TABLES}


def check_payload(payload: dict[str, list[dict[str, Any]]]) -> list[str]:
    """落库前的静态检查：只查列名与「空表却给空行」这类明显错。"""
    problems: list[str] = []
    for table, rows in payload.items():
        if not isinstance(rows, list):
            problems.append(f"{table} 不是数组")
            continue
        allowed = set(schema.TABLE_COLUMNS[table])
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                problems.append(f"{table} 第 {index} 行不是对象")
                continue
            unknown = sorted(set(row) - allowed)
            if unknown:
                problems.append(f"{table} 第 {index} 行有 schema 里没有的列：{unknown}")
    return problems


def payload_summary(payload: dict[str, list[dict[str, Any]]]) -> str:
    return " | ".join(f"{table} {len(rows)} 行" for table, rows in payload.items())


def coverage_counts(snapshot: Snapshot) -> dict[str, int]:
    """样本级可核算的覆盖计数。所有 `a/b` 断言的分子必须落在这个集合里。

    **样本必须和 §4 统计块用同一个定义**（2026-11-04 实测修正）：
    早先这里只排除置顶，而 `stats.summarize` 还多排除一类——点赞为空、无法进统计的行。
    实测知新那个号：§4 报「样本 30 篇」，§5 报「样本 31 篇」，同一个任务里两个分母。
    模型引用 `a/b` 时被迫二选一，闸门 `audit_ratios` 又按 §5 判——**等于两个口径互相打架**。

    现在统一到「能进统计的样本」：置顶与无点赞行都不算。
    置顶仍单列（它是账号门面，不是常规产能）；无点赞行同理不进任何分母。
    """
    notes = snapshot.notes
    sample = [
        row for row in notes
        if not _is_pinned(row) and str(row.get("likes") or "").strip()
    ]

    def filled(row: dict[str, Any], column: str) -> bool:
        value = str(row.get(column) or "").strip()
        return value not in ("", "⬜", "None")

    return {
        "n_total": len(notes),
        "n_sample": len(sample),
        "n_pinned": sum(1 for row in notes if _is_pinned(row)),
        "body": sum(1 for row in sample if str(row.get("note_id")) in snapshot.body_ready),
        "images": sum(1 for row in sample if str(row.get("note_id")) in snapshot.images_ready),
        "collects": sum(1 for row in sample if filled(row, "collects")),
        "comments": sum(1 for row in sample if filled(row, "comments")),
        # 「有评论**原文**」与「有评论**数量**」是两件事，分开数。
        # 早先只数了数量，于是「人群画像 ⬜」和「评论 5/30 可见」同时出现在一份报告里。
        "comments_text": sum(1 for row in sample if str(row.get("note_id")) in snapshot.comments_ready),
        "shares": sum(1 for row in sample if filled(row, "shares")),
        "dated": sum(1 for row in sample if filled(row, "published_at")),
    }


def _is_pinned(row: dict[str, Any]) -> bool:
    return str(row.get("pinned") or "").strip() in ("1", "true", "是", "y", "yes")


def allowed_ratios(snapshot: Snapshot) -> dict[int, set[int]]:
    """分母 → 允许的分子集合。

    只对「分母是样本量或笔记总数」的断言设限：这类 `a/b` 必须能由表直接数出来。
    其它分母（例如「4 篇有图样本里的 3 篇」）不在此列，不猜也不拦。
    """
    counts = coverage_counts(snapshot)
    allowed: dict[int, set[int]] = {}
    for total_key in ("n_sample", "n_total"):
        total = counts[total_key]
        values = {0, total}
        for key in ("body", "images", "collects", "comments", "shares", "dated"):
            values.add(counts[key])
            values.add(total - counts[key])
        allowed[total] = values
    return allowed


# 只认「这个数字在说哪个字段」的句式。为什么不做全量 a/b 白名单：
# 真实产出里存在大量代码并不建模的子集断言（如「首图通道覆盖 2/31」），
# 白名单会把它们全部判成错——闸门误杀真话，就等于逼人绕过闸门。
# 命中词表才校验，命中不了就放过，这条边界必须写死。
COLUMN_WORDS: dict[str, tuple[str, ...]] = {
    "body": ("正文层", "正文"),
    "images": ("图上文字", "图上"),
    "collects": ("收藏", "有藏"),
    "comments": ("评论", "有评"),
    "shares": ("分享",),
    "dated": ("发布日期", "发布日"),
}


def _word_pattern() -> str:
    words = [word for group in COLUMN_WORDS.values() for word in group]
    words.sort(key=len, reverse=True)
    return "|".join(re.escape(word) for word in words)


CLAIM_AFTER_RE = re.compile(rf"(\d+)\s*/\s*(\d+)\s*(?:篇|条)?\s*(?:无|有|缺|未)?\s*({_word_pattern()})")
CLAIM_BEFORE_RE = re.compile(rf"(?:有|无|缺|未)\s*({_word_pattern()})\s*(\d+)\s*/\s*(\d+)")


def ratio_claims(text: str) -> list[tuple[int, int, str]]:
    """从一句话里挑出「样本级覆盖」断言：分子、分母、说的是哪个字段。"""
    claims = [(int(a), int(b), word) for a, b, word in CLAIM_AFTER_RE.findall(text)]
    claims += [(int(b), int(c), word) for word, b, c in CLAIM_BEFORE_RE.findall(text)]
    return claims


def audit_ratios(snapshot: Snapshot, payload: dict[str, list[dict[str, Any]]]) -> list[str]:
    """挑出「说的是某个覆盖字段、数字却数不出来」的断言。

    为什么值得单独做一遍：模型在能直接引用的事实上也会漂。实测出现过
    「29/31 篇无收藏与评论」，而表里是 27/31（31 减掉 4 篇有数据的）——
    这种错读起来完全合理，人眼极难抓，并且会顺着 sample_basis 一路传下去。
    """
    allowed = allowed_ratios(snapshot)
    counts = coverage_counts(snapshot)
    problems: list[str] = []
    for table, rows in payload.items():
        for index, row in enumerate(rows, start=1):
            label = str(row.get(ID_COLUMN.get(table, ""), "") or f"第 {index} 行")
            for column, value in row.items():
                if not isinstance(value, str):
                    continue
                for numerator, denominator, word in ratio_claims(value):
                    if denominator not in allowed or numerator in allowed[denominator]:
                        continue
                    problems.append(
                        f"{table}.{label}.{column}: 「{numerator}/{denominator}」与表的计数不符"
                        f"（{word}）｜{_ratio_hint(counts, denominator)}"
                    )
    return problems


def _ratio_hint(counts: dict[str, int], total: int) -> str:
    """告诉模型「该用哪个数」，比只说「错了」省掉一整轮往返。"""
    if counts["n_sample"] == total:
        return (f"样本 {counts['n_sample']}｜正文 {counts['body']}｜图上 {counts['images']}"
                f"｜收藏 {counts['collects']}｜评论 {counts['comments']}｜分享 {counts['shares']}"
                f"｜有日期 {counts['dated']}")
    return f"笔记总数 {counts['n_total']}｜样本 {counts['n_sample']}"


DATE_DEFAULTS = {
    "verdicts": ("decided_on",),
    "findings": ("sampled_on",),
    # 假设表 2026-11-12 起也有日期：`recorded_on` 是「这一行是什么时候记下的」。
    # 它进键之后，结案才能追加新行（见 `close.py`）。模型不知道今天几号，
    # 所以和 `decided_on` 一样由代码无条件写——否则 V6 会因缺 recorded_on 拒收闭案行。
    "hypotheses": ("recorded_on",),
}

# 判决的复核间隔：`due_on` 相对 `decided_on` 往后推多少天。
# 覆盖 `decided_on` 时必须按**模型原本的意图间隔**重锚 `due_on`，否则间隔会被压成 0。
ANCHORED_SPANS = {"verdicts": ("due_on",)}


def _shift(date_text: str, days: int) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(date_text) + timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return date_text


def _reanchor_due(row: dict[str, Any], original_decided: str, on: str) -> str:
    """把 `due_on` 按模型原本的间隔重锚到新的 `decided_on` 上。

    **为什么必须做这件事**（2026-11-06 实测）：E21 那次修复把 `decided_on`
    无条件覆盖成真实拆解日，模型原文是 10-28（数据窗口末日）→ 11-04，
    间隔 7 天；覆盖后变成 11-04 → 11-04，**间隔 0 天**。
    后果不是报错，是 `D1` 在判决落库的当天就开始喊「到期未复核」——
    一条永远不可能按时执行的纪律，喊两天就变成噪音（E12）。

    覆盖起点却不覆盖终点，等于把「他给了 7 天」改写成「他要求当天复核」。
    间隔才是模型真正表达的意思，所以按间隔重锚，而不是照抄原来的 `due_on`。
    返回空串表示不需要重锚（模型没写 `due_on` 就交白卷，不许我们替它编）。
    """
    due = str(row.get("due_on") or "").strip()
    if not due or not original_decided:
        return due
    from datetime import date

    try:
        span = (date.fromisoformat(due) - date.fromisoformat(original_decided)).days
    except (TypeError, ValueError):
        return due
    return _shift(on, span)


def normalize_rows(
    payload: dict[str, list[dict[str, Any]]], account_id: str, on: str
) -> dict[str, list[dict[str, Any]]]:
    """补两样模型不该操心、又决定校验成败的东西：归属账号与日期。

    **日期是确定性的，代码说了算**（2026-11-04 修正）：`decided_on` / `sampled_on`
    就是「本次拆解发生在哪天」，代码知道，模型不知道。

    早先只在字段为空时才补，实测立刻出事：知新那轮模型自己填了 `2026-10-28`
    ——那是**数据窗口的末日**，不是拆解日。于是判决的 `decided_on` 比真实早 7 天，
    直接污染「到期该复核」的告警（D1/D2 按 decided_on + due_on 算天数）。
    这种错读起来完全合理，而且不会让任何校验报错。

    所以改成**无条件覆盖**，并把模型的原始值打出来——覆盖不静默，否则就是另一种编数据。

    **连带义务**（2026-11-06 补）：`due_on` 是相对 `decided_on` 的锚点。
    只改起点不改终点，间隔会被压成 0，D1 在落库当天就开始喊。
    所以按模型原本的间隔重锚 `due_on`（见 `_reanchor_due`）。

    `account_id` 仍只在空时补：模型偶尔会填错账号，但那种情况该由 V7 报出来让人看，
    不该被静默改写。
    """
    for table, rows in payload.items():
        for row in rows:
            if "account_id" in row and not str(row.get("account_id") or "").strip():
                row["account_id"] = account_id
            for column in DATE_DEFAULTS.get(table, ()):
                if column not in row:
                    continue
                written = str(row.get(column) or "").strip()
                if written and written != on:
                    row[f"__{column}_was"] = written    # 供调用方打印后被摘掉
                for anchored in ANCHORED_SPANS.get(table, ()):
                    shifted = _reanchor_due(row, written, on)
                    if shifted and shifted != str(row.get(anchored) or "").strip():
                        row[f"__{anchored}_was"] = str(row.get(anchored) or "").strip()
                        row[anchored] = shifted
                row[column] = on
    return payload


def collect_date_overrides(payload: dict[str, list[dict[str, Any]]]) -> list[tuple[str, str, str]]:
    """把 `normalize_rows` 覆盖掉的模型原值取出来（表名, 列名, 原值），并从行里摘掉。

    打印它是纪律的一部分：**静默纠正也是编数据**——只不过编的是「模型没写错」这个假象。
    """
    found: list[tuple[str, str, str]] = []
    for table, rows in payload.items():
        for row in rows:
            for key in [k for k in row if k.startswith("__") and k.endswith("_was")]:
                column = key[2:-4]
                found.append((table, column, str(row.pop(key) or "")))
    return found


def drop_empty_rows(payload: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """模板里的整行空值会被模型原样带回来——那不是一行结论，是没写。"""
    dropped = 0
    for table, rows in payload.items():
        kept = []
        for row in rows:
            if isinstance(row, dict) and any(str(value or "").strip() for value in row.values()):
                kept.append(row)
            else:
                dropped += 1
        payload[table] = kept
    return payload, dropped
