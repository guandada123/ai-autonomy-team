#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paired_compare.py —— 配对比较：多席位 vs 单 agent（只读，纯标准库）

## 为什么用配对而不是两样本

两臂答**同一批 27 题**，所以比的是"逐题差异"，不是"两个独立通过率"。
配对设计只看**不一致对**（discordant pairs）：

    b = 单 agent 错、多席位对（多席位赢的题）
    c = 单 agent 对、多席位错（多席位输的题）
    非劣效成立 ⟺ (b − c) / n ≥ −margin

按 margin=5pp、n=27：允许净输 0.05×27 = 1.35 题 → **c − b ≤ 1** 即成立。

→ 同一批题既能当参照又能当实验组，**不需要扩到 59 题**（那是独立两样本算出来的）。

## 用法

    python3 audit/baseline/paired_compare.py --multi multi-answers.json
    python3 audit/baseline/paired_compare.py --multi x.json --margin 0.05 --json

multi-answers.json 形如 {"T02": 35, "Q22": 4, ...}（缺题按"未作答=错"处理并单独列出）
"""

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load_json(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def norm(v):
    """比较用规范化：字符串去空白转小写；数值原样。"""
    if isinstance(v, str):
        return v.strip().lower().replace("×", "x").replace(" ", "")
    return v


def grade(answer, truth):
    """返回 True/False。truth 为 None 表示该题作废（不参与）。

    ★ 必须与**冻结的判分脚本** `judge_baseline.py` 同口径，否则基准就不是冻结那个数：
      - 字典作答（如 {"pct":73,"stmts":6049}）对应列表真值 → 按有序值比；
      - 数值按相对误差 ≤0.5%。
    """
    if truth is None:
        return None
    if isinstance(answer, dict) and isinstance(truth, list):
        answer = list(answer.values())
    if isinstance(truth, list) and isinstance(answer, list):
        return len(answer) == len(truth) and all(grade(a, t) for a, t in zip(answer, truth))
    if isinstance(truth, (int, float)) and isinstance(answer, (int, float)):
        return abs(answer - truth) <= max(abs(truth) * 0.005, 1e-9)
    return norm(answer) == norm(truth)


def main():
    ap = argparse.ArgumentParser(description="配对比较：多席位 vs 单 agent")
    ap.add_argument("--multi", required=True, help="多席位作答 JSON（{题号: 答案}）")
    ap.add_argument("--margin", type=float, default=0.05, help="非劣效 margin（默认 5pp）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # 单一真源：冻结的两份真值 + 两轮盲答（**只读，不修改冻结文件**）
    gt1 = load_json("ground-truth-20261024.json")
    gt2 = load_json("ground-truth-round2.json")
    single = {}
    single.update(
        {
            k: v
            for k, v in load_json("single-agent-answers-20261024.json").items()
            if k.startswith("T")
        }
    )
    single.update(load_json("answers-round2-blind.json")["answers"])

    frozen = load_json("frozen-v1.json")  # 作废清单的单一真源
    truth = {}
    for k, v in list(gt1.items()) + list(gt2.items()):
        if not str(k).startswith(("T", "Q")):
            continue
        # ★ 作废清单只认**冻结锚点**（单一真源），不在两处真值文件里各判一次：
        #   实测踩过——T01 的 verdict 写成「excluded：题面未声明口径」，
        #   用 == "excluded" 判不出来，导致 n=28（正确 27）、单 agent 对 21（正确 20）。
        truth[k] = None if k in frozen["excluded"] else v.get("v")

    multi = (
        load_json(Path(args.multi).name)
        if Path(args.multi).parent == BASE
        else json.loads(Path(args.multi).read_text(encoding="utf-8"))
    )

    rows, b, c, both_ok, both_bad, void, missing = [], 0, 0, 0, 0, 0, []
    for qid in sorted(truth):
        t = truth[qid]
        if t is None:
            void += 1
            continue
        if qid not in multi:
            missing.append(qid)
            m_ok = False
        else:
            m_ok = grade(multi[qid], t)
        s_ok = grade(single.get(qid), t)
        if s_ok and m_ok:
            both_ok += 1
        elif not s_ok and m_ok:
            b += 1
        elif s_ok and not m_ok:
            c += 1
        else:
            both_bad += 1
        rows.append(
            {
                "q": qid,
                "single": s_ok,
                "multi": m_ok,
                "delta": (
                    "赢"
                    if m_ok and not s_ok
                    else "输"
                    if s_ok and not m_ok
                    else "同对"
                    if s_ok
                    else "同错"
                ),
            }
        )

    n = b + c + both_ok + both_bad
    # ★ 自校验：本工具算出的"单 agent 成绩"必须复现冻结记录（20/27）。
    #   判定器自身坏了却不报警 = 最危险的那类腐坏；宁可在这里硬退出。
    single_pass = both_ok + c
    expect = frozen["combined"]["pass"]
    if n != frozen["combined"]["valid_n"] or single_pass != expect:
        print(
            f"[ERR] 判定器与冻结记录不一致：算得 {single_pass}/{n}，"
            f"冻结为 {expect}/{frozen['combined']['valid_n']}——工具本身有问题，拒绝出结论",
            file=sys.stderr,
        )
        return 2
    diff = (b - c) / n if n else 0.0
    ok = diff >= -args.margin
    res = {
        "n": n,
        "b": b,
        "c": c,
        "both_ok": both_ok,
        "both_bad": both_bad,
        "void": void,
        "missing": missing,
        "diff": round(diff, 4),
        "margin": args.margin,
        "non_inferior": ok,
        "verdict": ("非劣效成立" if ok else "非劣效不成立"),
        "note": (
            "零个不一致对 = 零信息：只能说未观察到差异，不能宣称相等" if (b == 0 and c == 0) else ""
        ),
        "rows": rows,
    }

    if args.json:
        print(
            json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False, indent=2)
        )
    else:
        print(f"[paired] n={n}  单agent对={both_ok + c}  多席位对={both_ok + b}")
        print(f"  一致：都对 {both_ok} / 都错 {both_bad}")
        print(f"  不一致：b（多席位赢）={b}  c（多席位输）={c}")
        print(f"  差值 (b−c)/n = {diff:+.3f}   判据 ≥ −{args.margin:.2f} → **{res['verdict']}**")
        if missing:
            print(f"  ⚠️ 多席位未作答 {len(missing)} 题（按错计）：{', '.join(missing[:5])}")
        if res["note"]:
            print(f"  ⚠️ {res['note']}")
        if void:
            print(f"  （另有 {void} 题作废，不参与）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
