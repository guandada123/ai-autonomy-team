# D-0011 · 任务参数化：new_round --task 与轮次级 spec 锚定

- **日期**：2026-10-20
- **决策者**：PM-01（用户连续指令「继续」）
- **操作分级**：🟡 中（改机制 → 必须带回归，新增 3 项）
- **状态**：已执行（回归 48/48 连跑两次全绿）

## 决策

让 002（及以后任务）可以真正开轮：

1. `new_round.py` 新增 `--task`（缺省 001，行为不变）：判断项 spec 路径、提示词「你的读取域」段、`round-manifest.json` 的 `task` 字段都按任务解析。
2. `seat_check.py`：`verify_final` 的 spec sha 锚定从 round-manifest 读任务号；裁决模板第 6 节的 verify 命令按 `--fixtures-dir` 解析出本轮任务的 spec 目录（此前写死 `fixtures/spec`，002 的签字人会拿 002 答案跑 001 判据且不报错）。
3. `regress.py` 新增 3 项，幂等守门从 45 移到 48（必须最后跑）：
   - **45** 002 判据可加载（算术 10/10 + 判断层 2 项 pending → exit 3）
   - **46** `--task 002` 提示词只列 002 输入、manifest.task=002（防任务串台 —— D-0010 审计发现的那次 allow 污染事故固化成回归）
   - **47** `--task` 缺省 = 001 行为不变（防回归）
   - **48** 回归幂等守门（原 45）

## 依据

| # | 依据 | 来源 |
|---|---|---|
| 1 | `new_round.py:408` spec 路径写死 `fixtures/spec/adjudication.json`；`:469` allow 全局直拼进提示词 | 代码审查（D-0010 审计发现） |
| 2 | `seat_check.py:1231` spec sha 锚定写死；模板 `:1972` verify 命令写死 `--spec-dir fixtures/spec` | 同上 |
| 3 | 改后实测：`--task 002` 提示词 allow 段 = `fixtures/002/{TASK.md,ledger.csv,bill.csv}`，不含 001 输入；`round-manifest.json.task = "002"` | `_trash/tmp-regress/t002p/` |
| 4 | 缺省行为不变：001 提示词仍是根 fixtures，002 文件未混入 allow 段 | `_trash/tmp-regress/t001p/` |
| 5 | 回归 48/48 连跑两次 exit 0；`build_package.py --check` 22/22 一致 | 本次执行记录 |

## 备选方案与未采用理由

| 备选 | 未采用理由 |
|---|---|
| 把 002 夹具复制进根 `fixtures/` 轮换使用 | 两任务的 truth/spec 互相覆盖，001 的三轮证据链与冻结基线全部作废 |
| seat_check 也加 `--task` 新参数 | 已有 `--spec` / `--fixtures-dir`，够用；从 round-manifest 读任务号是单一真源，少一个可漂移的入口 |
| 不加回归直接改 | 项目铁律：改机制必须带回归；46/47 两条正是把「任务串台」从事故变成可自动判定 |

## 风险边界（🟡）

| 项 | 内容 |
|---|---|
| 影响范围 | `scripts/new_round.py`、`scripts/judges/seat_check.py`、`scripts/regress.py`（同步至包内）；文档项数 45 → 48 |
| 已知残余 | `seat_check.py` 读取域 lint（1477 行附近）仍按 001 基线检查必需禁读项；002 的输入文件未在 allow 里，席位申报读取会被判 unclassified（报告给 AD-01，不判 error）—— 接受，理由：未申报读取本就不可见，宁可少声明也不把两个任务混在一张清单里 |
| 回滚方式 | `_archive/transfer-m4-20261019/ai-autonomy-team/` 整树覆盖 + 重建 MANIFEST |

## 审计发现

- 首跑回归直接崩：`--round t002p`（非纯数字）不加 round 前缀，我按 `t002p/round1/` 找 manifest → FileNotFoundError。与 `--check` 同一规则（C4 类），已在回归里按正确路径取。
- 幂等守门从 45 挪到 48 后文档要跟着改 —— 「项数写死在文档」这个模式第二次制造漂移，protocol/07 的 S7 已改为项数无关表述，README/清单保留具体数字但标注「随版本增长」。
- 001 的裁决记录仍未落盘（2026-10-20 01:05 复查）。

## 修订记录

| 日期 | 改了什么 | 为什么 |
|---|---|---|
| 2026-10-20 | 初版 | 002 接入开轮器 |
