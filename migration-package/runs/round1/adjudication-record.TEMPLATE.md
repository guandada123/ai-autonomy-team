# 人类裁决记录 · 待你填写

> **这个文件只有你能写。** 机器不得代填，工具链不会自动生成它。
>
> 背景：`fixtures/truth.json` 里原本有 4 条判据，把「口径差该不该算金额差异」这类**判断**编码成了期望值，
> 导致硬裁判拿着被争议的判断去裁决争议 —— 它只能复述，无法仲裁（见 `.learnings/L-0002`）。
> 现已把判断项分离出来，交由你裁决。

---

## 步骤

### 1. 先看机器给定的待裁决清单

```bash
python3 judges/verify.py \
    --spec fixtures/spec/adjudication.json \
    --spec-layer adjudication \
    --answer runs/round1/answer-final.json \
    --out runs/round1/pending-adjudication.json
```

预期：`verdict=pending_adjudication`，**退出码 3**（未裁决 ≠ 通过）。

### 2. 逐项选择，填入下方 decisions

把本文件另存为 `runs/round1/adjudication-record.json`，填好 `recorded_by` 与每项 `chosen`。

```json
{
  "task_id": "SYNTH-RECON-001",
  "source": "human_adjudication",
  "recorded_by": "<填你的名字>",
  "recorded_at": "<ISO8601 时间>",
  "decisions": [
    {"id": "ADJ-001", "chosen": "<count 或 exclude>", "rationale": "<你的理由>"},
    {"id": "ADJ-002", "chosen": "<as_diff 或 not_diff>", "rationale": "<你的理由>"},
    {"id": "ADJ-003", "chosen": "<as_diff 或 not_diff>", "rationale": "<你的理由>"}
  ]
}
```

### 3. 重跑验证

```bash
python3 judges/verify.py \
    --spec fixtures/spec/adjudication.json \
    --spec-layer adjudication \
    --record runs/round1/adjudication-record.json \
    --answer runs/round1/answer-final.json \
    --out runs/round1/adjudicated.json
```

预期：`verdict=adjudicated`，退出码 0，且每一项带 `source=human_adjudication` 与 `decided_by`。

---

## 三项待裁决的判断轴

### ADJ-001 ·「口径差」是否计入金额差异？

**事实**：T104 台账 2120.00(含税) vs 账单 2000.00(不含税)，差 120.00；T105 台账 1060.00 vs 账单 1000.00，差 60.00。
两者比值均为 **1.06**（6% 税率，n=2）。

| 选项 | 含义 | 后果 |
|---|---|---|
| `count` | 计入 —— 视为应收缺口 180.00 | `amount_diff_total = 680.02`，`excluded_amount_total = 0.00` |
| `exclude` | 不计入 —— 视为同一经济价值的两种口径表述 | `amount_diff_total = 500.02`，`excluded_amount_total = 180.00` |

**争议要点**：支持 `exclude` 的一方说 1.06 精确吻合说明是同笔金额的口径差异；
支持 `count` 的一方说 n=2 不足以确立税率规则、契约也明令禁止换算，且**风险不对称**——
多报差异的代价是一次复核，少报差异的代价是它永久消失。

### ADJ-002 ·「跨月归属」是否计为一条差异？

**事实**：T106 台账 2026-09-30 vs 账单 2026-10-01，金额均 4800.00（金额一致，仅归属期不同）。

| 选项 | 含义 | 后果 |
|---|---|---|
| `as_diff` | 计为差异 | `diff_count = 7` |
| `not_diff` | 不计为差异 | `diff_count = 6` |

### ADJ-003 ·「口径差」是否计为一条差异？

| 选项 | 含义 | 后果 |
|---|---|---|
| `as_diff` | 计入差异清单（口径差也需跟进） | `diff_count = 7` |
| `not_diff` | 不入清单（已完全解释） | `diff_count = 5` |

> ⚠️ ADJ-002 与 ADJ-003 都影响 `diff_count` / `diffs`，两者组合会互相影响。
> 当前设计按「单轴独立选择」实现；若你的实际判断是**联合分支**（例如「跨月计、口径不计」是一个整体策略），
> 请在填完后告知，我把它改为联合选项表达。

---

## 一个必须知道的边界

`verify.py` 只能校验本文件的**声明字段**（`source` / `recorded_by` 非空 / 有 decisions），
**无法证明它确实由人而非模型撰写**。

> 真正的保证是程序性的：本文件由你在机器流程之外填好、放入路径，**工具链不对它做任何自动生成**。
> 一旦有人把「自动生成 adjudication-record」写进流水线，这道闸门立刻失效，而校验不会报警。

这条边界已写入 `docs/protocol/06-写入域与判据分层.md`。
