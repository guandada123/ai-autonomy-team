#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_v2_passk.py —— v2 四角色对照实验 pass^k 汇总（D-0083 §四预注册指标的机械实现）。

与 score_v2.py 的分工：score_v2.py 面向「同轮多席多数票」（v1.3/v2 前提卡语义）；
本脚本面向四角色臂（单作答席 R4 + R3 veto 剔除）的跨轮 pass^k 对比。沿用其两条纪律：
缺答记缺不记 0；常数基准同场对照。

臂 M 计分口径：R3 veto=true 的 (题,轮) 记 VOID——不计分不隐没，从分母剔除。

用法: python3 audit/baseline/v2/score_v2_passk.py
退出码：0 正常 ｜ 2 输入/产物缺失
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUNDS = [1, 2, 3]
N_Q = 25


def load_answers(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {qid: str(a.get("verdict", "")).strip().upper() for qid, a in d["answers"].items()}


def load_vetoes(path: Path) -> set:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {str(v["qid"]).strip() for v in d.get("vetoes", []) if v.get("veto")}


def pk(ok_by_round: dict, k: int, denom_by_round: dict | None = None) -> tuple[float, int]:
    if denom_by_round is None:
        denom_by_round = {r: set(range(N_Q)) for r in ROUNDS}
    """k=1: 全部题轮的平均命中；k=3: 连续 3 轮全对的题数占比。"""
    if k == 1:
        hits = sum(len(ok_by_round[r]) for r in ROUNDS)
        denom = sum(len(denom_by_round[r]) for r in ROUNDS)
        return (hits / denom if denom else 0.0), hits
    scored_all = set.intersection(*[set(denom_by_round[r]) for r in ROUNDS])
    all_ok = set.intersection(*[ok_by_round[r] for r in ROUNDS]) & scored_all
    return (len(all_ok) / len(scored_all) if scored_all else 0.0), len(all_ok)


def balanced_recall(rows: dict, truth: dict, scored: dict) -> float | None:
    """平衡准确率：各标签召回的均值（沿 score_v2.py 纪律，抗多数类绑架）。"""
    by_cls: dict[str, list[int]] = {}
    for q, ok in rows.items():
        if q not in scored:
            continue
        c = truth[q]
        by_cls.setdefault(c, [0, 0])
        by_cls[c][1] += 1
        if ok:
            by_cls[c][0] += 1
    if not by_cls:
        return None
    recalls = [v[0] / v[1] for v in by_cls.values() if v[1]]
    return round(sum(recalls) / len(recalls), 3) if recalls else None


def main() -> int:
    ap = argparse.ArgumentParser(description="v2 四角色对照实验 pass^k 汇总")
    ap.add_argument("--multi-name", default="r4",
                    help="臂 M 作答文件名前缀：r4=一审，r4b=二审（默认 r4）")
    ap.add_argument("--out", default="score-summary.json", help="输出文件名（相对本目录）")
    args = ap.parse_args()
    adj = json.loads((HERE / "adjudication-v2.json").read_text(encoding="utf-8"))
    truth = {qid: it["verdict"].strip().upper() for qid, it in adj["items"].items()}
    if len(truth) != N_Q:
        print(f"🔴 裁决表 {len(truth)} 题 != {N_Q}")
        return 2

    const_right = {q for q, v in truth.items() if v == "A"}  # 常数基准（全 A）

    per_round, errs = {}, []
    for r in ROUNDS:
        try:
            s = load_answers(HERE / f"runs/single-r{r}.json")
            m = load_answers(HERE / f"runs/{args.multi_name}-r{r}.json")
            veto = load_vetoes(HERE / f"runs/R3-r{r}.json")
        except FileNotFoundError as e:
            errs.append(str(e))
            continue
        if len(s) != N_Q or len(m) != N_Q:
            errs.append(f"r{r}: S={len(s)} M={len(m)} 作答数不足")
            continue
        s_ok = {q for q in s if s[q] == truth[q]}
        m_scored = {q for q in m if q not in veto}
        m_ok = {q for q in m_scored if m[q] == truth[q]}
        per_round[r] = {"s_ok": s_ok, "m_ok": m_ok, "m_scored": m_scored,
                        "veto": veto, "s_rows": {q: s[q] == truth[q] for q in s},
                        "m_rows": {q: (m[q] == truth[q]) if q in m_scored else None for q in m}}
    if errs:
        print("🔴 输入不完整：", errs)
        return 2

    s_denom = {r: set(per_round[r]["s_rows"]) for r in ROUNDS}
    m_denom = {r: set(per_round[r]["m_scored"]) for r in ROUNDS}
    s1, s1h = pk({r: per_round[r]["s_ok"] for r in ROUNDS}, 1, s_denom)
    s3, s3h = pk({r: per_round[r]["s_ok"] for r in ROUNDS}, 3, s_denom)
    m1, m1h = pk({r: per_round[r]["m_ok"] for r in ROUNDS}, 1, m_denom)
    m3, m3h = pk({r: per_round[r]["m_ok"] for r in ROUNDS}, 3, m_denom)

    sm, ms = [], []  # S对M错 / M对S错（仅计臂 M 未 VOID 的题轮）
    for r in ROUNDS:
        for q in per_round[r]["m_scored"]:
            s_r, m_r = q in per_round[r]["s_ok"], q in per_round[r]["m_ok"]
            if s_r and not m_r:
                sm.append(f"{q}-r{r}")
            elif m_r and not s_r:
                ms.append(f"{q}-r{r}")
    net_m_loss = len(sm) - len(ms)

    bal_s = balanced_recall(per_round[1]["s_rows"] | per_round[2]["s_rows"] | per_round[3]["s_rows"],
                            truth, {q for r in ROUNDS for q in per_round[r]["m_scored"]})
    summary = {
        "pass1": {"single": round(s1, 4), "multi": round(m1, 4), "constant_A": round(len(const_right) / N_Q, 4)},
        "pass3": {"single": round(s3, 4), "multi": round(m3, 4), "constant_A": round(len(const_right) / N_Q, 4)},
        "hits": {"pass1": {"single": s1h, "multi": m1h},
                 "pass3": {"single": s3h, "multi": m3h, "constant_A": len(const_right)}},
        "inconsistent_pairs": {"S_right_M_wrong": sm, "M_right_S_wrong": ms, "net_M_loss": net_m_loss},
        "r3_vetoes_total": sum(len(per_round[r]["veto"]) for r in ROUNDS),
        "r3_vetoes_detail": {f"r{r}": sorted(per_round[r]["veto"]) for r in ROUNDS},
        "balanced_accuracy": {"single": bal_s,
                              "multi": balanced_recall(
                                  {k: v for r in ROUNDS for k, v in per_round[r]["m_rows"].items()},
                                  truth, {q for r in ROUNDS for q in per_round[r]["m_scored"]})},
        "acceptance": ("multi_better" if -net_m_loss >= 2 else
                       "non_inferior" if net_m_loss <= 1 else "inferior"),
    }
    summary["multi_name"] = args.multi_name
    (HERE / args.out).write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
