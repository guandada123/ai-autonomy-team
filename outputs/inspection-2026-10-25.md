# 任务状态巡检报告 · 2026-10-25 08:22 (GMT+8)

> 范围：`migration-package/runs/round1`–`round34`（共 34 轮）
> 数据源：`verdict-*.json` / `adjudication-record.json` / `answer-final.json` / `answer-status.json` / `seat-check.json` / `metrics.json`
> 方法：只读扫描，未修改任何 `round*` 产出。区分**真实异常**与**闸门正确生效 / 旧版式残留**（噪音）。
> 阈值基准：`flip_rate` 告警阈值 = 0.4（严格大于，`docs/protocol/07` §4.2）。

---

## ① 超时未完成项（未达终态 / 待签署）

| 轮 | 状态 | 说明 |
|---|---|---|
| **round1** | `pending`（旧三轴 ADJ-001/002/003） | 无 `answer-status.json`、无人类签署记录。`round2` 的合并轴裁决已覆盖其议题，但 round1 自身始终未终态。 |
| **round8** | 无 `answer-final` | 修订轮（prev=round5），EX-01/02/03 因 `E_READ_DOMAIN_VIOLATION` 被否，闸门拦截至聚合前，从未产出终局。 |
| **round9** | 无 `answer-final` | 修订轮（prev=round6），EX-01 `E_MISSING_SEAT` + EX-02 `E_READ_DOMAIN_VIOLATION`，被否，未聚合。 |
| **round10** | 无 `answer-final` | 修订轮（prev=round7），EX-01/03 `E_READ_DOMAIN_VIOLATION`，被否，未聚合。 |

> round8/9/10 是被**前置闸门正确拦截**导致未终态（非静默失败），属设计内阻塞，见 ②。

---

## ② 阻塞项（含阻塞原因与所需输入）

### 🔴 A. 真实缺陷（S8-1 泄漏）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| **round13** | `EX-03` 因 `E_READ_DOMAIN_VIOLATION` 被 `seat-check` 否决（`sha256=ed2befbd66a6`），但 `answer-final.json` 的 `aggregation_inputs[]` 仍含 `EX-03`（status 标 `valid`）→ **被否席位进入聚合**，违反协议 §5.1 与 S8-1。 | 重聚合剔除 EX-03（从 `aggregation_inputs[]` 移除其 sha）→ 重跑 `seat_check --verify-final`；结构审计第 8 查须记「检测到且已拦」留痕。 |

### 🟠 B. 人类闸门（设计内，待用户处置）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| **round34** | `answer-status.status = awaiting_human_confirm`，`blocking = "删除是不可逆动作，须用户确认"`，置位时间 `2026-10-24T00:29:35`，**已等待 >32h 未决议**。 | 用户显式确认或明确拒绝该不可逆删除；确认前该结论不得视为终态。 |

### 🟡 C. 被否席位（与 ① 重叠，前置闸门已生效）
| 轮 | 阻塞原因 | 所需输入 |
|---|---|---|
| round1 | EX-01/02/03 `E_MISSING_CONCLUSION`+`E_MISSING_EVIDENCE`、AD-01 `E_MISSING_*`（4 席全否） | 重跑席位或按旧基线接受（不可回溯） |
| round4 | EX-01 `E_STANCE_OUT_OF_SPACE`、EX-03 `E_BAD_EVIDENCE_ITEM×7` | 重跑席位 |
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
| **round34** | 缺 `aggregation_inputs` 且 `adjudication_source=None`，仅含 `answer/why/conditions/human_veto` | 中（部分可凭 `why` 追溯） |

> 部分聚合（字段存在但席位不全，不属「无指针」但属不完整）：
> - **round12**：仅 `EX-01`（1/3）
> - **round15 / round28 / round33**：仅 `EX-01`+`EX-03`（2/3）

---

## ④ 翻边率超阈值项

| 轮 | flip_rate | 阈值 | 告警 | 说明 |
|---|---|---|---|---|
| round2 | `null` | 0.4 | 否 | 基线轮，立场不可比，未定义（≠0） |
| round3 | **0.2** | 0.4 | **否** | N=1/D=5，`EX-02` 在 ADJ-001 上 `exclude→count`；低于阈值 |

**结论：④ 无超阈值项。**

⚠️ 但需注明：**34 轮中仅 round2/round3 落盘了 `flip_rate` 字段，其余 32 轮未评估**（9 轮无 `metrics.json`：1/4/5/6/7/8/9/10/11；22 轮 `metrics_status=blocked_by_contract`：12–33；round34 无 metrics）。未完成评估≠干净——这些轮次因「存在被否席位指标拒绝计算（exit 5）」或早期未跑指标而缺值，**不能据此宣称翻边率整体受控**。

---

## 总览

- **① 超时未完成**：4 项（round1、8、9、10）
- **② 阻塞项**：1 项真实缺陷（round13 泄漏）＋1 项人类闸门（round34）＋被否席位（1/4/8/9/10）＋闸门已拦（round12）＋审计缺口（14–33）
- **③ 无证据指针**：4 项（round1、4、5、34；另 round12/15/28/33 聚合不全）
- **④ 翻边率超阈值**：**无**；但 32/34 轮未评估

> 相对上轮（2026-10-24）变化：round34 人类闸门已置位 >32h 仍未决议（上一轮记为「否决窗未过」已过期）；其余结论稳定，无新增真实缺陷。
