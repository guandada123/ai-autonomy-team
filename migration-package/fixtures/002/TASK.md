# 任务 TASK-BROKER-RECON-002 · 券商交割单与自记账核对（A股）

> 本任务使用**全合成数据**，不含任何真实账户、持仓或成交信息。

## 背景

自记账（我方记录，按交割日入账）与券商交割单（按成交日列示，费用单列）之间存在若干不一致。
请完成核对，输出**差异归因结果**。

本任务与 `SYNTH-RECON-001` 的关系：001 是通用「含税/不含税」对账，签署人在该域没有真实判断权责；
002 换到 A股交易成本这一签署人真正有判断权的域 —— **判错了有真实代价**（收益归因会偏乐观）。

## 输入

| 文件 | 说明 |
|---|---|
| `ledger.csv` | 自记账（我方记录） |
| `bill.csv` | 券商交割单（对方返回） |

自记账字段：`txn_id`、`date`（入账日）、`code`、`name`、`side`、`shares`、`price`、`amount`、`fee_total`、`note`

交割单字段：`txn_id`、`trade_date`（成交日）、`settle_date`（交割日）、`code`、`side`、`shares`、`price`、`amount`、`commission`、`stamp_tax`、`transfer_fee`、`fee_total`、`note`

费率口径（仅用于理解数据，**不得据此反推期望值**）：佣金 万分之2.5（最低 5 元）／印花税 卖出千分之0.5／过户费 万分之0.1，双向。

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
> 金额一律取 CSV 原始值，**不做含税/净额换算**。

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
| `dedup_removed_txn_count` | 判定为**重复**的单号个数（**按单号计，不按行计**） |
| `dedup_amount` | 上述重复单号所对应的**多出行**的金额合计 |

> 同一 `txn_id` 在一侧出现多次（如分批成交）即为重复。

#### C. 差异分类字段

| 字段 | 计算方法 |
|---|---|
| `diff_count` | 差异清单条数，须等于 `diffs` 数组长度 |
| `amount_diff_total` | 对每个归入「**计入**金额差异」类别的**非重复**单号，取 `abs(自记账 amount − 交割单 amount)`，求和 |
| `excluded_amount_total` | 对每个归入「**不计入**金额差异」类别的**非重复**单号，取差额，求和 |
| `excluded_categories` | 被排除在金额差异之外的**类别名**列表（若无则填 `[]`） |

> ⚠️ 四个易错点，严格按定义执行：
> 1. `amount` 列是**成交金额**，`fee_total` 是**费用**，两者分开取，不要相加后再比对。
> 2. 两个金额差异字段都是**差额**（`abs(自记账 − 交割单)`），不是交易全额。
> 3. **重复项不参与** `amount_diff_total` 与 `excluded_amount_total` —— 重复项金额**只由** `dedup_amount` 表达。
> 4. 一律取原始值，**不得**做净额/含费换算后再计算。

#### D. 差异的判定口径

「差异单号」= 两侧在**股数 / 成交金额 / 费用 / 日期**四列上不完全相同的单号。
仅单侧存在的单号（如新股中签、现金分红）亦为差异单号，差额取该单号在该侧的 `amount`。

## 约束

1. **不得引入外部数据**，只能用给定两份文件。
2. **所有数字必须由数据算出**，不得估计或手填。
3. 类别名由你自行定义，但**必须在整个输出中一致使用**。
4. `diffs` 中每条须含 `id` 与 `category`；`categories` 须与 `diffs` 自洽。
5. `falsification` 字段**不可省略**——缺失即视为结论不可裁决。
