# HTML 输出模板（git 式领域占比条 + 账号库页面 + 数据图）

> **用途**：account-research Step 8 RENDER 的 HTML 版输出骨架。v0.1 定盘「可视化不降级」——markdown 报告与 HTML 同源生成，缺一不可。
> **复用方式**：复制 §6 的组装骨架，替换 `{{占位符}}`；所有数字必须来自 markdown 报告的数据底稿，**禁止手填**。
> **视觉基调**：暖白纸张底 / 松绿锚点 / 陶土强调；浅色主题（背景一律浅色、文字一律深色）。
> **纪律总源**：SKILL.md 红线 R1–R6 + 本文件 §2 DOM 工程卫生清单。交付前两套自检全过才算完成。

---

## 1. 设计 token（复制到 `<style>` 顶部）

```css
:root {
  /* 纸面与墨色（浅色主题，禁止深色底） */
  --paper:      #FAF7F1;  /* 页面底：暖白纸张 */
  --surface:    #FFFEFA;  /* 卡片面 */
  --surface-2:  #F2EDE2;  /* 次级面（表头 / 标签底） */
  --ink:        #2B2925;  /* 主文字 */
  --ink-2:      #6E6A61;  /* 次文字 */
  --ink-3:      #9A948A;  /* 辅助文字 / 未激活 */
  --line:       #E5DDCE;  /* 发丝线 */

  /* 锚点与强调 */
  --pine:       #3F6B4F;  /* 松绿：锚点、正常态、结构 */
  --pine-deep:  #2C4E39;
  --pine-soft:  #DCE7DC;
  --terracotta: #C05B3C;  /* 陶土：强调、爆款、警示 */
  --terracotta-deep: #A34A30;
  --terracotta-soft: #F3E0D6;

  /* 占比条分段色板（固定轮换顺序：同一母题在每次重新生成的页面里颜色一致） */
  --seg-1: #3F6B4F;  /* 松绿 */
  --seg-2: #C05B3C;  /* 陶土 */
  --seg-3: #7FA183;  /* 松绿浅 */
  --seg-4: #D99B82;  /* 陶土浅 */
  --seg-5: #7A7440;  /* 橄榄 */
  --seg-6: #5B7B8C;  /* 灰蓝 */
  --seg-na: #C9C2B4; /* 未归类 / 未取证段（灰） */

  /* 间距刻度（唯一合法来源，禁止魔法数字） */
  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px;
  --sp-4: 16px; --sp-5: 24px; --sp-6: 32px;

  /* 字体（系统栈，禁止外链字体——离线可开是零漂移的前提） */
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
}

body { background: var(--paper); color: var(--ink); font-family: var(--font);
       font-variant-numeric: tabular-nums; margin: 0; }
.num { font-variant-numeric: tabular-nums; }
```

> **环比增减上色规则**（如需显示）：遵循中文市场惯例——**增长 = 陶土红 `--terracotta`，下降 = 松绿 `--pine`**。账号数据不是股票，此规则仅为保持与用户其他金融看板一致；不显示环比时可完全不使用这两个语义色。

---

## 2. DOM 工程卫生清单（违反任何一条 = 交付未完成）

| # | 规则 | 落地做法 |
|---|---|---|
| 1 | **零漂移** | 无外链字体 / 脚本 / 图片（全部内联）；数字一律 `tabular-nums`；标题两行截断用 `-webkit-line-clamp` 而非撑高容器；占比条分段宽度用百分比，容器定高，重绘不抖动 |
| 2 | **零缺失图标** | 图标一律手写内联 SVG（简单 path，`fill="currentColor"`）；禁止图标字体 / CDN 图标库；装饰性图标 `aria-hidden="true"`，语义性图标 `role="img"` + `aria-label` |
| 3 | **零多余间距** | 间距只取 `--sp-*` 刻度；布局用 `gap`，不用 `margin` 层层叠加；同一区块内不允许出现两个相邻同源间距元素 |
| 4 | **单一事实源** | HTML 数字与 markdown 报告**同源生成**；生成后抽查 ≥5 个数字对得上；对不上 = 回炉 |
| 5 | **无外部依赖** | 不引 CDN / 字体 / 图表库；图表用手写 inline SVG；文件可离线双击打开 |
| 6 | **打印友好** | `@media print { -webkit-print-color-adjust: exact; print-color-adjust: exact; }`，占比条与徽章底色不丢 |
| 7 | **响应式** | 单栏优先；账号库卡片网格 `repeat(auto-fill, minmax(280px, 1fr))`；窄屏占比条图例自动换行 |

---

## 3. 区块 A：git 式领域占比条

**数据契约**（来自报告第 3 节选题母题表）：

```json
{ "segments": [
    { "label": "选题母题名", "pct": 42, "count": 21, "color": "var(--seg-1)" },
    { "label": "未归类",     "pct": 6,  "count": 3,  "color": "var(--seg-na)" }
] }
```

**规则**：`pct` 合计必须 = 100；母题 >6 个时合并尾部为「其他」并在图例注明；未归类 / 未取证段必须用灰色 `--seg-na` 且不得合并隐藏。

```html
<section class="bar-block">
  <h3>领域占比 <span class="hint">选题母题 · 样本 N 篇 · 截至 {{DATA_CUTOFF}}</span></h3>
  <div class="stackbar" role="img" aria-label="领域占比条：{{各段 label+pct 的文本拼接}}">
    <span class="seg" style="width:{{PCT}}%;background:{{COLOR}}" title="{{LABEL}} {{PCT}}%（{{COUNT}} 篇）"></span>
    <!-- 每个 segment 一行；宽度即 pct -->
  </div>
  <ul class="legend">
    <li><i style="background:var(--seg-1)"></i><span class="legend-name">{{LABEL}}</span><span class="num">{{PCT}}%</span><span class="legend-count num">{{COUNT}} 篇</span></li>
    <!-- 每个 segment 一行；顺序与占比条一致 -->
  </ul>
</section>
<style>
.bar-block { background: var(--surface); border: 1px solid var(--line);
             border-radius: var(--sp-2); padding: var(--sp-4); }
.bar-block h3 { margin: 0 0 var(--sp-3); font-size: 15px; }
.bar-block .hint { color: var(--ink-3); font-size: 12px; font-weight: normal; margin-left: var(--sp-2); }
.stackbar { display: flex; height: 12px; border-radius: 6px; overflow: hidden;
            background: var(--surface-2); }
.stackbar .seg { height: 100%; }              /* 宽度由 inline style 给定，零漂移 */
.stackbar .seg:first-child { border-radius: 6px 0 0 6px; }
.stackbar .seg:last-child  { border-radius: 0 6px 6px 0; }
.legend { list-style: none; margin: var(--sp-3) 0 0; padding: 0;
          display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
          gap: var(--sp-2) var(--sp-4); }
.legend li { display: flex; align-items: center; gap: var(--sp-2); font-size: 13px; }
.legend i { width: 10px; height: 10px; border-radius: 3px; flex: none; }
.legend-name { color: var(--ink); }
.legend .num { margin-left: auto; color: var(--ink-2); }
.legend-count { color: var(--ink-3); font-size: 12px; min-width: 44px; text-align: right; }
</style>
```

---

## 4. 区块 B：账号库页面

**台账 schema**（持久化文件 `账号库台账.md`，markdown 表格，**只追加不改写**）：

```markdown
| 账号名 | 平台 | 领域标签 | 粉丝区间（观察值+日期） | 母题占比摘要 | 最近更新 | 对标状态 | 证据等级 | 来源链接+查证日期 | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| 示例账号A | 小红书 | 职场干货 | 8.2万（2026-10-16 公开页） | 简历40%/面试35%/职场25% | 2026-10-14 | 🔧 部分对标 | ② | <url>（2026-10-16） | 判 🔧：商单依赖团队 |
```

**台账纪律**：粉丝等变动值记「观察值 + 查证日期」而非当作恒定事实；状态变更（如 🔧 → ✅）**追加新行并在备注注明「替代 YYYY-MM-DD 行」**，不改旧行——账号库的可信度依赖可追溯历史。

**页面结构**：统计条（账号总数 / 已拆解 / 观察区）→ 领域筛选 chips（纯 CSS `:checked` + `display` 亦可，最多一段内联 vanilla JS，禁用外部库）→ 账号卡片网格。

```html
<section class="library">
  <div class="lib-head">
    <h3>账号库 <span class="hint">共 {{ACCOUNT_TOTAL}} 个 · 已拆解 {{DISSECTED}} · 观察区 {{WATCHING}}</span></h3>
    <!-- 筛选 chips：每领域一个 checkbox + label；选中态用 :checked + 兄弟选择器控制 .card[data-domain] 显隐 -->
  </div>
  <div class="cards">
    <article class="card" data-domain="{{DOMAIN_TAG}}">
      <header>
        <span class="acc-name">{{ACCOUNT_NAME}}</span>
        <span class="badge badge-xhs">小红书</span>   <!-- 平台徽标：文字徽标，不用图标 -->
      </header>
      <div class="tags">
        <span class="tag">{{DOMAIN_TAG}}</span>       <!-- 领域标签：松绿浅底 -->
      </div>
      <dl class="meta">
        <div><dt>粉丝</dt><dd class="num">{{FANS}}（{{CHECK_DATE}}）</dd></div>
        <div><dt>最近更新</dt><dd class="num">{{LAST_UPDATE}}</dd></div>
        <div><dt>对标状态</dt><dd><span class="status status-{{STATUS_KEY}}">{{STATUS_LABEL}}</span></dd></div>
        <div><dt>证据等级</dt><dd>{{EVIDENCE_DOT}}</dd></div>
      </dl>
      <footer><a href="{{SOURCE_URL}}">来源 · {{CHECK_DATE}}</a></footer>
    </article>
    <!-- 每个台账当前有效行一张卡片 -->
  </div>
</section>
<style>
.library h3 { font-size: 15px; margin: 0 0 var(--sp-3); }
.library .hint { color: var(--ink-3); font-size: 12px; font-weight: normal; margin-left: var(--sp-2); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
         gap: var(--sp-4); }
.card { background: var(--surface); border: 1px solid var(--line);
        border-radius: var(--sp-2); padding: var(--sp-4);
        display: flex; flex-direction: column; gap: var(--sp-3); }
.card header { display: flex; align-items: center; gap: var(--sp-2); }
.acc-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; }                    /* 名字超长省略，不撑卡 */
.badge-xhs { font-size: 11px; color: var(--terracotta-deep);
             background: var(--terracotta-soft); border-radius: var(--sp-1);
             padding: 0 var(--sp-2); flex: none; }
.tag { font-size: 12px; color: var(--pine-deep); background: var(--pine-soft);
       border-radius: var(--sp-1); padding: 0 var(--sp-2); }
.meta { margin: 0; display: grid; gap: var(--sp-2); font-size: 13px; }
.meta > div { display: flex; gap: var(--sp-2); }
.meta dt { color: var(--ink-3); width: 64px; flex: none; }
.meta dd { margin: 0; color: var(--ink-2); }
.status { font-size: 12px; border-radius: var(--sp-1); padding: 0 var(--sp-2); }
.status-ok   { color: #FFF; background: var(--pine); }              /* ✅ 直接对标 */
.status-part { color: var(--pine-deep); background: var(--pine-soft); } /* 🔧 部分对标 */
.status-test { color: var(--terracotta-deep); background: var(--terracotta-soft); } /* 🧪 先验证 */
.status-watch{ color: var(--ink-2); background: var(--surface-2); }  /* ⏸ 只观察 */
.status-drop { color: var(--ink-3); background: var(--surface-2);
               text-decoration: line-through; }                    /* ❌ 换对标 */
.card footer a { color: var(--pine); font-size: 12px; text-decoration: none; }
</style>
```

> 状态徽章五色与判决集合一一对应；**状态语义色不是涨跌语义**——✅/🔧 用松绿系（「可抄」），🧪 用陶土系（「待验」），⏸/❌ 用灰系（「不可证 / 停手」）。

---

## 5. 区块 C：数据图（手写 inline SVG）

### C1 逐篇互动量条形图（数据源 = 报告 1.2 数据底稿）

```html
<svg viewBox="0 0 680 220" preserveAspectRatio="xMidYMid meet" role="img"
     aria-label="逐篇互动量条形图" style="width:100%;height:auto">
  <!-- 网格线（先画，压在柱下） -->
  <line x1="40" y1="180" x2="660" y2="180" stroke="var(--line)" />
  <line x1="40" y1="120" x2="660" y2="120" stroke="var(--line)" stroke-dasharray="2 4" />
  <line x1="40" y1="60"  x2="660" y2="60"  stroke="var(--line)" stroke-dasharray="2 4" />
  <!-- 每根柱：x 等分；高 = 值/最大值 × 120；fill 按状态 -->
  <rect x="48"  y="{{Y}}" width="18" height="{{H}}" fill="var(--pine)" rx="2" />
  <rect x="80"  y="{{Y}}" width="18" height="{{H}}" fill="var(--terracotta)" rx="2" /> <!-- 爆款：陶土 -->
  <!-- 轴标签 -->
  <text x="40" y="196" font-size="10" fill="var(--ink-3)">{{日期}}</text>
  <text x="40" y="180" font-size="10" fill="var(--ink-3)" text-anchor="end">{{刻度值}}</text>
  <!-- 图例：松绿=常规 陶土=爆款 深墨=近90天 -->
</svg>
```

**用色规则**：常规柱 = `--pine`；样本内数据 Top 1（爆款锚点）= `--terracotta`；近 90 天发布的柱 = `--pine-deep`（若与爆款同一根，陶土优先）。柱数 >20 时聚合为周分组，图上注明聚合口径。

### C2 母题占比横条（区块 A 的卡片化形态，账号库内复用）

```html
<ul class="hbars">
  <li><span class="hb-label">{{LABEL}}</span>
      <span class="hb-track"><i style="width:{{PCT}}%;background:{{COLOR}}"></i></span>
      <span class="num hb-val">{{PCT}}%</span></li>
</ul>
<style>
.hbars { list-style:none; margin:0; padding:0; display:grid; gap:var(--sp-2); }
.hbars li { display:flex; align-items:center; gap:var(--sp-3); font-size:13px; }
.hb-label { width: 96px; flex:none; color:var(--ink-2);
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.hb-track { flex:1; height:8px; background:var(--surface-2); border-radius:4px; overflow:hidden; }
.hb-track i { display:block; height:100%; border-radius:4px; }
.hb-val { width: 44px; text-align:right; color:var(--ink-2); }
</style>
```

**SVG 卫生**：只用 `viewBox + preserveAspectRatio` 缩放；文字 `fill` 全部走 token；不用 `foreignObject`；每张图有 `role="img"` + `aria-label` 文字摘要（读屏用户拿到的是结论不是点位）。

---

## 6. 组装骨架（单文件，复制即用）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{REPORT_TITLE}}</title>
<style>
/* §1 token 原样粘贴 */
/* 以下为页面级骨架 */
.page { max-width: 960px; margin: 0 auto; padding: var(--sp-5) var(--sp-4);
        display: grid; gap: var(--sp-5); }
.page-head h1 { font-size: 20px; margin: 0 0 var(--sp-2); }
.page-head .sub { color: var(--ink-2); font-size: 13px; }
.verdict { background: var(--surface); border: 1px solid var(--line);
           border-left: 4px solid var(--pine); border-radius: var(--sp-2);
           padding: var(--sp-4); }
.verdict .v-label { font-size: 16px; font-weight: 600; }
.verdict dl { margin: var(--sp-3) 0 0; display: grid; gap: var(--sp-2); font-size: 13px; }
.verdict dt { color: var(--ink-3); }
.footnote { color: var(--ink-3); font-size: 12px; border-top: 1px solid var(--line);
            padding-top: var(--sp-3); }
@media print { * { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
</style>
</head>
<body>
<main class="page">
  <header class="page-head">
    <h1>{{REPORT_TITLE}}</h1>
    <p class="sub">{{ACCOUNT_NAME}} · 小红书 · 数据截至 {{DATA_CUTOFF}} · 生成于 {{GENERATED_AT}}</p>
  </header>

  <section class="verdict">   <!-- 判决卡：三件套 -->
    <div class="v-label">{{VERDICT_EMOJI}} {{VERDICT_LABEL}}</div>
    <dl>
      <div><dt>最值钱的一步</dt><dd>{{BEST_STEP}}</dd></div>
      <div><dt>最便宜的验证</dt><dd>{{CHEAPEST_TEST}}</dd></div>
      <div><dt>放弃信号</dt><dd>{{KILL_SIGNAL}}</dd></div>
    </dl>
  </section>

  <!-- §3 区块 A：git 式领域占比条 -->
  <!-- §5-C2 母题占比横条（可选，与占比条二选一或并列） -->
  <!-- §5-C1 逐篇互动量条形图 -->
  <!-- §4 区块 B：账号库页面（含本账号卡） -->

  <p class="footnote">证据分区：检索到的事实 vs 我的推理 · ⬜ 未取证 {{NA_COUNT}} 项 ·
    数字与《{{REPORT_TITLE}}.md》同源生成 · 查证日期见台账</p>
</main>
</body>
</html>
```

---

## 7. 交付前自检（HTML 版）

- [ ] token 与本文件 §1 一致（未自创颜色；浅色底深色字）
- [ ] 过 §2 DOM 工程卫生七条（逐条勾）
- [ ] 占比条 `pct` 合计 = 100；未归类 / 未取证段用 `--seg-na` 且可见
- [ ] 所有数字与 markdown 报告一致（抽查 ≥5 个）
- [ ] 账号库卡片与台账当前有效行一一对应；无手写链接缺失
- [ ] SVG 均有 `role="img"` + `aria-label`；无外部资源请求（断网可开）
- [ ] 五状态徽章与判决集合一一对应，未发明第六种状态
- [ ] 粉丝等变动值带查证日期，未写成恒定事实
- [ ] 台账按「只追加」更新，历史行未被改写
