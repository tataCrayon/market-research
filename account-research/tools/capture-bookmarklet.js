// 小红书笔记详情页 · 采集辅助书签小工具（合规边界内）
//
// 它做什么：在你**已经打开并滚到互动栏**的笔记详情页上，点一下书签，
//   把「笔记 id / 标题 / 赞 / 藏 / 评 / 时长」抄成一个结构化块，放进剪贴板。
//   你粘回本地文件，`ar parse-text` 直接入库，不用再手工排版、不用剔除背景主页文字。
//
// 它不做什么（红线，别改）：
//   ✗ 不发任何网络请求（只读当前已渲染的 DOM 与地址栏）
//   ✗ 不自动翻页 / 不批量 / 不点「下一条」
//   ✗ 不调用任何平台接口、不模拟点击、不碰验证码
//   → 与「人按 Ctrl+A」同一性质，只是把人眼抄写换成读页面已有文字。
//
// 安装：新建书签 → 名称填「AR采集」→ URL 粘贴下面压缩成一行的 javascript: 版本。
// 数字认不准时它会把候选数字列出来（candidates），**由你确认后填**——不猜。

(function () {
  const BLOCK_START = "===AR-NOTE-v1===";
  const BLOCK_END = "===END===";
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  const url = location.href;
  const idMatch = url.match(/(?:explore|discovery\/item)\/([0-9a-f]{24})/i);
  const noteId = idMatch ? idMatch[1] : "";

  // 只读当前页已渲染的节点；选择器取多个候补，命中即用，全不中就留空给人填。
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
  // 把带标签的原文交给人一眼读数，比任何正则都可靠；candidates 只是抄写时的提示。
  const barText = clean(
    (document.querySelector(".interact-container") || scope).innerText || ""
  ).slice(0, 120);
  const candidates = (barText.match(/\d+(?:\.\d+)?\s*[万wW]?/g) || []).slice(0, 8).join(" | ");

  const duration = clean((scope.querySelector("video") ? (scope.querySelector("video").duration || "") : "") || "");
  const mmss = text.match(/\b(\d{1,2}:\d{2})\b/);
  const bodyHead = clean(text).slice(0, 40);

  const block = [
    BLOCK_START,
    "note_id: " + noteId,
    "title: " + title,
    "published: ",
    "likes: ",
    "collects: ",
    "comments: ",
    "shares: ",
    "duration: " + (mmss ? mmss[1] : duration),
    "human_note: ",
    "url: " + url.split("?")[0],
    "raw_len: " + text.length,
    "body_head: " + bodyHead,
    "bar_text: " + barText,
    "candidates: " + candidates,
    BLOCK_END,
  ].join("\n");

  navigator.clipboard.writeText(block).then(
    () => alert(
      (noteId ? "已复制 note_id=" + noteId : "没认出 note_id，请手填") +
      "\n\n互动栏原文：\n" + (barText || "（没找到，请直接看页面）") +
      "\n\n按原文填 赞/藏/评——别用候选值猜。"
    ),
    () => prompt("剪贴板不可用，请手动复制：", block)
  );
})();
