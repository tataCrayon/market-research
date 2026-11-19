// 把 `AR.probePage` 放进一个**假 DOM** 里真跑一遍。
//
// ## 为什么必须单独跑它
//
// `extension_click.js` 把 `chrome.scripting.executeScript` 打桩了——于是 `probePage`
// 自己**一行都没被执行过**。而 v8 的全部新逻辑都在它里面：
//   · 主页与笔记页的区分
//   · `__INITIAL_STATE__` 的摘取（哪几棵子树、超限怎么办、某棵序列化失败怎么办）
//   · 弹层与整页的判据
// 桩掉它等于把「这次改动最可能出错的地方」排除在测试之外。
// 这正是「桩的保真度决定你能测出什么」那条教训的第二次应用。
//
// ## 假 DOM 的原则
//
// 只实现 `probePage` 真正用到的那几样：`location.href` / `document.title` /
// `document.querySelector` / `document.querySelectorAll` / `window.__INITIAL_STATE__`。
// **不引 jsdom**：多一个依赖就多一处「桩和真浏览器不一样」的未知，
// 而这里要验的是**我们自己写的取值逻辑**，不是选择器在该 DOM 实现下的行为。

const fs = require('fs');
const vm = require('vm');

const captureSrc = fs.readFileSync(process.argv[2], 'utf8');

function textNodes(selector) {
  // 只支持 `probePage` 用到的两种：meta[property="og:url"] 与 [data-swiper-slide-index] img
  return [];
}

function makePage({
  href = 'https://www.xiaohongshu.com/explore/6ac48e16000000001203ed6e',
  title = '某笔记 - 小红书',
  ogUrl = href,
  slides = [],
  cards = 0,
  state = undefined,
  stateThrows = false,
  // DOM 里的 `.user-name` 文本。`null` = 头部还没渲染。
  domNickname = null,
} = {}) {
  const document = {
    title,
    querySelector(selector) {
      if (selector === 'meta[property="og:url"]') {
        return ogUrl === null ? null : { getAttribute: () => ogUrl };
      }
      // v8.1：主页的判据是 **DOM 里有没有 `.user-name`**，不再看 state。
      // 桩必须能给出「有头部」与「没头部」两种页面，否则真机上那次
      // 「明明加载好了却报没渲染」的 bug 永远测不出来。
      if (selector === '.user-name') {
        return domNickname === null ? null : { textContent: domNickname };
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-swiper-slide-index] img') return slides;
      if (selector === 'section.note-item[data-note-id]') return new Array(cards).fill({});
      return [];
    },
  };
  const window = {};
  if (state !== undefined) {
    if (stateThrows) {
      Object.defineProperty(window, '__INITIAL_STATE__', {
        get() { throw new Error('页面脚本被拦了'); },
        configurable: true,
      });
    } else {
      window.__INITIAL_STATE__ = state;
    }
  }
  return {
    document,
    window,
    location: { href },
    JSON, Math, Object, Array, String, Boolean, Error,
  };
}

function runPage(env) {
  const sandbox = { ...env };
  // `probePage` 内部用的 `window` / `document` / `location` 是页面全局，
  // 沙箱里把它们放在顶层，等价于页面上下文。
  sandbox.window = env.window || sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(captureSrc, sandbox);
  return sandbox.AR.probePage();
}

const problems = [];
function expect(condition, message) {
  if (!condition) problems.push(message);
}

// ── 场景 1：主页，DOM 与 state 都在。必须摘出 `user` 并报 hasState。
const profileState = {
  user: {
    userPageData: { basicInfo: { nickname: '测试号' }, interactions: [] },
    noteQueries: [{ userId: 'acc-1' }],
    notes: [[{ noteCard: { noteId: 'n1' } }]],
  },
  // 这一棵**不在白名单里**：主页把它带上只是白占带宽。
  extra: { 不该出现: 'x'.repeat(500) },
};
const profile = runPage(makePage({
  href: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075',
  title: '测试号 - 小红书',
  ogUrl: null,
  cards: 32,
  domNickname: '测试号',
  state: profileState,
}));
console.log('主页：', JSON.stringify({
  kind: profile.kind, isFullPage: profile.isFullPage, hasState: profile.hasState,
  domCards: profile.domCards, noteId: profile.noteId,
  stateKeys: profile.state ? Object.keys(profile.state) : null,
}));
expect(profile.kind === 'profile', `主页被判成了 ${profile.kind}`);
expect(profile.isFullPage === true, '主页应当可采（头部已渲染）');
expect(profile.hasState === true, '主页必须报 hasState');
expect(profile.noteId === '62c98736000000001501e075', `主页 id 错了：${profile.noteId}`);
expect(profile.domCards === 32, `卡片数错了：${profile.domCards}`);
expect(profile.state && profile.state.user, '主页没把 user 子树摘出来');
// 白名单是 `user` / `note` / `feed` / `search` 四棵。**不在这四棵里的一律不带**——
// 整个 state 里混着登录态与采集者会话字段，传得越少越好。
expect(profile.state && profile.state.extra === undefined, '带上了不在白名单里的子树');

// ── 场景 1b：**真机那次翻车**。主页已经渲染好（昵称与卡片都在 DOM 里），
//    但 `__INITIAL_STATE__.user.userPageData` 是空的——站点是 CSR，首屏 state 没数据。
//    v8.0 用「state 里有昵称」当门槛，于是这个页面被判「还没渲染」，
//    用户在屏幕前看着完整的页面却采不了。
const csrProfile = runPage(makePage({
  href: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075',
  title: '测试号 - 小红书',
  ogUrl: null,
  cards: 32,
  domNickname: '测试号',
  state: { user: { userPageData: {}, activeTab: {} } },
}));
console.log('CSR 主页（state 空、DOM 齐）：', JSON.stringify({
  isFullPage: csrProfile.isFullPage, hasState: csrProfile.hasState, nickname: csrProfile.nickname,
}));
expect(csrProfile.isFullPage === true,
  'DOM 里有头部就必须可采——state 空不等于页面没渲染（2026-11-19 真机 bug）');
expect(csrProfile.hasState === false, 'state 里没有昵称时要如实报 hasState=false（发布日会缺）');
expect(csrProfile.nickname === '测试号', '没把 DOM 里的昵称带出来，面板无法显示「在采哪个号」');

// ── 场景 1c：主页但**真的**还没渲染（DOM 与 state 都没有昵称）→ 明说原因
const blankProfile = runPage(makePage({
  href: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075',
  ogUrl: null,
  cards: 0,
  domNickname: null,
  state: { user: { userPageData: {} } },
}));
console.log('未渲染主页：', JSON.stringify({
  isFullPage: blankProfile.isFullPage, reason: blankProfile.reason,
}));
expect(blankProfile.isFullPage === false, '未渲染的主页不该被当成可采');
expect(blankProfile.reason === 'profile_not_rendered', `原因没写清：${blankProfile.reason}`);

// ── 场景 3：整页打开的笔记。要报 hasState，且带上 note 子树（分享数/发布日靠它）
const detailState = {
  note: { noteDetailMap: { '6ac48e16000000001203ed6e': { note: { noteId: 'x', shareCount: '77' } } } },
  user: { userInfo: { userId: 'viewer' } },
};
const detail = runPage(makePage({
  state: detailState,
  slides: [{ complete: true, naturalWidth: 800 }, { complete: false, naturalWidth: 0 }],
}));
console.log('笔记：', JSON.stringify({
  kind: detail.kind, isFullPage: detail.isFullPage, hasState: detail.hasState,
  loaded: detail.loadedImages, total: detail.totalImages,
}));
expect(detail.kind === 'note', `笔记被判成了 ${detail.kind}`);
expect(detail.isFullPage === true, '整页笔记应当可采');
expect(detail.hasState === true, '有 state 的笔记必须报 hasState');
expect(detail.loadedImages === 1 && detail.totalImages === 2,
  `图片计数错了：${detail.loadedImages}/${detail.totalImages}`);
expect(detail.state && detail.state.note, '笔记没把 note 子树摘出来（分享数会永远 ⬜）');

// ── 场景 4：从主页点开的弹层（og:url 仍指主页）→ 必须拒，且原因可区分
const modal = runPage(makePage({
  href: 'https://www.xiaohongshu.com/explore/6ac48e16000000001203ed6e',
  ogUrl: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075',
}));
console.log('弹层：', JSON.stringify({ isFullPage: modal.isFullPage, reason: modal.reason }));
expect(modal.isFullPage === false, '弹层不该被当成整页笔记');
expect(modal.reason === 'modal', `弹层原因没写清：${modal.reason}`);

// ── 场景 5：state 太大 → 报 tooLarge，**不是**静默截断也不是崩掉
const huge = runPage(makePage({
  href: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075',
  ogUrl: null,
  state: { user: { userPageData: { basicInfo: { nickname: '大号' } }, filler: 'x'.repeat(4 * 1024 * 1024) } },
}));
console.log('超大 state：', JSON.stringify({
  tooLarge: huge.state && huge.state.tooLarge, chars: huge.state && huge.state.chars,
}));
expect(huge.state && huge.state.tooLarge === true, '超过上限时没有报 tooLarge');
expect(huge.state && huge.state.chars > 4 * 1024 * 1024, '没报出实际大小，人无法判断差多少');

// ── 场景 6：某棵子树不可序列化 → **只丢那一棵**，其余照常带上去
const cyclic = { user: { userPageData: { basicInfo: { nickname: '有环' } } } };
cyclic.user.self = cyclic.user;
const withCycle = runPage(makePage({
  href: 'https://www.xiaohongshu.com/user/profile/62c98736000000001501e075',
  ogUrl: null,
  state: cyclic,
}));
console.log('不可序列化：', JSON.stringify({
  hasUser: Boolean(withCycle.state && withCycle.state.user),
  isFullPage: withCycle.isFullPage,
}));
expect(withCycle.state && withCycle.state.user === null,
  '不可序列化的子树应当变成 null（留痕），而不是整份放弃');
expect(withCycle.isFullPage === true, '一棵子树坏了不该让整页不可采');

// ── 场景 7：不是小红书页面 → 明确拒
const foreign = runPage(makePage({
  href: 'https://www.xiaohongshu.com/search_result?keyword=abc',
  title: '搜索 - 小红书',
  ogUrl: null,
}));
console.log('搜索页：', JSON.stringify({ isFullPage: foreign.isFullPage, reason: foreign.reason, kind: foreign.kind }));
expect(foreign.isFullPage === false, '搜索页不该被当成可采（v8 还没做它）');

console.log();
if (problems.length) {
  for (const item of problems) console.log('❌', item);
  process.exit(1);
}
console.log('✅ probePage 的八个场景全部符合预期');
process.exit(0);