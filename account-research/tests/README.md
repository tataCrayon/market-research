# tests/ —— 验收测试

## 两套测试，分工不同

| 套件 | 命令 | 需要私有存档吗 | 测什么 |
|---|---|---|---|
| 单元测试 | `python -m unittest discover -s account-research/tests -t account-research` | ❌ 不需要（用合成 fixture） | 清洗 / 判别 / 归属 / 统计 / 校验器 / 只追加 |
| 回归自检 | `python account-research/ar.py selftest` | ✅ 需要 `account/` | 解析器对**真实存档**是否仍然正确（`account/` 缺失时输出 **SKIP**，不是 PASS） |

## 纪律

- 测试代码与**合成** fixture 入库；`account/` 真实存档在 gitignore，**绝不入库**（含采集者会话数据）
- `tests/fixtures/expected_kazike_20261022.json` 只含公开数据（标题 + 点赞数），来源 = 案例报告 §1.2
- 每个断言必须可证伪；发现历史数字与存档冲突时，**要么证明是时点漂移并写进 `volatile` 豁免，要么改代码**——不许改断言迁就代码
- 解析器验收标准（AGENTS.md §3）：对 `account/` 真实存档跑通，产出与报告数据节一致

## 当前覆盖（157 例 + 15 项回归断言）

- `test_parser.py`：JS 字面量清洗（含「字符串里的 undefined 不能动」）、五种页面判别、账号字段、**采集者数据不得污染账号行**、外来笔记丢弃留证、列表页不伪造藏/评/分享
- `test_store_stats.py`：同键不覆盖、换采集日追加漂移行、latest-per-note、置顶不进样本、爆文率随行打印定义、分形式、无来源行排除
- `test_validate.py`：V1–V8 每条规则各造一段坏数据撞一次（含台账日期倒挂）
- `test_links.py`：待采清单——高赞在前、**数字齐但没正文的必须列出来**（旧逻辑「数字齐就不列」让 2 篇一直没被采）、全齐的不列
- `test_images.py`：图片通道——URL 末段绑定（`fileId` 前缀匹配会全漏）、只存 HTML 时标「未存图」而不是凑相邻图、静态资源不得把存档认到别人家、孤立 `_files` 按内容文件名救回、swiper 按 `data-swiper-slide-index` 取序并去重、OCR 质量标记、缺依赖要报「装什么」、**两份文本的关系按 8-gram 重叠判而不是按字数倍数**（钉住「字数差 46 倍但内容相同」这种反例）、**张数拿平台自己写的 `fraction` 计数器校验**（绑到 < 声明 = 我们漏了；取不到就写 None 不猜）。**全部合成 fixture，不依赖 OCR 引擎**（引擎用假对象注入）。
