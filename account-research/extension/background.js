// 后台：把内容脚本抓到的块存成本地 txt，并提示结果。
// 只用 downloads API，不做任何网络请求。

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) {
    notify("拿不到当前标签页");
    return;
  }
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectFromPage,
    });
    const payload = result && result.result;
    if (!payload || !payload.block) {
      notify("这个页面没采到内容——确认是小红书笔记详情页，且已滑到互动栏");
      return;
    }
    const filename = `ar-note-${payload.noteId || "unknown"}.txt`;
    const url = "data:text/plain;charset=utf-8," + encodeURIComponent(payload.block);
    await chrome.downloads.download({ url, filename, saveAs: false });
    notify(
      (payload.noteId ? `已保存 ${filename}` : "没认出 note_id，已保存为 unknown") +
        "\n\n互动栏原文（照它填 赞/藏/评）：\n" +
        (payload.barText || "（没找到，请直接看页面）") +
        "\n\n下一步：ar ingest"
    );
  } catch (error) {
    notify("采集失败：" + (error && error.message ? error.message : String(error)));
  }
});

function notify(message) {
  // 用页面内 alert 之外的轻提示：这里退化为 console + 图标角标，避免注入更多 DOM。
  chrome.action.setTitle({ title: message.slice(0, 120) });
  console.log("[AR采集]", message);
}

// 这个函数会被序列化注入页面执行——必须是自包含的，不能引用外部变量。
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
