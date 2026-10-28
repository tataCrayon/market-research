# extension/ —— 详情页采集扩展（Chrome / Edge，Manifest V3）

> **它解决的问题**：详情页 `Ctrl+A` 复制要手工剔背景文字、`javascript:` 书签被 Chrome 拦。
> 换成扩展后：**打开笔记页 → 点一下图标 → txt 自动下载**。

## 装法（开发者模式加载，10 秒）

1. Chrome / Edge 地址栏输入 `chrome://extensions`（Edge 是 `edge://extensions`）
2. 打开右上角 **开发者模式**
3. 点 **加载已解压的扩展程序** → 选这个 `extension/` 目录
4. 工具栏出现 **AR 采集** 图标（若被折叠，点拼图图标固定它）

## 用法

1. **整页打开**一篇笔记（`xiaohongshu.com/explore/<id>`），等页面加载完
   - ⚠️ 不要从主页点开弹层——那样 `og:url` 还是主页，存下来是主页
2. 点一下工具栏的 **AR 采集** 图标
3. 自动下载 `ar-note-<note_id>.txt`
4. 攒几篇一起丢进 `account/inbox/` → `python account-research/ar.py ingest`

## 边界（这不是建议，是设计约束）

| 它做 | 它不做 |
|---|---|
| 读当前页**已渲染**的文字 | ❌ 不发任何网络请求（无 fetch / XHR / WebSocket） |
| 用 `data:` URL 触发本地下载 | ❌ 不翻页、不滚动加载、不批量 |
| 只读用户**主动点开**的这一页 | ❌ 不读 Cookie / localStorage / 会话 |
| —— | ❌ 不模拟平台控件、不接管浏览器会话 |

**权限只有三个**：`activeTab`（你点图标时才拿到当前页）、`scripting`（注入只读脚本）、`downloads`（存文件）。
**没有** `<all_urls>`、**没有** `cookies`、**没有** `webRequest`——权限最小化的意思就是"想加也没加"。

## 字段

自动拿到：`note_id`、标题、正文首 40 字、正文长度、时长（视频）、话题标签（随正文）。

**不自动填**：赞 / 藏 / 评——弹窗（图标悬停 title）会显示**互动栏原文**，照它填。
理由：`like-wrapper` 这类选择器平台一改就失效，自动填会变成**静默错数据**，
比多一次人工确认贵得多。

## 与书签版的关系

`tools/capture-bookmarklet.js` 是同一套逻辑的书签版。两者的差别：

| | 书签 | 扩展 |
|---|---|---|
| 安装 | 需手工粘贴 `javascript:` | 开发者模式加载目录 |
| Chrome 是否拦截 | 部分版本会拦 | 不会 |
| 是否常驻 | 书签栏 | 工具栏图标 |

**逻辑改动请同时改 `tools/capture-bookmarklet.js` 与 `extension/background.js`**，
`tests/test_bookmarklet_redlines.py` 会检查书签那份的红线。
