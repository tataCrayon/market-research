// 小红书笔记详情页 · 一键采集书签（v2：直接落文件）
//
// 【用法】在已经打开并滚到互动栏的笔记页，点一下书签 → 浏览器直接下载一个
//   ar-note-<note_id>.txt 到你本机 → 丢进 account/inbox/ → `ar ingest` 入库。
//   比 v1 少两步：不用复制、不用粘到文件里存盘。
//
// 【它做什么】读当前页面**已经渲染出来的文字**，拼成一个结构化块，用 Blob 存成本地文件。
//
// 【它不做什么 —— 这几条是红线，改代码前先读 docs/采集节流-额度与依据.md】
//   ✗ 不发任何网络请求（没有 fetch / XHR / WebSocket，也不碰平台接口）
//   ✗ 不翻页、不滚动加载、不批量、不点「下一条」
//   ✗ 不保存浏览器会话、不读 Cookie、不读 localStorage 之外的东西
//   ✗ 不模拟人操作平台控件——唯一一次 click() 是点在自己新建的下载链接上，
//     下载的是本机内存里的字符串，与平台没有任何交互
//   → 本质仍是「人打开页面 → 人触发 → 工具读已加载的 DOM」，只是把抄写换成了读。
//
// 【安装】新建书签 → 名称填「AR采集」→ URL 粘贴本文件压缩成一行的 javascript: 版本。

(function () {
  const BLOCK_START = "===AR-NOTE-v1===";
  const BLOCK_END = "===END===";
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  const url = location.href;
  const idMatch = url.match(/(?:explore|discovery\/item)\/([0-9a-f]{24})/i);
  const noteId = idMatch ? idMatch[1] : "";

  // 只读当前已渲染的节点；多候补，命中即用，全不中就留空交给人。
  const nodes = [
    document.querySelector("#noteContainer"),
    document.querySelector(".note-detail-mask .note-container"),
    document.querySelector("[class*='note-content']"),
    document.querySelector(".note-scroller"),
  ].filter(Boolean);
  const scope = nodes[0] || document.body;
  const text = clean(scope.innerText || "");

  const title = clean(
    (document.querySelector("#detail-title") || {}).textContent ||
    (document.querySelector(".note-content .title") || {}).textContent ||
    document.title.replace(/ - 小红书.*$/, "")
  );

  // 互动栏原文：**不做数字解析**（选择器一改就变成静默错数据）。
  const barText = clean(
    (document.querySelector(".interact-container") || scope).innerText || ""
  ).slice(0, 120);
  const candidates = (barText.match(/\d+(?:\.\d+)?\s*[万wW]?/g) || []).slice(0, 8).join(" | ");

  const video = scope.querySelector("video");
  const mmss = text.match(/\b(\d{1,2}:\d{2})\b/);
  const bodyHead = text.slice(0, 40);

  const block = [
    BLOCK_START,
    "note_id: " + noteId,
    "title: " + title,
    "published: ",
    "likes: ",
    "collects: ",
    "comments: ",
    "shares: ",
    "duration: " + (mmss ? mmss[1] : (video && video.duration ? String(video.duration) : "")),
    "human_note: ",
    "url: " + url.split("?")[0],
    "raw_len: " + text.length,
    "body_head: " + bodyHead,
    "bar_text: " + barText,
    "candidates: " + candidates,
    BLOCK_END,
  ].join("\n");

  // 本地生成文件：Blob 内容是本机内存里的字符串，不经过任何服务器。
  const blob = new Blob([block], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "ar-note-" + (noteId || "unknown") + ".txt";
  link.click();
  URL.revokeObjectURL(link.href);

  alert(
    (noteId ? "已保存 ar-note-" + noteId + ".txt" : "没认出 note_id，文件名会是 ar-note-unknown.txt") +
    "\n\n互动栏原文（照着填 赞/藏/评，别用候选值猜）：\n" + (barText || "（没找到，请直接看页面）") +
    "\n\n下一步：`ar ingest` 直接入库。"
  );
})();
