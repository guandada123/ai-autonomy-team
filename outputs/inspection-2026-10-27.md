# 任务状态巡检报告 · 2026-10-27 08:08 (GMT+8)

> 范围：`migration-package/runs/round1`–`round34`（共 34 轮，无新增 round35+）
> 数据源：`verdict-*.json` / `adjudication-record.json` / `answer-final.json` / `answer-status.json` / `seat-check*.json` / `metrics*.json`
> 方法：只读扫描，未修改任何 `round*` 产出。区分**真实异常**与**闸门正确生效 / 旧版式残留**（噪音）。
> 阈值基准：`flip_rate` 告警阈值 = 0.4（严格大于，`docs/protocol/07` §4.2）。
> 工具：`scripts/inspect_status.py` 程序化全量重扫 + 关键项磁盘核验。结论与 10-26 逐项核对，**唯一变化 = round34 人类闸门已解除**。

---

## ① 超时未完成项（未达终态 / 待签署）

| 轮 | 状态 | 说明 |
|---|---|---|
| **round1** | `pending`（旧三轴 ADJ-001/002/003） | 无 `answer-status.json`、无人类签署记录。`round2` 的合并轴裁决已覆盖其议题，但 round1 自身始终未终态。 |
| **round8** | 无 `answer-final` | 修订轮（prev=round5），EX-01/03 `E_READ_DOMAIN_VIOLATION` 被否，闸门拦截至聚合前，从未产出终局。 |
| **round9** | 无 `answer-final` | 修订轮（prev=round6），EX-01 `E_MISSING_SEAT` + EX-02 `E_READ_DOMAIN_VIOLATION`，被否，未聚合。 |
| **round10** | 无 `answer-final` | 修订轮（prev=round7），EX-01/03 `E_READ_DOMAIN_VIOLATION`，被否，未聚合。 |

> round8/9/10 是被**前置闸门正确拦截**导致未终态（非静默失败），属设计内阻塞，见 ②-C。

---

## ② 阻塞项（含阻塞原因与所需输入）

### 🔴 A. 真实缺陷（S8-1 泄漏）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| **round13** | `EX-03` 因 `E_READ_DOMAIN_VIOLATION` 被 `seat-check` 否决（`sha256=ed2befbd66a6…`），但 `answer-final.json` 的 `aggregation_inputs[]` 仍含 `EX-03`（status 标 `valid`）→ **被否席位进入聚合**，违反协议 §5.1 与 S8-1。脚本核对确认，为 34 轮中唯一真实泄漏。 | 重聚合剔除 EX-03（从 `aggregation_inputs[]` 移除其 sha）→ 重跑 `seat_check --verify-final`；结构审计第 8 查须记「检测到且已拦」留痕。 |

### ✅ B. 人类闸门（上轮阻塞，本轮已解除）
| 轮 | 原阻塞原因 | 本轮状态 |
|---|---|---|
| **round34** | `blocking = "删除是不可逆动作，须用户确认"`，10-26 时仍 `awaiting_human_confirm`（>2天未决） | **已于 `2026-10-26T08:36:04+08:00` 由 `human:guan` 决议关闭**（`resolution.by=human:guan`，`decision`：不执行删除，任务已由人工完成，见 `docs/decisions/D-0069-round34闸门已人工完成.md`）。`answer-status.status = resolved_by_human`，`blocking=None`。→ **不再阻塞**。注：`verdict-final.json` 仍残留 `hold_then_proceed`（veto_deadline 2026-10-25 已过期），但 `answer-status` 为权威终态记录，建议择机回填 verdict 以消歧。 |

### 🟡 C. 被否席位（与 ① 重叠，前置闸门已生效）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| round1 | EX-01/02/03 `E_MISSING_CONCLUSION`+`E_MISSING_EVIDENCE`、AD-01 `E_MISSING_*`（4 席全否） | 重跑席位或按旧基线接受（不可回溯） |
| round4 | PM-01 `E_READ_DOMAIN_VIOLATION`、EX-01 `E_STANCE_OUT_OF_SPACE`、EX-03 `E_BAD_EVIDENCE_ITEM×7` | 重跑席位 |
| round8/9/10 | 见 ①（读域越界 / 席位缺失） | 在读取域内重跑 |

### 🟡 D. 闸门已拦、未泄漏（真实违例但被正确排除）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| **round12** | `PM-01` `E_READ_DOMAIN_VIOLATION` 被否，**未**进入 `aggregation_inputs`（闸门保持）。但 `answer-final` 仅聚合 `EX-01`（1/3 辩论席）→ PM 产物存疑 + 聚合不全。 | PM-01 在读取域内重跑；补全聚合席位 |

### ⚪ E. 审计覆盖缺口（非结论阻塞，闸门已拦）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| round14–33（共 20 轮） | `AD-01` `E_MISSING_SEAT`（审计席位从未产出），已正确排除出 `aggregation_inputs`。结论可终态，但**缺结构审计保证**。 | 对上述各轮补跑 `AD-01` 结构审计 |

---

## ③ 无证据指针的结论（answer-final 缺 `aggregation_inputs[]`，无法回溯聚合席位与证据）

| 轮 | 情况 | 严重度 |
|---|---|---|
| **round1** | 旧版式，`aggregation_inputs` 缺失，不可回溯 | 高（且未终态） |
| **round4** | 旧版式，缺失 | 高（且未终态） |
| **round5** | 旧版式，缺失（但 `state=adjudicated`） | 中 |
| **round34** | 缺 `aggregation_inputs` 且 `adjudication_source=None`；但本轮已 `resolved_by_human`，且 `answer-final` 含 `why`/`conditions`/`evidence` 可部分追溯 EX-01/02/03 三席 → **随闸门关闭，严重度降至低、状态已关闭** | 低（已关闭） |

> 部分聚合（字段存在但席位不全，不属「无指针」但属不完整）：
> - **round12**：仅 `EX-01`（1/3）
> - **round15 / round28 / round33**：仅 `EX-01`+`EX-03`（2/3）
>
> 注：round8/9/10 无 `answer-final.json` 文件本身（归 ①），其「缺指针」为文件缺位的结果，不单列。

---

## ④ 翻边率超阈值项

| 轮 | flip_rate | 阈值 | 告警 | 说明 |
|---|---|---|---|---|
| round2 | `null` | 0.4 | 否 | 基线轮，立场不可比，未定义（≠0） |
| round3 | **0.2** | 0.4 | **否** | N=1/D=5，`EX-02` 在 ADJ-001 上 `exclude→count`；低于阈值 |

**结论：④ 无超阈值项。** 仅 round2（null）/ round3（0.2）落盘 metrics；其余 **32/34 轮无 metrics（未计算：9 轮无 metrics + 22 轮 blocked_by_contract + round34 无 metrics）→ 不能判「干净」，但未超阈值。**

---

## 与上轮（10-26）的差异摘要
- **唯一变化**：round34 人类闸门 `awaiting_human_confirm` → `resolved_by_human`（2026-10-26T08:36 by human:guan）。②-B 由「阻塞」转为「已解除」，③ 中 round34 由「中」降为「低/已关闭」。
- **其余全部稳定**：①（round1/8/9/10 未变）、②-A（round13 唯一真实泄漏未变）、②-C/D/E（被否席位 / 闸门保持 / 审计缺口未变）、③（round1/4/5 未变）、④（无超阈值未变）。
- **非「今日无异常」**：仍存在 round13 真实 S8-1 泄漏 + round1/8/9/10 未终态 + round1/4/5 缺证据指针。
