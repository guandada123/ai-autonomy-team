# 任务 TASK-BROKER-014 · 券商交割单与自记账核对（A股）

> 本任务使用**全合成数据**，不含任何真实账户、持仓或成交信息。
> 与 `TASK-BROKER-RECON-002` 同构：字段、口径、易错点完全一致，仅数据不同。

## 背景

自记账（我方记录，按入账日记账）与券商交割单（按成交日列示，费用单列）之间存在若干不一致。
请完成核对，输出**差异归因结果**。

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
    "ledger_rows": 0, "bill_rows": 0, "ledger_total": 0, "bill_total": 0,
    "raw_total_gap": 0, "dedup_removed_txn_count": 0, "dedup_amount": 0,
    "diff_count": 0, "amount_diff_total": 0, "excluded_amount_total": 0,
    "fee_diff_total": 0, "excluded_categories": []
  },
  "diffs": [{"id": "T000", "category": "你定义的类别名"}],
  "categories": {"你定义的类别名": ["T000"]},
  "falsification": "在什么条件下我这个结论是错的"
}
```

### 字段定义

> **契约只定义每个字段的计算方法，不预设你对「该不该算差异」的判断。**
> 金额一律取 CSV 原始值，**不做含税/净额换算**。

#### A. 纯算术字段

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
| `amount_diff_total` | 归入「**计入**金额差异」类别的**非重复**单号，取 `abs(自记账 amount − 交割单 amount)`，求和 |
| `excluded_amount_total` | 归入「**不计入**金额差异」类别的**非重复**单号，取差额，求和 |
| `fee_diff_total` | 费用列（`fee_total`）的差额合计：逐个**非重复**单号取 `abs(自记账 fee_total − 交割单 fee_total)` 求和；**单侧缺失时取整笔费用**。费用**不并入** `amount_diff_total` / `excluded_amount_total`（那两者只算 `amount` 列） |
| `excluded_categories` | 被排除在金额差异之外的**类别名**列表，必须是 `diffs[].category` 的**子集**（若无则填 `[]`，**并必须在 `excluded_categories_note` 说明为何为空**） |
| `excluded_categories_note` | 当 `excluded_categories` 为 `[]` 时，说明「为何没有任何类别被排除」；非空时可省略 |

> 📌 **`excluded_categories` 的语义（易误读，务必按此理解）**：
> 它列的是「**被排除出金额差异**的类别」，因此天然是 `diffs[].category` 的**真子集**：
> - `diffs` 列**全部**差异（含被排除的类别）；
> - `excluded_categories` 只列其中**不计入金额差异**的那几类。
>
> 所以「`excluded_categories` 非空而 `excluded_amount_total == 0`」**不是矛盾**——
> 日期差、股数差、费用差这些类别的 `amount` 差本就是 0。
> 机器校验：写入的类别必须出现在 `diffs[].category` 中（凭空新增会被 `W_CATEGORY_NOT_IN_DIFFS` 提示）。

> ⚠️ 四个易错点，严格按定义执行：
> 1. `amount` 列是**成交金额**，`fee_total` 是**费用**，两者分开取，不要相加后再比对。
> 2. 两个金额差异字段都是**差额**（`abs(自记账 − 交割单)`），不是交易全额。
> 3. **重复项不参与** `amount_diff_total` 与 `excluded_amount_total`。
> 4. 一律取原始值，**不得**做净额/含费换算后再计算。
>
> 📌 **空数组条款（D-0024 收口）**：`excluded_categories: []` 是**合法**的，但必须配 `excluded_categories_note`
> 解释原因（例：「本任务全部差异均为金额类，无类别需要排除」）。机器判据为二选一：
> 要么列表非空，要么列表为空且有说明。**"空" 与 "漏填" 必须可区分。**

#### D. 差异的判定口径

「差异单号」= 两侧在**股数 / 成交金额 / 费用 / 日期**四列上不完全相同的单号。
仅单侧存在的单号亦为差异单号，差额取该单号在该侧的 `amount`。

## 约束

1. **不得引入外部数据**，只能用给定两份文件。
2. **所有数字必须由数据算出**，不得估计或手填。
3. 类别名由你自行定义，但**必须在整个输出中一致使用**。
4. `diffs` 中每条须含 `id` 与 `category`；`categories` 须与 `diffs` 自洽。
5. `falsification` 字段**不可省略**——缺失即视为结论不可裁决。
