"""sitecustomize.py —— 覆盖率子进程引导（仅开发期使用，不参与产品运行）。

## 为什么需要它

本仓的测试全部是「父进程起子进程」：`regress.py` / `make selftest` 通过
`subprocess` 调 `verify.py`、`seat_check.py`、`new_round.py`……而 coverage 默认
**不追踪子进程**，直接 `coverage run regress.py` 只能看到 regress 自己那几百行。

Python 启动时会自动 import 名为 `sitecustomize` 的模块（若在 sys.path 上）。
本文件就是那个钩子：只要设置了 `COVERAGE_PROCESS_START`，每个子进程都会开始记账。

## 怎么用

不要直接调用本文件；用 `tools/coverage_run.sh`。
"""
import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:          # 覆盖率是旁路，绝不能因为它挂掉被测进程
        pass
