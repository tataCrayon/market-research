"""本地采集看板：把一个静态 HTML 页面生成到本地，列出待采链接。

**它只生成一个本地文件，不含任何抓取逻辑**：页面里的链接由你点、页面由浏览器加载、
保存由你按 Ctrl+S。看板做的事只有一件——把「该采哪几篇」摆成能一眼扫完、能中键连开的一屏，
省掉「翻文档、找链接、记进度」这几步。

为什么不做自动化：判据是「请求由谁发起」，不是「用什么工具发起」。
"""
from __future__ import annotations

import html
from typing import Any

STYLE = """
:root{--paper:#faf8f3;--ink:#241f1a;--muted:#7a6f63;--green:#2f6f52;--clay:#b8613c;--line:#e2dbcf}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 80px;background:var(--paper);color:var(--ink);
font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}
.sub{color:var(--muted);margin:0 0 22px;font-size:14px}
.steps{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin-bottom:22px}
.steps b{color:var(--green)}
.steps code{background:#f2eee6;padding:1px 6px;border-radius:4px;font-size:13px}
.row{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);
border-radius:10px;padding:12px 16px;margin-bottom:8px;text-decoration:none;color:inherit}
.row:hover{border-color:var(--green)}
.rank{width:26px;color:var(--muted);font-variant-numeric:tabular-nums;font-size:13px}
.likes{width:70px;text-align:right;font-variant-numeric:tabular-nums;color:var(--clay);font-weight:600}
.title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{font-size:12px;padding:2px 8px;border-radius:99px;background:#f2eee6;color:var(--muted);white-space:nowrap}
.tag.done{background:#e5f0ea;color:var(--green)}
.tag.warn{background:#fbe9e2;color:var(--clay)}
.count{color:var(--muted);font-size:13px;margin-bottom:10px}
.warn-line{background:#fbe9e2;border:1px solid #e8c3b4;border-radius:8px;padding:10px 14px;
color:#8c3f22;font-size:13px;margin:0 0 16px}"""


def _row_html(
    index: int, row: dict[str, Any], has_text: bool, note_url: str, token: str = ""
) -> str:
    note_id = str(row.get("note_id") or "")
    title = str(row.get("title") or "(无标题)")
    likes = str(row.get("likes") or "?")
    if not token:
        state, cls = "无 token", "tag warn"
    elif has_text:
        state, cls = "缺数字", "tag done"
    else:
        state, cls = "未采", "tag"
    # 带 token 的链接才能打开；没有 token 的退回裸链接并明确标注（它会 404）
    href = (
        f"https://www.xiaohongshu.com/explore/{note_id}"
        f"?xsec_token={html.escape(token)}&xsec_source=pc_user"
        if token
        else note_url.format(note_id=note_id)
    )
    return (
        f'<a class="row" href="{href}" target="_blank" rel="noopener">'
        f'<span class="rank">{index}</span>'
        f'<span class="likes">{html.escape(likes)}</span>'
        f'<span class="title">{html.escape(title)}</span>'
        f'<span class="{cls}">{state}</span>'
        f"</a>"
    )


def render_board(
    rows: list[tuple[dict[str, Any], bool]],
    note_url: str,
    account_label: str = "",
    tokens: dict[str, str] | None = None,
) -> str:
    """生成单文件看板。`rows` = [(笔记行, 是否已有正文)]，调用方负责排序与截断。

    `tokens` = {note_id: xsec_token}。有 token 才拼得出能打开的链接——
    裸 `/explore/<id>` 现在会被平台判 404（error_code=300031）。
    """
    tokens = tokens or {}
    pending = len(rows)
    done = sum(1 for _, has_text in rows if has_text)
    missing_token = sum(1 for row, _ in rows if not tokens.get(str(row.get("note_id") or "")))
    body = "\n".join(
        _row_html(index, row, has_text, note_url, tokens.get(str(row.get("note_id") or ""), ""))
        for index, (row, has_text) in enumerate(rows, start=1)
    )
    token_note = ""
    if missing_token:
        token_note = (
            f'<p class="warn-line">⚠️ {missing_token} 篇缺 xsec_token（标「无 token」）：'
            "这些链接点开会被平台判 <b>404</b>（<code>error_code=300031 当前笔记暂时无法浏览</code>）。"
            "请回对应账号主页点进那篇——那条路径永远带新鲜 token。</p>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>待采详情页 · {html.escape(account_label or "account-research")}</title>
<style>{STYLE}</style></head>
<body><div class="wrap">
<h1>待采详情页</h1>
<p class="sub">{html.escape(account_label)} · 共 {pending} 篇（其中 {done} 篇已有正文，只缺赞/藏/评）</p>
{token_note}
<div class="steps">
  <b>1.</b> 中键 / Ctrl+单击，一次连开 3–5 个链接（新标签页）<br>
  <b>2.</b> 逐个切过去，等页面加载完，按 <code>Ctrl+S</code> → 保存类型选「<b>网页，全部</b>」<br>
  <span style="color:#b8613c">⚠️ 别选「仅 HTML」：图文笔记的正文常常只排版在图上（实测正文层只占全文 1%–43%），「仅 HTML」会把图全丢掉</span><br>
  <b>3.</b> 全部存完，回到终端跑 <code>ar ingest</code> → <code>ar images --ocr</code>；刷新本页看还剩几篇
</div>
<p class="count">本页是纯静态文件：没有脚本、不发请求、不保存任何东西——它只提供链接，点开与保存都由你完成。</p>

<p class="count">按点赞降序；标「缺数字」的只需读赞/藏/评，不必再存正文。</p>
{body}
</div></body></html>
"""
