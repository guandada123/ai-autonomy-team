# D-0012 · 事实表派生任务参数化 + round4 首轮两项环境发现

- **日期**：2026-10-20
- **决策者**：PM-01（用户指令「继续」）
- **操作分级**：🟡 中（改裁判侧模板生成器 → 必须带字节级回归锚）
- **状态**：已执行（回归 48/48 连跑两次全绿）

## 决策

1. `derive_fact_rows` 按任务参数化：fixtures/&lt;task&gt;/spec/fact-schema.json 存在时走多列通用比对路径；**缺失时 001 默认布局逐字节不变**（回归第 33 项"round2 模板可逐字节重现"是安全锚）。
2. 002 新增 `spec/fact-schema.json`（金额/费用/股数 求和 + 日期 集合 + 行数 进签名）与 `spec/interpretations.json`（6 条候选解释，XJ-01 点名的口径歧义全部显式入册）。
3. round4 裁决模板生成成功 → precheck 第 11 项转绿 → **回归 47/48 → 48/48**。

## 依据

| # | 依据 | 来源 |
|---|---|---|
| 1 | 模板生成器 fail-closed 拒绝：`fixtures/002/ledger.csv 缺列 ['tax_included']` | `seat_check.py --emit-record-template` 实测输出 |
| 2 | 单列替换不可行：T206 只差股数（金额/日期/费用全等），单列 sig 会漏；T204 只差费用 —— 002 需要**多列**比对 | fixtures/002 两份 CSV 实测 |
| 3 | 回归 48/48 ×2；第 33 项（001 模板逐字节重现）通过 → 默认路径未被破坏 | `scripts/regress.py` |
| 4 | round4 事实表 8 行与两份 CSV 逐格相符（含 T208 费用 7.50≠7.54、T206 股数 0≠30 —— XJ-01 点名的两处疑点原始入表） | `runs/round4/adjudication-record.TEMPLATE.md` §1 |
| 5 | `inspect_agent_routing.py` 证实 round4 六席全部路由至 `glm-5.3-flash`（含未指定模型者） | `providerData.requestModelId` 运行时记录 |

## 风险边界（🟡）

| 项 | 内容 |
|---|---|
| 影响范围 | `scripts/judges/seat_check.py`（新增 _load_fact_schema/_derive_fact_rows_generic，_render_fact_table 双模式）；包内新增 002 的 2 个 spec 文件；MANIFEST 206 → 209 |
| 已知残余 | ① 第 8 查事实表尚无独立回归项（由第 33/39 项间接锚定 001 侧）；② 002 的 signature 未进 digest 基线；③ 通用路径的 sum 用 Decimal、set 用排序元组，fmt=int 的行为只在 002 实测过一次 |
| 回滚方式 | 删 002 的 fact-schema.json/interpretations.json + 回退 seat_check.py（_archive 整树可覆盖）+ 重建 MANIFEST |

## 审计发现

- **🔴 环境发现 1：席位 default 路由 ≠ 主会话模型。** round4 六席全部 GLM（含未显式指定者），XJ-01 与辩论席同家族 → 本轮跨家族前提不成立。软裁判权重设计值本就是 0，无需降权配置；但 **派席必须显式指定模型家族** 成为操作纪律，已写入 `.workbuddy/memory`。XJ-01 本轮线索按同家族降权解读。
- **🔴 环境发现 2：AD-01 v1 采信了 XJ-01 的跨家族"自报"** —— 恰是 protocol/04 明令禁止的取证方式（必须读 providerData）。第 1 查的家族核验目前无机器手段，已在 AD-01 barely_passed 中自曝；后续可让 seat_check 直接注入 providerData 家族字段（排期）。
- round4 首轮实测成本：六席 + PM/AD 重试 ≈ 9 次子代理调用，自报 tokens 合计约 17 万（与 params.json 126100 基数 + 33% 余量的预算框架量级一致）。
- 裁决模板 §1 事实表把 XJ-01 点名的两处疑点（T208 费用 0.04、T206 股数）以原始观测呈现 —— 「评委线索 → 事实表」这条通路闭环了。

## 修订记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-10-20 | 初版 | 002 裁决模板解锁 + 两项环境发现留痕 |
