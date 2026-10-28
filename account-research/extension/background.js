// 后台：把当前窗口里**所有已打开的小红书笔记页**各存一个 txt。
//
// 【为什么可以批量——这是本次设计的关键论证】
//   判断标准不是"一次处理几个页面"，而是**这些页面的请求是谁发起的**。
//   这些标签页全部是你自己一个个点开的：请求在页面加载时就已经发生，由你的浏览行为产生。
//   本脚本做的事是"把你**已经看过的页面上的文字**存下来"——**零请求、零导航、零新标签**。
//
//   被禁止的「批量存档」指的是**程序把页面拉回来**（程序发起请求）；
//   不被禁止的是「把已经在你眼前的页面存下来」（请求早已由人产生）。
//   这两件事看起来都叫"批量"，但一个增加请求，一个不增加——差别在于**有没有新的请求产生**。
//
// 【硬上限 MAX_TABS】不是技术限制，是行为限制：一次开 10 个标签页本身就会形成请求突发，
//   比人逐篇点更不像正常用户。建议一次开 3–5 个，读完再点一次保存。
//
// 【为什么必须跳过 discarded / loading 的标签】
//   Chrome 会把长时间不用的标签"丢弃"（discarded），它此时**不在内存里**。
//   对丢弃的标签注入脚本，浏览器需要**先把页面重新加载回来**——那才是真的产生了一次请求，
//   而且是由本扩展触发的。这会把"零请求"这条论证直接击穿。所以必须显式跳过。
//   同理，status === "loading" 的页面还没渲染完，读到的会是空壳。
//
// 【不做什么】不发网络请求、不导航、不开新 tab、不点页面上的平台控件、不读 cookie/storage。

const MAX_TABS = 10;
const NOTE_URL_RE = /(?:explore|discovery\/item)\/([0-9a-f]{24})/i;

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) {
    notify("拿不到当前标签页");
    return;
  }
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ currentWindow: true, url: "https://www.xiaohongshu.com/*" });
  } catch (error) {
    notify("读取标签页列表失败：" + String((error && error.message) || error));
    return;
  }

  const candidates = tabs.filter((t) => t.id && NOTE_URL_RE.test(t.url || ""));
  const targets = candidates.slice(0, MAX_TABS);

  let saved = 0;
  let needReload = 0;
  const failures = [];
  for (const target of targets) {
    // 丢弃 / 未加载完成的标签一律跳过：注入会触发重新加载 = 我们制造了一次请求。
    if (target.discarded || target.status === "loading") {
      needReload += 1;
      continue;
    }
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: target.id },
        func: collectFromPage,
      });
      const payload = result && result.result;
      if (!payload || !payload.block) {
        failures.push((target.url || "").slice(0, 60));
        continue;
      }
      const filename = `ar-note-${payload.noteId || "unknown"}.txt`;
      const url = "data:text/plain;charset=utf-8," + encodeURIComponent(payload.block);
      await chrome.downloads.download({ url, filename, saveAs: false });
      saved += 1;
    } catch (error) {
      failures.push((target.url || "").slice(0, 60));
    }
  }

  const skipped = candidates.length - targets.length;
  const parts = [`已保存 ${saved} 篇`];
  if (failures.length) parts.push(`失败 ${failures.length} 篇`);
  if (needReload) parts.push(`${needReload} 个标签未加载/已休眠，已跳过（避免为它重新加载页面）`);
  if (skipped > 0) parts.push(`${skipped} 个标签超出单次上限 ${MAX_TABS}`);
  notify(parts.join("；") + "\n\n下一步：ar watch 会自动入库");
});

function notify(message) {
  chrome.action.setTitle({ title: message.slice(0, 120) });
  console.log("[AR采集]", message);
}

// 这个函数会被序列化注入页面执行——必须自包含，不能引用外部变量。
function collectFromPage() {
  const BLOCK_START = "===AR-NOTE-v1===";
  const BLOCK_END = "===END===";
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  const url = location.href;
  const idMatch = url.match(/(?:explore|discovery\/item)\/([0-9a-f]{24})/i);
  const noteId = idMatch ? idMatch[1] : "";

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

  // 互动栏只取原文，不解析数字——选择器一改就会变成静默错数据。
  const barText = clean(
    (document.querySelector(".interact-container") || scope).innerText || ""
  ).slice(0, 120);
  const candidates = (barText.match(/\d+(?:\.\d+)?\s*[万wW]?/g) || []).slice(0, 8).join(" | ");

  const video = scope.querySelector("video");
  const mmss = text.match(/\b(\d{1,2}:\d{2})\b/);
  const block = [
    BLOCK_START,
    "note_id: " + noteId,
    "title: " + title,
    "published: ",
    "likes: ",
    "collects: ",
    "comments: ",
    "shares: ",
    "duration: " + (mmss ? mmss[1] : video && video.duration ? String(video.duration) : ""),
    "human_note: ",
    "url: " + url.split("?")[0],
    "raw_len: " + text.length,
    "body_head: " + text.slice(0, 40),
    "bar_text: " + barText,
    "candidates: " + candidates,
    BLOCK_END,
  ].join("\n");

  return { block, noteId, barText };
}
