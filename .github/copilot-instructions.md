# Copilot instructions

本仓库是**调研 Skill 合集**。仓库契约见根目录 [`AGENTS.md`](../../AGENTS.md)，本文件仅为索引。

## 你在本仓库工作时必须遵守

1. **每个调研 Skill 的产出必须是判决，不是信息**。禁止在模板或示例里写「值得考虑」「视情况而定」「建议进一步调研」；判决必须落到封闭选项集合中的一个，并附三件套：最值钱的一步 + 最便宜的验证（含时间预算）+ 放弃信号。
2. **证据分级**：①权威（监管/官方/原始数据源）②专业（行业报告、平台官方文档）③二手（内容农场、代办机构、SEO 博客）。二手只能当线索，不得当结论。
3. **未取证 ≠ 没问题**：查不到就写「未取证」并计入风险；禁止用记忆里的数字充当检索结果。
4. **不写死会变动的数字**：政策、价格、比例一律只给查证入口 + 要求现场取证并标注日期。

## 目录约定

```
AGENTS.md                        仓库契约（唯一真相源）
docs/SKILL-AUTHORING.md          怎么写一个调研 Skill
docs/lessons-learned.md          实证记录（只追加，不改写）
skills/<领域>-research/SKILL.md   Skill 入口
skills/<领域>-research/references/  清单、字段表、检索模板
skills/<领域>-research/examples/    真实产出样例（必须有）
skills/_template/                新 Skill 空模板
```

## 常见任务

- **新增调研 Skill** → 复制 `skills/_template/` 改名为 `skills/<领域>-research/`，按 `docs/SKILL-AUTHORING.md` 填写，跑一个真实 case 放进 `examples/`。
- **改已有 Skill** → 先读它文末「与其他 Skill 的关系」，不要越界改写别人的职责。
- **提交信息** → `<type>: <中文简述>`，type ∈ `feat` / `fix` / `docs` / `skill`。

完整要求（含提交前自检清单与禁止事项）以 `AGENTS.md` 为准。
