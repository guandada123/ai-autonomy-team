#!/usr/bin/env python3
"""题目准入闸：在出分之前先判「这道题还有没有用」。

## 为什么需要它

v2 的 26 道题四臂全对（D-0063），v3 的 10 道题常数基准 0.700 高过所有臂（D-0064）。
两次事故的共同点：**没有人检查题目本身还有没有区分度**，所有人都在算分数。

这两个教训来自公开 benchmark 的做法：
- BIG-Bench Hard 只收「强基线打不过人类平均」的题 —— 先筛题，再算分；
- τ-bench 用 pass^k 而不是 pass@1 —— 单次通过会高估能力。

## 它判三件事

| 判据 | 含义 | 处置 |
|---|---|---|
| `saturated` | 所有臂都答对 → 题目无法区分任何臂 | 退役或加难度 |
| `all_wrong` | 所有臂都答错 → 可能是题坏了，不是模型差 | 查题面/真值 |
| `constant_wins` | 「全答某一项」的常数基准 ≥ 最好的臂 | 该臂不如一条常数规则，分数不可用 |

另加 `pass^k`：同一臂跑多次时，`pass^1` 是各次平均，`pass^k` 是「k 次全对」的比例。
两者差距大 = 成绩靠运气。

## 用法

    python3 audit/kit/item_gate.py \
        --adjudication audit/baseline/v3/adjudication-v3.json \
        --arm single=audit/baseline/v3/arm-M1.json \
        --arm seats=audit/baseline/v3/seat-S1.json,audit/baseline/v3/seat-S2.json,audit/baseline/v3/seat-S3.json

同一臂多次重跑（算 pass^k）：

    --arm seats=r1.json,r2.json,r3.json --as-repeat

退出码：0 = 全部通过准入；1 = 有题不通过（可用于门禁或 pre-flight）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_answers(path: str) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d.get("answers", d)


def verdict_of(ans) -> str:
    if not isinstance(ans, dict):
        return ""
    return str(ans.get("verdict", "")).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser(description="题目准入闸：饱和 / 全错 / 常数胜出 / pass^k")
    ap.add_argument("--adjudication", required=True)
    ap.add_argument("--arm", action="append", required=True,
                    help="name=path[,path...]；多个 path 且加 --as-repeat 时算 pass^k")
    ap.add_argument("--as-repeat", action="store_true",
                    help="把同一臂的多个文件当作重复运行，而不是多个席位")
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args()

    doc = json.loads(Path(args.adjudication).read_text(encoding="utf-8"))
    refs = {q: v for q, v in doc["items"].items() if not v.get("withdrawn")}

    groups: dict[str, list[dict]] = {}
    for spec in args.arm:
        if "=" not in spec:
            raise SystemExit(f"--arm 需要 name=path 形式：{spec}")
        name, paths = spec.split("=", 1)
        groups[name] = [load_answers(p) for p in paths.split(",") if p]

    # ── 逐题判定 ────────────────────────────────────────────────────────────
    # 两种多文件语义必须分开，混用会得出相反的结论（首版就混了）：
    #   · 缺省 = 多个**席位** → 取多数票（≥2 席成票且过半）；凑不出多数记 None（不计分）
    #   · --as-repeat = 同一臂**重复运行** → pass^1 取均值、pass^k 要求 k 次全对
    # 首版把席位当重复运行，再用「全部答对才算 1」统计，等价于「三席全对才得分」，
    # 于是三个席位被按 unanimity 计分（v3 报 0.500，真实多数票是 5/9=0.556），
    # 且这个偏差方向恰好是**低估席位** → 更容易判出「常数胜出」。已修。
    valid_labels = {str(r["verdict"]).strip().lower() for r in refs.values()}
    rows = {}
    for q, ref in refs.items():
        gold = str(ref["verdict"]).strip().lower()
        per, per_repeat = {}, {}
        for name, runs in groups.items():
            votes = [verdict_of(r.get(q)) for r in runs]
            if args.as_repeat and len(votes) > 1:
                hits = [v == gold for v in votes]
                per_repeat[name] = {"pass^1": round(sum(hits) / len(hits), 3),
                                    "pass^k": 1.0 if all(hits) else 0.0,
                                    # 自己跟自己不一致 = 题目在它眼里有歧义，
                                    # 与「答对没答对」是两件事，必须单独记。
                                    "stable": len({v for v in votes if v}) <= 1,
                                    "votes": "".join(v.upper() for v in votes)}
                per[name] = per_repeat[name]["pass^k"]
                continue
            counted = [v for v in votes if v in valid_labels]
            if len(counted) > 1:
                top, cn = Counter(counted).most_common(1)[0]
                # 与 score_v2 同口径：至少 2 票且过半，否则判未成票
                per[name] = (1.0 if top == gold else 0.0) if (len(counted) >= 2 and cn * 2 > len(counted)) else None
            else:
                per[name] = 1.0 if counted and counted[0] == gold else (
                    0.0 if counted else None)
        vals = [v for v in per.values() if v is not None]
        rows[q] = {"gold": gold, "per_arm": per, "pass": per_repeat,
                   "ambiguous": any(v is None for v in per.values()),
                   "unstable": any(not pr["stable"] for pr in per_repeat.values()),
                   "saturated": bool(vals) and all(v == 1.0 for v in vals),
                   "all_wrong": bool(vals) and all(v == 0.0 for v in vals)}

    def acc_of(name: str) -> float:
        """把每题 0/1 平均；未成票的题不计入分母（与 score_v2 一致）。"""
        got = [rows[q]["per_arm"][name] for q in refs if rows[q]["per_arm"][name] is not None]
        return sum(got) / len(got) if got else 0.0

    # ── 常数基准：不看材料、永远答同一个标签 ────────────────────────────────
    n = len(refs)
    dist = Counter(r["verdict"].strip().lower() for r in refs.values())
    const_label, const_hits = dist.most_common(1)[0]
    const_acc = const_hits / n
    arm_acc = {name: acc_of(name) for name in groups}
    best_arm = max(arm_acc, key=lambda k: arm_acc[k]) if arm_acc else None
    best_acc = arm_acc.get(best_arm, 0.0)

    print(f"题数 {n} ｜ 参考分布 {dict(dist)}")
    print(f"常数基准：全答 {const_label} = {const_hits}/{n} = {const_acc:.3f}")
    for name in groups:
        amb = sum(1 for q in refs if rows[q]["per_arm"][name] is None)
        print(f"  {name:<10} {arm_acc[name]:.3f}" + (f"  （未成票 {amb} 题，不计分母）" if amb else ""))

    sat = [q for q, r in rows.items() if r["saturated"]]
    dead = [q for q, r in rows.items() if r["all_wrong"]]
    constant_wins = const_acc >= best_acc

    print(f"\n饱和（所有臂都对，题目已无区分度）：{len(sat)}/{n}")
    if sat:
        print("  " + " ".join(sat))
    if dead:
        print(f"全错（可能是题坏了）：{len(dead)} → {' '.join(dead)}")

    print()
    verdicts = []
    if constant_wins:
        verdicts.append(f"常数基准 {const_acc:.3f} ≥ 最好的臂 {best_arm} {best_acc:.3f}")
        print(f"🔴 常数胜出：{verdicts[-1]}\n   含义：这个臂不如「不看材料永远答同一项」，分数不可用于决策。")
    else:
        print(f"✅ 最好的臂 {best_arm} {best_acc:.3f} 高于常数基准 {const_acc:.3f}")
    if sat:
        print(f"🟡 有 {len(sat)} 道饱和题，应从计分中剔除或加难度后再跑")
    flip = [q for q, r in rows.items() if r["unstable"]]
    if flip:
        print(f"🟡 有 {len(flip)} 道题同一臂重复运行答案会变（题目有歧义，应先改题面）：")
        for q in flip:
            tags = " ｜ ".join(f"{k}:{v['votes']}" for k, v in rows[q]["pass"].items()
                               if not v["stable"])
            print(f"    {q}: {tags}")
    if dead:
        print(f"🔴 有 {len(dead)} 道题所有臂都错，先查题面与真值（不要归因为模型差）")

    rep = {q: r["pass"] for q, r in rows.items() if r["pass"]}
    if rep:
        print("\npass^k（重复运行）：")
        for name in groups:
            p1 = [rows[q]["pass"][name]["pass^1"] for q in rows if name in rows[q]["pass"]]
            pk = [rows[q]["pass"][name]["pass^k"] for q in rows if name in rows[q]["pass"]]
            if p1:
                print(f"  {name:<10} pass^1 {sum(p1)/len(p1):.3f} ｜ pass^k {sum(pk)/len(pk):.3f}"
                      f"（差 {sum(p1)/len(p1) - sum(pk)/len(pk):+.3f}，差距大=靠运气）")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"n": n, "distribution": dict(dist), "constant": {"label": const_label, "acc": const_acc},
             "best_arm": {"name": best_arm, "acc": best_acc}, "constant_wins": constant_wins,
             "saturated": sat, "all_wrong": dead, "unstable": flip, "rows": rows},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n明细：{args.json_out}")

    ok = (not sat) and (not dead) and (not constant_wins)
    print("\n准入判定：" + ("PASS 这批题可用" if ok else "FAIL 先修题，再谈分数"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
