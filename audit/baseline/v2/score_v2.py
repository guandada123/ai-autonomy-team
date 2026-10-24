#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_v2.py —— v2 判断类题库判分（不是判对错，是判"与参考裁决的关系"）

## 为什么不是判对错

判断类题没有唯一正确答案，只有"与谁一致"。所以主指标是**一致率**，
副指标是**理由覆盖率**，另单列**危险失误**（结论相反且没提到该担心的风险）。

## 四项指标（DESIGN-v2 第二节）

| 指标 | 定义 |
|---|---|
| 一致 | `verdict` 与参考裁决相同 |
| 危险失误 | verdict 相反，且 `top_risk` 未命中该题 top_risk 关键词 |
| 理由覆盖 | `reasons` 命中 key_reasons 的条数 / 总条数（关键词命中，报告里再人工复核） |
| 可裁决性 | 缺 `falsification` → 该题不计分 |

## 两臂

- 单席：`--single arm-M1.json`
- 多席：`--seats a.json b.json c.json`（按 verdict 多数票，≥2 席成票，且过半）

## 用法

    python3 audit/baseline/v2/score_v2.py --single audit/baseline/v2/arm-M1.json \
        --seats audit/baseline/v2/seat-S1.json audit/baseline/v2/seat-S2.json audit/baseline/v2/seat-S3.json

退出码：0 正常 ｜ 2 输入错误
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
NORM = lambda s: re.sub(r"\s+", "", str(s).lower())


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d.get("answers", d)


def hits(kws, text):
    t = NORM(text)
    return any(NORM(k) in t for k in kws)


def grade_one(ans, ref):
    """返回四项判定。ans 为 None 表示未作答。"""
    if not isinstance(ans, dict):
        return {"answered": False, "match": False, "danger": False,
                "coverage": 0.0, "falsifiable": False, "covered": []}
    verdict = str(ans.get("verdict", "")).strip().lower()
    reasons = " ".join(map(str, ans.get("reasons") or []))
    risk = str(ans.get("top_risk") or "")
    fals = str(ans.get("falsification") or "").strip()

    covered = [kr["r"] for kr in ref["key_reasons"] if hits(kr["kw"], reasons)]
    coverage = len(covered) / len(ref["key_reasons"]) if ref["key_reasons"] else 0.0
    match = verdict == ref["verdict"]
    danger = (not match) and (not hits(ref["top_risk"]["kw"], risk))
    return {"answered": True, "verdict": verdict, "match": match, "danger": danger,
            "coverage": round(coverage, 3), "covered": covered,
            "falsifiable": bool(fals), "risk_text": risk}


def summarize(name, arm, refs):
    rows, ok, danger, cov, fals = {}, 0, [], [], 0
    for q, ref in refs.items():
        r = grade_one(arm.get(q), ref)
        rows[q] = r
        if not r["falsifiable"]:
            continue                       # 缺证伪条件 → 不计分
        if r["match"]:
            ok += 1
        if r["danger"]:
            danger.append(q)
        cov.append(r["coverage"])
        fals += 1
    n = fals
    return {"arm": name, "n": n, "agree": ok,
            "agree_rate": round(ok / n, 3) if n else None,
            "danger": danger,
            "coverage": round(sum(cov) / len(cov), 3) if cov else None,
            "rows": rows}


def majority(seats, qids):
    """按 verdict 多数票：≥2 席成票，且须占有效票过半。"""
    out, amb = {}, {}
    for q in qids:
        votes = [str((s.get(q) or {}).get("verdict", "")).strip().lower() for s in seats]
        votes = [v for v in votes if v in ("do", "dont", "defer")]
        cnt = Counter(votes)
        if len(votes) < 2:
            amb[q] = f"有效票 {len(votes)} 席（<2）"
            continue
        top, n = cnt.most_common(1)[0]
        if n * 2 <= len(votes):
            amb[q] = f"无过半：{dict(cnt)}"
            continue
        # 取该 verdict 席位中理由覆盖最全的一份（原始条目），便于后续复核
        best = max((s[q] for s in seats if (s.get(q) or {}).get("verdict", "").lower() == top),
                   key=lambda a: len(a.get("reasons") or []))
        out[q] = best
    return out, amb


def main():
    ap = argparse.ArgumentParser(description="v2 判分：一致率 / 理由覆盖 / 危险失误")
    ap.add_argument("--single", required=True)
    ap.add_argument("--seats", nargs="+", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    refs = json.loads((HERE / "adjudication.json").read_text(encoding="utf-8"))["items"]
    single = load(args.single)
    seats = [load(p) for p in args.seats]
    multi, amb = majority(seats, list(refs))

    S = summarize("单席", single, refs)
    M = summarize(f"多席多数票（{len(seats)} 席）", multi, refs)

    # 逐席也看一下（判断分歧的来源）
    per = [summarize(Path(p).name, s, refs) for p, s in zip(args.seats, seats)]

    diff = None
    if S["agree_rate"] is not None and M["agree_rate"] is not None:
        diff = round(M["agree_rate"] - S["agree_rate"], 3)

    verdict = None
    if diff is not None:
        verdict = "增益成立" if diff >= 0.10 else ("多席更差" if diff <= -0.10 else "无差异")

    if args.json:
        print(json.dumps({"single": {k: v for k, v in S.items() if k != "rows"},
                          "multi": {k: v for k, v in M.items() if k != "rows"},
                          "per_seat": [{k: v for k, v in p.items() if k != "rows"} for p in per],
                          "delta": diff, "verdict": verdict, "ambiguous": amb},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[v2] 题数 {len(refs)} ｜ 参考裁决来源：用户 2026-10-24 确认")
        print(f"  单席   一致 {S['agree']}/{S['n']} = {S['agree_rate']}  "
              f"理由覆盖 {S['coverage']}  危险失误 {S['danger'] or '无'}")
        print(f"  多席   一致 {M['agree']}/{M['n']} = {M['agree_rate']}  "
              f"理由覆盖 {M['coverage']}  危险失误 {M['danger'] or '无'}")
        for q, why in amb.items():
            print(f"  ⚠️ {q} 未成票：{why}")
        print(f"  差值 {diff:+.3f} → **{verdict}**（判据：≥+0.10 增益 / |差值|<0.10 无差异 / ≤−0.10 更差）")
        print("  逐题（verdict：单席 / 多席 / 参考）：")
        for q in refs:
            s, m = S["rows"][q], M["rows"][q]
            print(f"    {q}: {s.get('verdict', '—'):5s} / {m.get('verdict', '—'):5s} / "
                  f"{refs[q]['verdict']:5s}  覆盖 {s['coverage']:.2f} vs {m['coverage']:.2f}"
                  f"{'  ⚠️危险失误' if m['danger'] else ''}")
        print(f"\n  逐席一致数：{[(p['arm'], p['agree'], p['n']) for p in per]}")
        print("  ⚠️ 6 题只能看方向，不能判显著：单题 = 16.7pp，容不下 10pp 的判据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
