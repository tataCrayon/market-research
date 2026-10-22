# tests/ —— 验收测试

## 纪律

- 测试代码入库；**fixtures（HTML 存档）绝不入库**——`account/` 在 gitignore，fixtures 通过路径引用（如 `ACCOUNT_DIR = Path("../../account")`，环境变量可覆盖）
- 每个测试必须可证伪：给定已知数据的存档 → 断言解析产出等于预期值（预期值来源 = case 报告 1.2 节人工核验过的数字）
- 解析器验收标准（AGENTS.md §3）：对 `account/` 真实存档跑通，产出与报告数据节一致

## 状态

待建（随 src/parser 第一个提交一起建立）。
