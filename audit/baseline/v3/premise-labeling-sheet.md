# 36 条校准集 · 盲标表（只看 card）

## 为什么只给 card

前提探针实际能看到的**只有 `card` 这两个字段**（`beneficiary` / `metric`）。
原始校准集里还有一列 `note`，那是原标注者写下的判断依据——给你看就等于给答案。
所以本表把 `note` 剔除了：你看到的与探针看到的完全一样。

## 怎么标

标准只有一条：**这张卡描述的前提如果不成立，该不该拦住这轮开工？**

- 该拦 → 填 `拦`
- 不该拦 → 填 `过`

> 提示：`metric` 为空、或受益人写的是「我们团队自己用」「机制本身」，都属常见情形，
> 不必因为它们看起来简陋就一律判「拦」——你要判断的是**前提是否成立**，不是**写得是否漂亮**。

| # | id | beneficiary | metric | 你的判定 |
|---|---|---|---|---|
| 1 | T01 | 我们团队自己用 | {} |  |
| 2 | T02 | 机制本身 | {} |  |
| 3 | T03 | 投资决策的准确性 | {"name": "更准", "baseline": null, "method": null} |  |
| 4 | T04 | 维护者 | {} |  |
| 5 | T05 | 研究机制的人 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 6 | T06 | 大家 | {} |  |
| 7 | T07 | 系统 | {} |  |
| 8 | T08 | 运维同学 | {"name": "稳定性", "baseline": null, "method": "看情况"} |  |
| 9 | T09 | 运维同学 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 10 | T10 | （空） | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 11 | N01 | 我，每天开盘前核对账本与券商截图时 | {"name": "对账残差", "baseline": 33.09, "method": "real_recon 逐仓残差"} |  |
| 12 | N02 | 看实盘卡片的我，以及任何接手此看板的人 | {"name": "死读字段数", "baseline": 4, "method": "field_provenance --consume… |  |
| 13 | N03 | 每天 8 点看日报的人，若显示日 ¥0 会误判 | {"name": "被抹成 0 的次数", "baseline": 0, "method": "单测 + 盘前实跑"} |  |
| 14 | N04 | 审计者与决策者，算错一次就污染证据链 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 15 | N05 | 要选型的人，选错要返工两周 | {"name": "候选维护活跃度", "baseline": "issue 中位 9.4 天", "method": "GitHub AP… |  |
| 16 | N06 | 误删真源的人，没有快照就只能重写 | {"name": "可恢复文件数", "baseline": 69, "method": "snapshot_sources --verif… |  |
| 17 | N07 | 散户投资者（每月查看对账单的 3 个账户） | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 18 | N08 | 负责发布的人 | {"name": "失败次数", "baseline": 0, "method": "pre_release_check 每日一次"} |  |
| 19 | N09 | 核对账本的人 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 20 | N10 | 团队里的审计角色 AD-01，它需要在每轮回答三条前提 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 21 | T11 | ["我", "团队"] | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 22 | T12 |     | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 23 | T13 | （空） | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 24 | T14 | 团队发布值班 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 25 | T15 | 老张 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 26 | T16 | 负责夜间发布的值班同学 | {"name": "发布耗时", "baseline": 0, "method": ""} |  |
| 27 | T17 | 负责夜间发布的值班同学 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 28 | T18 | 负责夜间发布的值班同学 | {"name": "错误决策数", "baseline": 0, "method": "每月从 decisions/ 统计被推翻条数"} |  |
| 29 | N11 | 团队发布值班人 | {"name": "夜间发布失败次数", "baseline": 0, "method": "CI 失败记录"} |  |
| 30 | N12 | 公司财务岗的会计 | {"name": "对账残差", "baseline": 33.09, "method": "逐仓残差"} |  |
| 31 | N13 | SRE on-call（夜间值班） | {"name": "告警响应时长", "baseline": "15 分钟", "method": "飞书告警时间戳"} |  |
| 32 | N14 | 核对账本的人 | {"name": "周返工次数", "baseline": "0 次/周", "method": "CI 失败记录"} |  |
| 33 | N15 | 误删真源的人 | {"name": "可恢复文件数", "baseline": 69, "method": "snapshot_sources --verif… |  |
| 34 | N16 | 要选型的人 | {"name": "候选维护活跃度", "baseline": "issue 中位 9.4 天", "method": "GitHub AP… |  |
| 35 | N17 | 接手此仓库的人 | {"name": "覆盖率", "baseline": 73, "method": "tools/coverage_run.sh", "so… |  |
| 36 | N18 | 下一个接入这套评审协议的项目，它的接手者 | {"name": "跨项目复用需要改的字段数", "baseline": 6, "method": "逐个项目试挂载"} |  |

## 标完之后

把最后一列回给我，例如 `1拦 2过 3拦 …`。我做三件事：
1. 与你标的结果 vs 现有 18 正 18 负，算一致率与 Cohen's Kappa；
2. 用你的标注重算阈值，检验原阈值是否被推翻；
3. 按新口径改写 D-0052，注明标注者是你、标注日期、以及是否盲标。
