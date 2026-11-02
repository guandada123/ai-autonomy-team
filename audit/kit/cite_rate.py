#!/usr/bin/env python3
"""可举证率指标：口径库的实际价值不是"答得更准"，而是"结论能被指到具体条款"。

背景（2026-11-01）：
    准确率指标已被证明无区分度——可盲测的 22 题两个模型家族都是 22/22；
    5 题消融里有库/无库臂均 5/5 逐题相同。故按预注册改用「可举证率」
    （定义见 audit/baseline/v2/runs/cite-rate-prereg.json，测量前锁定）。

★ 判据单一真源：引用的合格判定全部走 caliber_lib.check_item_refs（L-0040）。
  本文件只做"计数与出率"，不重复实现判定——否则闸门与指标会对同一份报告给出相反结论。

五项指标：
    1. 可举证率·保守 = 有效引用结论 / 全部结论
    2. 可举证率·可评 = 有效引用结论 / 口径类结论（分子分母同人群，不得越界）
    3. 引用有效率     = 库内存在的引用 id / 全部引用 id（防编造编号）
    4. 例外范围标注率 = 写明命中范围的 / 引用例外的结论（M-002 合规）
    5. 例外命中一致率 = 声明的例外已写入引用列表的 / 声明了例外的结论

用法：
    python3 audit/kit/cite_rate.py --reports runs/R1-post.json runs/R2-post.json
    python3 audit/kit/cite_rate.py --reports runs/R1-r1.json --json out.json
退出码：0=正常；1=参数、库或报告不可读。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from caliber_lib import check_item_refs, load_lib_ids  # noqa: E402  （判据单一真源）

ROOT = Path(__file__).resolve().parent.parent.parent
LIB = ROOT / "audit" / "baseline" / "v2" / "caliber-library.json"
ITEM_KEYS = ("items", "claims", "issues")


def score_report(data: dict, lib_ids: dict, alias: bool = False) -> dict:
    """对单份报告打分。纯函数：不读文件、不打印，便于单测。"""
    items = []
    for k in ITEM_KEYS:
        if isinstance(data.get(k), list) and data[k]:
            items = data[k]
            break

    total = 0
    caliber = 0
    cited_valid = 0
    cited_valid_caliber = 0
    refs_total = 0
    refs_valid = 0
    exc_items = 0
    exc_ok = 0
    declared_items = 0
    declared_consistent = 0
    bad_ids: list[str] = []

    for it in items:
        total += 1
        # 口径类：有该字段时按字段；无该字段的老报告视为全口径类（预注册中已声明）
        is_caliber = str(it.get("issue_type", "caliber")) == "caliber"
        if is_caliber:
            caliber += 1
        c = check_item_refs(it, lib_ids, alias=alias)
        refs_total += len(c["refs"])
        refs_valid += len(c["good"])
        for r in c["bad"]:
            if r not in bad_ids:
                bad_ids.append(r)
        if c["valid"]:
            cited_valid += 1
            if is_caliber:
                cited_valid_caliber += 1
        if c["exception_ids"]:
            exc_items += 1
            if c["scope_ok"]:
                exc_ok += 1
        if c["declared_exception_ids"]:
            declared_items += 1
            if not c["declared_not_cited"]:
                declared_consistent += 1

    return {
        "total": total,
        "caliber": caliber,
        "cited_valid": cited_valid,
        "cited_valid_caliber": cited_valid_caliber,
        "refs_total": refs_total,
        "refs_valid": refs_valid,
        "exc_items": exc_items,
        "exc_ok": exc_ok,
        "declared_items": declared_items,
        "declared_consistent": declared_consistent,
        "bad_ids": bad_ids,
        "cite_rate_conservative": (cited_valid / total) if total else None,
        "cite_rate_evaluable": (cited_valid_caliber / caliber) if caliber else None,
        "ref_validity": (refs_valid / refs_total) if refs_total else None,
        "exception_scope_rate": (exc_ok / exc_items) if exc_items else None,
        "declared_consistency": (declared_consistent / declared_items) if declared_items else None,
    }


def aggregate(scores: dict) -> dict:
    keys = (
        "total",
        "caliber",
        "cited_valid",
        "cited_valid_caliber",
        "refs_total",
        "refs_valid",
        "exc_items",
        "exc_ok",
        "declared_items",
        "declared_consistent",
    )
    s = {k: 0 for k in keys}
    for v in scores.values():
        for k in keys:
            s[k] += v[k]
    return {
        **s,
        "cite_rate_conservative": (s["cited_valid"] / s["total"]) if s["total"] else None,
        "cite_rate_evaluable": (s["cited_valid_caliber"] / s["caliber"]) if s["caliber"] else None,
        "ref_validity": (s["refs_valid"] / s["refs_total"]) if s["refs_total"] else None,
        "exception_scope_rate": (s["exc_ok"] / s["exc_items"]) if s["exc_items"] else None,
        "declared_consistency": (s["declared_consistent"] / s["declared_items"])
        if s["declared_items"]
        else None,
    }


def upper_bound_3_over_n(misses: int, n: int) -> float:
    """0 次失败时给出失败率上界 ≈ 3/n（结论审计纪律第 4 条）。"""
    return 1.0 if n == 0 else min(1.0, 3.0 / n)


def check_thresholds(agg: dict, min_cite: float | None, min_consistency: float | None) -> list[str]:
    """批次准入门槛（协议 §2）。返回未达标说明；空列表 = 通过。

    分母为 0（rate=None）时**不判失败**——无样本不该被当成不合规，
    也不能被当成合规：由调用方决定是否另行检查样本量。
    """
    out = []
    if (
        min_cite is not None
        and agg["cite_rate_conservative"] is not None
        and agg["cite_rate_conservative"] < min_cite
    ):
        out.append(f"可举证率·保守 {agg['cite_rate_conservative']:.0%} < 门槛 {min_cite:.0%}")
    if (
        min_consistency is not None
        and agg["declared_consistency"] is not None
        and agg["declared_consistency"] < min_consistency
    ):
        out.append(f"例外命中一致率 {agg['declared_consistency']:.0%} < 门槛 {min_consistency:.0%}")
    return out


def fmt(rate) -> str:
    """分母为 0 时显示 n/a——显示成 0% 会被误读成「全部不合格」。"""
    return "  n/a" if rate is None else f"{rate:>5.0%}"


def main() -> int:
    ap = argparse.ArgumentParser(description="可举证率指标（口径库的价值量化）")
    ap.add_argument("--reports", nargs="+", required=True, help="审查报告路径（可多个）")
    ap.add_argument("--json", default="", help="把逐份与汇总结果写入 JSON")
    ap.add_argument("--lib", default=str(LIB))
    ap.add_argument(
        "--min-cite", type=float, default=None, help="批次门槛：可举证率·保守下限（如 0.90）"
    )
    ap.add_argument(
        "--min-consistency", type=float, default=None, help="批次门槛：例外命中一致率下限"
    )
    ap.add_argument(
        "--alias",
        action="store_true",
        help="同时接受历史报告的同义字段 refs（宽松口径；与严格口径对照出数）",
    )
    args = ap.parse_args()

    try:
        lib_ids = load_lib_ids(Path(args.lib))
    except Exception as e:
        print(f"🔴 口径库不可读：{e}（不可静默降级，否则会算出假的 100%）", file=sys.stderr)
        return 1

    scores = {}
    for r in args.reports:
        p = Path(r)
        if not p.exists():
            print(f"🔴 报告不存在：{p}", file=sys.stderr)
            return 1
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"🔴 报告不可读：{p.name}（{type(e).__name__}）", file=sys.stderr)
            return 1
        scores[p.name] = score_report(data, lib_ids, alias=args.alias)

    agg = aggregate(scores)
    print("口径：" + ("宽松（含 refs 别名）" if args.alias else "严格（仅协议字段 caliber_refs）"))
    print(
        f"{'报告':26s}{'结论':>6s}{'有效':>6s}{'保守':>7s}{'可评':>7s}{'引用有效':>9s}{'例外范围':>9s}{'命中一致':>9s}"
    )
    for name, v in scores.items():
        print(
            f"{name:26s}{v['total']:>6d}{v['cited_valid']:>6d}"
            f"{fmt(v['cite_rate_conservative']):>7s}{fmt(v['cite_rate_evaluable']):>7s}"
            f"{fmt(v['ref_validity']):>9s}{fmt(v['exception_scope_rate']):>9s}"
            f"{fmt(v['declared_consistency']):>9s}"
        )
    print("-" * 79)
    print(
        f"{'合计':26s}{agg['total']:>6d}{agg['cited_valid']:>6d}"
        f"{fmt(agg['cite_rate_conservative']):>7s}{fmt(agg['cite_rate_evaluable']):>7s}"
        f"{fmt(agg['ref_validity']):>9s}{fmt(agg['exception_scope_rate']):>9s}"
        f"{fmt(agg['declared_consistency']):>9s}"
    )

    misses = agg["total"] - agg["cited_valid"]
    ub = upper_bound_3_over_n(misses, agg["total"])
    print()
    print(f"未引用 {misses}/{agg['total']}；若按 0 失败估计，未引用率上界 ≈ {ub:.3f}（3/n）")
    bad = sorted({b for v in scores.values() for b in v["bad_ids"]})
    print(f"编造编号：{'无 ✓' if not bad else '🔴 ' + str(bad)}")

    thr = check_thresholds(agg, args.min_cite, args.min_consistency)
    if thr:
        print(
            "🔴 未达批次门槛：" + "；".join(thr) + "（该批报告不得进 dossier 判分，先补引用再入库）"
        )
        return 1
    if thr == [] and (args.min_cite is not None or args.min_consistency is not None):
        print("✅ 达到批次门槛")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "pre_reg": "audit/baseline/v2/runs/cite-rate-prereg.json",
                    "alias": args.alias,
                    "per_report": scores,
                    "aggregate": agg,
                    "miss_upper_bound_3_over_n": ub,
                    "bad_ids": bad,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
