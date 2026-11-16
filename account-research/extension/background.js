// 后台（service worker）：**不再执行采集**，只负责图标上那点状态。
//
// ## 为什么把采集从这里挪走
//
// v7.1 把整条采集写在这个文件里，第一次真机点击就失败：
//
//   转换存档✗ The requested file could not be read, typically due to permission
//   problems that occur after a reference to a file has been acquired
//
// 那是 `DOMException: NotReadableError`，来自 `blob.arrayBuffer()`。
// `pageCapture.saveAsMHTML` 返回的**不是一个内存里的 Blob**，而是一个句柄——
// Chrome 把 MHTML 写进了 profile 的 blob storage，扩展上下文只拿到一个引用。
// 这种跨进程句柄在 MV3 的 service worker 里读不稳：worker 随时可能被回收，
// 而且它**没有渲染进程那套 File API**。
//
// 所以采集挪到了弹出面板（`popup.js` + `capture.js`，普通扩展页面 = 真渲染进程）。
// 代价是面板一关采集就断，界面上已明说「别关这个面板」。
// 这是有意换的：**一个会失败的重试成本，比一个「以为在跑其实没跑」的静默失败便宜得多。**
//
// ## 那这个文件还留着干什么
//
// 1. 面板把 badge 的状态发过来，这里负责画——面板一关 badge 也该留着，那是「刚才成了几篇」的记忆；
// 2. **兜底**：万一面板加载失败，Chrome 会退回到 `onClicked`，那时的反馈必须仍是可见的，
//    而不是「点了没反应」（E47）。
//
// ⚠️ 本文件**不许**出现 `pageCapture` / `scripting` 调用——它们都属于渲染进程那边。

const AR_BADGE = "AR_BADGE";

const BADGE_COLORS = {
  ok: "#3F6B4F",
  err: "#C05B3C",
  warn: "#7A7440",
  busy: "#9A948A",
};

// **即时可见的反馈**：图标上的小字 + 颜色。
// 只改 tooltip 等于没有反馈——用户不会去悬停一个「点了没反应」的按钮。
function badge(text, color, title) {
  try {
    chrome.action.setBadgeText({ text: String(text || "") });
    chrome.action.setBadgeBackgroundColor({ color: BADGE_COLORS[color] || BADGE_COLORS.ok });
  } catch (error) {
    // badge 失败不该让整次采集失败。
  }
  chrome.action.setTitle({ title: String(title || "").slice(0, 200) });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const kind = String((message && message.type) || "");
  if (kind !== AR_BADGE) return false;
  badge(message.text, message.color, message.title);
  sendResponse({ ok: true });
  return false;
});

chrome.action.onClicked.addListener(() => {
  // 正常情况下点图标会打开面板，这里不会触发。
  // 真触发只有一种可能：面板加载失败。那就必须说清楚，不能安静。
  badge("!", "err", "面板没能打开 —— 去 chrome://extensions 看这个扩展的报错，"
    + "或点「重新加载」后再试");
});
