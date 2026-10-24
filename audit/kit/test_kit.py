#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kit.py —— 判定工具的自检（`audit/kit/` 下三个脚本）。

## 为什么必须有它

`score.py` 产出的数字会被写进决策记录，`item_gate.py` 决定一批题能不能用，
`brief_lint.py` 决定一份题面能不能入库。三者都在**影响结论**，此前零测试。

已经发生过两次真实事故：

1. `brief_lint` 按「## 选项」精确匹配段名，实际写的是「## 选项（选一个）」→
   10 份题面全部误报「缺选项段」；
2. 把 `item_gate` 的多数票接到 `decide`（纯多数函数）上，单席只有 1 票 →
   每道题都判「未成票」，单席分数从 0.500 掉到 **0.000**。

两次都是「看起来对、跑起来也出数」，只有拿已知答案去撞才会现形。

## 三组检查

| 组 | 内容 |
|---|---|
| 单元 | 多数票边界、标签归一化、覆盖率「无数据 ≠ 0 分」、κ 的已知值 |
| 交叉 | `score.py` 与历史脚本 `score_v2.py` 在同一批数据上必须给出**同一个数** |
| 回归 | 历史轮次的成绩必须逐项复现（v2 26/26、v3 5/10 与 5/9） |

交叉这组是关键：`score_v2.py` 里另有一份多数票实现，我不去改它（它是产出过
被引用数字的历史脚本），而是用测试**钉住两者一致**。任何一处改了规则，这里立刻红。

## 用法

    python3 audit/kit/test_kit.py            # 人读
    python3 audit/kit/test_kit.py --json

退出码：0 全过 ｜ 1 有失败
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "audit/baseline/v2"))

import brief_lint  # noqa: E402
import label_saturation  # noqa: E402
import score  # noqa: E402
import score_v2  # noqa: E402  历史脚本，仅用于交叉验证

V3 = ROOT / "audit/baseline/v3"
V2 = ROOT / "audit/baseline/v2"

CHECKS: list[tuple[str, callable]] = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# ── 组一：多数票与单票边界 ───────────────────────────────────────────────

@check("decide: 2/3 过半取多数")
def _():
    assert score.decide(["a", "a", "c"], {"a", "c"}) == "a"


@check("decide: 1-1 平票不成票")
def _():
    assert score.decide(["a", "c"], {"a", "c"}) is None


@check("decide: 三票各一不成票")
def _():
    assert score.decide(["a", "b", "c"], {"a", "b", "c"}) is None


@check("decide: 单票在多数制下不成票（这是它与 resolve 的分界）")
def _():
    assert score.decide(["a"], {"a"}) is None


@check("decide: 未知标签被过滤后按有效票算")
def _():
    # 3 票里 1 票是无效标签 → 有效 2 票同向 → 成票
    assert score.decide(["x", "a", "a"], {"a"}) == "a"


@check("resolve: 单票直接采用（单席臂的必经路径）")
def _():
    assert score.resolve(["a"], {"a"}) == "a"


@check("resolve: 全是无效标签 → None")
def _():
    assert score.resolve(["x", "y"], {"a"}) is None


@check("resolve: 多票退回多数制（平票仍 None）")
def _():
    assert score.resolve(["a", "c"], {"a", "c"}) is None


# ── 组二：单题判定 ────────────────────────────────────────────────────────

@check("grade_one: 大小写归一（席位写 a、裁决表写 A 必须算对）")
def _():
    r = score.grade_one({"verdict": "A"}, {"verdict": "a"})
    assert r["match"] is True and r["verdict"] == "a"


@check("grade_one: 无理由字段时覆盖率记 None，不记 0")
def _():
    # 记 0 会把「没测」当「零分」，压低整臂均值
    r = score.grade_one({"verdict": "a"}, {"verdict": "a"})
    assert r["coverage"] is None


@check("grade_one: 题集无理由/风险时，缺 falsification 不算未作答")
def _():
    # v3 用户只给字母，不能因为席位没写证伪条件就把整题踢出分母
    r = score.grade_one({"verdict": "a", "reasons": ["x"]}, {"verdict": "a"})
    assert r["falsifiable"] is True and r["answered"] is True


@check("grade_one: 有 top_risk 关键词时，结论相反且未命中 = 危险失误")
def _():
    ref = {"verdict": "a", "top_risk": {"kw": ["成本"]}}
    r = score.grade_one({"verdict": "c", "top_risk": "没提到点子上"}, ref)
    assert r["danger"] is True
    r2 = score.grade_one({"verdict": "c", "top_risk": "成本会翻倍"}, ref)
    assert r2["danger"] is False


@check("grade_one: 未作答不被计为答错")
def _():
    r = score.grade_one(None, {"verdict": "a"})
    assert r["answered"] is False


@check("load_items: withdrawn 题被剔除")
def _():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "items.json"
        p.write_text(json.dumps({"items": {
            "Q1": {"verdict": "a"},
            "Q2": {"verdict": "b", "withdrawn": True}}}), encoding="utf-8")
        assert list(score.load_items(str(p))) == ["Q1"]


# ── 组三：平衡准确率与 κ ─────────────────────────────────────────────────

@check("summarize: 全答多数类 → raw 高、平衡恒为 0.50")
def _():
    # 8 题答 a、2 题答 b；臂全答 a
    refs = {f"Q{i}": {"verdict": "a" if i < 8 else "b"} for i in range(10)}
    arm = {q: {"verdict": "a"} for q in refs}
    s = score.summarize("x", arm, refs)
    assert s["agree_rate"] == 0.8, s["agree_rate"]
    assert abs(s["balanced"] - 0.5) < 1e-9, s["balanced"]


@check("fleiss_kappa: 完全一致 = 1.0")
def _():
    rows = [{"Q1": {"verdict": "a"}, "Q2": {"verdict": "b"}},
            {"Q1": {"verdict": "a"}, "Q2": {"verdict": "b"}}]
    assert score.fleiss_kappa(rows, ["Q1", "Q2"]) == 1.0


@check("fleiss_kappa: 每题都相反 = −1.0")
def _():
    rows = [{"Q1": {"verdict": "a"}, "Q2": {"verdict": "a"},
             "Q3": {"verdict": "a"}, "Q4": {"verdict": "a"}},
            {"Q1": {"verdict": "b"}, "Q2": {"verdict": "b"},
             "Q3": {"verdict": "b"}, "Q4": {"verdict": "b"}}]
    assert score.fleiss_kappa(rows, ["Q1", "Q2", "Q3", "Q4"]) == -1.0


@check("fleiss_kappa: 混合样本 = −1/3（手算值）")
def _():
    # 两题一致、两题相反：P̄=0.5，p_a=0.75/p_b=0.25 → P_e=0.625
    # κ = (0.5−0.625)/(1−0.625) = −0.333
    rows = [{"Q1": {"verdict": "a"}, "Q2": {"verdict": "a"},
             "Q3": {"verdict": "a"}, "Q4": {"verdict": "a"}},
            {"Q1": {"verdict": "a"}, "Q2": {"verdict": "a"},
             "Q3": {"verdict": "b"}, "Q4": {"verdict": "b"}}]
    k = score.fleiss_kappa(rows, ["Q1", "Q2", "Q3", "Q4"])
    assert abs(k - (-1 / 3)) < 0.002, k


# ── 组四：题面 lint ──────────────────────────────────────────────────────

def _lint(text: str):
    with tempfile.TemporaryDirectory() as d:
        q = Path(d) / "Q99"
        q.mkdir()
        (q / "brief.md").write_text(text, encoding="utf-8")
        return brief_lint.lint_one(q / "brief.md")


@check("brief_lint: 段名带括号也要认（曾因此把 10 份全误报缺段）")
def _():
    t = ("# Q99 · 测试\n\n## 情形\n这是一段情形。\n\n"
         "## 事实\n- 一条事实。\n\n## 选项（选一个）\n- `A` 甲方案\n- `B` 乙方案\n")
    assert _lint(t) == [], _lint(t)


@check("brief_lint: 情形段含结论词 → FAIL")
def _():
    t = ("# Q99\n\n## 情形\n先前的结果是失败。\n\n## 事实\n- 事实。\n\n"
         "## 选项\n- `A` 甲方案说明\n- `B` 乙方案说明\n")
    rules = [i["rule"] for i in _lint(t)]
    assert "R1" in rules, rules


@check("brief_lint: 情形段引用决策编号 → FAIL")
def _():
    t = ("# Q99\n\n## 情形\n已按 D-0063 处理。\n\n## 事实\n- 事实。\n\n"
         "## 选项\n- `A` 甲方案说明\n- `B` 乙方案说明\n")
    rules = [i["rule"] for i in _lint(t)]
    assert "R2" in rules, rules


@check("brief_lint: 事实段含评价词 → FAIL")
def _():
    t = ("# Q99\n\n## 情形\n一段情形。\n\n## 事实\n- 代价可观。\n\n"
         "## 选项\n- `A` 甲方案说明\n- `B` 乙方案说明\n")
    rules = [i["rule"] for i in _lint(t)]
    assert "R3" in rules, rules


@check("brief_lint: 选项描述不对称 → WARN 而非 FAIL")
def _():
    t = ("# Q99\n\n## 情形\n一段情形。\n\n## 事实\n- 事实。\n\n"
         "## 选项\n- `A` 很长很长的方案说明文字\n- `B` 短\n")
    lv = {i["rule"]: i["level"] for i in _lint(t)}
    assert lv.get("R5") == "WARN", lv


@check("label_saturation: invariant_run 数出最长「连续同值」段")
def _():
    ths = [1, 2, 3, 4, 5]
    # 1.0,1.0,0.9,0.9,1.0 → 最长 2
    assert label_saturation.invariant_run(
        {1: 1.0, 2: 1.0, 3: 0.9, 4: 0.9, 5: 1.0}, ths) == 2
    # 每点都不同 → 1
    assert label_saturation.invariant_run(
        {1: 1.0, 2: 0.9, 3: 0.8, 4: 0.7, 5: 0.6}, ths) == 1
    # 全程不变 → 5（这正是 heldout 的实测形态）
    assert label_saturation.invariant_run(
        {t: 1.0 for t in ths}, ths) == 5


@check("label_saturation: 当前前提标注集判定为饱和（S1/S2 命中）")
def _():
    # 钉住当前状态：若哪天标签被独立标注替换、或规则改动，这条会变，
    # 届时须有意更新，而不是让「1.00 全对」悄悄继续被当成有效性证据。
    grid = label_saturation.sweep(["calibration", "heldout"], [3, 5, 20])
    assert grid["calibration"][5] == 1.0, grid
    assert grid["heldout"][3] == grid["heldout"][20] == 1.0, grid


# ── 组五：交叉验证（关键）────────────────────────────────────────────────

@check("交叉: score.py 与历史 score_v2.py 在同一批数据上给出同一个数（v2 第四轮）")
def _():
    refs_doc = json.loads((V2 / "adjudication-r4.json").read_text(encoding="utf-8"))
    refs = {k: v for k, v in refs_doc["items"].items() if not v.get("withdrawn")}
    arm = score.load_answers(str(V2 / "r4-M1.json"))
    a = score_v2.summarize("s", arm, refs)
    b = score.summarize("s", arm, refs)
    for k in ("n", "agree", "agree_rate", "balanced"):
        assert a[k] == b[k], (k, a[k], b[k])


@check("交叉: 两份多数票实现给出同一组裁决（v3 三席）")
def _():
    refs_doc = json.loads((V3 / "adjudication-v3.json").read_text(encoding="utf-8"))
    refs = refs_doc["items"]
    seats = [score.load_answers(str(V3 / f"seat-{s}.json")) for s in ("S1", "S2", "S3")]
    valid = {str(v["verdict"]).strip().lower() for v in refs.values()}
    qids = list(refs)
    m_old, amb_old = score_v2.majority(seats, qids, valid)
    m_new, amb_new = score.majority_arms(seats, qids, valid)
    assert set(m_old) == set(m_new), (sorted(m_old), sorted(m_new))
    for q in m_old:
        assert score.label_of(m_old[q]) == score.label_of(m_new[q]), q
    assert set(amb_old) == set(amb_new), (sorted(amb_old), sorted(amb_new))


# ── 组六：历史成绩回归 ───────────────────────────────────────────────────

@check("回归: v3 首轮 单席 5/10、多席 5/9、常数 0.700")
def _():
    refs = score.load_items(str(V3 / "adjudication-v3.json"))
    single = score.summarize("s", score.load_answers(str(V3 / "arm-M1.json")), refs)
    seats = [score.load_answers(str(V3 / f"seat-{s}.json")) for s in ("S1", "S2", "S3")]
    valid = {str(v["verdict"]).strip().lower() for v in refs.values()}
    multi, _ = score.majority_arms(seats, list(refs), valid)
    m = score.summarize("m", multi, refs)
    assert (single["agree"], single["n"]) == (5, 10), single["agree"]
    assert (m["agree"], m["n"]) == (5, 9), m["agree"]


@check("回归: v2 第四轮 单席与多席均 26/26")
def _():
    refs = score.load_items(str(V2 / "adjudication-r4.json"))
    assert len(refs) == 26
    for f in ("r4-M1", "r4-S1"):
        s = score.summarize("x", score.load_answers(str(V2 / f"{f}.json")), refs)
        assert s["agree"] == 26, (f, s["agree"])


@check("回归: v3 重复跑 pass^3 = M1 .400 / S1 .600 / S2 .400 / S3 .400")
def _():
    refs = score.load_items(str(V3 / "adjudication-v3.json"))
    want = {"M1": 0.4, "S1": 0.6, "S2": 0.4, "S3": 0.4}
    for arm, exp in want.items():
        runs = [score.load_answers(str(V3 / f"{p}-{arm}.json"))
                for p in ("arm" if arm == "M1" else "seat", "r2", "r3")]
        got = score.summarize_repeat(arm, runs, refs)["pass_at_k"]
        assert abs(got - exp) < 1e-9, (arm, got, exp)


# ── 运行器 ───────────────────────────────────────────────────────────────

def main() -> int:
    fails = []
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as e:                       # 断言失败与意外异常都要报
            fails.append((name, f"{type(e).__name__}: {e}"))

    if "--json" in sys.argv:
        print(json.dumps({"total": len(CHECKS), "failed": len(fails),
                          "fails": [{"check": n, "err": e} for n, e in fails]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[kit-test] {len(CHECKS) - len(fails)}/{len(CHECKS)} 通过")
        for n, e in fails:
            print(f"  🔴 {n}\n     {e}")
        if not fails:
            print("  ✅ 全部通过（含与历史 score_v2 的交叉验证）")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
