# tools/ —— 开发期工具（不进产品依赖）

本仓产品代码**零第三方依赖**（D-0032）。这里的工具只在开发机上跑，缺了不影响
`make selftest` / `make check` / 交付包。

## coverage：子进程覆盖率

### 为什么不能直接 `coverage run`

本仓的测试是「父进程起子进程」：`regress.py` 与 `make selftest` 通过 `subprocess`
调 `verify.py`、`seat_check.py`、`new_round.py`……coverage 默认**不追踪子进程**，
直接跑只能看到父进程那几百行，很容易得出"覆盖率 20%"这种误导性结论。

`tools/sitecustomize.py` 是那个钩子：Python 启动时自动 import 它，只要设了
`COVERAGE_PROCESS_START`，每个子进程都会记账，最后 `coverage combine` 合并。

### 怎么跑

```bash
python3 -m venv /tmp/cov-venv
/tmp/cov-venv/bin/pip install coverage
make coverage                 # 或 tools/coverage_run.sh
```

脚本会在 `/tmp/aiteam-coverage/` 落数据；旧数据**改名**保留，不删除
（项目规则：删除的唯一去向是 `_trash/`）。

### 2026-10-23 基线（51 项回归 + 全量自检）

| 指标 | 数值 |
|---|---|
| 统计范围 | `scripts/` + `migration-package/`（不含快照与 `.bak`） |
| 语句总数 | 6,049 |
| 覆盖率 | **73%** |
| 合并的子进程数据文件 | 62 个 |

分文件（≥50% 才列，完整表跑 `make coverage` 看）：

| 文件 | 覆盖 |
|---|---|
| `regress.py` | 97% |
| `octagon.py` | 92% |
| `make_tasks_20.py` / `new_round.py` / `precheck.py` | 86–87% |
| `pre_release_check.py` | 84% |
| `metrics.py` / `verify.py` / `check_docs_consistency.py` | 77–78% |
| `seat_check.py` | 70% |
| `premise_probe.py` | 60%（补第 50 项前为 **0%**） |
| `check_consistency.py` / `redact_report.py` | 64–65% |
| `build_package.py` / `make_backup.py` / `gen_manifest.py` | 51–57% |

**覆盖最低的三处，都是"平时不跑、出事才跑"的路径**：

1. `verify_handover.py` 22% —— 只在接手/换机时跑；
2. `snapshot_sources.py` 31% —— 打快照时跑，回归里只覆盖校验分支；
3. `pre_push_scan.py` 34% —— 只在 `git push` 时跑，回归不触发。

这三处的共同点是「低频 + 高后果」，适合用**独立自检**而不是回归覆盖。

### 已知盲区（量不出来，别当成"没被测"）

- `scripts/` 与 `migration-package/` 有 22 份同名副本；测试跑的是**包内**副本，
  所以 `scripts/new_round.py` 一类会显示 0%，那是**副本**，不是未测试代码。
- 纯手工脚本永远 0%：`real_recon.py`（真账）、`team_metrics.py`（看板）、
  `sync_doc_numbers.py`（`make sync-docs` 触发）、`inspect_agent_routing.py`（换机实测）。
  它们不适合塞进回归，只需要在自己的触发点上自检。

### 这一版基线直接产出的一条修复

`premise_probe.py` 原为 **0%**——L3 前提审查是方案里唯一的新意，却没有任何自动测试，
只有 `--calibrate` 的 20 条**自标**样本（2026-10-23 扩到 36 条，并补了回归第 51 项守着它不退化）。已补回归第 50 项（坏卡命中 3 条 / 好卡 0 命中）。

### 复现与口径（2026-10-29 补，审计 P2）

基线 83 的来源与复现方式（此前未记录，导致数字不可复核）：

```bash
ls /tmp/cov-venv/bin/python || (python3 -m venv /tmp/cov-venv && /tmp/cov-venv/bin/pip install coverage)
PATH="/tmp/cov-venv/bin:$PATH" ./tools/coverage_run.sh    # 内含子进程记账 + combine + gate 比较
```

**口径注意**：
- 基线是**单次实测**（TOTAL 83%，7488 语句 / 1244 未覆盖），不是多次采样中位数。
  `coverage_gate.sh` 用 -2 容差吸收抖动；若将来抖动超过 2pp，应改为 3 次取中位数再抬基线。
- `make coverage` 目标本身要求已装 coverage 的解释器；未装时该目标只打印说明，不静默通过。
- 抬基线前必须先跑出**更高**的 TOTAL，禁止凭估算改 `coverage-baseline.txt`（ratchet 只许上）。
