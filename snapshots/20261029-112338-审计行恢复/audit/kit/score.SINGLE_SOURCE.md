# score.py —— 判分器单一真源（Single Source of Truth）

## 为什么要有这一份

审计 §六-P2 点名：判分器若推广到 Claw / QTS / StockInsight，必须**先定单一真源 + 同步机制**，
否则会在三个仓库各长一份、口径漂移（正是 `L-` 反复警告的「同一语义多副本」）。

**结论**：`audit/kit/score.py` 是本判分器的唯一真源。其它仓库里的 `tools/audit_kit/score.py`
都是**同步副本**，由 `sync_score.sh` 生成，**禁止在副本上直接改**。

## 同步机制

    bash audit/kit/sync_score.sh [目标仓库路径 ...]

- 不传参数时，默认同步到 `docs/../Claw`、`StockInsight`（同层 `WorkBuddy/` 下）；
  `QTS` 当前仓库位置未确认，**不自动同步**，需显式给出路径。
- 每个目标写入 `tools/audit_kit/score.py`，文件头注入来源 commit 与时间戳；
- 同步前校验源文件自包含（仅标准库依赖），避免把隐藏依赖带进别的仓库；
- 副本与真源逐字节一致（头部注释除外），git diff 一眼可辨漂移。

## 版本锚

- 单一真源：`ai-autonomy-team/audit/kit/score.py`
- 当前引用 commit：见各副本头部 `SYNCED FROM @ <commit>`
- 改规则只在本仓库改、跑 `test_kit.py` 钉住数字、**再**跑 `sync_score.sh` 推送。

## 验收（不变量）

- `score.py` 只依赖标准库（`argparse/json/re/pathlib/collections`）——已核实；
- 任何副本与真源的差异只能来自头部注释，正文必须相同；
- 升级规则后，`audit/kit/test_kit.py` 必须先全绿，副本才允许更新。
