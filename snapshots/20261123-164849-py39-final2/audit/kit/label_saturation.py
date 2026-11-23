#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""label_saturation.py —— 标注集饱和检查（判断「这套标注能不能验证规则」）。

## 为什么需要它

D-0052 已经用文字写过一句：标注者 = 判据实现者，所以只能证明「判据与标注一致」，
不能证明「判据与真实价值一致」。但这句话一直没有**可测的判据**，直到本文件。

2026-10-24 实测前提探针（`scripts/premise_probe.py`）：

| 集合 | n | 准确率 |
|---|---|---|
| calibration | 36 | **1.00**（TP=18 FP=0 TN=18 FN=0） |
| heldout | 10 | **1.00** |

而且留出集在 **th=3 到 th=20 之间准确率完全不变**（只有 th=30 才掉到 0.80）。

两条结论：
1. **准确率 1.00 = 标签可以被规则完全解释** → 标签极可能是「照规则标出来的」，
   于是它验证的是「实现符合规格」，不是「规格正确」；
2. **留出集对阈值不敏感** → 它抓不到过拟合。一个在 18 个阈值点上都给同一个分数的
   留出集，事实上是第二份训练集。

## 三条判据

| 编号 | 判据 | 说明 |
|---|---|---|
| S1 | 任一集合 best_accuracy == 1.00 | 标签可由规则完全解释，不能作为正确性证据 |
| S2 | 留出集在 ≥5 个相邻阈值上准确率不变 | 该集合对阈值无区分力，不是独立检验 |
| S3 | 校准集与留出集的准确率始终相等 | 两套集合同源，留出没有起到留出作用 |

## 与其它闸门的分工

| 工具 | 查什么 |
|---|---|
| `brief_lint.py` | 题面里有没有写着答案 |
| `item_gate.py` | 一批题还有没有区分度（饱和 / 常数胜出 / 全错） |
| **`label_saturation.py`** | **一套标注能不能验证规则（饱和 / 阈值不敏感 / 同源）** |

## 用法

    python3 audit/kit/label_saturation.py
    python3 audit/kit/label_saturation.py --sweep 3,4,5,6,8,10,12,15,20,25,30
    python3 audit/kit/label_saturation.py --json

退出码：0 三条判据全过（罕见）｜ 1 有命中（当前必然 1）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import premise_probe as pp  # noqa: E402

DEFAULT_SWEEP = [3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30]


def sweep(sets: list[str], ths: list[int]) -> dict:
    """跑 (集合 × 阈值) 的准确率网格。err 表示该阈值下探针抛错。"""
    grid = {}
    for s in sets:
        grid[s] = {}
        for th in ths:
            try:
                grid[s][th] = pp.calibrate(min_chars=th, set_name=s)["accuracy"]
            except Exception as e:  # 不能让一个阈值炸掉整张表
                grid[s][th] = f"ERR:{type(e).__name__}"
    return grid


def invariant_run(series: dict, ths: list[int]) -> int:
    """最长的一段「连续阈值上取值完全相同」的长度。用于 S2。"""
    best = cur = 1
    for i in range(1, len(ths)):
        if series.get(ths[i]) == series.get(ths[i - 1]):
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def evaluate(grid: dict, sets: list[str], ths: list[int]) -> list[dict]:
    """对一张 (集合 × 阈值) 网格跑 S1/S2/S3，返回命中列表。

    为什么抽成函数：判定逻辑若只活在 `main()` 里，测试要验证它就只能再抄一份
    规则（两处实现必然漂移）。CLI 与 test_kit 共用这一份，才是同一把尺。
    """
    hits = []

    # ── S1：任何集合拿到满分 ────────────────────────────────────────────
    for s in sets:
        best = max((v for v in grid[s].values() if isinstance(v, float)), default=None)
        if best is not None and best >= 0.999:
            hits.append(
                {
                    "rule": "S1",
                    "set": s,
                    "msg": f"最佳准确率 {best:.2f} = 满分 → 标签可由规则完全解释，"
                    f"不能作为「规则正确」的证据",
                }
            )

    # ── S2：留出集对阈值不敏感 ──────────────────────────────────────────
    for s in sets:
        run = invariant_run(grid[s], ths)
        if run >= 5:
            hits.append(
                {
                    "rule": "S2",
                    "set": s,
                    "msg": f"在 {run} 个相邻阈值上准确率完全不变 → 该集合抓不到过拟合，"
                    f"事实上不是独立检验",
                }
            )

    # ── S3：两个集合曲线始终相同 ────────────────────────────────────────
    if len(sets) >= 2:
        a, b = sets[0], sets[1]
        same = [th for th in ths if grid[a].get(th) == grid[b].get(th)]
        if len(same) == len(ths):
            hits.append(
                {
                    "rule": "S3",
                    "set": f"{a}/{b}",
                    "msg": f"{len(ths)} 个阈值下两者准确率始终相等 → 两套集合同源，"
                    f"留出没有起到留出作用",
                }
            )
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="标注集饱和检查")
    ap.add_argument("--sets", default="calibration,heldout")
    ap.add_argument("--sweep", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    ths = [int(x) for x in args.sweep.split(",") if x.strip()] if args.sweep else DEFAULT_SWEEP

    grid = sweep(sets, ths)
    hits = evaluate(grid, sets, ths)

    if args.json:
        print(
            json.dumps(
                {"sets": sets, "thresholds": ths, "grid": grid, "hits": hits},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if hits else 0

    print(f"[label-audit] 集合 {sets} ｜ 阈值 {len(ths)} 点 {ths}")
    print(f"{'th':>4}  " + "  ".join(f"{s:>14}" for s in sets))
    for th in ths:
        cells = []
        for s in sets:
            v = grid[s].get(th)
            cells.append(f"{v:.2f}" if isinstance(v, float) else str(v))
        print(f"{th:>4}  " + "  ".join(f"{c:>14}" for c in cells))

    print()
    if hits:
        for h in hits:
            print(f"  🔴 {h['rule']} [{h['set']}] {h['msg']}")
        print("\n★ 有命中不代表脚本坏了，代表**这套标注不足以验证规则**。")
        print("  唯一已知的出路：让独立于实现者的人重新标注（Q03 用户裁 A），再比一次 Kappa。")
    else:
        print("  ✅ 三条判据全过：这套标注具备独立检验能力")

    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
