# 任务状态巡检 · 2026-10-24

> 范围：`migration-package/runs/round1`–`round34`（共 **34** 轮；round34 为本次新增，上次巡检 2026-10-23 仅覆盖至 round33）。
> 主数据源（用户指定）：`verdict-*.json` / `adjudication-record.json` / `answer-final.json` + `docs/`。
> 补充数据源（同轮目录）：`seat-check.json`（契约闸门）、`metrics.json`（翻边率）、`answer-status.json`（终态）。
> 阈值依据：`docs/protocol/07-轮次与指标.md` — 翻边率告警阈值 `0.4`（严格大于）；S8-1 = 被否决席位不得进入 `aggregation_inputs[]`（§5.1）。
> 性质：**只读巡检，未修改任何被检产出**。

---

## ① 超时未完成项（未达终态）

| 轮次 | 任务 | 状态 | 说明 |
|---|---|---|---|
| round1 | SYNTH-RECON-001（基线） | pending=3，无自身签署 | `pending-adjudication.json` 列 ADJ-001/002/003（旧三轴），无独立 human `adjudication-record.json`；其裁决被 round2/3 合并轴记录（ADJ-001=count、ADJ-002=7）覆盖。历史基线残留，非在飞未完成。 |
| round8 | T-01 修订轮（prev=round5） | 未终态 | 无 `answer-final.json`；`seat-check` 显示 EX 席位 read-domain 越界，闸门**正确拦截至聚合前**。 |
| round9 | T-02 修订轮（prev=round6） | 未终态 | 同上；EX-01 缺失 + EX-02 越界。 |
| round10 | T-03 修订轮（prev=round7） | 未终态 | 同上；EX-01/EX-03 越界。 |

> round8/9/10 即原计划"三任务各一轮修订轮测翻边率"的三轮。被闸门拦截至聚合前（非静默失败）。**需确认**：主动废弃 or 在飞未完成。
> **round34 不在此列**：已具备 answer-final / verdict / adjudication-record，处于合法的 `awaiting_human_confirm` 终态（否决窗口至 2026-10-25T00:29:35，当前 2026-10-24 08:14 仍在窗内），非超时未完成。
> 注：协议未定义逐轮"超时时钟"，此处"未完成"= 截至巡检仍未进入 adjudicated / 已签署终态。

---

## ② 阻塞项（含阻塞原因与所需输入）

### ⚠️ round13 — 真实 S8-1 违规（被否席位进入聚合）
- **任务**：T-06（evidence_round）。
- **阻塞原因**：EX-03 因 `E_READ_DOMAIN_VIOLATION` 被 `seat-check` 否决（`aggregation_blocked=[EX-03]`），但 `answer-final.json` 的 `aggregation_inputs[]` 仍为 `[EX-01, EX-02, EX-03]` —— 被否决席位 EX-03 仍进入聚合，违反 `docs/protocol/07` §5.1 写入者硬门与演练失败判据 S8-1。
- **根因**：写入者闸门（`seat_check --verify-final`）未拦截或未执行。
- **所需输入**：补跑 `--verify-final`（应 exit 1）→ PM 从 `aggregation_inputs` 剔除 EX-03，或令 EX-03 在读取域内重产合规产出后重聚合；复核该轮已签署裁决记录是否仍有效。

### round4 — 席位契约违规 + 结论无指针
- **任务**：002（evidence_round）。
- **阻塞原因**：EX-01 `E_STANCE_OUT_OF_SPACE`、EX-03 `E_BAD_EVIDENCE_ITEM×7` 被否决；`answer-final.json` **缺** `aggregation_inputs` 字段（结论无任何席位指针可追溯）。闸门保持（未泄漏）。
- **所需输入**：席位在读取域/合规重产，并补齐 `aggregation_inputs[]`。

### round12 — 写入者自身越域
- **任务**：T-05（evidence_round）。
- **阻塞原因**：PM-01 自身 `E_READ_DOMAIN_VIOLATION`（写入者越域）；聚合仅含 EX-01（对被否决席位闸门保持，未泄漏）。
- **所需输入**：PM 在读取域重产或复核越域内容是否影响结论。

### round8 / round9 / round10 — 席位越域，被闸门拦截至聚合前（见 ①）
- **阻塞原因**：EX 席位 read-domain 越界/缺失，无法进入聚合，无 `answer-final`。
- **所需输入**：席位在读取域内重产合规产出 → 方可进入聚合与裁决。

### 🆕 round34 — 人为门阻塞（设计内 human-in-the-loop，非缺陷）
- **任务**：本地备份从 4 组清到 2 组（删除不可逆动作的审议轮）。
- **阻塞原因**：`answer-status.json` 显式 `blocking = "删除是不可逆动作，须用户确认"`；`verdict-final.json` 为 `hold_then_proceed`，`veto_deadline = 2026-10-25T00:29:35`（当前仍在窗内，约 16 小时后到期）。
- **所需输入**：人工确认 —— 在否决窗口内执行删除，或行使否决（置 `human_veto`）。属协议设计的"人类闸门"终态，与 S8-1/越域类缺陷性质不同。

### 结构观察（非聚合阻塞，单列）
- **round14–33** 及 **round24**（另含 PM-01）：`AD-01` 审计席 `E_MISSING_SEAT`（可选审计席未产出）。这些轮次均已 human 签署、`answer-status=adjudicated`，AD-01 按协议设计**不进入聚合**，故不构成聚合阻塞，仅属**审计覆盖缺口**。

---

## ③ 无证据指针的结论

| 轮次 | 问题 |
|---|---|
| round1 | `answer-final.json` 缺强制字段 `aggregation_inputs[]`（旧版式，早于 P10 §5.1 写入者硬门），结论不可回溯到席位产出。 |
| round4 | 同上缺失（且结论无任何席位指针）。 |
| round5 | 同上缺失（已有 human 签署裁决记录，但聚合结论无法回溯到具体席位产出）。 |
| 🆕 round34 | `answer-final.json` 无 canonical `aggregation_inputs[]` 字段；但 `adjudication-record.adjudication_source = "EX-01/EX-02/EX-03 三席"` 且 `evidence` 三行给出可复现证明（bundle 祖先 / tar comm 独有 0/0/0 / 2348 还原 9197 文件）。经**不同通道可追溯**，严重度低于 1/4/5 的"字段缺失且无可追溯通道"。注明：round34 为 backup-cleanup 任务，采用 `hold_then_proceed + veto` 模式、未走算术聚合流水线，缺 `aggregation_inputs` 与其任务形态一致；建议补一个结构化指针字段以利统一回溯。 |

> 其余 27 轮（round2/3/6/7/11–33，除 round13 已在 ② 列为泄漏）均含有效 `aggregation_inputs[]`，可追溯。
> round13 的 `aggregation_inputs[]` **存在但含被否决席位 EX-03**（见 ②），性质为"指针污染"而非"字段缺失"，同属可追溯性缺口。

---

## ④ 翻边率超阈值项

- **无超阈值项。** 翻边率告警阈值 `0.4`（严格大于）。
- 仅 **round2**（`flip_rate=null`，D=0 未定义，按协议绝不当 0）与 **round3**（`flip_rate=0.2 ≤ 0.4`，`alert=false`）落盘 `metrics.json`。
- 其余 **32 轮**（含 🆕 round34，无 `metrics.json`）**未计算** `metrics.json` → 翻边率**未评估**。
- **结论**：未发现翻边率超阈值；但 32 轮未评估，不能据此判定"干净"。

---

## 附：本次相对 2026-10-23 巡检的新增/变化

1. **新增 round34**（2026-10-24 00:28 开轮）：backup-cleanup 审议轮，verdict `hold_then_proceed`，`answer-status=awaiting_human_confirm`，否决窗口至 2026-10-25T00:29:35。
   - 计入 ②（设计内 human-gate 阻塞，所需输入 = 人工确认/否决）；
   - 计入 ③（缺 canonical `aggregation_inputs[]`，但有 `adjudication_source` 通道可追溯）；
   - 计入 ④ 的"未评估"集（无 `metrics.json`）。
2. **① / ② / ③ 既有项（round1、4、5、8、9、10、12、13）与上次一致**，文件时间戳均为 2026-10-19/20，未变动；本次已逐轮交叉核对 `aggregation_blocked` × `aggregation_inputs`，确认仅 round13 为真实 S8-1 泄漏，round4/12 闸门保持。
3. **"未评估"轮次计数**：由 31 增至 **32**（新增 round34）。
