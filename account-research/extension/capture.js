// 采集引擎：从「找到笔记页」到「字节送进宿主」的**全部逻辑**，不碰 DOM。
//
// ⚠️ 它必须跑在**有渲染进程的扩展文档**里（弹出面板），**不能跑在 service worker 里**。
// 原因见下面 `readBlob` 的长注释——这是 v7.2 修掉的那个 bug 的核心。

const MAX_TABS = 10;
const NOTE_URL_RE = /(?:explore|discovery\/item)\/([0-9a-f]{24})/i;
// 必须与 `src/account_research/host.py` 的 HOST_NAME 一致，
// 也与注册表项 `Software\Google\Chrome\NativeMessagingHosts\<这个名字>` 一致。
const HOST_NAME = "com.account_research.capture";
const PROTOCOL_VERSION = 1;
// 兜底的分块字符数，只在宿主没在 `hello` 里给 `chunk_chars` 时使用。
// **正常路径永远走宿主声明的值**——两边各自硬编码一个数字，改了一边就会在生产上断线。
const FALLBACK_CHUNK_CHARS = 96 * 1024;

// 采集期间**面板不能关**。原因：`pageCapture` 返回的是文件-backed 句柄，
// 只有渲染进程（面板）能可靠读出字节；面板一关，这次采集就断了。
//
// 这句话放在引擎里而不是界面里，是因为它是**引擎的限制**，不是排版问题。
// 界面上有好几处要显示它（标题、进度、完成提示），只写一处就有人看不到。
const KEEP_PANEL_OPEN = "采集期间别关这个面板";

const AR = (function () {
  "use strict";

  // ── Blob → base64 ──────────────────────────────────────────────────────
  //
  // ## v7.2：为什么要两条读法，为什么不能读在 service worker 里
  //
  // 真实失败（2026-11-16，第一次真机点击）：
  //
  //   转换存档✗ The requested file could not be read, typically due to permission
  //   problems that occur after a reference to a file has been acquired
  //
  // 那是 `DOMException: NotReadableError`，来自 `blob.arrayBuffer()`。
  // 注意它**不是说 blob 是空的**：`blob.size` 读得出非零值，只是**取不到背后的数据**。
  //
  // `pageCapture.saveAsMHTML` 返回的不是存在内存里的一串字节，而是一个**句柄**
  // （Chrome 把 MHTML 写进了 profile 的 blob storage，扩展上下文只拿到引用）。
  // 这种跨进程的句柄在 **service worker** 里读不稳：
  // MV3 的 worker 随时可能被回收、它没有渲染进程的 File API 兜底路径，
  // 而 blob 的实际数据属于浏览器进程里的一块临时存储。
  //
  // 所以改成在**扩展的普通页面**（弹出面板 = 真渲染进程）里读：
  // 同一类 API、同一套 blob 注册表，而且渲染进程里**有 `FileReader`**
  // ——对文件-backed 的 blob，它走的是浏览器进程里那条已知可用的读路径。
  //
  // ## 两条读法为什么都要留着
  //
  // `FileReader` 是这次修复的根据（渲染进程专用）；`arrayBuffer()` 是最直接的，
  // 在内存 blob 上更省事。两条都失败才报错，并且**把两条各自的原因都写进错误信息**
  // ——只报一条的话，下次还是不知道另一条通不通。

  function toBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    // 一次 `String.fromCharCode.apply` 传太多参数会爆栈，32 KB 是常见的安全值。
    const chunk = 0x8000;
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunk));
    }
    return btoa(binary);
  }

  // 读法一：`FileReader`。**它是这里唯一一个「渲染进程专用」的读法**，
  // 也就是为什么这个文件必须跑在面板里：service worker 里根本没有 `FileReader`。
  // 对「文件-backed 的 blob」（`pageCapture` 返回的就是），
  // FileReader 走的是浏览器进程里那条已知可用的读路径。
  function readViaFileReader(blob) {
    if (typeof FileReader === "undefined") {
      throw new Error("这个上下文没有 FileReader（是不是跑在 service worker 里了？）");
    }
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("FileReader 读取失败"));
      reader.onabort = () => reject(new Error("FileReader 被中止"));
      reader.readAsArrayBuffer(blob);
    });
  }

  // 读法二：`blob.arrayBuffer()`。最直接，但对文件-backed 的 blob 可能报
  // `NotReadableError`——正是 v7.1 在 service worker 里踩到的那个。
  function readViaArrayBuffer(blob) {
    return blob.arrayBuffer();
  }

  // 读法三：`createObjectURL` + `fetch`。
  //
  // ⚠️ 这条**不是**「换了个看起来不一样的 API」那种假兜底——它是**另一条真实路径**：
  // 前面两条抢的是扩展自己的 blob 注册表，这条走的是网络栈（blob URL 由浏览器解析）。
  // 实测里用来救「注册表里没有、但 blob storage 里还在」这一种。
  //
  // 判据（E68）：它在主路径失败的那个场景里会不会表现不同？
  // 这里会——`arrayBuffer()` 报 NotReadableError 时它有时能读出完整字节。
  async function readViaObjectUrl(blob) {
    if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
      throw new Error("这个上下文没有 URL.createObjectURL");
    }
    const url = URL.createObjectURL(blob);
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`取 blob 失败：HTTP ${response.status}`);
      return await response.arrayBuffer();
    } finally {
      // 立刻撤销：blob URL 会一直钉住内存，20 MB 的存档撤销晚一次就是白占 20 MB。
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }
  }

  async function readBlob(blob) {
    const methods = [
      // 渲染进程专用：service worker 里根本没有 FileReader，这是 v7.2 修复的根据。
      ["FileReader", readViaFileReader],
      ["arrayBuffer", readViaArrayBuffer],
      ["objectURL", readViaObjectUrl],
    ];
    const errors = [];
    for (const [name, read] of methods) {
      try {
        const buffer = await read(blob);
        if (!buffer || buffer.byteLength === 0) {
          errors.push(`${name}: 读到 0 字节`);
          continue;
        }
        return { buffer, method: name, errors };
      } catch (error) {
        errors.push(`${name}: ${String((error && error.message) || error).slice(0, 120)}`);
      }
    }
    const detail = errors.join(" | ");
    throw new Error(`读不到存档字节（size=${blob.size}）—— ${detail}`);
  }

  // Blob → base64。对外仍叫这个名字（面板只关心「拿到 base64 了」）。
  async function blobToBase64(blob) {
    const { buffer, method, errors } = await readBlob(blob);
    return { base64: toBase64(buffer), method, errors, bytes: buffer.byteLength };
  }

  // ── 与本地宿主的会话 ───────────────────────────────────────────────────
  //
  // 一次采集开一个，处理完所有标签页再关。
  // 不「每个文件一次 connectNative」的理由：每次连接都会**启动一个新的宿主进程**，
  // 10 篇笔记就是 10 次 Python 冷启动（每次几百毫秒）。
  class NativeSession {
    constructor(port) {
      this.port = port;
      this.nextId = 1;
      this.pending = new Map();
      this.closed = false;
      this.chunkChars = FALLBACK_CHUNK_CHARS;
      port.onMessage.addListener((message) => this._settle(message));
      port.onDisconnect.addListener(() => this._disconnect());
    }

    static open() {
      return new Promise((resolve, reject) => {
        let port;
        try {
          port = chrome.runtime.connectNative(HOST_NAME);
        } catch (error) {
          reject(error);
          return;
        }
        const session = new NativeSession(port);
        session.request({ type: "hello" }, 5000).then((reply) => {
          const version = Number(reply && reply.protocol);
          if (version !== PROTOCOL_VERSION) {
            session.close();
            reject(new Error(`版本不匹配：扩展 ${PROTOCOL_VERSION}，宿主 ${version || "未知"}`));
            return;
          }
          const declared = Number(reply && reply.chunk_chars);
          if (declared > 0) session.chunkChars = declared;
          resolve(session);
        }).catch((error) => {
          session.close();
          reject(error);
        });
      });
    }

    request(payload, timeoutMs = 120000) {
      if (this.closed) return Promise.reject(new Error("会话已关闭"));
      const id = this.nextId++;
      const message = Object.assign({}, payload, { id });
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          this.pending.delete(id);
          reject(new Error(`宿主 ${Math.round(timeoutMs / 1000)} 秒没回应`));
        }, timeoutMs);
        this.pending.set(id, { resolve, reject, timer });
        try {
          this.port.postMessage(message);
        } catch (error) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(error);
        }
      });
    }

    _settle(message) {
      const entry = this.pending.get(message && message.id);
      if (!entry) return;  // 不是我们发的（或已超时），忽略
      clearTimeout(entry.timer);
      this.pending.delete(message.id);
      entry.resolve(message);
    }

    _disconnect() {
      this.closed = true;
      const lastError = chrome.runtime.lastError;
      const reason = (lastError && lastError.message) || "宿主断开了连接";
      for (const [, entry] of this.pending) {
        clearTimeout(entry.timer);
        entry.reject(new Error(reason));
      }
      this.pending.clear();
    }

    // 一次完整上传：begin → chunk…（分块）→ end。
    async upload({ noteId, base64 }) {
      const sessionId = `s${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await this.request({ type: "begin", session: sessionId, note_id: noteId });
      const step = this.chunkChars;
      for (let offset = 0; offset < base64.length; offset += step) {
        const data = base64.slice(offset, offset + step);
        const reply = await this.request({ type: "chunk", session: sessionId, data });
        if (reply && reply.ok === false) throw new Error(reply.error || "上传数据块失败");
      }
      return this.request(
        { type: "end", session: sessionId },
        // 入库要拆包、解析、写表、跑 OCR（本地 onnxruntime），20 MB 的笔记实测十几秒。
        180000,
      );
    }

    close() {
      this.closed = true;
      try {
        this.port.disconnect();
      } catch (error) {
        // 已经断了就算了。
      }
    }
  }

  // 宿主没装 / 名字不对 / 版本不匹配 / 被注销，Chrome 给的原话都很薄，
  // 必须翻译成「你现在该做什么」，否则看起来就是「插件坏了」。
  function hostHint(error) {
    const message = String((error && error.message) || error);
    if (/not found|找不到|Specified native messaging host/i.test(message)) {
      return "本地宿主没注册 —— 在项目里跑一次 `python ar.py host --register`（只需一次），然后重试。";
    }
    if (/forbidden|Access to the specified native messaging host/i.test(message)) {
      return "宿主清单里没允许这个扩展 ID —— 重新跑 `python ar.py host --register`（扩展 ID 变过必须重注册）。";
    }
    if (/版本|protocol/i.test(message)) {
      return `宿主与扩展的协议版本不一致（${message}）—— 两边一起更新后重新注册。`;
    }
    return `连不上本地宿主：${message}。跑 \`python ar.py host --doctor\` 看哪一环断了。`;
  }

  function matchNoteId(url) {
    const match = String(url || "").match(NOTE_URL_RE);
    return match ? match[1] : "";
  }

  // ── 主流程 ─────────────────────────────────────────────────────────────
  //
  // 返回一个**结构化结果**（不是字符串）：面板要按字段上色、要能显示「失败在哪一层」。
  // 调用方（面板）负责呈现；这里只负责采、读、传。
  async function captureAll(options) {
    const onProgress = (options && options.onProgress) || (() => {});
    const result = {
      saved: 0, failed: 0, failures: [], bytes: 0,
      withoutState: 0, partialImages: 0, needReload: 0, skipped: 0,
      total: 0, logs: [], readMethod: "",
    };

    let tabs = [];
    try {
      tabs = await chrome.tabs.query({ currentWindow: true, url: "https://www.xiaohongshu.com/*" });
    } catch (error) {
      result.failures.push("读取标签页列表失败：" + String((error && error.message) || error));
      result.failed = 1;
      return result;
    }

    const candidates = tabs.filter((tab) => tab.id && NOTE_URL_RE.test(tab.url || ""));
    if (!candidates.length) {
      // 最常见的一种「没反应」：当前窗口里没有笔记页。
      result.failures.push("当前窗口里没有小红书笔记页（先中键连开几篇）");
      return result;
    }
    const targets = candidates.slice(0, MAX_TABS);
    result.total = targets.length;
    result.skipped = candidates.length - targets.length;

    let session;
    try {
      session = await NativeSession.open();
    } catch (error) {
      result.failures.push(hostHint(error));
      result.failed = 1;
      return result;
    }

    try {
      for (const [index, target] of targets.entries()) {
        onProgress({ index, total: targets.length, noteId: matchNoteId(target.url || "") });
        if (target.discarded || target.status === "loading") {
          result.needReload += 1;
          continue;
        }
        // **每一步单独包一层，让报错自己说清是哪一层坏的。**
        // 把四步塞进一个大 try，`A requested file...` 出来时谁也说不清是谁抛的——
        // 「猜」正是 v5→v6 反复两轮的原因。
        const fail = (step, error) => {
          const message = String((error && error.message) || error).slice(0, 200);
          result.failures.push(`${step}✗ ${message}`);
          result.failed += 1;
        };

        let probe;
        try {
          // 先确认这是**整页打开**的笔记页，不是从主页点开的弹层。
          // 判据同 `initialstate.classify`：弹层是 SPA 内部跳转，文档没重新加载，
          // `og:url` 仍指向主页 → 存下来的是主页。
          // MAIN world：`hasState` 要读页面自己的 `window.__INITIAL_STATE__`。
          const [probeResult] = await chrome.scripting.executeScript({
            target: { tabId: target.id },
            world: "MAIN",
            func: probePage,
          });
          probe = (probeResult && probeResult.result) || null;
        } catch (error) {
          fail("识别页面", error);
          continue;
        }
        if (!probe || !probe.isFullPage) {
          result.failures.push(
            probe && probe.reason === "modal"
              ? "从主页点开的弹层（不是整页打开）"
              : `无法识别页面（${(target.url || "").slice(-24)}）`
          );
          result.failed += 1;
          continue;
        }
        if (!probe.hasState) result.withoutState += 1;
        // 懒加载提示：图没加载过，存下来的 MHTML 里就只剩远程 URL、本地没字节。
        // 只能**提醒**，不能替人去滚——滚动会触发下载，那才是「程序制造请求」。
        if (probe.totalImages > probe.loadedImages) result.partialImages += 1;

        let blob;
        let converted;
        // **存档 + 读字节要作为一个整体重试。**
        //
        // 只重试「读」是没用的：读失败说明这个句柄背后的数据已经不在了，
        // 再读同一个句柄还是失败。要重新让 Chrome 存一次，拿一个新句柄。
        // （实测过一次瞬时失败：blob 还在落盘时立刻读会报 NotReadableError。）
        for (let attempt = 0; attempt < 2; attempt += 1) {
          try {
            blob = await chrome.pageCapture.saveAsMHTML({ tabId: target.id });
          } catch (error) {
            fail("存档页面", error);
            blob = null;
            break;
          }
          if (!blob || !blob.size) {
            result.failures.push("存档得到 0 字节（页面可能还没渲染完，稍等再试）");
            result.failed += 1;
            blob = null;
            break;
          }
          try {
            converted = await blobToBase64(blob);
            break;
          } catch (error) {
            if (attempt === 0) {
              // 第一次读不出来：重新存一次再读。间歇性失败多半是这一种。
              await new Promise((resolve) => setTimeout(resolve, 250));
              continue;
            }
            fail("转换存档", error);
            converted = null;
          }
        }
        if (!blob || !converted) continue;
        result.readMethod = converted.method;

        let reply;
        try {
          reply = await session.upload({
            noteId: matchNoteId(target.url || "") || probe.noteId || "unknown",
            base64: converted.base64,
          });
        } catch (error) {
          fail("入库", error);
          continue;
        }
        if (!reply || !reply.ok) {
          result.failures.push(`入库✗ ${(reply && reply.error) || "宿主没回结果"}`);
          result.failed += 1;
          continue;
        }
        result.saved += 1;
        result.bytes += blob.size;
        if (reply.log) result.logs.push(String(reply.log).trim());
        onProgress({ index, total: targets.length, noteId: probe.noteId, done: true });
      }
    } finally {
      session.close();
    }
    return result;
  }

  // 只读：库的现状。面板据此显示「今日已采 / 库里篇数 / 有正文」。
  async function readStatus() {
    let session;
    try {
      session = await NativeSession.open();
    } catch (error) {
      return { ok: false, hostReady: false, hint: hostHint(error) };
    }
    try {
      const reply = await session.request({ type: "status" }, 10000);
      return { ok: true, hostReady: true, status: reply };
    } catch (error) {
      return { ok: false, hostReady: true, hint: String((error && error.message) || error) };
    } finally {
      session.close();
    }
  }

  // ⚠️ 本函数被序列化注入页面主上下文执行——必须自包含，不能引用外部变量或 chrome.*。
  // 只读已渲染的 DOM 与已存在的 JS 对象；不发请求、不滚动、不点击平台控件。
  function probePage() {
    const NOTE_RE = /(?:explore|discovery\/item)\/([0-9a-f]{24})/i;
    const match = location.href.match(NOTE_RE);
    const noteId = match ? match[1] : "";
    if (!noteId) {
      return { isFullPage: false, noteId: "", reason: "not_a_note" };
    }

    // **必须是整页打开**，不能是从主页点开的弹层。
    const ogMeta = document.querySelector('meta[property="og:url"]');
    const ogUrl = ogMeta ? ogMeta.getAttribute("content") || "" : "";
    if (!NOTE_RE.test(ogUrl)) {
      return { isFullPage: false, noteId, reason: "modal" };
    }

    // 读 state 只为了**报告**它有没有（分享数/精确发布日靠它）；解析仍交给 Python 侧，
    // 保持「一份真相源」——这里不重算数字，否则两条路径迟早对不上。
    let hasState = false;
    try {
      const state = window.__INITIAL_STATE__;
      const wrapper =
        state && state.note && state.note.noteDetailMap && state.note.noteDetailMap[noteId];
      hasState = !!(wrapper && wrapper.note);
    } catch (error) {
      hasState = false;
    }

    // 统计 slider 里**真正加载出来**的图片张数。⚠️ 只读数，不修改页面。
    //
    // **为什么需要**：小红书的详情页图片是 `loading="lazy"` 的，只有滚动到那一张，
    // 浏览器才会下载它；**没下载过的图不在内存里**，`pageCapture` 也就存不到。
    // 实测（`6aed6c2b`，16 图）：16 个 slide 里只有 3 张真的下载过。
    //
    // 判据用 `img.complete && naturalWidth > 0`：这两个属性在**图已下载并解码**时才成立。
    // 不能用「src 是不是 http」来判断——所有 img 的 src 都是远程地址。
    let loadedImages = 0;
    let totalImages = 0;
    try {
      const slides = document.querySelectorAll('[data-swiper-slide-index] img');
      totalImages = slides.length;
      for (const img of slides) {
        if (img.complete && img.naturalWidth > 0) loadedImages += 1;
      }
    } catch (error) {
      loadedImages = 0;
      totalImages = 0;
    }

    return { isFullPage: true, noteId, hasState, loadedImages, totalImages };
  }

  return {
    MAX_TABS, HOST_NAME, PROTOCOL_VERSION,
    captureAll, readStatus, blobToBase64, readBlob, NativeSession,
    hostHint, matchNoteId, probePage, KEEP_PANEL_OPEN,
  };
})();

// 挂到全局：浏览器里 `popup.js` 靠它取 `AR`；测试沙箱（vm）里靠它取到同一个对象。
// **不用 `const AR = ...` 就够了**——`const` 是词法绑定，不会出现在全局对象上，
// 而 vm 沙箱正是通过全局对象取值的。这个坑让第一次跑测试时拿到 `undefined`。
if (typeof globalThis !== "undefined") {
  globalThis.AR = AR;
}
