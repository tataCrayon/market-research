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
    return Snapshot(
        account=account,
        notes=merged,
        stats=summarize(notes, account_id, k_values),
        body_ready=body_ready,
        images_ready=images_ready,
        existing_ids=collect_existing_ids(data_dir),
    )


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
    return "\n".join((
        f"- 样本 {stats.n_sample} 篇（置顶 {stats.n_pinned} 篇单列，不进样本；因缺来源被排除 {stats.excluded} 行）",
        f"- 点赞：篇均 {likes.mean} | 中位 {likes.median} | 最高 {likes.maximum} | 最低 {likes.minimum}",
        f"- 爆文率（相对账号自身，K 值无共识）：{burst or '⬜'}",
        f"- 分形式：{forms}",
        f"- 字段覆盖：{coverage}",
        f"- 时间窗：{stats.window[0]} → {stats.window[1]}",
        "- 互动率**不可算**：曝光量仅作者后台可见（fields 表已登记 ⬜），不得编分母",
    ))


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
        f"- 有收藏 {counts['collects']}/{sample} | 有评论 {counts['comments']}/{sample}"
        f" | 有分享 {counts['shares']}/{sample}",
        f"- 有发布日期 {counts['dated']}/{sample}",
        "- **引用任何 `a/b` 之前先在这里找对应数**；找不到的数不许写，写成 ⬜ 并计入 sample_basis",
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
        "## 1 先读这些契约（按顺序）",
        "",
        "- `account-research/agent/account-analyst.md`（你的契约：七条禁止、自治边界）",
        "- `account-research/skill/SKILL.md`（九步流程、硬信念、判决规则）",
        "- `account-research/skill/references/overall-workflow.md`（端到端与质量闸门）",
        "- `account-research/skill/references/xiaohongshu-metrics.md`（指标口径与可得性）",
        "- `account-research/data/findings.csv` 与 `playbook.csv`（**已有行**：新 finding_id / action_id 不得与它们重号）",
        f"- 正文与图上文字目录：`{_rel(Path('account') / 'notes-text', repo_root)}`",
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
        "- [ ] 每条 hypothesis 都有 experiment 与 due_on，status 四选一",
        "- [ ] verdicts 每行都有 next_step / cheapest_test / abandon_signal",
        "- [ ] 全文没有 URL、没有图片路径、没有 ⬜ 被填成数字",
        "- [ ] sample_basis 写清了样本量与缺口（含图上文字缺多少）",
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
    """样本级可核算的覆盖计数。所有 `a/b` 断言的分子必须落在这个集合里。"""
    notes = snapshot.notes
    sample = [row for row in notes if not _is_pinned(row)]

    def filled(row: dict[str, Any], column: str) -> bool:
        value = str(row.get(column) or "").strip()
        return value not in ("", "⬜", "None")

    return {
        "n_total": len(notes),
        "n_sample": len(sample),
        "n_pinned": len(notes) - len(sample),
        "body": sum(1 for row in sample if str(row.get("note_id")) in snapshot.body_ready),
        "images": sum(1 for row in sample if str(row.get("note_id")) in snapshot.images_ready),
        "collects": sum(1 for row in sample if filled(row, "collects")),
        "comments": sum(1 for row in sample if filled(row, "comments")),
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
}


def normalize_rows(
    payload: dict[str, list[dict[str, Any]]], account_id: str, on: str
) -> dict[str, list[dict[str, Any]]]:
    """补两样模型不该操心、又决定校验成败的东西：归属账号与日期。

    - `account_id`：模型可能忘记填。空着会被 V7 判成「归属断裂」，
      而实际原因是拼装方没收尾——这种失败不该由模型承担。
    - 日期：`decided_on` / `sampled_on` 是本次拆解的发生日，本就是确定的。
    除这两项外**不补任何字段**：其余留空是证据缺口，补了就是编数据。
    """
    for table, rows in payload.items():
        for row in rows:
            if "account_id" in row and not str(row.get("account_id") or "").strip():
                row["account_id"] = account_id
            for column in DATE_DEFAULTS.get(table, ()):
                if column in row and not str(row.get(column) or "").strip():
                    row[column] = on
    return payload


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
