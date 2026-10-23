# 任务状态巡检 · 2026-10-23

> 范围：`migration-package/runs/round1`–`round33`（共 33 轮）。
> 主数据源（用户指定）：`verdict-*.json` / `adjudication-record.json` / `answer-final.json` + `docs/`。
> 补充数据源（同轮目录，用于 ②/④）：`seat-check.json`（契约闸门）、`metrics.json`（翻边率）。
> 阈值依据：`docs/protocol/07-轮次与指标.md` — 翻边率告警阈值 `0.4`（严格大于）；S8-1 = 被否决席位不得进入 `aggregation_inputs[]`。
> 性质：**只读巡检，未修改任何被检产出**。

---

## ① 超时未完成项

| 轮次 | 任务 | 状态 | 说明 |
|---|---|---|---|
| round1 | SYNTH-RECON-001（基线） | pending=3，无自身签署 | `pending-adjudication.json` 列 ADJ-001/002/003（旧三轴），无独立 human `adjudication-record.json`；其裁决被 round2/3 的合并轴记录（ADJ-001=count、ADJ-002=7）覆盖。任务层面最终已裁决，但 round1 产物自身从未签 → **历史基线残留，非在飞未完成**。 |
| round8 | T-01 修订轮（prev=round5） | 未终态 | 无 `answer-final.json` / `adjudication-record.json` / `answer-status.json`。 |
| round9 | T-02 修订轮（prev=round6） | 未终态 | 同上。 |
| round10 | T-03 修订轮（prev=round7） | 未终态 | 同上。 |

> round8/9/10 即试点复盘报告原计划"三任务各一轮修订轮测翻边率"的那三轮。三者 `seat-check` 显示 EX 席位 read-domain 越界（round8 三席全越界、round9 EX-02 越界+EX-01 缺失、round10 EX-01/EX-03 越界），闸门**正确拦截至聚合前**（非静默失败）。→ 列为未完成观察项，需确认是「主动废弃」还是「在飞未完成」。

---

## ② 阻塞项（含阻塞原因与所需输入）

### ⚠️ round13 — 真实 S8-1 违规（被否席位进入聚合）
- **任务**：T-06（evidence_round）。
- **阻塞原因**：EX-03 因 `E_READ_DOMAIN_VIOLATION` 被 `seat-check` 否决（`aggregation_blocked` 含 EX-03），但 `answer-final.json` 的 `aggregation_inputs[]` 仍含 EX-03（seats = EX-01/EX-02/EX-03）。即"被否决席位进入聚合"，违反 `docs/protocol/07` §5.1 写入者硬门与演练成功判据 S8-1。
- **根因**：写入者闸门（`seat_check --verify-final`）未拦截或未执行。
- **所需输入**：补跑 `--verify-final`（应 exit 1）→ PM 从 `aggregation_inputs` 剔除 EX-03，或令 EX-03 在读取域内重产合规产出后重聚合；并复核该轮已签署裁决记录是否仍有效。

### round4 — 席位契约违规 + 结论无指针
- **任务**：002（evidence_round）。
- **阻塞原因**：EX-01 `E_STANCE_OUT_OF_SPACE`、EX-03 `E_BAD_EVIDENCE_ITEM×7` 被否决；`answer-final.json` **缺** `aggregation_inputs` 字段（结论无任何席位指针可追溯）。
- **所需输入**：席位在读取域/合规重产，并补齐 `aggregation_inputs[]`。

### round12 — 写入者自身越域
- **任务**：T-05（evidence_round）。
- **阻塞原因**：PM-01 自身 `E_READ_DOMAIN_VIOLATION`（写入者越域）；聚合仅含 EX-01（对被否决席位闸门保持）。
- **所需输入**：PM 在读取域重产或复核越域内容是否影响结论。

### round8 / round9 / round10 — 席位越域，被闸门拦截至聚合前（见 ①）
- **阻塞原因**：EX 席位 read-domain 越界/缺失，无法进入聚合。
- **所需输入**：席位在读取域内重产合规产出 → 方可进入聚合与裁决。

### 结构观察（非聚合阻塞，单列）
- **round14–33**（及 round24 另含 PM-01）：`AD-01` 审计席位 `E_MISSING_SEAT`（可选审计席未产出）。这些轮次均已 human 签署且 `answer-status=adjudicated`，AD-01 产出按协议设计**不进入聚合**，故不构成聚合阻塞，仅属**审计覆盖缺口**。

---

## ③ 无证据指针的结论

| 轮次 | 问题 |
|---|---|
| round1 | `answer-final.json` 缺强制字段 `aggregation_inputs[]`（旧版式，早于 P10 §5.1 写入者硬门）。 |
| round4 | 同上缺失（且结论无任何席位指针）。 |
| round5 | 同上缺失（虽已有 human 签署裁决记录，但聚合结论无法回溯到具体席位产出）。 |

> 其余 30 轮（round2/3/6/7/11–33）均含有效 `aggregation_inputs[]`，可追溯。
> 注：round13 的 `aggregation_inputs[]` **存在但含被否决席位 EX-03**（见 ②），与 round1/4/5 的"字段缺失"性质不同，但同属可追溯性缺口。

---

## ④ 翻边率超阈值项

- **无超阈值项。** 翻边率告警阈值 `0.4`（严格大于）。
- 仅 **round2**（`flip_rate=null`，D=0 未定义，按协议绝不当 0）与 **round3**（`flip_rate=0.2 ≤ 0.4`，`alert=false`）落盘 `metrics.json`。
- 其余 **31 轮**（round1、round4–round33 中除 2/3 外）**未计算** `metrics.json` → 翻边率**未评估**。
- **结论**：未发现翻边率超阈值；但 31 轮未评估，不能据此判定"干净"。

---

## 附：本次相对 2026-10-22 巡检的新增/变化

1. **新增确认 round13 的 S8-1 违规**：被否决席位 EX-03 进入了 `aggregation_inputs[]`（上一轮巡检将其与 round4/8/9/10/12/13 一并归为"闸门生效/夹具"而排除，本次经 `aggregation_inputs` 与 `aggregation_blocked` 交叉比对，确认 round13 属真实泄漏，round4/12 闸门保持）。
2. **round8/9/10 明确为未终态修订轮**（T-01/02/03 修订轮，prev=round5/6/7），非单纯夹具。
3. **③ 稳定**：round1/4/5 缺 `aggregation_inputs[]` 不变。
4. **④ 稳定**：仍仅 round2/3 落盘 metrics，31 轮未评估。
