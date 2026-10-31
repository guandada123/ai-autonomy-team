# 引用缺陷台账（2026-11-01）

由可举证率指标查出。原则：**不回溯改写历史报告**（证据不可事后修饰），只立账、可在未来重跑时核销。
判定函数：`caliber_lib.check_item_refs`（闸门与指标共用）。

## A 类：声明了例外命中却未写入 caliber_refs（20 条）

| 报告 | 题号 | 声明的 id | 实际 caliber_refs |
|---|---|---|---|
| R1-post.json | V2-07 | E-003 | C-002 |
| R1-post.json | V2-11 | E-001 | C-003 |
| R1-post.json | V2-12 | E-001 | C-003 |
| R1-post.json | V2-13 | E-001 | C-003 |
| R1-post.json | V2-14 | E-001 | C-003 |
| R1-post.json | V2-15 | E-001 | C-003 |
| R1-post.json | V2-21 | E-002 | C-005 |
| R1-post.json | V2-22 | E-002 | C-005 |
| R1-post.json | V2-24 | E-003 | C-005 |
| R1-post.json | V2-25 | E-002 | C-005 |
| R2-post.json | V2-06 | C-005, E-002, M-002 | C-002 |
| R2-post.json | V2-09 | E-002 | C-002 |
| R2-post.json | V2-11 | E-001 | C-003 |
| R2-post.json | V2-12 | M-001 | C-003, E-001 |
| R2-post.json | V2-13 | E-001 | C-003 |
| R2-post.json | V2-14 | E-001 | C-003 |
| R2-post.json | V2-15 | E-001 | C-003 |
| R2-post.json | V2-23 | E-002 | C-005 |
| R1-caliber-pilot.json | V2-24 | C-002 | C-005, E-003 |
| R2-caliber-pilot.json | V2-24 | M-002 | C-005, C-002, E-003 |

## B 类：引用了例外但未写明命中范围（15 条）

| 报告 | 题号 | 引用的例外 | exception_hit 片段 |
|---|---|---|---|
| xjudge-A.json | N-01 | E-002 | （空） |
| xjudge-A.json | N-02 | E-001 | （空） |
| xjudge-A.json | N-04 | E-002 | （空） |
| xjudge-A.json | N-05 | E-001 | （空） |
| xjudge-B.json | N-01 | E-002 | （空） |
| xjudge-B.json | N-02 | E-001 | （空） |
| xjudge-B.json | N-04 | E-002 | （空） |
| judge-1.json | N-01 | E-002 | （空） |
| judge-1.json | N-02 | E-001 | （空） |
| judge-1.json | N-04 | E-002 | （空） |
| judge-1.json | N-05 | E-003 | （空） |
| judge-2.json | N-01 | E-002 | （空） |
| judge-2.json | N-02 | E-001 | （空） |
| judge-2.json | N-04 | E-002 | （空） |
| judge-2.json | N-05 | E-003 | （空） |

## 处置

- 立账不改报告：历史产出保持原样，缺陷可核销不可抹除。
- 防复发：协议 §2「引用纪律」已写死两条硬要求；`caliber_lib --audit-review` 会逐条点名。
- 未来若重跑这些批次并补齐引用，逐条划掉即可，本文件保留原始记录。
