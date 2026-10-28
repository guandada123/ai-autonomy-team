# 测试体系全面审计报告（自动化测试专家团 · 2026-10-28）

- **审计对象**：ai-autonomy-team 的测试体系（回归/门禁/自检/实验判分/CI/覆盖率）
- **证据等级**：**E1（真实执行）**——Phase 2 全部套件在本机实跑、门禁 H 假绿在 /tmp 克隆上破坏性实测；
  浏览器链路（env_probe 判 E3）与被审计对象无关，不影响定级
- **团队**：齐覆盖（结构与用例质量，Phase 1）→ 史克立（E1 实跑取证，Phase 2）→ 陶根因（归因与定级，Phase 3）→ 甄可验（汇编裁决，本报告）
- **方法自限**：Phase 1 未跑套件（由 Phase 2 补）；CI 历史仅回溯 gh 可见 100 条内；G1 跨家族路由为人工项未覆盖

## 一、总裁决

**测试体系本体：结构优秀，运行拓扑断裂。** 用例设计维度（可证伪判据、负样本、预注册、幂等守门）
明显高于同规模脚本项目平均；但**当前自动化触发拓扑实际空转**——CI 因 billing 自 10-24 起一次
未真跑、本地聚合入口 `make selftest` 常红、推送前唯一防线（门禁 H）存在被实验坐实的假绿面。

| 维度 | 判定 |
|---|---|
| 放行 / 打回 | **有条件放行**：核心防线（回归 56/56、门禁 8/8、接手验收 6/6）当前全绿可用；但 P0 三项（F1/F2/F3）必须在 CI 恢复（11-01）前修复，否则恢复后第一天即失去判别力 |
| 明确计数 | 实跑通过：regress **56/56**、门禁 **8/8**、verify_handover **6/6**、spec_conformance OK（盲区 3 张）、孤儿自检 41/41 + 21/21 + 3/3；**失败：make selftest 红**（test_kit 34/35）；**未执行**：make coverage（未跑）、G1 跨家族路由（人工项） |

## 二、结构盘点（Phase 1）

| 层 | 入口 | 项数 | 触发时机 |
|---|---|---|---|
| 全量回归 | `scripts/regress.py` | 56（末项幂等守门） | 门禁 B / CI |
| 发布门禁 | `scripts/pre_release_check.py` | 8 项 A–H | CI / 手动 |
| 接手验收 | `scripts/verify_handover.py` | 6 | 仅手动 |
| 判据自检 | `judges/verify.py --self-test` | 27 | 门禁 C |
| 脚本自检 | `make selftest` 聚合 | new_round 33 / octagon 46 / redact 5 / pre_push 18 / make_backup **10** / test_kit **35** | CI / 手动 |
| 快照自检 | `snapshot_sources --self-test` | 17（仅 prune+catalog） | 仅手动 |
| 实验判分 | `audit/kit/test_kit.py` 35 项 + `paired_compare` 冻结自校验 | — | 仅 selftest |
| 覆盖率 | `make coverage`（tools/coverage_run.sh） | 10-23 基线 73%（已过期 5 天，回归 51→56） | 仅手动 |

**零测试触达的 scripts**：canon.py、field_provenance.py、fsutil.py、inspect_agent_routing.py（文档声明人工）、夹具生成器 ×3、real_recon.py、team_metrics.py、sync_doc_numbers.py（均为合理豁免或间接覆盖；**夹具生成器无自检**意味着"夹具坏了"只能靠下游判据间接暴露）。

## 三、实测结果与两个意外（Phase 2，E1）

1. **`make selftest` 当前红（exit 2）**：test_kit 34/35——label_saturation 断言失败（标注集饱和，
   heldout 全阈值 1.00）。违反 Makefile 自己的设计原则（:142「不把题不合格混进 selftest」、
   :175「label-audit 当前必然命中——如实报告」）→ 聚合门禁常红 = 狼来了。
2. **CI 自 2026-10-24 起 billing 拦截一次未跑**（annotation 原文「job was not started because
   recent account payments have failed」）——不是"4 秒失败无日志"，是 job 根本没启动；
   本地 selftest 常红叠加 = 自动化拓扑空转。
3. **门禁 H 假绿实锤**（/tmp/h-test 四实验）：`--verify` 只比对 `_index.json` 记录哈希 vs 当前
   真源（snapshot_sources.py:312–336），**快照目录副本完全不被读**——污染快照副本 → PASS；
   删快照文件 → PASS；且快照污染后 verify 给的"回滚命令"会把污染字节灌回真源（有害循环）。
4. 其余实跑全绿；item_gate 如实 FAIL（exit 1，与 Makefile 注释一致——不是坏了，是**无测试守护**）；
   三个"孤儿自检"实测全绿（seat_check 41/41、metrics 21/21、check_consistency 3/3）但零自动触发。

## 四、盲区与根因（Phase 1 + Phase 3 合并，10 项缺陷 → 4 根因族）

| 根因族 | 机制 | 涉及 |
|---|---|---|
| **R1 信号通道污染** | 数据/环境状态报告混进"代码坏了才红"的 exit-code 通道 | F1（selftest 常红）、F2（CI billing，外部放大器） |
| **R2 守门员无守卫** | 验证工具只测"该绿时绿"，FAIL 分支（假绿方向）从未被负样本撞过 | F3（H 假绿+有害回滚）、F4（item_gate 零测试）、F9（告警侧分支未验证）、F10（grade 无负样本） |
| **R3 清单过期不同步** | 手写枚举清单（selftest 列表、E/G 扫描目录、计数文字）不派生自真源 | F6（计数漂移 ×4）、F7（E/G 盲区）、F5 修正版（孤儿的是 --self-test 入口而非功能路径）、F10 后半 |
| **R4 运行防护缺失** | 单机时代脚本进入自动化时代 | F8（无 timeout、scratch 固定路径互踩） |

盲区明细（10 条，均带 文件:行 证据，全文见团队报告）：B1 item_gate 零行为测试；B2 门禁 H 判据
本体零测试；B3 三个孤儿 --self-test；B4 门禁 G 非递归只扫 docs 顶层；B5 门禁 E 不含 audit/
outputs/real-runs/；B6 零 timeout 零并发防护；B7 守夜脚本告警侧分支从未验证会红；B8 判分口径
无负样本且 baseline 不在门禁；B9 计数漂移实锤 4 处（verify_handover.py:20、regress.py:102、
Makefile:47、selftest.yml:46）；B10 CI 停摆期低频高后果路径（verify_handover 22% / snapshot --verify 31% /
pre_push_scan 34% 覆盖率最低三处）无任何触发者。

## 五、修复优先级（陶根因定级，含依赖顺序）

| 级 | 项 | 理由 / 不修的后果 | 代价 |
|---|---|---|---|
| **P0-1** | F1：label_saturation 断言从 test_kit 的 exit-code 通道移出，改为报告项（**不修阈值**——饱和是事实，调阈值=改量尺灭告警） | selftest 是一切修复的验证信号源；常红会淹没 11-01 恢复后第一个真回归 | ~15 行 |
| **P0-2** | F2：CI billing 恢复（**账号动作，非代码**）；恢复后第一天手动触发 workflow 并**亲眼确认绿**，不以"没收到失败邮件"为通过依据 | 找回唯一自动触发通道 | 人工 |
| **P0-3** | F3：门禁 H 改**三向比对**（索引记录 ↔ 快照副本实际字节 ↔ 真源，三类差异分别报告）+ **删除裸 `cp -R` 回滚建议，改为 restore 子命令并前置校验副本哈希**；必须带正负样本测试，修完重放 /tmp/h-test 四实验（污染/删文件/三方改全 FAIL，干净 PASS） | 防线反向变攻击面的实证；残余理论洞（三方一致地错）由 git 历史兜底（snapshots 已入库），不加第二锚 | ~60–80 行 |
| **P1-1** | F7：门禁 E 加 audit/、templates/、outputs/；G 改 `rglob` + 根级四文档（D-0053 同款修法） | 性价比全场最高（~6 行堵住不可逆的密钥泄露面） | ~6 行 |
| **P1-2** | F8：regress run() 加 timeout（verify_handover 的 600s 先例）+ scratch 路径加 pid/ts | 防 CI 挂死与并行互踩 flaky | ~15 行 |
| **P1-3** | F4：item_gate 补行为测试（含单席 resolve 已知值；历史上单席路径崩过 0.500→0.000） | 门禁级 FAIL 结论无守护 | ~80–120 行 |
| **P2** | F6：修 4 处计数 + 全库 `grep -rn "50/50\|30 项\|50 项"` 扫尾；F5：Makefile selftest 收编 3 个 --self-test（各秒级）；F9：告警侧 fixture ~30 行；F10：grade 负样本 + baseline 进门禁 compile | 慢性病，按触碰顺手修 | 各小 |

**依赖顺序**：F1 必须最先（否则一切修复的验证信号被常红淹没）→ F3（带正负样本）→ F7/F8/F6/F5 可并行 → CI 恢复后跟进 F4/F9/F10。
**11-01 恢复前完成**：F1、F3、F7、F8（本地全绿）；**恢复后第一天**：手动触发并亲眼确认绿。

## 六、覆盖率评估

**中风险，不紧急**。脚本型项目行覆盖率 ≠ 一切——关键行为大多有行为级断言兜底。但 10-23 基线
（73%，6,049 语句）已过期且无自动刷新；基线自己指出的三个最低分恰是 B10 的低频高后果路径
（verify_handover 22% / snapshot --verify 31% / pre_push_scan 34%）。建议：CI 恢复后把
`make coverage` 挂进 weekly，基线重跑一次；纯手工脚本 0% 是合理豁免（tools/README 已诚实标注）。

## 七、值得保留的优点（防止审计变成只报忧）

可证伪判据成体系（负样本断言"因何失败"regress.py:451,745–747）；阳性样本不依赖活数据
（第 54/55 项全合成）；幂等守门把"套件自污染"变成可判定（regress.py:1084–1092）；
test_kit 与历史 score_v2 的交叉一致性断言（防双口径分叉）；退出码语义全链路消费；
教训回路（L-0008/D-0064/D-0073）真实运转。**本次审计的 4 个根因族里，没有一个能逃过
"预注册 + 负样本"这套既有方法——问题只是它还没被应用到守门员自己身上。**

## 八、本次审计局限

Phase 1 未逐条清点 octagon/redact_report/pre_push_scan/new_round 内部项数；compare_v1.1–v1.3
按同构处理未精读；CI billing 起始日仅回溯到 10-27 实证；D 组实验在 /tmp 克隆（真仓库零改动，
`git status` 干净）。
