# 过滤条件与人员属性命中矩阵（Candidate Matching）

> **用途**：`career-research` Step 4 FILTER（命中矩阵）、Step 5 AUDIT（差距审计）的深度资料 + JSON schema。
> **首要红线**：本文件处理的「人员属性」是**画像属性**，不是自然人档案。见第 0 节。

---

## 0. 隐私红线（先于一切）

### 允许的用途
- **个人职业规划**：本人自愿提供自己的履历，评估与岗位的差距
- **团队编制规划 / 岗位设计**：用**虚构或聚合画像**推演岗位要求与编制结构
- **示例与教学**：明示的虚构画像

### 禁止的用途（遇即停止执行）
- 对**真实第三方自然人**建档、评分、排序、筛查、画像归档
- 收集 / 存储 / 输出第三方真实个人的：姓名、联系方式、证件号、社交账号、雇主可识别信息、简历原文、可回溯到个人的项目细节
- 用本 Skill 的输出替代简历筛选系统对真实候选人做决策

### 为什么这条是本 Skill 的首要红线
「按年限 / 城市 / 学历 / 履历筛选人员」这个需求，往前一步就是**对真实人群做画像打分**。两者技术实现几乎相同，但性质完全不同：

| | 岗位侧匹配（允许） | 自然人侧评分（禁止） |
|---|---|---|
| 输入 | 岗位要求 × 本人或虚构画像 | 第三方真实个人数据 |
| 输出 | 「这个岗要求 3 年相关经验，画像值 0 年 → ❌」 | 「张三 62 分，排名 7/30」 |
| 性质 | 职业决策支持 | 个人信息处理 / 就业歧视风险 |

**强制性写法**：输出的「命中的人员属性」必须落在 `matched_attributes` 结构里，**字段名只含条件维度（years / city / education / experience），不含任何自然人标识**。

> 示例报告中的画像必须显式标注：**「示例数据，非真实自然人」**。

---

## 1. 四类过滤条件

| 条件 | 参数 | 取证要点 | 最容易出错的地方 |
|---|---|---|---|
| **工作年限** | `years_of_experience.{min,max,band,relevant_only}` | JD 原文是「3 年工作经验」还是「3 年**相关**工作经验」 | **「相关」二字决定转岗者是否出局**。`relevant_only` 默认 true |
| **城市** | `city.{include,relocate_ok}` | 逐岗取证实际工作地 | 默认「一线城市都有」。实测 AI 应用开发岗分布在北上广深 + 无锡/重庆/安庆/昆明/西安，**杭州是否密集属于未取证** |
| **学历** | `education.{level,major_restricted,is_hard}` | 法定门槛（国家职业标准「普通受教育程度」）+ 市场门槛（JD）分记 | 默认「本科够用」/「能力强可放宽」。校招与国企 JD 的学历常是硬门槛 |
| **履历背景** | `candidate_profile.{current_role,domain_history,project_experience,skills,credentials}` | 逐条对账任职要求三级拆分 | 把「做过开发」直接算成「可迁移」，拿不出证据件 |

### 年限的三个口径（必须分开记）

1. `years_total` —— 总工龄
2. `years_relevant` —— **相关岗位**年限（JD 最常卡的那一档）
3. `years_in_direction` —— 在本方向内的年限

命中矩阵的「工作年限」一行，要明确写清用的是哪个口径：

```
| 工作年限 | 3 年及以上【相关】工作经验 | 总工龄 5 年 / 相关 0 年 | ⚠️ 部分命中 | JD 原文：… | relevant_only=true |
```

---

## 2. 命中矩阵规范

**四值判定**（不许出现第五种，不许留空）：

| 值 | 含义 |
|---|---|
| ✅ **命中** | 画像值满足要求，且有来源证明要求值 |
| ⚠️ **部分命中** | 满足一部分（如总年限够但相关年限不够；学历够但部分岗位要硕士） |
| ❌ **未命中** | 明确不满足（硬门槛） |
| ⬜ **未取证** | 要求值或画像值无法取证。**计入差距清单，不得当命中处理** |

**矩阵行（每岗一张）**：

| 过滤条件 | 岗位要求（取证） | 画像值 | 命中 | 证据 | 备注 |
|---|---|---|---|---|---|
| 工作年限 | | | | | 口径：总/相关 |
| 城市 | | | | | 禁止默认一线 |
| 学历 | | | | | 必须判软/硬 |
| 履历·过往经历 | | | | | domain_history |
| 履历·项目经验 | | | | | project_experience |
| 履历·技能 | | | | | skills |
| 履历·证书资质 | | | | | credentials |
| **组合结论** | — | — | | — | AND / OR / 加权 |

### 组合逻辑

- **AND（默认）**：全部 ✅ 才算可投；任一 ❌ 即 ❌；有 ⚠️ 则整体 ⚠️；有 ⬜ 则**整体标记「待取证」**，不得判 ✅
- **OR**：用户显式指定时用（如「杭州或上海任一即可」）
- **加权（weighted）**：用户给权重时用。**硬门槛不参与加权**——一条 ❌ 压过九条 ✅

> 这条是红线 R3 的落地：**硬门槛是 AND，不是加权平均**。

---

## 3. 差距审计（心脏 B）

逐条对账「任职要求 × 履历」，每条标三选一：

### 类型 A：**可迁移**
- 必须有**证据件**：具体项目 / 交付物 / 可核验指标 / 可出示链接
- 「我觉得能」「做过类似的」不算
- 写法：`可迁移 | 证据件：独立开发并上线 Flutter App（95 文件 / 4 万行），可出示仓库与上架页`

### 类型 B：**需补**
- 必须给 **时间预算**（默认 ≤8 周）+ **最便宜的验证**
- 超过 time-box（8 周）→ **升级为换路径**，不再是「需补」
- 写法：`需补 | 动作：用 Python 搭一个可演示的 RAG 服务并开源 | 6 周 | 验证：3 个真实用户用它检索自己的文档 | 放弃信号：8 周内跑不通端到端`

### 类型 C：**硬门槛不可补**
- 当前路径不可达：学历层级、从业资格 / 执照、城市不可搬迁、年限刚性要求
- **处理是「换路径」，不是「硬填」**
- 写法：`硬门槛 | 该岗位为硕士起招（校招 JD 原文…），当前为本科学历 | 换路径：① 转向本科学历可投的社招岗；② 先做邻近岗积累相关年限`

### 差距清单表

| # | 差距项 | 类型 | 证据件 / 补齐动作 | 时间预算 | 最便宜的验证 | 放弃信号 |
|---|---|---|---|---|---|---|

**排序**：硬门槛 → 需补 → 可迁移。判决先看硬门槛。

---

## 4. JSON 输出 schema（`output.format: json`）

```json
{
  "schema_version": "1.0",
  "privacy": {
    "profile_source": "self | synthetic | aggregate",
    "contains_natural_person_data": false,
    "usage": "personal_planning | headcount_planning | job_design"
  },
  "report": {
    "direction": "",
    "focus_post": "",
    "generated_at": "YYYY-MM-DD",
    "verdict": "直接投 | 换岗 | 先补短板 | 硬门槛不可逾越 | 放弃该方向"
  },
  "posts": [
    {
      "post_id": "P02",
      "post_name": "",
      "official_code": null,
      "aliases": [],
      "level": "核心岗 | 邻近岗 | 支撑岗",
      "health": "扩张 | 稳定 | 萎缩 | 未取证",
      "duties": [],
      "requirements": { "hard": [], "soft": [], "bonus": [] },
      "tool_stack": [],
      "deliverables": [],
      "kpi": [],
      "years_band": { "min": null, "max": null, "relevant_only": true },
      "cities": [],
      "education": {
        "market_level": "",
        "statutory_level": "",
        "is_hard": null,
        "major_restricted": false,
        "evidence": ""
      },
      "salary_band": { "min": null, "max": null, "currency": "CNY", "evidence_level": "未取证" },
      "matched_attributes": {
        "years":     { "required": "", "profile": "", "basis": "relevant", "hit": "命中|部分命中|未命中|未取证", "evidence": "" },
        "city":      { "required": "", "profile": "", "hit": "", "evidence": "" },
        "education": { "required": "", "profile": "", "hit": "", "evidence": "" },
        "experience": {
          "domain_history":     { "hit": "", "evidence": "" },
          "project_experience": { "hit": "", "evidence": "" },
          "skills":             { "hit": "", "evidence": "" },
          "credentials":        { "hit": "", "evidence": "" }
        }
      },
      "combine_logic": "AND",
      "combine_result": { "hit": "", "blocking_items": [] },
      "gaps": [
        {
          "item": "",
          "type": "可迁移 | 需补 | 硬门槛",
          "evidence_artifact": "",
          "action": "",
          "time_budget_weeks": null,
          "cheapest_test": "",
          "kill_signal": ""
        }
      ],
      "evidence": [
        { "claim": "", "level": "①权威 | ②专业 | ③二手 | 未取证", "url": "", "checked_at": "" }
      ]
    }
  ]
}
```

**字段级隐私约束**：`matched_attributes` 及其子节点**不得出现** name / phone / email / id_number / employer / resume_url 等任何自然人标识字段。

---

## 5. 常见失败模式

| 失败模式 | 症状 | 修法 |
|---|---|---|
| 把「相关年限」当成总工龄 | 命中矩阵给 ✅，实际简历筛选即淘汰 | `relevant_only` 默认 true，口径写进备注列 |
| 城市默认一线 | 城市行写「一线城市均可」，实则未取证 | 逐岗取证；无证据即 ⬜ |
| 学历默认可放宽 | 「能力强就行」 | 判软硬必须引 JD 原文；判不出即 ⬜ |
| 可迁移没有证据件 | 写「可做迁移」但无项目/交付物 | 证据件必填，填不出降级为「需补」 |
| 需补没有 time-box | 「花时间学一下」 | 必须给周数；>8 周升级为换路径 |
| ⬜ 未取证被当命中 | 城市没查到就当「应该有机会」 | ⬜ 计入差距清单，组合结论标记待取证，不得判 ✅ |
| 输出里混入自然人标识 | 表格出现真实姓名/公司/简历链接 | 命中 R1，整份报告作废重做 |
