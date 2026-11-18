// 用**真实 capture.js 源码**跑一遍完整采集流程，只把 chrome.* 与 FileReader 换成桩。
//
// ⚠️ 这个桩里有**五条必须保持**的仿真，每一条都对应一个真实踩过的坑：
//
// 1. **`pageCapture` 返回的不是内存 Blob，是「文件-backed」句柄。**
//    它的 `size` 读得出来，`arrayBuffer()` 却抛 `NotReadableError`：
//      The requested file could not be read, typically due to permission problems
//      that occur after a reference to a file has been acquired
//    这正是 v7.1 在 service worker 里炸的那个错。桩必须重现它，
//    否则「用 FileReader 兜底」这个修法根本测不到。
//
// 2. **`FileReader` 只在渲染进程（= 面板）里存在。**
//    桩按上下文决定它有没有：`service-worker` 里没有、`popup` 里有。
//    这条仿真守住的是**架构决定**——哪天有人把采集挪回 background，测试会当场红。
//
// 3. **宿主对单条消息有上限，超了端口断。** 桩把上限设 64 KB、块大小声明 8 KB，
//    逼出分块。把分块「简化」掉会被立刻发现。
//
// 4. **宿主是独立进程，回包只能靠 id 配对。** 桩用随机延时打乱回包。
//
// 5. **`lastError` 只在断开那一刻可读。** 用 getter 模拟。
//
// 这五条都有对应断言守着（见 `tests/test_extension_flow.py`）。

const fs = require('fs');
const vm = require('vm');

const captureSrc = fs.readFileSync(process.argv[2], 'utf8');

// 桩里宿主的单条消息上限。真实 Chrome 的限额不归我们管，所以取小值来逼出分块。
const MAX_MESSAGE_BYTES = 64 * 1024;
// 桩里宿主声明的块大小（base64 字符数）。**故意设小**，让 60 KB 样本切成十几块。
const STUB_CHUNK_CHARS = 8192;
// 桩里的协议版本。必须跟着 capture.js 的 `PROTOCOL_VERSION` 走——
// 写成常量而不是从源码里解析，是为了让「两边一起改」这件事在测试里**必须发生一次**。
const STUB_PROTOCOL = 2;

const log = { badge: [], uploads: [], errors: [], host: [], progress: [] };

// 宿主侧收到的「文件」：session → { noteId, chunks: [Buffer] }
const sessions = new Map();

// 「第一次读不出来」的仿真只用一次：第二次存档要给一个能读的。
const transientUsed = new Set();
// 每次 `saveAsMHTML` 都记一笔：用来证明「读失败 → 重新存一次」真的发生了。
const saveAttempts = [];

// `createObjectURL` 的桩：按 blob 上的 `readableByUrl` 决定 fetch 能不能取到。
const urlToBlob = new Map();
let urlCounter = 0;
function createObjectURL(blob) {
  const url = `blob:stub/${++urlCounter}`;
  urlToBlob.set(url, blob);
  return url;
}
async function fetchObjectUrl(url) {
  const blob = urlToBlob.get(url);
  if (!blob || blob.readableByUrl === false) {
    throw new Error('Failed to fetch');
  }
  const bytes = blob._bytes;
  return {
    ok: true,
    status: 200,
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length),
  };
}

// 仿真 1 的核心：一个「有 size 但读不出来」的 blob。
class FileBackedBlob {
  constructor(bytes, options) {
    this.size = bytes.length;
    this._bytes = Buffer.from(bytes);
    this.type = 'multipart/related';
    // `broken` = 句柄背后的数据此刻还不在（blob 正在落盘）。
    // 这时**三条读法全都会失败**——这才叫「真·读不出来」。
    // 否则就变成「换个读法总有一条能成」，测不出「要重新存一次」这个修法。
    this.broken = Boolean(options && options.broken);
    // `readableByUrl` = `createObjectURL` + `fetch` 这条读法能不能成。
    // 真实世界里它有时能救回「扩展注册表里没有、但 blob storage 里还在」的那一种。
    this.readableByUrl = !this.broken;
  }
  arrayBuffer() {
    // 真实错误：DOMException NotReadableError
    const error = new Error(
      'The requested file could not be read, typically due to permission problems '
      + 'that occur after a reference to a file has been acquired.');
    error.name = 'NotReadableError';
    return Promise.reject(error);
  }
}

// 仿真 2：`FileReader` 只在渲染进程里有，而且它能读到文件-backed blob 的内容
// （因为它走的是浏览器进程那条已知可用的读路径）。
function installFileReader(sandbox, context) {
  if (context !== 'popup') {
    sandbox.FileReader = undefined;  // service worker 里没有这个类
    return;
  }
  sandbox.FileReader = class FakeFileReader {
    constructor() { this.result = null; this.error = null; this.onload = null; this.onerror = null; }
    readAsArrayBuffer(blob) {
      setTimeout(() => {
        try {
          // `broken` 的句柄：FileReader 也读不出来（数据此刻真的不在）。
          if (blob.broken) throw new Error('The requested file could not be read');
          const bytes = blob._bytes;
          this.result = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length);
          if (this.onload) this.onload();
        } catch (error) {
          this.error = error;
          if (this.onerror) this.onerror();
        }
      }, 1);
    }
  };
}

function hostHandle(message) {
  const session = String(message.session || 'default');
  const reply = (extra) => ({ id: message.id, session, ...extra });

  switch (String(message.type || '')) {
    case 'hello':
      // 协议版本必须与 capture.js 的 `PROTOCOL_VERSION` 一致，
      // 否则扩展会在握手时就说「版本不匹配」——那正是 v2 想要的失败方式。
      return reply({ ok: true, protocol: STUB_PROTOCOL, chunk_chars: STUB_CHUNK_CHARS, host: 'stub' });
    case 'begin':
      // v2：`state` 与这次存档配对，落在同一个 session 里。
      sessions.set(session, {
        noteId: String(message.note_id || ''),
        kind: String(message.kind || 'note'),
        state: message.state || null,
        chunks: [],
      });
      return reply({ ok: true, chunks: 0 });
    case 'chunk': {
      const upload = sessions.get(session);
      if (!upload) return reply({ ok: false, error: '还没 begin 就发 chunk' });
      const data = String(message.data || '');
      if (!/^[A-Za-z0-9+/]*={0,2}$/.test(data) || data.length % 4 !== 0) {
        sessions.delete(session);
        return reply({ ok: false, error: '块不是合法 base64' });
      }
      upload.chunks.push(Buffer.from(data, 'base64'));
      return reply({ ok: true, chunks: upload.chunks.length });
    }
    case 'end': {
      const upload = sessions.get(session);
      sessions.delete(session);
      if (!upload) return reply({ ok: false, error: '还没 begin 就发 end' });
      const bytes = Buffer.concat(upload.chunks);
      if (!bytes.length) return reply({ ok: false, error: '这份存档是 0 字节' });
      log.uploads.push({ noteId: upload.noteId, kind: upload.kind, bytes,
                         chunks: upload.chunks.length, state: upload.state });
      return reply({
        ok: true, exit_code: 0, note_id: upload.noteId, bytes: bytes.length,
        log: `✅ ${upload.noteId}：${upload.kind === 'profile' ? '主页' : '详情页'}\nnotes：新增 1 行（校验先于落盘 ✅）`,
      });
    }
    case 'status':
      return reply({ ok: true, today: '2026-11-16', accounts: 3, notes: 68,
                     collected_today: 2, with_body: 13, quota_notes: [2, 30],
                     data_dir: 'D:\\stub\\data' });
    case 'abort':
      sessions.delete(session);
      return reply({ ok: true });
    default:
      return reply({ ok: false, error: `不认识的消息类型：${message.type}` });
  }
}

// 仿真 5：`lastError` 只在断开后存在，被读一次就清掉。
let disconnectReason = null;
function makePort() {
  const listeners = { message: [], disconnect: [] };
  let closed = false;
  return {
    onMessage: { addListener: (fn) => listeners.message.push(fn) },
    onDisconnect: { addListener: (fn) => listeners.disconnect.push(fn) },
    postMessage(raw) {
      if (closed) throw new Error('Attempting to use a disconnected port object');
      const message = JSON.parse(JSON.stringify(raw));  // 仿真：Chrome 走 JSON
      const encoded = Buffer.byteLength(JSON.stringify(message), 'utf8');
      if (encoded > MAX_MESSAGE_BYTES) {                 // 仿真 3：超限断线
        disconnectReason = 'Native host has exited.';
        closed = true;
        log.host.push(`拒绝 ${encoded} 字节（上限 ${MAX_MESSAGE_BYTES}）→ 端口断开`);
        for (const fn of listeners.disconnect) fn();
        return;
      }
      const reply = hostHandle(message);
      log.host.push(`${message.type}#${message.id} → ${reply.ok ? 'ok' : 'err'}`
        + (message.type === 'chunk' ? ` (${String(message.data || '').length} 字符)` : ''));
      // 仿真 4：随机延时打乱回包顺序
      const delay = message.type === 'end' ? 30 : Math.floor(Math.random() * 12);
      setTimeout(() => { if (!closed) for (const fn of listeners.message) fn(reply); }, delay);
    },
    disconnect() { closed = true; for (const fn of listeners.disconnect) fn(); },
  };
}

const chromeStub = {
  tabs: {
    query: async () => ([
      { id: 1, url: 'https://www.xiaohongshu.com/explore/6ac48e16000000001203ed6e', status: 'complete', discarded: false },
      // 仿真 1：这一篇的存档是「文件-backed」的，arrayBuffer() 会抛 NotReadableError
      { id: 2, url: 'https://www.xiaohongshu.com/explore/6ad9810e00000000330219f4', status: 'complete', discarded: false },
      { id: 3, url: 'https://www.xiaohongshu.com/explore/6ac48e16000000001203ed6f', status: 'complete', discarded: true },
      // v8：主页也要能采。它排在列表最后，靠「主页优先」的排序提到最前面。
      { id: 4, url: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075', status: 'complete', discarded: false },
      // 既不是笔记也不是主页的页面（搜索结果页）——不该被当成候选。
      { id: 9, url: 'https://www.xiaohongshu.com/search_result?keyword=abc', status: 'complete', discarded: false },
    ]),
  },
  scripting: {
    executeScript: async ({ target }) => ([{
      result: target.tabId === 1
        // tab 1：懒加载没滚完（16 张里只加载了 3 张）→ 应触发「有图没加载出来」提示
        ? { isFullPage: true, kind: 'note', noteId: 'y', hasState: false, totalImages: 16, loadedImages: 3, state: null }
        // tab 2：正常整页
        : target.tabId === 4
          // tab 4：主页。state 是它的唯一数据源，必须带上来。
          ? { isFullPage: true, kind: 'profile', noteId: '62c98736000000001501e075',
              hasState: true, totalImages: 0, loadedImages: 0, domCards: 32,
              state: { user: { userPageData: { basicInfo: { nickname: '测试主页' } }, notes: [] } } }
          : { isFullPage: true, kind: 'note', noteId: 'z', hasState: true, totalImages: 4, loadedImages: 4, state: null },
    }]),
  },
  pageCapture: {
    saveAsMHTML: async ({ tabId }) => {
      // **两篇都是文件-backed 的**——真实情况就是如此，`pageCapture` 从不返回内存 Blob。
      //
      // tab 1 再加一层仿真：**第一次存出来的句柄读不出来，第二次才读得出**。
      // 对应实测遇到过的瞬时失败（blob 还在落盘时立刻读）。
      // 所以「读」的重试必须是「重新存 + 再读」——只重读同一个句柄会一直失败，
      // 因为坏掉的是那个句柄，不是那一次调用。
      const bytes = Buffer.alloc(60 * 1024, 0x41);
      saveAttempts.push({ tabId, at: Date.now() });
      if (tabId === 1 && !transientUsed.has(tabId)) {
        transientUsed.add(tabId);
        return new FileBackedBlob(bytes, { broken: true });
      }
      return new FileBackedBlob(bytes);
    },
  },
  runtime: {
    connectNative: (name) => {
      if (name !== 'com.account_research.capture') {
        throw new Error('Specified native messaging host not found.');
      }
      return makePort();
    },
    onMessage: { addListener: () => {} },
    sendMessage: async () => ({ ok: true }),
    get lastError() { return disconnectReason ? { message: disconnectReason } : undefined; },
  },
  action: {
    setBadgeText: ({ text }) => log.badge.push(text),
    setBadgeBackgroundColor: () => {},
    setTitle: () => {},
  },
};

// 仿真 2b：`URL.createObjectURL` 也只在渲染进程里有。
// MV3 的 service worker 里它根本不存在——这正是 v6 当年要借 offscreen 文档的原因。
function installUrl(sandbox, context) {
  if (context !== 'popup') {
    sandbox.URL = {};  // 没有 createObjectURL
    return;
  }
  sandbox.URL = { createObjectURL, revokeObjectURL: () => {} };
  sandbox.fetch = fetchObjectUrl;
}

function makeSandbox(context) {
  const sandbox = {
    chrome: chromeStub,
    console: { log: () => {}, error: (m) => log.errors.push(String(m)), warn: () => {} },
    setTimeout, clearTimeout, Promise, JSON, String, Number, Math, Array, Object, Date,
    RegExp, Error, Uint8Array, Buffer, btoa, atob,
  };
  installFileReader(sandbox, context);
  installUrl(sandbox, context);
  vm.createContext(sandbox);
  vm.runInContext(captureSrc, sandbox);
  return sandbox;
}

(async () => {
  // ── 场景 A：在**面板**（渲染进程）里跑，其中一篇是文件-backed blob
  //    预期：靠 FileReader 救回来，两篇都入库。
  const popup = makeSandbox('popup');
  if (!popup.AR) {
    console.log('❌ capture.js 没有导出 AR');
    process.exit(1);
  }

  const result = await popup.AR.captureAll({
    onProgress: ({ index, total, noteId, done }) => log.progress.push(
      `${index + 1}/${total} ${noteId || '…'}${done ? ' ✓' : ''}`),
  });

  console.log('=== 进度 ===');
  console.log('  ', log.progress.join(' → '));
  console.log('=== 协议往返 ===');
  for (const line of log.host) console.log('  ', line);
  console.log('=== 存档次数 ===');
  console.log('  ', saveAttempts.map((item) => `tab${item.tabId}`).join(' '),
    `（共 ${saveAttempts.length} 次；3 篇正常应 3 次，多出 1 次说明 tab1 的重存生效了）`);
  console.log('=== 宿主收到的文件 ===');
  for (const item of log.uploads) {
    const statePart = item.state ? `  state ${JSON.stringify(item.state).length} 字符` : '  无 state';
    console.log(`   ${item.kind} ${item.noteId}  ${item.bytes.length} B  分 ${item.chunks} 块${statePart}`);
  }
  console.log('=== 结果 ===');
  console.log('  ', JSON.stringify({
    saved: result.saved, failed: result.failed, failures: result.failures,
    partialImages: result.partialImages, needReload: result.needReload,
    readMethod: result.readMethod,
  }, null, 2));
  if (log.errors.length) console.log('=== 控制台错误 ===', log.errors);

  const problems = [];
  // 3 篇可采：主页 1（tab4）+ 笔记 2（tab1 重存 1 次、tab2 1 次）。
  if (result.saved !== 3) {
    problems.push(`应入库 3 篇（1 主页 + 2 笔记），实际 ${result.saved}（失败 ${result.failed}：${result.failures}）`);
  }
  if (result.readMethod !== 'FileReader') {
    problems.push(`文件-backed blob 应该靠 FileReader 读回来，实际用的是 ${result.readMethod}`);
  }
  if (!log.host.some((line) => line.includes('拒绝') === false && line.includes('chunk'))) {
    problems.push('没有任何 chunk 往返——分块逻辑没被执行');
  }
  const chunks = log.uploads[0] ? log.uploads[0].chunks : 0;
  if (chunks < 2) problems.push(`只分了 ${chunks} 块（上限 ${MAX_MESSAGE_BYTES}）`);
  if (log.uploads.some((item) => item.bytes.length !== 60 * 1024)) {
    problems.push('上传字节数与原始存档不一致');
  }
  if (result.partialImages !== 1) {
    problems.push(`应有 1 篇报「有图没加载出来」，实际 ${result.partialImages}`);
  }
  if (result.needReload !== 1) {
    problems.push(`应跳过 1 个休眠标签，实际 ${result.needReload}`);
  }
  // v8：主页必须带上 state。`pageCapture` 把 `<script>` 剥了，
  // 没有这份 state 的主页存档**解析出来是个空账号**——而它照样会显示「已入库 1 篇」。
  const profileUpload = log.uploads.find((item) => item.kind === 'profile');
  if (!profileUpload) {
    problems.push('主页没有被上传——主页采集功能没生效');
  } else if (!profileUpload.state || !profileUpload.state.user) {
    problems.push('主页上传时没带 state：那篇主页会入库成一个没有粉丝/简介的空账号');
  }
  // 关键：tab1 的第一次存档读不出来，**必须重新存一次**才成。
  // 只重读同一个句柄会一直失败——坏的是那个句柄，不是那一次调用。
  if (saveAttempts.length !== 4) {
    problems.push(`存档次数应为 4（3 篇 + tab1 重存一次），实际 ${saveAttempts.length}`
      + ' —— 「读失败就重存一次」这条修复没生效');
  }
  // 失败必须带原因：v8 之前宿主只回 `ok:false`，扩展只能显示「宿主没回结果」，
  // 而真实原因（拒收 / 找不到底行 / 存档坏）躺在 log 里，面板默认不展开它。
  if (result.failures.some((text) => text.includes('宿主没回结果'))) {
    problems.push('失败信息里还出现「宿主没回结果」——那是宿主没给原因时的假话兜底');
  }

  // ── 场景 B：在 **service worker** 里跑同一段代码。
  //    预期：明确失败，而且错误里点名「没有 FileReader」——
  //    这条断言守住的是**架构决定**：采集属于面板，不属于 background。
  const worker = makeSandbox('service-worker');
  const workerResult = await worker.AR.captureAll({});
  console.log();
  console.log('=== 在 service worker 里跑（应当失败） ===');
  console.log('  saved=', workerResult.saved, ' failures=', workerResult.failures);
  if (workerResult.saved !== 0) {
    problems.push('在 service worker 里竟然成功了 —— 那说明「文件-backed blob」没被真的仿真');
  }
  if (!/FileReader|读不到存档字节/.test((workerResult.failures || []).join(' '))) {
    problems.push('service worker 里的失败没有说清原因');
  }

  console.log();
  if (problems.length) {
    for (const item of problems) console.log('❌', item);
    process.exit(1);
  }
  console.log('✅ 完整流程通过');
  process.exit(0);
})();
