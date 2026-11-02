#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aggregate_seats.py —— 多席位作答多数票汇总器（只读输入，纯标准库）

## 它做什么

把 N 份席位作答（`{题号: 值}`）按题做**多数票**，产出一份可直接喂给
`audit/baseline/paired_compare.py --multi` 的作答文件，并把「无多数票」的题单列出来。

## 三个刻意的设计

1. **`null` 不算票**：席位答 `null` 表示"完全无线索"，不是"答案是 null"。
   若把它当票，三席都说不知道反而会"多数票 = 不知道"，看起来像有结论。
2. **比较前先规范化**：与 `paired_compare.grade` 同口径（字符串去空白/小写/`×`→`x`；
   字典按值序转列表）。否则同一答案写成 `70.5×50.5` 与 `70.5x50.5` 会被算成两票分歧。
3. **无多数票 = 题面歧义，不猜**：3 席给出 3 个不同值时不取中位数、不取"最像的"，
   直接记为歧义并**不进多数票结果**（对判分器而言等同未作答=错），另在报告里单列。

## 用法

    python3 audit/baseline/multi/aggregate_seats.py seat-S1.json seat-S2.json seat-S3.json \
        --out audit/baseline/multi/multi-answers.json

退出码：0 正常 ｜ 2 输入有问题（题号不一致 / 文件缺 / 票数少于 3）
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def canon(v):
    """与 paired_compare.grade 的同口径规范化（用于**投票计数**，不用于判分）。"""
    if isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, list):
        return tuple(canon(x) for x in v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    if isinstance(v, str):
        return v.strip().lower().replace("×", "x").replace(" ", "")
    return v


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    ans = d.get("answers", d)
    return {k: v for k, v in ans.items() if not str(k).startswith("_")}, d


def main():
    ap = argparse.ArgumentParser(description="多席位多数票汇总")
    ap.add_argument("seats", nargs="+", help="席位作答 JSON")
    ap.add_argument("--out", required=True, help="输出 multi-answers JSON")
    ap.add_argument("--min-seats", type=int, default=3)
    args = ap.parse_args()

    if len(args.seats) < args.min_seats:
        print(f"[ERR] 席位数 {len(args.seats)} < {args.min_seats}", file=sys.stderr)
        return 2

    loaded = [load(p) for p in args.seats]
    keysets = [set(a) for a, _ in loaded]
    common = set.intersection(*keysets)
    if len(common) == 0:
        print("[ERR] 席位之间题号无交集", file=sys.stderr)
        return 2

    qids = sorted(common, key=lambda q: (q[0], int("".join(c for c in q if c.isdigit()) or 0)))
    multi, ambiguous, detail = {}, [], {}
    for q in qids:
        raw = [a.get(q) for a, _ in loaded]
        votes = [canon(v) for v in raw if v is not None]
        cnt = Counter(votes)
        detail[q] = {"raw": raw, "counts": {str(k): n for k, n in cnt.items()}}
        if not cnt:
            ambiguous.append({"q": q, "reason": "三席均答 null（无票）", "raw": raw})
            continue
        top, n = cnt.most_common(1)[0]
        # ★ 两道闸，缺一不可（实测定出来的）：
        #   ① 有效票 < 2 席 → 不算多数票。否则 S1/S2 弃答、S3 全猜时，
        #      一个猜出来的值会因为「唯一一票」变成多数票——等于让瞎猜统治结果。
        #   ② 多数票要占**实际投票**的过半，否则 3 席 3 个值也是"多数"。
        if len(votes) < 2:
            ambiguous.append(
                {"q": q, "reason": f"有效票仅 {len(votes)} 席（<2），不构成多数票", "raw": raw}
            )
            continue
        if n * 2 <= len(votes):
            ambiguous.append(
                {
                    "q": q,
                    "reason": f"未过半数：最高票 {n}/{len(votes)} 有效票"
                    f"（{len(cnt)} 个不同值、共 {len(loaded)} 席）",
                    "raw": raw,
                }
            )
            continue
        # 取该多数值对应的**原始**写法（保持类型，避免二次规范化影响判分）
        multi[q] = next(v for v in raw if canon(v) == top)

    out = {
        "_note": f"多席位多数票汇总（{len(args.seats)} 席），由 aggregate_seats.py 生成",
        "seats": [str(Path(p).name) for p in args.seats],
        "n_questions": len(qids),
        "n_majority": len(multi),
        "n_ambiguous": len(ambiguous),
        "ambiguous": ambiguous,
        "answers": multi,
        "votes": detail,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[agg] {len(args.seats)} 席 / {len(qids)} 题 → 有多数票 {len(multi)}，"
        f"无多数票 {len(ambiguous)}"
    )
    for a in ambiguous:
        print(f"  ⚠️ {a['q']}: {a['reason']}  原始={a['raw']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
