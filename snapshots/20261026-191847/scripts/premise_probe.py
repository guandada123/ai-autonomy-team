#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""premise_probe.py —— L3 前提审查：三条探针 + 校准（只读，纯标准库）

## 为什么是「字段判定」而不是「语义判定」

最初想用关键词识别自由文本，但那样阈值只能靠感觉调，且**不可证伪**。
改为：提案必须填一张结构化「前提卡」（beneficiary / metric / do_nothing_cost），
探针只判**字段是否合规**。这样：
  - 判定可复现：同输入必同输出；
  - 阈值有物理含义（正文字数、必备键、布尔值），不是玄学；
  - 误报可定位到具体字段，而不是"模型觉得不对"。

## 用法

    python3 scripts/premise_probe.py --card card.json          # 判定单张前提卡
    python3 scripts/premise_probe.py --calibrate               # 跑内置标注集，算 P/R
    python3 scripts/premise_probe.py --calibrate --th 12       # 换阈值重算

退出码：0 无命中 / 1 有命中 / 2 输入错误
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "migration-package"
PROBE_DIR = PKG / "audit" / "probes" / "premise"


def load_probes():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(PROBE_DIR.glob("P-*.json"))]


def _text(v):
    return v.strip() if isinstance(v, str) else ""


def judge(probe, card, min_chars=None):
    """返回 (hit: bool, reason: str)。hit=True 表示“答不出/不合规”。"""
    field = probe["field"]
    rules = probe.get("rules", {})
    val = card.get(field)

    if rules.get("type") == "concrete_subject":
        if not isinstance(val, str) or not val.strip():
            return True, "字段缺失"
        s = _text(val)
        if len(s) < 4:
            return True, f"过短（{len(s)}<4）"
        # 判「去掉泛称后还剩多少字」，而不是总字数：
        # 「负责发布的人」只有 6 字但很具体；「我们团队自己用」7 字却全是泛称。
        bl = rules.get("generic_blacklist") or []
        stripped = s
        for w in bl:
            stripped = stripped.replace(w, "")
        need = min_chars if min_chars is not None else rules.get("min_chars_specific", 6)
        if len(stripped.strip()) < need:
            return True, f"去掉泛称后仅 {len(stripped.strip())} 字（<{need}），无具体受损方"
        return False, "有具体主体"

    if rules.get("type") == "baseline_present":
        if not isinstance(val, dict):
            return True, "字段缺失或类型错"
        miss = [k for k in rules.get("required_keys", [])
                if val.get(k) in (None, "", [], {})]
        if miss:
            return True, f"缺 {'/'.join(miss)}"
        return False, "含 name/baseline/method"

    if rules.get("type") == "comparable_cost":
        if not isinstance(val, dict):
            return True, "字段缺失或类型错"
        miss = [k for k in rules.get("required_keys", []) if val.get(k) in (None, "", [], {})]
        if miss:
            return True, f"缺 {'/'.join(miss)}"
        for k in rules.get("must_be_true", []):
            if val.get(k) is not True:
                return True, f"{k} 不为 true（不可比较）"
        return False, "可比较"

    return True, f"未知规则类型 {rules.get('type')}"


def run_card(card, min_chars=None, probes=None):
    probes = probes or load_probes()
    hits = []
    for p in probes:
        hit, why = judge(p, card, min_chars=min_chars)
        if hit:
            hits.append({"id": p["id"], "action": p["hit_action"], "severity": p["severity"], "reason": why})
    return _apply_person_exemption(card, hits, probes, min_chars)


def _apply_person_exemption(card, hits, probes, min_chars):
    """「短但具体」豁免：2–4 字的**具体人名**不应仅因字数被判泛称。

    ## 为什么（D-0071 盲区清单第 1 类）

    P-01 用「去掉泛称词后剩余字数」代理"受益方是否具体"。该代理在 2–4 字的人名上**误报**：
    `老张`（2 字）、`王工`（2 字）都是具体到人的答案，却被判「无具体受损方」。
    2026-10-26 的表述无关性检验中，用户对这两类卡明确判「批」。

    ## 豁免条件（三重，全部满足才放行）

    1. P-01 是**唯一**命中项 —— 即 P-02（指标三键）、P-03（后果可比）都合规。
       读作："答不出谁受损"仍成立时，给一次"字数虽短但指标与后果都齐"的机会；
    2. 去掉泛称词后的剩余串匹配 `rules.person_pattern`（人名/角色尊称的形态）；
    3. 命中长度区间 `rules.person_exempt_len`（默认 2–4 字）。

    ## 刻意保守

    只认形态**明确**的写法（老张/小王/王工/李总/张老师/赵经理），
    不认 `维护者`、`运维同学` 这类**角色名**——它们不是具体的人，仍应判拦。
    宁可漏放（继续误报），也不放宽到角色词（那会让"维护者"过关）。

    pattern 由 P-01 的 JSON 提供，不在代码里硬编码；缺该字段时本函数原样返回。
    """
    p01 = next((p for p in probes if p["id"] == "P-01-value"), None)
    if not p01:
        return hits
    rules = p01.get("rules", {})
    pat, span = rules.get("person_pattern"), rules.get("person_exempt_len") or [2, 4]
    if not pat or len(hits) != 1 or hits[0]["id"] != "P-01-value":
        return hits

    s = _text(card.get(p01["field"]))
    stripped = s
    for w in rules.get("generic_blacklist") or []:
        stripped = stripped.replace(w, "")
    stripped = stripped.strip()
    lo, hi = span
    if lo <= len(stripped) <= hi and re.fullmatch(pat, stripped):
        return []          # 豁免：唯一命中是字数，且形态是人名 → 不拦
    return hits


def load_set(set_name):
    """读一个标注集。set_name 只允许小写字母与连字符（防目录穿越）。"""
    if not re.fullmatch(r"[a-z][a-z-]{0,20}", set_name):
        raise ValueError(f"非法标注集名：{set_name!r}")
    path = PROBE_DIR / f"{set_name}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def calibrate(min_chars=None, set_name="calibration"):
    """内置标注集：样本取自真实文档（正=该报警，负=不该报警）。

    标注依据写在每条 note 里，可逐条复核；不是我凭感觉打的分。
    """
    path, data = load_set(set_name)
    tp = fp = tn = fn = 0
    rows = []
    for case in data["cases"]:
        hits = run_card(case["card"], min_chars=min_chars)
        pred = len(hits) > 0
        truth = bool(case["should_hit"])
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif not pred and not truth:
            tn += 1
        else:
            fn += 1
        rows.append((case["id"], truth, pred, [h["id"] for h in hits], case.get("note", "")))
    n = len(data["cases"])
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / n if n else 0.0
    return {"n": n, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": prec, "recall": rec, "f1": f1, "accuracy": acc,
            "min_chars": min_chars, "rows": rows}


def main():
    ap = argparse.ArgumentParser(description="L3 前提探针（只读）")
    ap.add_argument("--card", default="", help="前提卡 JSON 路径")
    ap.add_argument("--calibrate", action="store_true", help="跑内置标注集算 P/R")
    ap.add_argument("--th", type=int, default=None, help="覆盖 min_chars 阈值做敏感性分析")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--set", default="calibration", dest="set_name",
                    help="标注集名（默认 calibration；heldout 为留出集，不参与调参）")
    args = ap.parse_args()

    if args.card:
        try:
            card = json.loads(Path(args.card).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ERR] 读取前提卡失败：{e}", file=sys.stderr)
            return 2
        hits = run_card(card, min_chars=args.th)
        if args.json:
            print(json.dumps({"hits": hits}, ensure_ascii=False, indent=2))
        else:
            print(f"[premise] 命中 {len(hits)} 条")
            for h in hits:
                print(f"  🔴 {h['id']:<12} {h['action']:<24} {h['reason']}")
            if not hits:
                print("  ✅ 三条前提均可回答")
        return 1 if hits else 0

    if args.calibrate:
        r = calibrate(min_chars=args.th, set_name=args.set_name)
        if args.json:
            print(json.dumps({k: v for k, v in r.items() if k != "rows"}, ensure_ascii=False, indent=2))
        else:
            print(f"[calibrate] 集={args.set_name} n={r['n']}  "
                  f"min_chars={r['min_chars'] or '按探针默认'}")
            print(f"  TP={r['tp']} FP={r['fp']} TN={r['tn']} FN={r['fn']}")
            print(f"  precision={r['precision']:.2f} recall={r['recall']:.2f} "
                  f"f1={r['f1']:.2f} accuracy={r['accuracy']:.2f}")
            bad = [x for x in r["rows"] if x[1] != x[2]]
            if bad:
                print("  判错的样例：")
                for cid, truth, pred, ids, note in bad:
                    print(f"    {cid}: 应为{'报警' if truth else '放过'}，实际{'报警' if pred else '放过'} {ids} — {note[:40]}")
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
