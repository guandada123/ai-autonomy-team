# 任务状态巡检报告 · 2026-10-22

> 范围：`migration-package/runs/round*/`（共 33 轮：round1–round33，其中 round8/9/10 为未执行夹具）
> 数据源：`verdict-*.json`、`adjudication-record.json`、`answer-final.json`；补充读取 `seat-check.json` / `metrics.json` / `pending-adjudication.json`（均只读，未修改任何产出）
> 阈值依据：`docs/protocol/07-轮次与指标.md`（翻边率告警阈值 0.4，严格大于）；`docs/protocol/04-双裁判规范.md`、`05`、`06`
> 巡检结论：**四项中有三项存在需要关注的项，非「今日无异常」**

---

## ① 超时未完成项（人类闸门未过 / 未签署）

| 轮次 | 未完成项 | 阻塞状态 | 说明 |
|---|---|---|---|
| **round1** | `pending-adjudication.json` 含 **ADJ-001 / ADJ-002 / ADJ-003** 三项 pending | `adjudication-record.json` 仅有 `TEMPLATE.md` 与 `SELFTEST-PLUMBING-ONLY.json`，**无 `recorded_by` 人类签署** | 基线轮的人类闸门未过 |

- 说明：round1 的 3 项 pending 为 **F15 合并轴前的旧三轴形态**（ADJ-002/ADJ-003 已退役合并为单轴 DIFFSET）。其等价判断已被 **round2 已签署合并轴裁决记录**（关，2026-10-20，`source=human_adjudication`）在实质上覆盖，但 round1 自身从未独立签署 —— 属已知待办（见工作记忆「待用户签署人类裁决记录」）。
- 其余 32 轮（含 round8/9/10 夹具）均有已签署或无需签署的裁决记录，**无其他未完成的人类闸门**。

---

## ② 阻塞项（含阻塞原因与所需输入）

**结论：无真实阻塞项。** 以下为巡检中出现的「疑似阻塞」信号及其排除理由：

| 信号来源 | 表面现象 | 排除理由（非真实阻塞） |
|---|---|---|
| round4 `seat-check.json` | EX-01（`E_STANCE_OUT_OF_SPACE`）、EX-03（`E_BAD_EVIDENCE_ITEM×7`）被契约否决 | `answer-final.json` 已明确将二者排除出聚合（aggregation_note 记载「其数值结论一律不进入 answer」）→ **闸门正确生效**，轮次已完成且已签署 → 健康样本 |
| round8 / round9 / round10 | seat-check 含 read-domain / missing-seat 违规，且无 answer-final | **负向回归夹具**（round8/9 由 `round-stage-b` 改名而来，用于证明契约闸门会触发）；非待解锁任务 |
| round12 / round13 | PM-01 / EX-03 有 `E_READ_DOMAIN_VIOLATION` | 二者均**未进入聚合**（answer-final 的 `aggregation_inputs` 全 valid），违规系被否决席位的预期记录 |
| round14–round33 `seat-check.json` | AD-01 `E_MISSING_SEAT`（sha256=null） | **过期的 stage-B 校验残留**：这些轮次均已产出 answer-final 且已签署，AD-01 实际存在 → 非当前阻塞 |

**全局核验（排除真实阻塞的依据）：**
- 无任何 `verdict-*.json` 的 `verdict == "fail"`（无待人工覆盖的硬裁判失败）；
- 无任何 `metrics_status == "blocked_by_contract"`；
- 无任何 `answer-final.json` 引用 `seat_check_status != "valid"` 的席位（PM 写入门未被绕过）；
- 30 个含 answer-final 的轮次其 `aggregation_inputs` 全部 `status=valid`。

---

## ③ 无证据指针的结论

按协议 `07 §5.1`，PM 产物 `answer-final.json` **必须含 `aggregation_inputs[]`**（`seat` / `sha256` / `seat_check_status`），以机械化回溯到被契约校验过的席位产出。以下轮次**缺该字段**：

| 轮次 | 问题 | 性质 |
|---|---|---|
| **round1** | `answer-final.json` 无 `aggregation_inputs[]` | 旧版式（早于 P10 引入的强制字段） |
| **round4** | `answer-final.json` 无 `aggregation_inputs[]` | 旧版式 |
| **round5** | `answer-final.json` 无 `aggregation_inputs[]` | 旧版式 |

- 影响：这三轮的最终结论**无法机械化回溯**到「被校验为 valid 的席位产出」，审计链断开（依赖人工阅读 aggregation_note 文本）。
- 覆盖范围：其余 30 轮（round2, 6–7, 11–33）均含 `aggregation_inputs` 且 **全部 `status=valid`** → 结论可回溯。
- 其他层面核查：各 `verdict-*.json` 结论均带 `evidence[]`（如 round1 `verdict-arithmetic.json` 含 spec+answer 指针）；席位产出 `EX-*.json` 无「有结论却空 evidence」的情况。故「无证据指针」问题**仅集中在上述三轮的旧版式 answer-final 元数据结构**，而非结论实质无据。

---

## ④ 翻边率超阈值项

**结论：无超阈值项。**

| 轮次 | flip_rate | 阈值 | 是否告警 |
|---|---|---|---|
| round2 | `null`（D=0，未定义，按协议绝不当 0） | 0.4 | 否 |
| round3 | `0.2` | 0.4 | 否（alert=false） |

- **覆盖缺口（重要）**：33 轮中**仅 2 轮（round2 / round3）落盘了 `metrics.json`**，其余 31 轮未计算 / 未存储翻边率。按协议 `D=0 → null`（缺失 ≠ 干净），这 31 轮**既不能被断言「翻边率正常」，也不能被断言「超阈值」——属未评估状态**，不应据此误判为「无异常」。

---

## 巡检方法学附注（便于复核）

- 「超时」在数据中没有显式 SLA/截止时间字段，故①以「人类裁决闸门未过（pending 且未签署）」作为「未完成项」的操作化定义。
- ② 严格区分了「契约闸门正确生效（健康）」「负向夹具的预期违规」「过期的 stage-B seat-check 残留」三类噪声，仅报告真实阻塞。
- ③ 以协议 §5.1 的 `aggregation_inputs[]` 强制字段作为「结论带证据指针」的机械判据。
- ④ 的阈值与「null 处理」严格取自 `docs/protocol/07 §4.2`。
- 本巡检为**只读**，未对 `migration-package/runs/` 下任何文件做增删改。
