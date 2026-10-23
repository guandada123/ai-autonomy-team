# D-0015 · 生成器 one_side 任务 ADJ-001 implies 与选项语义脱节（阻塞性 bug 修复）

- **日期**：2026-10-20
- **决策者**：PM-01（用户指令「继续」；发现于签字前置检查）
- **操作分级**：🔴 高——不改则 round5/6/7 **任何**签字都无法到达 adjudicated
- **状态**：已执行（回归 50/50 ×2、批自检 20/20）

## 决策

修复 make_tasks_20.py 中 one_side 类型任务 ADJ-001 的 implies，并把三个既有任务
（T-01/T-02/T-03）的 spec 同步修正。

## 依据

| # | 依据 | 来源 |
|---|---|---|
| 1 | 旧 implies：count→amount_gap+fee_gap（26646.79）、exclude→amount_gap/fee_gap（26640/6.79）——移动量只有 6.79 的**费用**，与题目「单侧缺失是否计入」完全无关 | `fixtures/tasks/T-0{1,2,3}/spec/adjudication.json` 实测 |
| 2 | 旧 count 期望 26646.79 违反 TASK.md 字段定义（amount_diff_total = Σ abs(amount 差)，费用不得并入） | TASK.md §C 易错点 1 |
| 3 | 正确语义：count→单侧计入（gap/0）；exclude→单侧移出（price_diff/单侧额）。T-01: 26640/0 与 120/26520；T-02: 14440/0 与 120/14320；T-03: 26640/0 与 120/26520 | 对实际 CSV 逐行复算 |
| 4 | 回归 50/50 ×2、批自检 20/20（修复途中自捕 Decimal/float 混算一处） | `scripts/regress.py` |

## 与「保留歧义」决策的边界

用户保留的是**排除准则之争**（哪些类别该移出——T-02 三席各执一词）。本次修的是
**implies 与选项文本脱节**（移动量错了两个数量级、语义错位）——它让任何口径的签字都
无法判 adjudicated，属于 bug 而非歧义。修复后，口径之争完整保留：count/exclude 的
移动量现在真实反映了「单侧缺失计入 vs 整类移出」。

## 风险边界

| 项 | 内容 |
|---|---|
| 影响范围 | make_tasks_20.py（one_side 分支）；T-01/02/03 三个 spec 的 ADJ-001 implies（带 implies_fixed 留痕字段） |
| 已知残余 | truth.json 的 expected（26640/6.79 等）是第三种口径（「费用不计入」默认值），已标注「非真值」保留；签 count 后 PM 改判须声明零金额排除类别（excluded_categories 空列表会拒） |
| 回滚 | implies_fixed 字段标注 + _archive 整树覆盖 |

## 修订记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-10-20 | 初版 | 签字前置检查发现 implies 阻塞，D-0012 生成器缺陷第二例 |
