# 材料包 V2-07 · 覆盖率要不要设阈值门禁

## 情形
项目刚把覆盖率度量接上（此前完全没测过）。第一次跑出全局覆盖率 **73%**，语句 6,049 条。
现在要定：要不要把"覆盖率不得低于某值"设成发布门禁？

## 事实
- 这个仓库的测试**全部是父进程起子进程**（`regress.py` / `make selftest` 通过 `subprocess` 调
  `verify.py` / `seat_check.py` / `new_round.py`）；coverage 默认不追踪子进程，
  直接 `coverage run regress.py` 只能看到 regress 自己那几百行——为此专门写了
  `sitecustomize.py` + `combine` 一套机制才测全
- 基线只测过**一次**，没有第二次、第三次
- 覆盖率与"测的东西对不对"没有直接关系：空断言也能刷高行覆盖

## 请你裁
`do`（设阈值门禁）/ `dont`（只测量、记录基线，不设阈值）/ `defer`
