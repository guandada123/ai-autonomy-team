# 任务 TASK-SYNTH-RECON-001 · 跨平台数据核对与差异归因

> 本任务使用**全合成数据**，不含任何真实业务信息。

## 背景

台账（我方记录）与账单（对方返回）之间存在若干不一致。请完成核对，输出**差异归因结果**。

## 输入

| 文件 | 说明 |
|---|---|
| `ledger.csv` | 台账（我方记录） |
| `bill.csv` | 账单（对方返回） |

字段：`txn_id`（单号）、`date`（日期）、`amount`（金额）、`tax_included`（`Y`=含税，`N`=不含税）、`party`（渠道）、`note`（备注）

## 输出

严格 JSON，写入 `answer.json`：

```json
{
  "summary": {
    "ledger_rows": 0,
    "bill_rows": 0,
    "ledger_total": 0,
    "bill_total": 0,
    "raw_total_gap": 0,
    "dedup_removed_txn_count": 0,
    "dedup_amount": 0,
    "diff_count": 0,
    "amount_diff_total": 0,
    "excluded_amount_total": 0,
    "excluded_categories": []
  },
  "diffs": [
    {"id": "T000", "category": "你定义的类别名"}
  ],
  "categories": {
    "你定义的类别名": ["T000"]
  },
  "falsification": "在什么条件下我这个结论是错的"
}
```

### 字段定义

> **契约只定义每个字段的计算方法，不预设你对「该不该算差异」的判断。**
> 所有金额字段一律取**原始值**（两份 CSV 中 `amount` 列的数值），**不做含税/不含税换算**。

#### A. 纯算术字段（无解释空间，照算即可）

| 字段 | 计算方法 |
|---|---|
| `ledger_rows` | `ledger.csv` 数据行数（不含表头） |
| `bill_rows` | `bill.csv` 数据行数（不含表头） |
| `ledger_total` | `ledger.csv` 的 `amount` 列合计 |
| `bill_total` | `bill.csv` 的 `amount` 列合计 |
| `raw_total_gap` | `bill_total − ledger_total` |

#### B. 重复项字段

| 字段 | 计算方法 |
|---|---|
| `dedup_removed_txn_count` | 你判定为**重复**的单号个数（**按单号计，不按行计**） |
| `dedup_amount` | 上述重复单号所对应的**多出行**的金额合计（即「去重后消失的那些行」的金额之和） |

#### C. 差异分类字段

| 字段 | 计算方法 |
|---|---|
| `diff_count` | 差异清单条数，须等于 `diffs` 数组长度 |
| `amount_diff_total` | 对每个归入「**计入**金额差异」类别的**非重复**单号，取 `abs(台账amount − 账单amount)`，求和 |
| `excluded_amount_total` | 对每个归入「**不计入**金额差异」类别的**非重复**单号，取 `abs(台账amount − 账单amount)`，求和 |
| `excluded_categories` | 被排除在金额差异之外的**类别名**列表（若无则填 `[]`） |

> ⚠️ 三个易错点，请严格按定义执行：
> 1. 两个金额差异字段都是**差额**（`abs(台账 − 账单)`），**不是交易全额**。
> 2. **重复项不参与** `amount_diff_total` 与 `excluded_amount_total` 的计算——重复项金额**只由** `dedup_amount` 表达。
> 3. 一律取**原始值**，**不得**做含税/不含税换算后再计算。

## 约束

1. **不得引入外部数据**，只能用给定两份文件。
2. **所有数字必须由数据算出**，不得估计或手填。
3. 类别名由你自行定义，但**必须在整个输出中一致使用**。
4. `diffs` 中每条须含 `id` 与 `category`；`categories` 须与 `diffs` 自洽。
5. `falsification` 字段**不可省略**——缺失即视为结论不可裁决。
