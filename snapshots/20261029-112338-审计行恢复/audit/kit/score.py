#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score.py —— 通用判分器（题集无关）。v3 Q10 用户裁 C 的落地件。

## 为什么要抽这一版

`baseline/v2/score_v2.py` 能用，但它跟题集绑死了三处：

1. 裁决表默认读 `audit/baseline/v2/adjudication.json`（写死路径）
2. `key_reasons` / `top_risk` 当作必有字段（v3 只有字母，得靠补丁绕过）
3. 每次只能单跑，重复运行的 pass^k 要另用 item_gate

抽出来以后，判分逻辑与「哪一批题、用什么标签、有没有理由字段」全部解耦。
**验收标准不是"能跑"，是"必须复现 score_v2 的数字"**——两轮历史数据逐项对齐才算通过。

## 题集格式（最简）

    {"items": {"Q01": {"verdict": "A"}, "Q02": {"verdict": "B"}}}

可选字段（有才算，没有就跳过对应指标）：

    {"verdict": "A",
     "key_reasons": [{"r": "理由名", "kw": ["关键词"]}],   # 有 → 算理由覆盖率
     "top_risk":    {"kw": ["关键词"]},                    # 有 → 算危险失误
     "withdrawn":   true}                                  # true → 整题不计分

## 臂格式

    --arm NAME=file.json                    单次
    --arm NAME=f1.json,f2.json,f3.json       同一臂重复 k 次

重复跑时额外报：`pass^1`（各次均值）、`pass^k`（k 次全对）、`自稳`（臂内答案自洽率）。

## 指标

| 指标 | 定义 | 为什么要有 |
|---|---|---|
| 一致率 | verdict 与参考裁决相同 | 主指标 |
| 平衡准确率 | 各类召回的均值 | 裁决分布偏斜时，raw 会被多数类绑架；全答多数类按构造恒为 0.50 |
| 常数基准 | 全答最频繁标签的得分 | **一个不看材料的常数**。臂不如它就没有存在价值 |
| 理由覆盖 | reasons 命中 key_reasons 的比例 | 结论对 ≠ 理由说到点上 |
| 危险失误 | 结论相反且 top_risk 未命中 | 错得离谱与错得合理是两件事 |
| pass^k | k 次重复全对 | 单次通过会高估能力（τ-bench：k=1 0.46 → k=4 0.225） |
| Fleiss κ | 多臂间的一致性（扣除碰巧） | κ≈1 说明几席其实是同一个评委，聚合等于没聚合 |

## 用法

    python3 audit/kit/score.py \
        --items audit/baseline/v3/adjudication-v3.json \
        --arm M1=audit/baseline/v3/arm-M1.json \
        --arm S1=audit/baseline/v3/seat-S1.json \
        --arm S2=audit/baseline/v3/seat-S2.json \
        --arm S3=audit/baseline/v3/seat-S3.json \
        --single M1 --seats S1,S2,S3

退出码：0 正常 ｜ 2 输入错误 ｜ 3 题集为空
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

NORM = lambda s: re.sub(r"\s+", "", str(s).lower())


def load_answers(path: str) -> dict:
    """臂作答文件：允许 {"answers": {...}} 或直接 {...}。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d.get("answers", d)


def load_items(path: str) -> dict:
    """题集：允许 {"items": {...}} 或直接 {...}。撤下的题剔除。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    items = d.get("items", d)
    return {k: v for k, v in items.items() if not v.get("withdrawn")}


def label_of(ans) -> str:
    """取一题的 verdict，统一小写去空白。未知/缺失返回空串。

    ★ 归一化必须在**两侧**都做。只降一侧会得到「全错」这种离谱结果
      （v3 首跑实测：席位写 a、裁决表写 A）。
    """
    if not isinstance(ans, dict):
        return ""
    return str(ans.get("verdict", "")).strip().lower()


def hits(kws, text: str) -> bool:
    t = NORM(text)
    return any(NORM(k) in t for k in (kws or []))


def grade_one(ans, ref: dict) -> dict:
    """单题判定。返回 answered / match / danger / coverage / falsifiable。"""
    krs = ref.get("key_reasons") or []
    tr = ref.get("top_risk") or {}
    if not isinstance(ans, dict):
        return {"answered": False, "verdict": "", "match": False, "danger": False,
                "coverage": None, "falsifiable": False}

    verdict = label_of(ans)
    reasons = " ".join(map(str, ans.get("reasons") or []))
    risk = str(ans.get("top_risk") or "")
    fals = str(ans.get("falsification") or "").strip()

    covered = [kr.get("r", "") for kr in krs if hits(kr.get("kw"), reasons)]
    # 题集没有理由字段时覆盖率记 None，不记 0：把「没测」记成「0 分」会压低整臂均值。
    coverage = round(len(covered) / len(krs), 3) if krs else None

    match = bool(verdict) and verdict == str(ref["verdict"]).strip().lower()
    danger = (not match) and bool(tr.get("kw")) and (not hits(tr["kw"], risk))

    # 「可裁决性」只在题集本身要求时才检查（有理由或风险字段）。
    require_fals = bool(krs or tr.get("kw"))
    falsifiable = True if not require_fals else bool(fals)

    return {"answered": bool(verdict), "verdict": verdict, "match": match,
            "danger": danger, "coverage": coverage, "falsifiable": falsifiable}


def summarize(name: str, arms: dict, refs: dict) -> dict:
    """一个臂的汇总。arms 是 {题号: 作答}（重复跑时由调用方先折叠或逐次传入）。"""
    rows, ok, danger, cov, n = {}, 0, [], [], 0
    for q, ref in refs.items():
        r = grade_one(arms.get(q), ref)
        rows[q] = r
        if not r["falsifiable"]:
            continue                      # 缺证伪条件 → 该题不计分
        n += 1
        if r["match"]:
            ok += 1
        if r["danger"]:
            danger.append(q)
        if r["coverage"] is not None:
            cov.append(r["coverage"])

    by_cls: dict[str, list[int]] = {}
    for q, r in rows.items():
        if not r["falsifiable"]:
            continue
        c = str(refs[q]["verdict"]).strip().lower()
        by_cls.setdefault(c, [0, 0])
        by_cls[c][1] += 1
        if r["match"]:
            by_cls[c][0] += 1
    recalls = {c: round(v[0] / v[1], 3) for c, v in by_cls.items() if v[1]}
    balanced = round(sum(recalls.values()) / len(recalls), 3) if recalls else None

    return {"arm": name, "n": n, "agree": ok, "missing": [q for q in refs if q not in arms],
            "agree_rate": round(ok / n, 3) if n else None,
            "balanced": balanced, "recalls": recalls,
            "coverage": round(sum(cov) / len(cov), 3) if cov else None,
            "danger": danger, "rows": rows}


def summarize_repeat(name: str, runs: list[dict], refs: dict) -> dict:
    """同一臂重复 k 次的汇总：pass^1 / pass^k / 自稳。

    自稳 = 该题在 k 次里答案始终相同的比例。**它跟对错无关**：
    自稳低说明题在它眼里有歧义，应该改题面，而不是判它「答错」。
    """
    per_run = [summarize(f"{name}#{i + 1}", r, refs) for i, r in enumerate(runs)]
    p1 = round(sum(x["agree_rate"] or 0 for x in per_run) / len(per_run), 3)

    pk_hits, stable, detail = 0, 0, {}
    for q in refs:
        gold = str(refs[q]["verdict"]).strip().lower()
        votes = [label_of(r.get(q)) for r in runs]
        if all(v == gold for v in votes):
            pk_hits += 1
        kinds = {v for v in votes if v}
        if len(kinds) <= 1:
            stable += 1
        else:
            detail[q] = "".join(v.upper() or "·" for v in votes)

    n = len(refs)
    runs_rates = [x["agree_rate"] for x in per_run]
    return {"arm": name, "k": len(runs), "n": n,
            "pass_at_1": p1,
            "pass_at_k": round(pk_hits / n, 3) if n else None,
            "stability": round(stable / n, 3) if n else None,
            "unstable": detail,
            # 单次成绩的波动区间：直接看出「这个数能不能当基线」
            "spread": [min(runs_rates), max(runs_rates)] if runs_rates else None,
            "per_run": [{k: v for k, v in x.items() if k != "rows"} for x in per_run]}


def decide(votes: list[str], valid: set[str]) -> str | None:
    """把一组票裁决成一个标签：**≥2 票且严格过半**，否则返回 None（未成票）。

    这是全项目**唯一**的多数票实现。`score.py` 与 `item_gate.py` 都调它。
    历史上的 `baseline/v2/score_v2.py` 保留自己那一份——它是产出过被引用数字的
    历史脚本，改它会让那些数字失去可复现性；改由 `test_kit.py` 的交叉验证
    保证两者口径一致（任何一处改了规则，交叉验证立刻红）。

    「未成票」必须与「答错」分开：2 席各投 1 票不构成多数，按答错计会凭空
    制造一次失败，也会让分母悄悄变大。
    """
    counted = [v for v in votes if v in valid]
    if len(counted) < 2:
        return None
    top, cn = Counter(counted).most_common(1)[0]
    return top if cn * 2 > len(counted) else None


def resolve(votes: list[str], valid: set[str]) -> str | None:
    """把一组票解析成一个标签：**单票直接采用，多票走多数制**。

    与 `decide` 的区别只在「1 票」这个边界：
      · `decide` 是纯多数函数，1 票返回 None（多数制在 1 票上没有意义）
      · `resolve` 是「一个臂给出答案」的语义，1 票就是那个答案

    为什么必须分开：`item_gate` 允许只给一个文件的臂（单席），
    若也走多数制，单席会每道题都判「未成票」→ 分数直接变 0。
    实测踩过：重构时把单席误接到决定多数上，单席从 0.500 掉到 0.000。
    """
    counted = [v for v in votes if v in valid]
    if not counted:
        return None
    if len(counted) == 1:
        return counted[0]
    return decide(votes, valid)


def majority_arms(arms: list[dict], qids, valid: set[str]):
    """跨臂多数票：≥2 票且过半才成票，否则记为未成票（不计分母）。

    取多数派里理由写得最全的那份原始作答，便于后续复核。
    """
    out, amb = {}, {}
    for q in qids:
        votes = [label_of(a.get(q)) for a in arms]
        counted = [v for v in votes if v in valid]
        win = decide(votes, valid)
        if win is None:
            amb[q] = (f"有效票 {len(counted)} 席（<2）" if len(counted) < 2
                      else f"无过半：{dict(Counter(counted))}")
            continue
        out[q] = max((a[q] for a in arms if label_of(a.get(q)) == win),
                     key=lambda x: len(x.get("reasons") or []))
    return out, amb


def fleiss_kappa(rater_rows: list[dict], qids) -> float | None:
    """Fleiss' κ：多评分者、多类别的一致性（已扣除碰巧一致）。

    P_i = (Σ_j n_ij² − m) / (m(m−1))         每个题目的评分者间一致度
    P̄   = mean(P_i)
    p_j = 每题该类别占比的均值
    P_e = Σ_j p_j²                            碰巧一致的概率
    κ   = (P̄ − P_e) / (1 − P_e)

    κ 高说明这些"席"其实在给同一个答案——**聚合等于没聚合**。
    返回 None 表示样本不足（少于 2 题或少于 2 席）。
    """
    m = len(rater_rows)
    if m < 2:
        return None
    usable = []
    for q in qids:
        votes = [label_of(r.get(q)) for r in rater_rows]
        votes = [v for v in votes if v]
        if len(votes) < 2:
            continue                          # 弃答的题不参与（不能当成一个类别）
        mq = len(votes)
        usable.append((Counter(votes), mq))
    if len(usable) < 2:
        return None

    p_sum, cat_tot, total = 0.0, Counter(), 0
    for cnt, mq in usable:
        p_sum += (sum(c * c for c in cnt.values()) - mq) / (mq * (mq - 1))
        for c, v in cnt.items():
            cat_tot[c] += v
        total += mq
    p_bar = p_sum / len(usable)
    p_e = sum((v / total) ** 2 for v in cat_tot.values())
    if abs(1 - p_e) < 1e-12:
        return None
    return round((p_bar - p_e) / (1 - p_e), 3)


def kappa_band(k: float | None) -> str:
    """Landis & Koch 的经验区间，仅作阅读辅助，不是判据。"""
    if k is None:
        return "样本不足"
    if k < 0.0:
        return "差于随机"
    if k < 0.20:
        return "极低（几乎独立）"
    if k < 0.40:
        return "偏低"
    if k < 0.60:
        return "中等"
    if k < 0.80:
        return "较高"
    return "接近同一评委"


def main() -> int:
    ap = argparse.ArgumentParser(description="通用判分器：一致率 / 平衡准确率 / 常数基准 / pass^k / Fleiss κ")
    ap.add_argument("--items", required=True, help="题集（含参考裁决）")
    ap.add_argument("--arm", action="append", required=True,
                    metavar="NAME=FILE[,FILE...]", help="臂；逗号分隔表示同一臂重复跑")
    ap.add_argument("--single", help="哪一臂是单席（用于报差值）")
    ap.add_argument("--seats", help="哪几臂组成多席（逗号分隔，做多数票）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    refs = load_items(args.items)
    if not refs:
        print("[ERR] 题集为空", file=sys.stderr)
        return 3

    parsed: dict[str, list[dict]] = {}
    for spec in args.arm:
        if "=" not in spec:
            print(f"[ERR] --arm 需要 NAME=FILE 形式，收到 {spec!r}", file=sys.stderr)
            return 2
        name, files = spec.split("=", 1)
        paths = [p for p in files.split(",") if p]
        if not paths:
            print(f"[ERR] {name} 没有给文件", file=sys.stderr)
            return 2
        parsed[name] = [load_answers(p) for p in paths]

    valid = {str(v["verdict"]).strip().lower() for v in refs.values()}
    qids = list(refs)

    # ── 逐题多数类基准：不看材料的常数 ───────────────────────────────────
    dist = Counter(str(v["verdict"]).strip().lower() for v in refs.values())
    const_label, const_hits = dist.most_common(1)[0]
    const_acc = round(const_hits / len(refs), 3)

    # ── 每臂汇总 ────────────────────────────────────────────────────────
    arms_out = {}
    for name, runs in parsed.items():
        if len(runs) == 1:
            arms_out[name] = {k: v for k, v in summarize(name, runs[0], refs).items() if k != "rows"}
        else:
            arms_out[name] = summarize_repeat(name, runs, refs)

    # ── 多席多数票（用各臂第 1 次；重复跑时明确标注用的是哪一次）─────────
    multi, amb = None, {}
    seat_names = [s for s in (args.seats.split(",") if args.seats else []) if s]
    used_run = 1
    if seat_names:
        missing = [s for s in seat_names if s not in parsed]
        if missing:
            print(f"[ERR] --seats 里这些臂没定义：{missing}", file=sys.stderr)
            return 2
        multi, amb = majority_arms([parsed[s][0] for s in seat_names], qids, valid)
        multi_sum = {k: v for k, v in summarize(f"多席多数票（{len(seat_names)} 席）", multi, refs).items()
                     if k != "rows"}

    # ── Fleiss κ：跨席一致性（揭示"几席其实是同一个评委"）───────────────
    k_seats = fleiss_kappa([parsed[s][0] for s in seat_names], qids) if seat_names else None

    # ── 差值 ────────────────────────────────────────────────────────────
    delta = verdict = None
    if args.single and multi is not None:
        s_rate = arms_out[args.single].get("agree_rate")
        m_rate = multi_sum["agree_rate"]
        if s_rate is not None and m_rate is not None:
            delta = round(m_rate - s_rate, 3)
            verdict = "增益成立" if delta >= 0.10 else ("多席更差" if delta <= -0.10 else "无差异")

    if args.json:
        out = {"n_items": len(refs), "distribution": dict(dist),
               "constant_baseline": {"label": const_label, "rate": const_acc},
               "arms": arms_out, "multi": multi_sum if multi is not None else None,
               "ambiguous": amb, "delta": delta, "verdict": verdict,
               "fleiss_kappa_seats": k_seats, "seat_run_used": used_run}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"[kit] 题数 {len(refs)} ｜ 参考分布 {dict(dist)}")
    print(f"  常数基准：全答 {const_label.upper()} = {const_hits}/{len(refs)} = {const_acc:.3f}"
          f"（臂低于它 = 不如一条不看材料的规则）")
    for name, a in arms_out.items():
        if "k" in a:
            print(f"  {name:<6} pass^1 {a['pass_at_1']:.3f} ｜ pass^{a['k']} {a['pass_at_k']:.3f}"
                  f" ｜ 自稳 {a['stability']:.3f}"
                  + (f"  ⚠️ 抖动 {len(a['unstable'])} 题：{a['unstable']}" if a["unstable"] else ""))
        else:
            print(f"  {name:<6} 一致 {a['agree']}/{a['n']} = {a['agree_rate']}"
                  f" ｜ 平衡 {a['balanced']} ｜ 理由覆盖 {a['coverage']}"
                  f" ｜ 危险失误 {a['danger'] or '无'}"
                  + (f"  ⚠️ 缺答 {a['missing']}" if a["missing"] else ""))

    if multi is not None:
        print(f"  多席   一致 {multi_sum['agree']}/{multi_sum['n']} = {multi_sum['agree_rate']}"
              f" ｜ 平衡 {multi_sum['balanced']} ｜ 理由覆盖 {multi_sum['coverage']}"
              f"  （用各席第 {used_run} 次作答）")
        for q, why in amb.items():
            print(f"    ⚠️ {q} 未成票：{why}")
        if k_seats is not None:
            print(f"  Fleiss κ（{len(seat_names)} 席）= {k_seats:.3f} → {kappa_band(k_seats)}"
                  f"　★ κ 越高，聚合越接近「把同一个答案数两遍」")
        if delta is None:
            print("  差值 无法计算（无有效题）——本轮不出结论")
            return 1
        print(f"  差值 {delta:+.3f} → **{verdict}**"
              f"（判据：≥+0.10 增益 / |Δ|<0.10 无差异 / ≤−0.10 更差）")
        if multi_sum["agree_rate"] is not None and multi_sum["agree_rate"] < const_acc:
            print(f"  🔴 多席低于常数基准（{multi_sum['agree_rate']} < {const_acc}）："
                  f"这批题上聚合没有产生信息，先查题再谈席位")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
