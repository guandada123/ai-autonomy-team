#!/usr/bin/env python3
"""cite_rate.py 的验收用例。判据按反证写：实现被改坏时须变红（见 .learnings/L-0042）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cite_rate import (
    score_report,
    aggregate,
    upper_bound_3_over_n,  # noqa: E402
    check_thresholds,
)
from caliber_lib import load_lib_ids, audit_review  # noqa: E402

# 判据单一真源后，lib_ids 统一为 {id: entry} 形态
LIB_IDS = {
    "C-001": {"type": "principle"},
    "C-005": {"type": "principle"},
    "E-001": {"type": "exception"},
    "M-004": {"type": "meta"},
}


def rep(items):
    return {"items": items}


def main() -> int:
    fail = []
    stats = {"n": 0}

    def check(name, cond, detail=""):
        stats["n"] += 1
        if cond:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name}  {detail}")
            fail.append(name)

    print("用例 1：全部有效引用")
    s = score_report(
        rep(
            [
                {"qid": "A", "issue_type": "caliber", "caliber_refs": ["C-001", "M-004"]},
                {"qid": "B", "issue_type": "caliber", "caliber_refs": ["C-005"]},
            ]
        ),
        LIB_IDS,
    )
    check("保守=100%", s["cite_rate_conservative"] == 1.0, str(s["cite_rate_conservative"]))
    check("可评=100%", s["cite_rate_evaluable"] == 1.0)
    check("引用有效=100%", s["ref_validity"] == 1.0)
    check("无编造编号", s["bad_ids"] == [], str(s["bad_ids"]))

    print("用例 2：含一个编造编号 → 整条判无效（防刷）")
    s = score_report(
        rep([{"qid": "A", "issue_type": "caliber", "caliber_refs": ["C-001", "C-099"]}]), LIB_IDS
    )
    check("该条不计入有效引用", s["cited_valid"] == 0, str(s["cited_valid"]))
    check("引用有效率 50%", abs(s["ref_validity"] - 0.5) < 1e-9, str(s["ref_validity"]))
    check("记录编造编号", s["bad_ids"] == ["C-099"], str(s["bad_ids"]))

    print("用例 3：无引用 → 0，且计入分母（保守口径不隐没）")
    s = score_report(
        rep(
            [
                {"qid": "A", "issue_type": "caliber", "caliber_refs": []},
                {"qid": "B", "issue_type": "caliber"},
            ]
        ),
        LIB_IDS,
    )
    check("保守=0", s["cite_rate_conservative"] == 0.0)
    check("分母仍为 2", s["total"] == 2, str(s["total"]))

    print("用例 4：例外必须写命中范围（M-002）")
    a = score_report(
        rep(
            [
                {
                    "qid": "A",
                    "issue_type": "caliber",
                    "caliber_refs": ["E-001"],
                    "exception_hit": None,
                    "detail": "缺字段",
                }
            ]
        ),
        LIB_IDS,
    )
    b = score_report(
        rep(
            [
                {
                    "qid": "A",
                    "issue_type": "caliber",
                    "caliber_refs": ["E-001"],
                    "exception_hit": "E-001",
                    "detail": "命中：内部系统缺字段",
                }
            ]
        ),
        LIB_IDS,
    )
    check("未写范围 → 0%", a["exception_scope_rate"] == 0.0, str(a["exception_scope_rate"]))
    check("写了范围 → 100%", b["exception_scope_rate"] == 1.0)
    check(
        "未写范围不影响可举证率本身",
        a["cite_rate_conservative"] == 1.0,
        str(a["cite_rate_conservative"]),
    )

    print("用例 5：fact 类结论从可评分母剔除，保守分母保留")
    s = score_report(
        rep(
            [
                {"qid": "A", "issue_type": "fact", "caliber_refs": []},
                {"qid": "B", "issue_type": "caliber", "caliber_refs": ["C-001"]},
            ]
        ),
        LIB_IDS,
    )
    check(
        "保守=50%（fact 无引用即失败）",
        abs(s["cite_rate_conservative"] - 0.5) < 1e-9,
        str(s["cite_rate_conservative"]),
    )
    check(
        "可评=100%（分母只有 1 条口径类）",
        s["cite_rate_evaluable"] == 1.0,
        str(s["cite_rate_evaluable"]),
    )
    check("caliber 计数=1", s["caliber"] == 1, str(s["caliber"]))

    print("用例 6：跨报告聚合")
    agg = aggregate(
        {
            "x": score_report(
                rep([{"qid": "A", "issue_type": "caliber", "caliber_refs": ["C-001"]}]), LIB_IDS
            ),
            "y": score_report(
                rep([{"qid": "B", "issue_type": "caliber", "caliber_refs": []}]), LIB_IDS
            ),
        }
    )
    check("合计分母 2", agg["total"] == 2, str(agg["total"]))
    check(
        "合计可举证率 50%",
        abs(agg["cite_rate_conservative"] - 0.5) < 1e-9,
        str(agg["cite_rate_conservative"]),
    )

    print("用例 6b：率不得越界（分子分母必须同人群）")
    s = score_report(
        rep(
            [
                {"qid": "A", "issue_type": "fact", "caliber_refs": ["C-001"]},
                {"qid": "B", "issue_type": "caliber", "caliber_refs": ["C-001"]},
            ]
        ),
        LIB_IDS,
    )
    check(
        "可评率 ≤100%",
        s["cite_rate_evaluable"] is not None and s["cite_rate_evaluable"] <= 1.0,
        str(s["cite_rate_evaluable"]),
    )
    check(
        "可评率=100%（只有 B 计入分子分母）",
        abs(s["cite_rate_evaluable"] - 1.0) < 1e-9,
        str(s["cite_rate_evaluable"]),
    )
    s0 = score_report(rep([{"qid": "A", "issue_type": "none", "caliber_refs": []}]), LIB_IDS)
    check(
        "分母为 0 → None（不显示 0%）",
        s0["cite_rate_evaluable"] is None,
        str(s0["cite_rate_evaluable"]),
    )
    check(
        "无例外引用时例外率 → None（不显示 0%）",
        s0["exception_scope_rate"] is None,
        str(s0["exception_scope_rate"]),
    )

    print("用例 7：声明了例外命中却没写进引用列表 → 一致性下降（2026-11-01 实测缺陷）")
    s = score_report(
        rep(
            [
                {
                    "qid": "A",
                    "issue_type": "caliber",
                    "caliber_refs": ["C-003"],
                    "exception_hit": "E-001，命中：内部系统缺字段",
                }
            ]
        ),
        LIB_IDS,
    )
    check("一致性=0%", s["declared_consistency"] == 0.0, str(s["declared_consistency"]))
    check("识别到声明的例外", s["declared_items"] == 1, str(s["declared_items"]))
    s2 = score_report(
        rep(
            [
                {
                    "qid": "A",
                    "issue_type": "caliber",
                    "caliber_refs": ["E-001"],
                    "exception_hit": "E-001，命中：内部系统缺字段",
                }
            ]
        ),
        LIB_IDS,
    )
    check(
        "写进引用后一致性=100%", s2["declared_consistency"] == 1.0, str(s2["declared_consistency"])
    )

    print("用例 8：0 失败给置信上界（不得表述为『绝不漏引』）")
    check(
        "n=50 → 0.06",
        abs(upper_bound_3_over_n(0, 50) - 0.06) < 1e-9,
        str(upper_bound_3_over_n(0, 50)),
    )
    check("n=0 → 1.0（无样本不给假安心）", upper_bound_3_over_n(0, 0) == 1.0)

    print("用例 8b：批次门槛（分母为 0 不判失败，也不算合规）")
    ok = aggregate(
        {
            "x": score_report(
                rep(
                    [
                        {
                            "qid": "A",
                            "issue_type": "caliber",
                            "caliber_refs": ["E-001"],
                            "exception_hit": "E-001，命中：内部系统",
                        }
                    ]
                ),
                LIB_IDS,
            )
        }
    )
    bad = aggregate(
        {
            "y": score_report(
                rep([{"qid": "A", "issue_type": "caliber", "caliber_refs": []}]), LIB_IDS
            )
        }
    )
    check(
        "达标批次无未达标项",
        check_thresholds(ok, 0.90, 0.90) == [],
        str(check_thresholds(ok, 0.9, 0.9)),
    )
    check(
        "不达标批次被拦下",
        len(check_thresholds(bad, 0.90, 0.90)) == 1,
        str(check_thresholds(bad, 0.9, 0.9)),
    )
    none_agg = {"cite_rate_conservative": None, "declared_consistency": None}
    check(
        "分母为 0 不判失败（无样本不是不合规）",
        check_thresholds(none_agg, 0.90, 0.90) == [],
        str(check_thresholds(none_agg, 0.9, 0.9)),
    )

    print("用例 9：库不可读必须抛错，不静默降级")
    try:
        load_lib_ids(Path("/nonexistent/caliber-library.json"))
        check("抛错", False, "竟然返回了，会算出假的 100%")
    except Exception:
        check("抛错", True)

    print("用例 10：闸门与指标不得分歧（L-0040 单一真源守卫）")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        libf = td / "lib.json"
        libf.write_text(
            json.dumps(
                {
                    "entries": [
                        {"id": "C-001", "type": "principle"},
                        {"id": "E-001", "type": "exception"},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 同一份样本：一条无引用（证据不足），一条引用例外但没写范围（M-002 违规）
        bad_rep = td / "r.json"
        bad_rep.write_text(
            json.dumps(
                {
                    "items": [
                        {"qid": "A", "issue_type": "caliber", "caliber_refs": []},
                        {
                            "qid": "B",
                            "issue_type": "caliber",
                            "caliber_refs": ["E-001"],
                            "exception_hit": None,
                            "detail": "缺字段",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        problems, _, _ = audit_review([bad_rep], lib_path=libf)
        sc = score_report(json.loads(bad_rep.read_text(encoding="utf-8")), load_lib_ids(libf))
        check("闸门判出 2 类问题", len(problems) == 2, str(problems))
        check(
            "指标同步：可举证率=50%（A 无引用）",
            abs(sc["cite_rate_conservative"] - 0.5) < 1e-9,
            str(sc["cite_rate_conservative"]),
        )
        check(
            "指标同步：例外范围率=0%（与闸门 M-002 判定一致）",
            sc["exception_scope_rate"] == 0.0,
            str(sc["exception_scope_rate"]),
        )
        # 反向：合格样本两边都应无问题
        ok_rep = td / "ok.json"
        ok_rep.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "qid": "A",
                            "issue_type": "caliber",
                            "caliber_refs": ["E-001"],
                            "exception_hit": "E-001",
                            "detail": "命中：内部系统缺字段可修复",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        p2, _, _ = audit_review([ok_rep], lib_path=libf)
        s3 = score_report(json.loads(ok_rep.read_text(encoding="utf-8")), load_lib_ids(libf))
        check("合格样本闸门无问题", p2 == [], str(p2))
        check(
            "合格样本指标全 100%",
            s3["cite_rate_conservative"] == 1.0
            and s3["exception_scope_rate"] == 1.0
            and s3["declared_consistency"] == 1.0,
            str(
                {
                    k: s3[k]
                    for k in (
                        "cite_rate_conservative",
                        "exception_scope_rate",
                        "declared_consistency",
                    )
                }
            ),
        )

    print()
    if fail:
        print(f"🔴 {len(fail)} 项失败（共 {stats['n']} 项断言）：{fail}")
        return 1
    print(f"✅ 全部通过（{stats['n']} 项断言）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
