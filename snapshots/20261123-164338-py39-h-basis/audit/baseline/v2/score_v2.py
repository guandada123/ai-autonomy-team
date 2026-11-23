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
        return {
            "answered": False,
            "match": False,
            "danger": False,
            "coverage": 0.0,
            "falsifiable": False,
            "covered": [],
        }
    verdict = str(ans.get("verdict", "")).strip().lower()
    reasons = " ".join(map(str, ans.get("reasons") or []))
    krs = ref.get("key_reasons") or []
    risk = str(ans.get("top_risk") or "")
    fals = str(ans.get("falsification") or "").strip()
    require_fals = bool(krs or (ref.get("top_risk") or {}).get("kw"))

    covered = [kr["r"] for kr in krs if hits(kr["kw"], reasons)]
    # 裁决表没有理由（v3 用户只给字母）时，覆盖率记为 None 而不是 0——
    # 把「没测」记成「0 分」是拿缺数据当坏结果，会压低整臂均值。
    coverage = round(len(covered) / len(krs), 3) if krs else None
    # 两侧都归一化再比：席位常写小写 a，裁决表写 A。只在取 verdict 时降一侧，
    # 会得到「26 题全错」这种离谱结果（v3 首跑实测）。
    match = verdict == str(ref["verdict"]).strip().lower()
    tr = ref.get("top_risk") or {}
    danger = (not match) and bool(tr.get("kw")) and (not hits(tr["kw"], risk))
    # 裁决表没有理由/风险（v3：用户只给字母）时，本轮不测「可裁决性」，
    # 不能因为席位没写 falsification 就把整题判成未作答（否则 n=0，分母消失）。
    if not require_fals and not fals:
        fals = "（本轮不测可裁决性）"
    return {
        "answered": True,
        "verdict": verdict,
        "match": match,
        "danger": danger,
        "coverage": coverage,
        "covered": covered,
        "falsifiable": bool(fals),
        "risk_text": risk,
    }


def summarize(name, arm, refs):
    rows, ok, danger, cov, fals = {}, 0, [], [], 0
    for q, ref in refs.items():
        r = grade_one(arm.get(q), ref)
        rows[q] = r
        if not r["falsifiable"]:
            continue  # 缺证伪条件 → 不计分
        if r["match"]:
            ok += 1
        if r["danger"]:
            danger.append(q)
        if r["coverage"] is not None:
            cov.append(r["coverage"])
        fals += 1
    n = fals
    missing = [q for q in refs if q not in arm]
    # 平衡准确率：各类召回的均值。裁决分布偏斜时，raw 准确率会被多数类绑架，
    # 「全答多数类」按构造只能拿 0.50，这样才有可比性。
    by_cls = {}
    for q, r in rows.items():
        if not r["falsifiable"]:
            continue
        c = refs[q]["verdict"]
        by_cls.setdefault(c, [0, 0])
        by_cls[c][1] += 1
        if r["match"]:
            by_cls[c][0] += 1
    recalls = {c: (v[0] / v[1]) for c, v in by_cls.items() if v[1]}
    bal = round(sum(recalls.values()) / len(recalls), 3) if recalls else None
    return {
        "arm": name,
        "n": n,
        "agree": ok,
        "missing": missing,
        "balanced": bal,
        "recalls": recalls,
        "agree_rate": round(ok / n, 3) if n else None,
        "danger": danger,
        "coverage": round(sum(cov) / len(cov), 3) if cov else None,
        "rows": rows,
    }


def majority(seats, qids, valid):
    """按 verdict 多数票：≥2 席成票，且须占有效票过半。

    valid：裁决表里实际出现的标签集合。**不硬编码 do/dont/defer**——
    v3 起用 A/B/C 中性标签，避免「do=做」这种字面联想干扰作答。
    """
    out, amb = {}, {}
    for q in qids:
        votes = [str((s.get(q) or {}).get("verdict", "")).strip().lower() for s in seats]
        votes = [v for v in votes if v in valid]
        cnt = Counter(votes)
        if len(votes) < 2:
            amb[q] = f"有效票 {len(votes)} 席（<2）"
            continue
        top, n = cnt.most_common(1)[0]
        if n * 2 <= len(votes):
            amb[q] = f"无过半：{dict(cnt)}"
            continue
        # 取该 verdict 席位中理由覆盖最全的一份（原始条目），便于后续复核
        best = max(
            (s[q] for s in seats if (s.get(q) or {}).get("verdict", "").lower() == top),
            key=lambda a: len(a.get("reasons") or []),
        )
        out[q] = best
    return out, amb


def main():
    ap = argparse.ArgumentParser(description="v2 判分：一致率 / 理由覆盖 / 危险失误")
    ap.add_argument("--single", required=True)
    ap.add_argument("--seats", nargs="+", required=True)
    ap.add_argument("--json", action="store_true")
    # 第四轮起裁决表可切换：标签对调后参考裁决翻面，但材料与事实不变
    ap.add_argument(
        "--adjudication",
        default="adjudication.json",
        help="裁决表文件名（相对 audit/baseline/v2/）",
    )
    args = ap.parse_args()

    _doc = json.loads((HERE / args.adjudication).read_text(encoding="utf-8"))
    # 撤下的题不计分（V2-02：参考裁决本身待重裁）
    refs = {k: v for k, v in _doc["items"].items() if not v.get("withdrawn")}
    single = load(args.single)
    seats = [load(p) for p in args.seats]
    _valid = {str(v["verdict"]).strip().lower() for v in refs.values()}
    multi, amb = majority(seats, list(refs), _valid)

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
        print(
            json.dumps(
                {
                    "single": {k: v for k, v in S.items() if k != "rows"},
                    "multi": {k: v for k, v in M.items() if k != "rows"},
                    "per_seat": [{k: v for k, v in p.items() if k != "rows"} for p in per],
                    "delta": diff,
                    "verdict": verdict,
                    "ambiguous": amb,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"[v2] 题数 {len(refs)} ｜ 参考裁决来源：用户 2026-10-24 确认")
        print(
            f"  单席   一致 {S['agree']}/{S['n']} = {S['agree_rate']}  "
            f"平衡 {S['balanced']}  理由覆盖 {S['coverage']}  危险失误 {S['danger'] or '无'}"
            + (f"  ⚠️ 缺答 {S['missing']}" if S["missing"] else "")
        )
        print(
            f"  多席   一致 {M['agree']}/{M['n']} = {M['agree_rate']}  "
            f"平衡 {M['balanced']}  理由覆盖 {M['coverage']}  危险失误 {M['danger'] or '无'}"
            + (f"  ⚠️ 缺答 {M['missing']}" if M["missing"] else "")
        )
        for q, why in amb.items():
            print(f"  ⚠️ {q} 未成票：{why}")
        from collections import Counter as _C

        _cnt = _C(r["verdict"] for r in refs.values())
        _mc, _mn = _cnt.most_common(1)[0]
        print(
            f"  多数类基准线：全答 {_mc} = {_mn}/{sum(_cnt.values())} = {_mn / sum(_cnt.values()):.3f}"
            f"（分布 {dict(_cnt)}）—— raw 准确率须与它对比才有意义，平衡准确率下它恒为 0.50"
        )
        if diff is None:
            print("  差值 无法计算（无有效题）——本轮不出结论")
            return 1
        print(
            f"  差值 {diff:+.3f} → **{verdict}**（判据：≥+0.10 增益 / |差值|<0.10 无差异 / ≤−0.10 更差）"
        )
        print("  逐题（verdict：单席 / 多席 / 参考）：")
        for q in refs:
            s, m = S["rows"][q], M["rows"][q]
            # coverage 可能是 None（裁决表没有理由时本轮不测覆盖率），不能直接 .2f
            _f = lambda c: "—" if c is None else f"{c:.2f}"
            print(
                f"    {q}: {s.get('verdict', '—'):5s} / {m.get('verdict', '—'):5s} / "
                f"{str(refs[q]['verdict']):5s}  覆盖 {_f(s['coverage'])} vs {_f(m['coverage'])}"
                f"{'  ⚠️危险失误' if m['danger'] else ''}"
            )
        print(f"\n  逐席一致数：{[(p['arm'], p['agree'], p['n']) for p in per]}")
        _n = len(refs)
        _pp = 100.0 / _n
        _tail = "容不下" if _pp > 10 else "需 2 题净差才够"
        print(f"  ⚠️ {_n} 题只能看方向，不能判显著：单题 = {_pp:.1f}pp，{_tail} 10pp 的判据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
