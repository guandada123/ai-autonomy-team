# D-0016 · 费用差在金额字段无落点 → ADJ-001 两选项 implies 相同

- **日期**：2026-10-20（补落为正式文件；实施早于此）
- **依据来源**：`scripts/make_tasks_20.py:360-375` 注释
- **操作分级**：🟡 中（改 9/20 任务的裁决轴语义）
- **状态**：**已被契约 3.0 取代**（新增 `fee_diff_total` 后本决策的妥协不再必要）

## 决策（历史）

在 `summary` 六字段尚**无费用差落点**的前提下，`fee_omission` 场景的 ADJ-001
两选项（`count` / `exclude`）`implies` 写成**相同值**，使该轴成为「中性轴」。

## 依据

| # | 依据 | 来源 |
|---|---|---|
| 1 | 费用差 `fee_gap` 属 `fee_total` 列，而 `amount_diff_total` / `excluded_amount_total` 都定义在 `amount` 列差上 —— 费用差在六字段中无处安放 | `make_tasks_20.py` `expected` 构造 |
| 2 | 旧版把 `fee_gap` 加进/移出 `amount` 字段，违反 TASK.md 易错点 1「费用不并入金额」 | 同文件注释 |
| 3 | 两选项同值后，机器无法反推席位立场 → 人闸门签字失去可验证性（9/20 任务受影响） | 契约 3.0 裁定记录 §二 |

## 备选方案与未采用理由

| 备选 | 当时未采用理由 |
|---|---|
| 把 fee_gap 塞进 `excluded_amount_total` | 违反「费用不并入金额」，字段语义被污染 |
| 直接新增 `fee_diff_total` | 当时未做；契约 3.0 时才补上（见下） |

## 后续：本决策已被取代

契约 3.0（2026-10-20）新增 `summary.fee_diff_total`，两选项 `implies` 恢复可辨：

| 选项 | amount_diff_total | excluded_amount_total | fee_diff_total |
|---|---|---|---|
| `count` | `amount_gap` | `0.0` | **`fee_gap`** |
| `exclude` | `amount_gap` | `0.0` | **`0.0`** |

**保留本文件的理由**：它记录了「中性轴」的成因。后续若再发现中性轴，应先问
「是不是又有一个量没有落点」，而不是直接视为生成器 bug —— 2026-10-20 审计曾在此犯过一次误判。

## 相关

- 裁定记录：[`契约3.0-口径裁定记录.md`](../契约3.0-口径裁定记录.md) §二
- 实现：`scripts/make_tasks_20.py` `opts1` 构造（现两场景均含 `fee_diff_total`）
