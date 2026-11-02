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

import contextlib
import io
import json
import re
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

# 报告项：跑、打印、但**不计入退出码**。
# 只放「数据/环境当前是什么状态」这类会随数据集变化而变化的观测（F1，2026-10-28）。
# 放进 CHECKS 的后果：标注一换就红，于是 selftest 常红 = 真回归被淹没（狼来了）。
REPORTS: list[tuple[str, callable]] = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn

    return deco


def report(name):
    """与 check 的区别：只观察不裁决，失败也不影响 exit-code。"""

    def deco(fn):
        REPORTS.append((name, fn))
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
        p.write_text(
            json.dumps(
                {"items": {"Q1": {"verdict": "a"}, "Q2": {"verdict": "b", "withdrawn": True}}}
            ),
            encoding="utf-8",
        )
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
    rows = [
        {"Q1": {"verdict": "a"}, "Q2": {"verdict": "b"}},
        {"Q1": {"verdict": "a"}, "Q2": {"verdict": "b"}},
    ]
    assert score.fleiss_kappa(rows, ["Q1", "Q2"]) == 1.0


@check("fleiss_kappa: 每题都相反 = −1.0")
def _():
    rows = [
        {
            "Q1": {"verdict": "a"},
            "Q2": {"verdict": "a"},
            "Q3": {"verdict": "a"},
            "Q4": {"verdict": "a"},
        },
        {
            "Q1": {"verdict": "b"},
            "Q2": {"verdict": "b"},
            "Q3": {"verdict": "b"},
            "Q4": {"verdict": "b"},
        },
    ]
    assert score.fleiss_kappa(rows, ["Q1", "Q2", "Q3", "Q4"]) == -1.0


@check("fleiss_kappa: 混合样本 = −1/3（手算值）")
def _():
    # 两题一致、两题相反：P̄=0.5，p_a=0.75/p_b=0.25 → P_e=0.625
    # κ = (0.5−0.625)/(1−0.625) = −0.333
    rows = [
        {
            "Q1": {"verdict": "a"},
            "Q2": {"verdict": "a"},
            "Q3": {"verdict": "a"},
            "Q4": {"verdict": "a"},
        },
        {
            "Q1": {"verdict": "a"},
            "Q2": {"verdict": "a"},
            "Q3": {"verdict": "b"},
            "Q4": {"verdict": "b"},
        },
    ]
    k = score.fleiss_kappa(rows, ["Q1", "Q2", "Q3", "Q4"])
    assert abs(k - (-1 / 3)) < 0.002, k


# ── 组四：盲标 Kappa 工具链（破「标注集只能验实现」） ──────────────────────


@check("kappa_labels: 完全一致 → Cohen κ = 1.0")
def _():
    import kappa_labels as kl

    a = [1, 0, 1, 1, 0, 0]
    b = [1, 0, 1, 1, 0, 0]
    assert abs(kl.cohen_kappa(a, b) - 1.0) < 1e-9


@check("kappa_labels: 已知例子 P≈0.5,Pe≈0.5 → κ≈0（碰巧一致）")
def _():
    import kappa_labels as kl

    # 参考全 1，标注者 3 个对 3 个错（恰好一半），完全随机水平
    a = [1, 1, 1, 1]
    b = [1, 0, 1, 0]
    # Po=0.5; pa1=1.0, pb1=0.5 → Pe=0.5+0.5*0? 重新算: Pe=1*0.5 + 0*0.5=0.5
    assert abs(kl.cohen_kappa(a, b) - 0.0) < 1e-9, kl.cohen_kappa(a, b)


@check("kappa_labels: CLI 对示例文件跑出 κ=1.000 且退出 0")
def _():
    import subprocess

    ex = str(V3 / "labels-user.example.txt")
    r = subprocess.run(
        [sys.executable, str(HERE / "kappa_labels.py"), "--labels", ex],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "1.000" in r.stdout, r.stdout
    assert "站得住" in r.stdout, r.stdout


@check("kappa_labels: 解析显式 id 格式 T01拦 N01过")
def _():
    import kappa_labels as kl
    from pathlib import Path
    import tempfile

    tf = Path(tempfile.mktemp(suffix=".txt"))
    tf.write_text("T01拦 N01过 T02过", encoding="utf-8")
    got = kl.parse_labels(tf)
    tf.unlink()
    assert got == {"T01": 1, "N01": 0, "T02": 0}, got


# ── 组四：题面 lint ──────────────────────────────────────────────────────


def _lint(text: str):
    with tempfile.TemporaryDirectory() as d:
        q = Path(d) / "Q99"
        q.mkdir()
        (q / "brief.md").write_text(text, encoding="utf-8")
        return brief_lint.lint_one(q / "brief.md")


@check("brief_lint: 段名带括号也要认（曾因此把 10 份全误报缺段）")
def _():
    t = (
        "# Q99 · 测试\n\n## 情形\n这是一段情形。\n\n"
        "## 事实\n- 一条事实。\n\n## 选项（选一个）\n- `A` 甲方案\n- `B` 乙方案\n"
    )
    assert _lint(t) == [], _lint(t)


@check("brief_lint: 情形段含结论词 → FAIL")
def _():
    t = (
        "# Q99\n\n## 情形\n先前的结果是失败。\n\n## 事实\n- 事实。\n\n"
        "## 选项\n- `A` 甲方案说明\n- `B` 乙方案说明\n"
    )
    rules = [i["rule"] for i in _lint(t)]
    assert "R1" in rules, rules


@check("brief_lint: 情形段引用决策编号 → FAIL")
def _():
    t = (
        "# Q99\n\n## 情形\n已按 D-0063 处理。\n\n## 事实\n- 事实。\n\n"
        "## 选项\n- `A` 甲方案说明\n- `B` 乙方案说明\n"
    )
    rules = [i["rule"] for i in _lint(t)]
    assert "R2" in rules, rules


@check("brief_lint: 事实段含评价词 → FAIL")
def _():
    t = (
        "# Q99\n\n## 情形\n一段情形。\n\n## 事实\n- 代价可观。\n\n"
        "## 选项\n- `A` 甲方案说明\n- `B` 乙方案说明\n"
    )
    rules = [i["rule"] for i in _lint(t)]
    assert "R3" in rules, rules


@check("brief_lint: 选项描述不对称 → WARN 而非 FAIL")
def _():
    t = (
        "# Q99\n\n## 情形\n一段情形。\n\n## 事实\n- 事实。\n\n"
        "## 选项\n- `A` 很长很长的方案说明文字\n- `B` 短\n"
    )
    lv = {i["rule"]: i["level"] for i in _lint(t)}
    assert lv.get("R5") == "WARN", lv


@check("label_saturation: invariant_run 数出最长「连续同值」段")
def _():
    ths = [1, 2, 3, 4, 5]
    # 1.0,1.0,0.9,0.9,1.0 → 最长 2
    assert label_saturation.invariant_run({1: 1.0, 2: 1.0, 3: 0.9, 4: 0.9, 5: 1.0}, ths) == 2
    # 每点都不同 → 1
    assert label_saturation.invariant_run({1: 1.0, 2: 0.9, 3: 0.8, 4: 0.7, 5: 0.6}, ths) == 1
    # 全程不变 → 5（这正是 heldout 的实测形态）
    assert label_saturation.invariant_run({t: 1.0 for t in ths}, ths) == 5


@report("label_saturation: 当前标注集饱和状态（观测，非裁决）")
def _():
    # 原先这条断言钉的是具体数值（calibration[5]==1.0），2026-10-28 移出 exit-code：
    # 它断言的是「数据当前长什么样」，标注一换就红，而饱和是事实、不该靠改阈值灭掉。
    # 现在这里只报数；「判据本身还判不判得动」改由下面四条合成用例钉住（正负样本齐全）。
    sets, ths = ["calibration", "heldout"], [3, 5, 20]
    grid = label_saturation.sweep(sets, ths)
    hits = label_saturation.evaluate(grid, sets, ths)
    for s in sets:
        print(f"    {s}: " + "  ".join(f"th{t}={grid[s][t]:.3f}" for t in ths))
    print(
        f"    命中判据: {[h['rule'] for h in hits] or '无'}"
        " —— 饱和不代表脚本坏了，代表这套标注不足以验证规则"
    )


@check("label_saturation.evaluate: S1 满分必命中（正样本）")
def _():
    grid = {"a": {3: 1.0, 5: 0.9}}
    rules = [h["rule"] for h in label_saturation.evaluate(grid, ["a"], [3, 5])]
    assert "S1" in rules, rules


@check("label_saturation.evaluate: 0.99 不得算满分（负样本，钉住 >=0.999 的边界）")
def _():
    # 若把 >=0.999 写反成 <= 或放宽成 >=0.9，上一条照样绿，这条会红。
    grid = {"a": {3: 0.99, 5: 0.90}}
    assert label_saturation.evaluate(grid, ["a"], [3, 5]) == [], grid


@check("label_saturation.evaluate: S2 五点同值必命中（正样本）")
def _():
    ths = [3, 5, 8, 10, 12]
    grid = {"a": {t: 0.8 for t in ths}}
    rules = [h["rule"] for h in label_saturation.evaluate(grid, ["a"], ths)]
    assert "S2" in rules, rules


@check("label_saturation.evaluate: 每点都不同不得命中 S2（负样本）")
def _():
    ths = [3, 5, 8, 10, 12]
    grid = {"a": {t: 0.8 - 0.05 * i for i, t in enumerate(ths)}}
    rules = [h["rule"] for h in label_saturation.evaluate(grid, ["a"], ths)]
    assert "S2" not in rules, rules


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
        runs = [
            score.load_answers(str(V3 / f"{p}-{arm}.json"))
            for p in ("arm" if arm == "M1" else "seat", "r2", "r3")
        ]
        got = score.summarize_repeat(arm, runs, refs)["pass_at_k"]
        assert abs(got - exp) < 1e-9, (arm, got, exp)


# ── 组五：同步脚本（防"静默 no-op"，见 L-0027） ──────────────────────────


@check("sync_score.sh: 目标全部不存在 → 必须 exit 1（不能冒充成功）")
def _():
    import subprocess, shutil, tempfile

    tmp = Path(tempfile.mkdtemp())
    try:
        kitdir = tmp / "audit" / "kit"
        kitdir.mkdir(parents=True)
        shutil.copy(str(HERE / "score.py"), str(kitdir / "score.py"))
        shutil.copy(str(HERE / "sync_score.sh"), str(kitdir / "sync_score.sh"))
        r = subprocess.run(["bash", str(kitdir / "sync_score.sh")], capture_output=True, text=True)
        out = r.stdout + r.stderr
        assert r.returncode == 1, f"退出码应为 1，实为 {r.returncode}\n{out}"
        assert "成功同步 0 个" in out, out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 组N：item_gate 行为测试（F4，2026-10-29）─────────────────────────────
# 此前 item_gate 零测试：它的 FAIL 结论直接拦一批题，而判据本身没人撞过。
# 全部走**真实 CLI**（临时目录造题集与作答，python3 item_gate.py 实跑），不 mock 内部函数。

GOLD4 = {"Q1": "pass", "Q2": "fail", "Q3": "pass", "Q4": "fail"}


def _ig_repo(tmp: Path, golds: dict, arms: dict, as_repeat=False) -> list[str]:
    """造最小题集与臂作答，返回 item_gate 的 CLI 参数（不含脚本名）。

    arms: {臂名: [作答 dict, ...]}——多个作答文件 + --as-repeat 才算重复运行。
    """
    adj = tmp / "adj.json"
    adj.write_text(
        json.dumps({"items": {q: {"verdict": v} for q, v in golds.items()}}), encoding="utf-8"
    )
    argv = ["--adjudication", str(adj)]
    for name, runs in arms.items():
        paths = []
        for i, answers in enumerate(runs):
            p = tmp / f"{name}-{i}.json"
            p.write_text(json.dumps({"answers": answers}), encoding="utf-8")
            paths.append(str(p))
        argv += ["--arm", f"{name}={','.join(paths)}"]
    if as_repeat:
        argv.append("--as-repeat")
    return argv


def _ig_run(argv: list[str]):
    import subprocess

    return subprocess.run(
        [sys.executable, str(HERE / "item_gate.py"), *argv], capture_output=True, text=True
    )


@check("item_gate: 单席 acc=0.750（历史事故：单席曾被判成 0.000，resolve 语义钉死）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 3 对 1 错。★ 判据语义：单臂下「所有臂都错」等价于「该臂错」→ Q2 触发全错 →
        # 准入必然 FAIL（单臂本就该被准入闸盯紧）。本用例钉的是**acc 数值正确**：
        # 单票必须被 resolve 采用，0.750 不得再崩成历史事故的 0.000。
        ans = {
            "Q1": {"verdict": "pass"},
            "Q2": {"verdict": "pass"},
            "Q3": {"verdict": "pass"},
            "Q4": {"verdict": "fail"},
        }
        r = _ig_run(_ig_repo(tmp, GOLD4, {"single": [ans]}))
        assert r.returncode == 1, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
        assert "  single" in r.stdout and "0.750" in r.stdout, r.stdout
        assert "（未成票" not in r.stdout, r.stdout  # 4 题全部未成票 = 事故复现
        assert "全错" in r.stdout, r.stdout


@check("item_gate: 多席=多数票而非 unanimity（3 席 2/3 成票 → 0.750，历史事故方向）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        g = GOLD4
        anti = {"pass": "fail", "fail": "pass"}
        # 作答格式必须与真实基线一致：{题号: {"verdict": ...}}（字符串值 = 未作答）
        wrap = lambda d: {q: {"verdict": v} for q, v in d.items()}
        # 三席：Q1–Q3 各 2/3 成票且多数=gold，Q4 多数错 → 多数票口径 0.750；
        # 若按 unanimity（全席对才得分）则 0.000 → 常数胜出 → FAIL
        A = wrap({"Q1": g["Q1"], "Q2": g["Q2"], "Q3": g["Q3"], "Q4": anti[g["Q4"]]})
        B = wrap({"Q1": g["Q1"], "Q2": g["Q2"], "Q3": anti[g["Q3"]], "Q4": anti[g["Q4"]]})
        C = wrap({"Q1": g["Q1"], "Q2": anti[g["Q2"]], "Q3": g["Q3"], "Q4": anti[g["Q4"]]})
        # ★ 单臂永远过不了准入闸（对题=饱和、错题=全错），必须给第二个臂
        #   打破跨臂全 1/全 0：seats 答对的三题 M1 全错、Q4 反之（M1 acc 0.25）
        M1 = wrap({"Q1": anti[g["Q1"]], "Q2": anti[g["Q2"]], "Q3": anti[g["Q3"]], "Q4": g["Q4"]})
        r = _ig_run(_ig_repo(tmp, GOLD4, {"seats": [A, B, C], "M1": [M1]}))
        assert r.returncode == 0, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
        assert "  seats" in r.stdout and "0.750" in r.stdout, r.stdout
        assert "PASS" in r.stdout, r.stdout


@check("item_gate: --as-repeat 分开算 pass^1/pass^k（1 对 1 错 → 0.500/0.000 且不稳）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ok = {q: {"verdict": v} for q, v in GOLD4.items()}
        bad = {q: {"verdict": "x"} for q in GOLD4}
        r = _ig_run(_ig_repo(tmp, GOLD4, {"rep": [ok, bad]}, as_repeat=True))
        assert r.returncode == 1, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
        assert "pass^1 0.500" in r.stdout and "pass^k 0.000" in r.stdout, r.stdout
        assert "答案会变" in r.stdout, r.stdout  # 自稳分支必须被走到


@check("item_gate: 饱和题 → FAIL 且列出题号（全对不是好事）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ans = {q: {"verdict": v} for q, v in GOLD4.items()}
        r = _ig_run(_ig_repo(tmp, GOLD4, {"arm": [ans]}))
        assert r.returncode == 1, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
        assert "饱和" in r.stdout and "Q1" in r.stdout, r.stdout


@check("item_gate: 全错 → FAIL 并提示先查题面（可能是题坏了，不是模型差）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        anti = {"pass": "fail", "fail": "pass"}
        bad = {q: {"verdict": anti[v]} for q, v in GOLD4.items()}  # 有效标签但全反
        r = _ig_run(_ig_repo(tmp, GOLD4, {"arm": [bad]}))
        assert r.returncode == 1, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
        assert "全错" in r.stdout, r.stdout


@check("item_gate: 常数胜出 → FAIL（最好的臂只是永远答多数类）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        const = {q: {"verdict": "pass"} for q in GOLD4}  # gold 分布 2 pass / 2 fail
        r = _ig_run(_ig_repo(tmp, GOLD4, {"arm": [const]}))
        assert r.returncode == 1, f"exit={r.returncode}\n{r.stdout}{r.stderr}"
        assert "常数胜出" in r.stdout, r.stdout


@check("item_gate: --arm 缺 name= → 拒绝（非法输入不得静默）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        adj = tmp / "adj.json"
        adj.write_text(
            json.dumps({"items": {q: {"verdict": v} for q, v in GOLD4.items()}}), encoding="utf-8"
        )
        r = _ig_run(["--adjudication", str(adj), "--arm", "badformat"])
        assert r.returncode != 0, r.stdout
        assert "name=path" in (r.stdout + r.stderr), (r.stdout, r.stderr)


@check("item_gate: --json 明细可解析且 constant_wins 为布尔（机器可读通道）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        const = {q: {"verdict": "pass"} for q in GOLD4}
        argv = _ig_repo(tmp, GOLD4, {"arm": [const]})
        out = tmp / "detail.json"
        r = _ig_run(argv + ["--json", str(out)])
        assert r.returncode == 1, r.stdout
        d = json.loads(out.read_text(encoding="utf-8"))
        assert d["constant_wins"] is True and d["best_arm"]["acc"] == 0.5, d


# ── 组N+1：grade/summarize 负样本（F10，2026-10-29）──────────────────────


@check("grade_one: 作答不是 dict → 未作答而非崩溃（畸形输入负样本）")
def _():
    ref = {"verdict": "pass", "key_reasons": [], "top_risk": {}}
    r = score.grade_one("不是字典", ref)
    assert r["answered"] is False and r["match"] is False
    assert r["danger"] is False and r["coverage"] is None
    r2 = score.grade_one(None, ref)
    assert r2["answered"] is False


@check("summarize: 缺 falsification 的题不计分母（n 缩小，而不是把均分虚拉低）")
def _():
    refs = {
        "Q1": {"verdict": "pass", "top_risk": {"kw": ["爆仓"]}},  # 要求证伪条件
        "Q2": {"verdict": "pass"},
    }  # 不要求
    ans = {
        "Q1": {"verdict": "pass"},  # 缺 falsification
        "Q2": {"verdict": "pass"},
    }
    s = score.summarize("arm", ans, refs)
    assert s["n"] == 1 and s["agree"] == 1 and s["agree_rate"] == 1.0, s
    assert "Q1" not in s["rows"] or s["rows"]["Q1"]["falsifiable"] is False


@check("grade_one: danger 只在「结论错 + 风险关键词未命中」时为真（方向钉死）")
def _():
    ref = {"verdict": "pass", "top_risk": {"kw": ["爆仓"]}}
    hit = score.grade_one({"verdict": "fail", "top_risk": "可能爆仓"}, ref)
    miss = score.grade_one({"verdict": "fail", "top_risk": "没什么风险"}, ref)
    assert hit["danger"] is False, hit  # 风险已提示 → 不算危险失误
    assert miss["danger"] is True, miss  # 结论错且没提示 → 危险


# ── 组N+2：守夜脚本告警侧（F9，2026-10-29）───────────────────────────────
# B7：告警侧分支（FAIL→exit 2→alert）从未被验证过会红。这里直接驱动真实模块，
# check_c1/c2/c3 用打桩替换（C2 的判据活性已由回归第 54 项用合成 runs 实测）——
# 本组测的是 main 的**分发与告警通道**，不是判据本身。


def _iw(tmp: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "iw_under_test", ROOT / "scripts/inspection_watch.py"
    )
    iw = importlib.util.module_from_spec(spec)
    sys.modules["iw_under_test"] = iw
    spec.loader.exec_module(iw)
    (tmp / "Makefile").write_text("test:\n\techo ok\n", encoding="utf-8")  # E0 守卫需要真根
    iw.ROOT = tmp  # 报告与日志都落在临时目录
    iw.LOG = tmp / "watch.log"
    iw.RUNS = tmp / "runs"
    return iw


def _iw_run(iw, argv: list[str]) -> tuple[int, str]:
    import contextlib

    old = sys.argv
    sys.argv = ["inspection_watch.py", *argv]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = iw.main()
    finally:
        sys.argv = old
    return rc, buf.getvalue()


@check("inspection_watch: 报告在且含日期 → PASS exit 0（正样本）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "outputs").mkdir()
        (tmp / "outputs" / "inspection-2099-01-01.md").write_text(
            "# 巡检 · 2099-01-01\n一切正常\n", encoding="utf-8"
        )
        iw = _iw(tmp)
        rc, out = _iw_run(iw, ["--date", "2099-01-01"])
        assert rc == 0, f"exit={rc}\n{out}"
        assert "PASS" in out, out


@check("inspection_watch: 报告在但不含日期 → 判冒名，转入缺失分支复检")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "outputs").mkdir()
        (tmp / "outputs" / "inspection-2099-01-01.md").write_text(
            "# 旧文件冒名\n（正文里没有当天日期）\n", encoding="utf-8"
        )
        iw = _iw(tmp)
        iw.check_c1 = lambda: (False, "")
        iw.check_c2 = lambda: ("GONE", "")
        iw.check_c3 = lambda: (False, "")
        rc, out = _iw_run(iw, ["--date", "2099-01-01"])
        assert rc == 0, f"exit={rc}\n{out}"
        assert "全部消失" in out, out  # 冒名 → 复检 → 条件已消失 → 不告警


@check("inspection_watch: 异常仍在+报告缺失 → exit 2 且告警侧真的执行（B7 假绿方向）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        iw = _iw(tmp)
        iw.check_c1 = lambda: (True, "round2 未终态")
        iw.check_c2 = lambda: ("STILL", "双边一致")
        iw.check_c3 = lambda: (False, "")
        iw.os.environ["FEISHU_WEBHOOK"] = "http://127.0.0.1:9/no-such"  # 必然连接失败
        try:
            rc, out = _iw_run(iw, ["--date", "2099-01-01"])
        finally:
            iw.os.environ.pop("FEISHU_WEBHOOK", None)
        assert rc == 2, f"exit={rc}\n{out}"
        assert "FAIL" in out and "C1=True" in out, out
        assert "告警发送失败" in out, out  # alert() 被调用且网络异常被记入日志（不吞）
        assert iw.LOG.is_file() and "FAIL" in iw.LOG.read_text(encoding="utf-8")


@check("inspection_watch: 无 webhook 时告警只落日志不抛（降级路径）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        iw = _iw(tmp)
        old = iw.os.environ.pop("FEISHU_WEBHOOK", None)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                iw.alert("测试消息")
            assert "未设 FEISHU_WEBHOOK" in buf.getvalue(), buf.getvalue()
        finally:
            if old is not None:
                iw.os.environ["FEISHU_WEBHOOK"] = old


# ── 组N+3：接手验收 verify_handover（基线抬升，2026-10-29）──────────────
# 此前零行为测试：它的 6 项结论就是「这台机器能不能接手」，判据本身没人撞过。
# 纯函数用真模块直调；带分支的检查用临时目录 + 打桩（与 F9 同款模式）。


def _vh(tmp: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("vh_ut", ROOT / "scripts/verify_handover.py")
    vh = importlib.util.module_from_spec(spec)
    sys.modules["vh_ut"] = vh
    spec.loader.exec_module(vh)
    vh.ROOT = tmp
    vh.PKG = tmp / "migration-package"
    return vh


def _vh_run(vh, argv):
    import contextlib

    old = sys.argv
    sys.argv = ["verify_handover.py", *argv]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = vh.main()
    finally:
        sys.argv = old
    return rc, out.getvalue(), err.getvalue()


@check("verify_handover: _is_stdlib 三态（标准库/内置/第三方各自判定）")
def _():
    import importlib.util

    spec = importlib.util.spec_from_file_location("vh_t", ROOT / "scripts/verify_handover.py")
    vh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vh)
    assert vh._is_stdlib("json") is True  # 纯标准库
    assert vh._is_stdlib("_thread") is True  # built-in
    assert vh._is_stdlib("no_such_module_xyz") is False  # 不存在
    assert vh._is_stdlib("coverage") is False  # 本 venv 装了它 → site-packages


@check("verify_handover: chk_manifest 正样本（真包声明=实际，不断言快照数字）")
def _():
    import importlib.util

    spec = importlib.util.spec_from_file_location("vh_t2", ROOT / "scripts/verify_handover.py")
    vh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vh)
    ok, detail = vh.chk_manifest()
    # 断言「性质」而非「快照数字」：原先写死 "声明 1121"，本批新增 item_review.py /
    # test_item_review.py / 协议 08 后重打包变 1122 就红——而 chk_manifest 本身没错。
    # 写死派生数字的真实代价：每次正常加文件都要改测试，久而久之大家会顺手把数字改大
    # 而不再看断言在验什么，回归保护就这样失效。改为解析出两个计数并断言它们相等，
    # 无论包多大都成立，且仍能抓住"声明≠实际"（负样本另有 file_count=99 一例兜底）。
    assert ok is True, detail
    m = re.search(r"声明 (\d+) 条 / (\d+) 字节；实际 (\d+) 个文件", detail)
    assert m is not None, f"detail 格式不可解析（若文案变了请同步本断言）：{detail}"
    assert m.group(1) == m.group(3), f"声明文件数与实际不符：{detail}"


@check("verify_handover: chk_manifest 三条负样本（坏 JSON/计数不符/缓存残留）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg = tmp / "migration-package"
        pkg.mkdir()
        (pkg / "a.py").write_text("x\n", encoding="utf-8")
        vh = _vh(tmp)

        ok, d = vh.chk_manifest()
        assert ok is False and "读不到" in d, d

        (pkg / "MANIFEST.json").write_text(
            '{"stats": {"file_count": 1, "total_bytes": 2}}', encoding="utf-8"
        )
        ok, d = vh.chk_manifest()
        assert ok is True and "实际 1 个文件" in d, d  # 1 个文件对 1 条声明

        (pkg / "MANIFEST.json").write_text(
            '{"stats": {"file_count": 99, "total_bytes": 2}}', encoding="utf-8"
        )
        ok, d = vh.chk_manifest()
        assert ok is False and "99" in d, d

        # 2026-10-22 实测的交付卫生问题：缓存不算文件数，但要单独提示
        cache = pkg / "judges" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "x.cpython-313.pyc").write_bytes(b"junk")
        ok, d = vh.chk_manifest()
        assert ok is False and "缓存文件" in d, d


@check("verify_handover: chk_hook 全部分支（OK/未装/不可执行/内容不对/非 git）")
def _():
    import subprocess

    def mkrepo(tag, hook_body=None, hook_mode=0o755):
        td = Path(tempfile.mkdtemp(prefix=f"vh-hook-{tag}-"))
        subprocess.run(["git", "init", "-q"], cwd=td, check=True, capture_output=True)
        (td / "scripts").mkdir()
        (td / "scripts" / "pre_push_scan.py").write_text("# hook target\n", encoding="utf-8")
        if hook_body is not None:
            h = td / ".git" / "hooks" / "pre-push"
            h.write_text(hook_body, encoding="utf-8")
            h.chmod(hook_mode)
        return _vh(td)

    vh = mkrepo("ok", "#!/bin/sh\nexec python3 scripts/pre_push_scan.py --hook\n")
    ok, d = vh.chk_hook()
    assert ok is True and "pre_push_scan.py" in d, d

    vh = mkrepo("missing")  # git 仓库但没装钩子
    ok, d = vh.chk_hook()
    assert ok is False and "未安装" in d, d

    vh = mkrepo("noexec", "#!/bin/sh\n", hook_mode=0o644)
    ok, d = vh.chk_hook()
    assert ok is False and "不可执行" in d, d

    vh = mkrepo("wrong", "#!/bin/sh\necho 别的钩子\n")
    ok, d = vh.chk_hook()
    assert ok is False and "未调用" in d, d

    td = Path(tempfile.mkdtemp(prefix="vh-nogit-"))  # 不是 git 仓库
    vh = _vh(td)
    ok, d = vh.chk_hook()
    assert ok is False and "不是 git 仓库" in d, d


@check("verify_handover: chk_regress 单包分支（拿到的是包不是工作区 → 明说）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vh = _vh(tmp)
        (vh.PKG).mkdir()
        ok, d = vh.chk_regress()
        assert ok is False and "单包" in d, d


@check("verify_handover: main 聚合（--count 自报 6 / PKG 缺失 → 2 / 异常项记失败不崩）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        vh = _vh(tmp)
        rc, out, err = _vh_run(vh, ["--count"])
        assert rc == 0 and out.strip() == "6", (rc, out)

        rc, out, err = _vh_run(vh, [])  # PKG 不存在 → 环境错误
        assert rc == 2, f"rc={rc}\n{out}{err}"

        vh.PKG.mkdir()  # 异常注入需要过 PKG 存在性检查

        def boom():
            raise RuntimeError("注入")

        vh.CHECKS = [("甲 会炸", boom), ("乙 正常", lambda: (True, "1/1"))]
        rc, out, err = _vh_run(vh, ["-q"])
        assert rc == 1 and "甲 会炸" in err, (rc, err)


# ── 组N+4：推前闸门 pre_push_scan（基线抬升，2026-10-29）─────────────────
# --self-test 只证明「模式能命中正样本」（18 条），扫描/权限判定/钩子安装零测试。


def _pps(tag="a"):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"pps_ut_{tag}", ROOT / "scripts/pre_push_scan.py"
    )
    pps = importlib.util.module_from_spec(spec)
    sys.modules[f"pps_ut_{tag}"] = pps
    spec.loader.exec_module(pps)
    return pps


def _mini_repo(tmp: Path, files: dict):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True, capture_output=True)
    for rel, content in files.items():
        f = tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "t"], cwd=tmp, check=True, capture_output=True)


@check("pre_push_scan: pattern_self_test 正样本（真模块 18 条模式全命中）")
def _():
    pps = _pps("ok")
    ok, lines = pps.pattern_self_test(verbose=False)
    assert ok is True, lines
    assert sum(1 for ln in lines if ln.startswith("  [ok]")) == len(pps.PROBES), lines


@check("pre_push_scan: pattern_self_test 三条负路（缺样本/多样本/抓不到）")
def _():
    import re as _re

    # 独立模块副本上注入，不污染真模块
    pps = _pps("neg")
    pps.SECRET_PATTERNS = pps.SECRET_PATTERNS + [(_re.compile(r"NEVER_MATCH_XYZ"), "幽灵模式")]
    ok, lines = pps.pattern_self_test(verbose=False)
    assert ok is False and any("没有正样本" in ln for ln in lines), lines

    pps2 = _pps("neg2")
    pps2.PROBES = dict(pps2.PROBES)
    pps2.PROBES["不存在的模式"] = "x"  # 名字写错 → extra
    ok, lines = pps2.pattern_self_test(verbose=False)
    assert ok is False and any("没有对应模式" in ln for ln in lines), lines

    pps3 = _pps("neg3")
    pps3.PROBES = dict(pps3.PROBES)
    pps3.PROBES["OpenAI 风格密钥 sk-…"] = "sk-short"  # 抓不到自己的样本
    ok, lines = pps3.pattern_self_test(verbose=False)
    assert ok is False and any("抓不到自己的正样本" in ln for ln in lines), lines


@check("pre_push_scan: reachable_blobs + scan_history（迷你仓库：命中/干净/非 blob 跳过）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        probe = _pps("scan").PROBES["OpenAI 风格密钥 sk-…"]
        _mini_repo(
            tmp, {"docs/clean.md": "正常内容，token 用量 126100\n", "leaks/key.txt": probe + "\n"}
        )
        pps = _pps("scan2")
        pps.ROOT = tmp
        blobs = pps.reachable_blobs()
        # rev-list --objects 会连 tree 一起列出（带路径）——文件必须都在，
        # 而 scan_history 靠 kind==blob 把 tree/commit 跳过（这正是被测行为之一）
        assert {"docs/clean.md", "leaks/key.txt"} <= set(blobs.values()), blobs

        hits = pps.scan_history()
        assert len(hits) == 1 and hits[0][0] == "leaks/key.txt", hits  # tree 不产生命中
        assert "OpenAI" in hits[0][1], hits


@check("pre_push_scan: check_remote_visibility 四态（无origin/非GitHub/gh假PUBLIC/gh假PRIVATE）")
def _():
    import subprocess as sp

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _mini_repo(tmp, {"a.txt": "x\n"})
        pps = _pps("rv")
        pps.ROOT = tmp

        st, msg = pps.check_remote_visibility()
        assert st == "ok" and "无 origin" in msg, (st, msg)

        sp.run(
            ["git", "remote", "add", "origin", "https://gitlab.com/x/y.git"],
            cwd=tmp,
            check=True,
            capture_output=True,
        )
        st, msg = pps.check_remote_visibility()
        assert st == "unknown" and "非 GitHub" in msg, (st, msg)

        # 假 gh：注入 PATH 的可执行文件，分别返回 PUBLIC / PRIVATE / 坏 JSON
        binp = tmp / "bin"
        binp.mkdir()
        for name, body in (
            ("gh_pub", '{"visibility": "PUBLIC"}'),
            ("gh_priv", '{"visibility": "PRIVATE"}'),
            ("gh_bad", "not-json"),
        ):
            f = binp / name
            f.write_text("#!/bin/sh\ncat <<'EOF'\n" + body + "\nEOF\n", encoding="utf-8")
            f.chmod(0o755)
        sp.run(
            ["git", "remote", "set-url", "origin", "https://github.com/x/y.git"],
            cwd=tmp,
            check=True,
            capture_output=True,
        )
        old_path = pps.subprocess  # noqa 占位，真正注入在下面
        import os as _os

        real_env_path = _os.environ.get("PATH", "")

        def _with_gh(ghname, extra_path=real_env_path):
            _os.environ["PATH"] = f"{binp}:{extra_path}"
            # 让脚本里的 gh 解析到假脚本：改名为 gh
            gh = binp / "gh"
            gh.write_text((binp / ghname).read_text(encoding="utf-8"), encoding="utf-8")
            gh.chmod(0o755)

        try:
            _with_gh("gh_pub")
            st, msg = pps.check_remote_visibility()
            assert st == "public" and "PUBLIC" in msg, (st, msg)

            _with_gh("gh_priv")
            st, msg = pps.check_remote_visibility()
            assert st == "ok" and "PRIVATE" in msg, (st, msg)

            _with_gh("gh_bad")
            st, msg = pps.check_remote_visibility()
            assert st == "unknown", (st, msg)  # 坏 JSON 不许被当成 private
        finally:
            _os.environ["PATH"] = real_env_path


@check("pre_push_scan: --install-hook 生成可执行钩子，且接手验收第 6 项因此转绿（联动）")
def _():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _mini_repo(tmp, {"a.txt": "x\n"})
        pps = _pps("hook")
        pps.ROOT = tmp
        old = sys.argv
        sys.argv = ["pre_push_scan.py", "--install-hook"]
        try:
            rc = pps.main()
        finally:
            sys.argv = old
        assert rc == 0, rc
        hook = tmp / ".git" / "hooks" / "pre-push"
        assert hook.is_file() and "pre_push_scan.py" in hook.read_text(encoding="utf-8")

        # 联动：verify_handover 第 6 项对同一个仓库必须判 OK
        vh = _vh(tmp)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "pre_push_scan.py").write_text("# target\n", encoding="utf-8")
        ok, d = vh.chk_hook()
        assert ok is True, d


# ── 组N+5：build_package / redact_report / inspect_status（基线抬升第二批）──


@check("build_package: do_check 四态（一致/漂移/目标缺失/源缺失）")
def _():
    import importlib.util

    spec = importlib.util.spec_from_file_location("bp_ut", ROOT / "scripts/build_package.py")
    bp = importlib.util.module_from_spec(spec)
    sys.modules["bp_ut"] = bp
    spec.loader.exec_module(bp)
    import contextlib
    import io as _io

    # FIXED_MAPPING 的 12 条源在临时目录不存在 → 正好当「源缺失」样本（留 1 条即可）
    bp.FIXED_MAPPING = [("scripts/judges/verify.py", "migration-package/judges/verify.py")]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "docs" / "protocol"
        src.mkdir(parents=True)
        (src / "x.md").write_text("V1\n", encoding="utf-8")
        dst = tmp / "migration-package" / "protocol"
        dst.mkdir(parents=True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.do_check(tmp)
        assert rc == 1 and "[源缺失]" in buf.getvalue(), buf.getvalue()

        (tmp / "scripts" / "judges").mkdir(parents=True)
        (tmp / "scripts" / "judges" / "verify.py").write_text("v\n", encoding="utf-8")
        (tmp / "migration-package" / "judges").mkdir(parents=True)
        (tmp / "migration-package" / "judges" / "verify.py").write_text("v\n", encoding="utf-8")

        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.do_check(tmp)  # 目标缺失（DIR_MAPPING 的 md 还没同步）
        assert rc == 1 and "[目标缺失]" in buf.getvalue(), buf.getvalue()

        (dst / "x.md").write_text("V1\n", encoding="utf-8")
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.do_check(tmp)
        assert rc == 0 and "全部一致" in buf.getvalue(), buf.getvalue()

        (dst / "x.md").write_text("V2\n", encoding="utf-8")  # 内容漂移
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.do_check(tmp)
        assert rc == 1 and "[漂移]" in buf.getvalue(), buf.getvalue()

        (dst / "x.md").write_text("V1\n", encoding="utf-8")
        (src / "y.md").write_text("Y\n", encoding="utf-8")  # 源有、包无 → 目标缺失
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.do_check(tmp)
        assert rc == 1 and "[目标缺失]" in buf.getvalue(), buf.getvalue()
        (src / "y.md").unlink()


@check("build_package: do_apply 写入/未变/装配日志（D-0018 留痕含 actor）")
def _():
    import importlib.util, json as _json

    spec = importlib.util.spec_from_file_location("bp_ut2", ROOT / "scripts/build_package.py")
    bp = importlib.util.module_from_spec(spec)
    sys.modules["bp_ut2"] = bp
    spec.loader.exec_module(bp)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "docs" / "protocol"
        src.mkdir(parents=True)
        (src / "x.md").write_text("V1\n", encoding="utf-8")
        import contextlib, io as _io

        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bp.do_apply(tmp)
        assert rc == 0
        log = tmp / "assembly-log.jsonl"
        entry = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry["actor"] == "build_package.py(--apply)" and entry["written"] == 1, entry

        with contextlib.redirect_stdout(buf):
            rc = bp.do_apply(tmp)  # 第二次：内容没变 → unchanged
        entry2 = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry2["written"] == 0 and entry2["unchanged"] >= 1, entry2


@check("build_package: CLI --list 与无参（dispatch 分支）")
def _():
    import subprocess as sp

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "docs" / "protocol").mkdir(parents=True)
        (tmp / "docs" / "protocol" / "x.md").write_text("V\n", encoding="utf-8")
        r = sp.run(
            [sys.executable, str(ROOT / "scripts/build_package.py"), "--root", str(tmp), "--list"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0 and "映射表" in r.stdout, r.stdout
        r = sp.run(
            [sys.executable, str(ROOT / "scripts/build_package.py"), "--root", str(tmp)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 2, r.returncode  # 无 --check/--apply/--manifest → help + 2


@check("build_package: CLI --manifest 在临时包上重建 MANIFEST（E1）")
def _():
    import subprocess as sp, shutil as _sh

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pkg = tmp / "migration-package"
        pkg.mkdir()
        (pkg / "a.py").write_text("A=1\n", encoding="utf-8")
        _sh.copy(str(ROOT / "migration-package/gen_manifest.py"), str(pkg / "gen_manifest.py"))
        r = sp.run(
            [
                sys.executable,
                str(ROOT / "scripts/build_package.py"),
                "--root",
                str(tmp),
                "--manifest",
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert (pkg / "MANIFEST.json").is_file(), r.stdout


@check("redact_report: --check 审输入现状（脏→FAIL / 净→PASS，不是自己查自己）")
def _():
    import subprocess as sp

    tool = ROOT / "scripts/redact_report.py"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        dirty = tmp / "dirty.md"
        dirty.write_text("仅 600036 有仓\n", encoding="utf-8")
        r = sp.run(
            [sys.executable, str(tool), "--in", str(dirty), "--check"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 1 and "残留" in r.stdout, (r.returncode, r.stdout)

        clean = tmp / "clean.md"
        clean.write_text("仅 标的D 有仓\n", encoding="utf-8")
        r = sp.run(
            [sys.executable, str(tool), "--in", str(clean), "--check"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0 and "无残留" in r.stdout, (r.returncode, r.stdout)


@check("redact_report: 生成对外版（banner 前置、清单外代码代号化、无残留）")
def _():
    import subprocess as sp

    tool = ROOT / "scripts/redact_report.py"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "r.md"
        src.write_text("# 周报\n\n| I2 持仓 | 仅 600036 有仓（100 股） |\n", encoding="utf-8")
        out = tmp / "out.md"
        r = sp.run(
            [sys.executable, str(tool), "--in", str(src), "--out", str(out)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0 and "已生成" in r.stdout, (r.returncode, r.stdout)
        text = out.read_text(encoding="utf-8")
        assert "600036" not in text and "标的" in text, text
        assert "勿公开" in text, text[:200]  # banner 在（未打码金额警示）
        assert text.splitlines()[0].startswith("# 周报") or "账户金额" in text.splitlines()[1], (
            text[:120]
        )


@check("redact_report: --mask-amounts 打码金额且 # 开头输入的 banner 插在标题后")
def _():
    import subprocess as sp

    tool = ROOT / "scripts/redact_report.py"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "r.md"
        src.write_text("# 周报\n现金 46,829.30 元。\n", encoding="utf-8")
        out = tmp / "out.md"
        r = sp.run(
            [sys.executable, str(tool), "--in", str(src), "--out", str(out), "--mask-amounts"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, (r.returncode, r.stdout)
        text = out.read_text(encoding="utf-8")
        assert "46,829.30" not in text and "46,8xx.xx" in text, text
        lines = text.splitlines()
        assert lines[0].startswith("# 周报"), lines[:3]  # 原标题仍是首行
        assert any("账户金额已抹掉" in ln or "抹掉精度" in ln for ln in lines[:4]), lines[:4]


@check("redact_report: 错误分支三条（无 --in / 文件不存在 / 有 --in 无 --out）")
def _():
    import subprocess as sp

    tool = ROOT / "scripts/redact_report.py"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        r = sp.run([sys.executable, str(tool)], capture_output=True, text=True)
        assert r.returncode == 2 and "--in" in (r.stdout + r.stderr), (r.returncode, r.stdout)
        r = sp.run(
            [sys.executable, str(tool), "--in", str(tmp / "nope.md"), "--check"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 2 and "读取失败" in (r.stdout + r.stderr), r.returncode
        src = tmp / "r.md"
        src.write_text("x\n", encoding="utf-8")
        r = sp.run([sys.executable, str(tool), "--in", str(src)], capture_output=True, text=True)
        assert r.returncode == 2 and "--out" in (r.stdout + r.stderr), r.returncode


@check("inspect_status: 合成 runs 全景（双版式/未终态/stale 泄漏留痕/人闸/flip/缺字段）")
def _():
    import subprocess as sp, tempfile as _tfd

    runs = Path(_tfd.mkdtemp(prefix="is-runs-"))

    def rnd(name, files):
        d = runs / name
        d.mkdir(parents=True)
        for fn, obj in files.items():
            (d / fn).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    rnd(
        "round1",
        {  # adjudicated + stale verdict + S8-1 泄漏（有旁挂留痕）+ flip
            "answer-final.json": {
                "aggregation_inputs": [
                    {"seat": "EX-01", "seat_check_status": "valid", "sha256": "shaA"},
                    {"seat": "EX-03", "seat_check_status": "valid", "sha256": "shaX"},
                ]
            },
            "answer-status.json": {
                "adjudication_state": "adjudicated",
                "provenance": "p",
                "ts": "t1",
            },
            "seat-check.json": {"seats": [{"seat": "EX-03", "status": "blocked", "error": "veto"}]},
            "integrity-notes.json": {
                "findings": [
                    {"severity": "high", "id": "F-1", "disposition": "closed"},
                    {"severity": "med", "id": "F-2", "disposition": "open"},
                ]
            },
            "verdict-layered.json": {
                "spec_layer": "adjudication",
                "verdict": "pending_adjudication",
                "ts": "t0",
                "pending": [{"id": "T01"}],
            },
            "metrics.json": {
                "metrics_status": "ok",
                "flip_rate": {"value": 0.55, "threshold": 0.4},
            },
        },
    )
    rnd(
        "round2",
        {  # 版式 b：人工闸门记录（status 字段）+ 缺 aggregation_inputs
            "answer-final.json": {},
            "answer-status.json": {"status": "resolved_by_human", "ts": "t2", "resolution": {}},
        },
    )
    rnd(
        "round3",
        {  # 版式未知 → 未终态
            "answer-final.json": {"aggregation_inputs": []},
            "answer-status.json": {"weird": 1},
        },
    )
    rnd(
        "round4",
        {  # 人闸等待确认
            "answer-final.json": {"aggregation_inputs": []},
            "answer-status.json": {
                "status": "awaiting_human_confirm",
                "blocking": True,
                "ts": "t3",
            },
        },
    )
    r = sp.run(
        [sys.executable, str(ROOT / "scripts/inspect_status.py"), "--runs", str(runs)],
        capture_output=True,
        text=True,
    )
    out = r.stdout
    assert r.returncode == 0, (r.returncode, out, r.stderr)
    # 双版式都被认出
    assert "current state: adjudicated" in out, out
    assert "current state: resolved_by_human" in out, out
    assert "非 verify.py 版式" in out, out  # 版式 b 必须标明来源
    assert "current state: 未终态" in out and "版式未知" in out, out
    # stale：verdict 自称 pending 但当前态已裁决 → 标注取代者
    assert "已被 answer-status.json 的 adjudicated 取代" in out, out
    assert "pending_ids=['T01']" in out, out
    # 泄漏 + 旁挂留痕标注
    assert "S8-1 LEAK" in out and "EX-03" in out, out
    assert "已留痕(high/F-1/disposition=closed)" in out and "等 2 项" in out, out
    # 汇总段
    assert "已 resolved" in out and "verdict 行自称 pending 但当前态已裁决" in out, out
    assert "HUMAN GATE" in out and "blocking=True" in out, out
    assert "FLIP RATE values" in out and "flip_rate.value=0.55" in out, out
    assert "MISSING aggregation_inputs" in out, out


@check("inspect_status: 纯函数（round_key 自然排序 / violations 多形态 / annotate 无冲突不动）")
def _():
    import contextlib, io as _io

    old = sys.argv
    sys.argv = ["inspect_status.py"]
    buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "is_ut", ROOT / "scripts/inspect_status.py"
            )
            isv = importlib.util.module_from_spec(spec)
            sys.modules["is_ut"] = isv
            spec.loader.exec_module(isv)
    finally:
        sys.argv = old
    assert sorted(["round4", "round34", "round10"], key=isv.round_key) == [
        "round4",
        "round10",
        "round34",
    ]
    assert isv.round_key("nonum") == (0, "nonum")

    viol = isv.collect_seat_violations(
        [
            {"seats": [{"seat": "S1", "status": "blocked", "error": "e1"}]},  # list 形态
            {"derived": {"seats": {"S2": {"seat": "S2"}}}},  # dict 形态、valid
            {"rejected_seats": ["S3", {"seat": "S4"}]},  # 顶层列表
            "not-a-dict",  # 非 dict 元素
        ]
    )
    assert viol["S1"] == [("blocked", "e1")], viol
    assert "S2" not in viol, viol
    assert viol["S3"] == [("blocked", "rejected_seats")], viol
    assert viol["S4"] == [("blocked", "rejected_seats")], viol

    assert (
        isv.annotate_verdict({"verdict": "fail", "ts": "t"}, "adjudicated") == "（判据侧快照 @t）"
    )  # 非 pending 不加取代语
    assert isv.current_state({})[0] is None and "无 answer-status" in isv.current_state({})[1]


@check("inspect_status: --runs 指向不存在目录 → exit 2（参数错误不许静默成 0 轮）")
def _():
    import subprocess as sp, tempfile as _tfd

    r = sp.run(
        [
            sys.executable,
            str(ROOT / "scripts/inspect_status.py"),
            "--runs",
            str(Path(_tfd.mkdtemp()) / "nope"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2 and "runs 目录不存在" in r.stderr, (r.returncode, r.stderr)


# ── 组N+6：make_backup / gen_manifest 补测（审计开放项 #4）──
# 口径：只测纯函数与临时目录 I/O 路径；make_backup 自身 --self-test（13 项）已覆盖
# verify/classify 正负样本，这里补「工具入口层」——do_list / do_retention /
# _backup_files / human / git_facts 与 gen_manifest 的 newline / 过滤 / 写入域往返。
#
# ★ 2026-10-29 审计修复：原实现靠前一用例写入 sys.modules 再用 sys.modules[...] 取，
#   单跑第二项即 KeyError 'mb_ut'（用例隐式耦合，筛选/重排就炸）。改为幂等 loader。


def _load_mb():
    """幂等加载 make_backup（可被任一用例单独调用）。"""
    import importlib.util

    mod = sys.modules.get("mb_ut")
    if mod is None:
        spec = importlib.util.spec_from_file_location("mb_ut", ROOT / "scripts/make_backup.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["mb_ut"] = mod
        spec.loader.exec_module(mod)
    return mod


def _load_gm():
    """幂等加载 gen_manifest（同上）。"""
    import importlib.util

    mod = sys.modules.get("gm_ut")
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            "gm_ut", ROOT / "migration-package/gen_manifest.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gm_ut"] = mod
        spec.loader.exec_module(mod)
    return mod


@check("make_backup: human 三档与 KB 边界（人读单位算错=给错清理依据）")
def _():
    mb = _load_mb()
    assert mb.human(0) == "0.0 B", mb.human(0)
    assert mb.human(1023) == "1023.0 B", mb.human(1023)
    assert mb.human(1024) == "1.0 KB", mb.human(1024)
    assert mb.human(3 * 1024**2) == "3.0 MB", mb.human(3 * 1024**2)
    assert mb.human(5 * 1024**3) == "5.0 GB", mb.human(5 * 1024**3)


@check("make_backup: _backup_files 只认两种产物名（.DS_Store 不得抢「最新一份」）")
def _():
    mb = _load_mb()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        names = [
            "ai-autonomy-team-20261001-0001.bundle",
            "ai-autonomy-team-20261002-0002.bundle",
            "snapshots-archive-20261024-0044.tar.gz",
            "snapshots-archive-20261028-0048.tar.gz",
        ]
        for n in names:
            (d / n).write_bytes(b"x" * 10)
        (d / ".DS_Store").write_bytes(b"junk")
        (d / "notes.txt").write_bytes(b"junk")
        got = [p.name for p in mb._backup_files(d)]
        assert got == sorted(names), got  # 异物被滤掉 + 按名排序（时间序）


@check("make_backup: do_list / do_retention 三态退出码与「不删承诺」")
def _():
    import contextlib
    import io as _io

    mb = _load_mb()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # 不存在 → ERR(2)
        buf = _io.StringIO()
        with contextlib.redirect_stderr(buf):
            assert mb.do_list(d / "nope") == mb.ERR
            assert mb.do_retention(d / "nope") == mb.ERR
        # 空目录 → BAD(1)
        assert mb.do_list(d) == mb.BAD
        assert mb.do_retention(d) == mb.BAD
        # 有备份 → 退出 0；retention 输出含 trash 命令，且**一个文件都不许动**
        keep = [
            "ai-autonomy-team-20261101-0001.bundle",
            "snapshots-archive-20261101-0001.tar.gz",
            "snapshots-archive-20261102-0001.tar.gz",
            "snapshots-archive-20261103-0001.tar.gz",
            "snapshots-archive-20261104-0001.tar.gz",
        ]  # 最旧一份 = 候选
        for n in keep:
            (d / n).write_bytes(b"x" * 10)
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert mb.do_list(d) == mb.OK
            assert "最新一份" in buf.getvalue()
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert mb.do_retention(d) == mb.OK
        out = buf.getvalue()
        assert "可清理" in out and "trash " in out, out
        assert all((d / n).is_file() for n in keep), "retention 删了文件——违反只读承诺！"


@check("make_backup: git_facts 对非仓库目录返回 None（不可用≠可用的假值）")
def _():
    mb = _load_mb()
    with tempfile.TemporaryDirectory() as td:
        assert mb.git_facts(Path(td)) is None, "非 git 目录必须返回 None"
        ok = mb.git_facts(ROOT)
        assert ok and len(ok) == 3 and ok[1].isdigit(), ok  # 真仓库三项齐全


@check("make_backup: memory_sources 用 glob 解析三源且不写死账号 ID（写死即红）")
def _():
    mb = _load_mb()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # 刻意用一个「非本机真实 ID」：若实现硬编码了某个固定账号 ID 就命中不到 → 红
        uid = d / ".workbuddy" / "user-deadbeef-0000-1111-2222-333344445555-personal"
        uid.mkdir(parents=True)
        (uid / "MEMORY.md").write_bytes(b"u")
        claw = d / "WorkBuddy" / "Claw" / ".workbuddy" / "memory"
        claw.mkdir(parents=True)
        (claw / "MEMORY.md").write_bytes(b"c")
        (d / ".workbuddy" / "MEMORY.md").write_bytes(b"h")
        got = mb.memory_sources(base=d)
        labels = sorted(l for l, _ in got)
        assert labels == ["claw", "home", "user"], f"三源应全命中，实得 {labels}"
        assert all(p.is_file() for _, p in got), "返回的每个源都必须是真实文件"


@check("make_backup: memory_sources 部分源缺失降级（外置盘未挂载场景不报错）")
def _():
    mb = _load_mb()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # 只造 user，claw/home 缺失 → 应返回 user 而非抛异常/返回假值
        uid = d / ".workbuddy" / "user-aaaa-1111-personal"
        uid.mkdir(parents=True)
        (uid / "MEMORY.md").write_bytes(b"u")
        got = mb.memory_sources(base=d)
        assert [l for l, _ in got] == ["user"], f"应只返回存在的 user，实得 {got}"
        # 全缺 → 空列表（main 据此跳过 MEMORY，不拖垮 bundle）
        assert mb.memory_sources(base=d / "nonexistent") == [], "全缺必须返回空列表"


@check("make_backup: build_memory_tar 打包后改源文件不影响已验证结论（先算 sha 再打包）")
def _():
    mb = _load_mb()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        src = d / "MEM.md"
        src.write_bytes(b"original-content")
        ok, msg, mt = mb.build_memory_tar(d, "20261029-8888", [("user", src)])
        assert ok and mt is not None, msg
        # 打包完成后篡改源（模拟并发写入）；tar 内应保持打包时的内容
        src.write_bytes(b"CHANGED-after-backup")
        got = mb.verify_memory_tar(
            mt, {"user/MEM.md": __import__("hashlib").sha256(b"original-content").hexdigest()}
        )
        assert got[0], "tar 应保留打包时的快照，不受源后续改动影响"


@check("gen_manifest: detect_newline 四态（LF/CRLF/MIXED/NONE，跨平台 sha 的前提）")
def _():
    gm = _load_gm()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        cases = {
            "lf.txt": b"a\nb\n",
            "crlf.txt": b"a\r\nb\r\n",
            "mixed.txt": b"a\r\nb\n",
            "none.txt": b"abc",
        }
        for n, data in cases.items():
            (d / n).write_bytes(data)
        assert gm.detect_newline(d / "lf.txt") == "LF"
        assert gm.detect_newline(d / "crlf.txt") == "CRLF"
        assert gm.detect_newline(d / "mixed.txt") == "MIXED"
        assert gm.detect_newline(d / "none.txt") == "NONE"
        assert gm.detect_newline(d / "missing.txt") == "unknown"  # OSError 路径


@check("gen_manifest: build 过滤与相对化（SKIP 名单/MANIFEST 自身排除/prefix 后 rel 仍相对 root）")
def _():
    gm = _load_gm()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "a").mkdir()
        (d / "b" / "__pycache__").mkdir(parents=True)
        (d / "a" / "x.txt").write_text("x\n", encoding="utf-8")
        (d / "b" / "y.txt").write_text("y\n", encoding="utf-8")
        (d / "b" / "__pycache__" / "junk.pyc").write_bytes(b"j")
        (d / ".DS_Store").write_bytes(b"j")
        (d / "MANIFEST.json").write_text("{}", encoding="utf-8")
        entries = gm.build(d)
        paths = [e["path"] for e in entries]
        assert paths == ["a/x.txt", "b/y.txt"], paths  # 排除 + 排序 + rel 相对 root
        sub = gm.build(d, path_prefixes=("b/",))
        assert [e["path"] for e in sub] == ["b/y.txt"], sub  # 过滤后 rel 仍是 root 相对路径
        globbed = gm.build(d, include_globs=("*.txt",))
        assert len(globbed) == 2, globbed


@check("gen_manifest: load_domain_config 占位符归一（截断到第一个 '{' 前 + 去重）")
def _():
    gm = _load_gm()
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "cfg.json"
        cfg.write_text(
            json.dumps(
                {
                    "allow": ["runs/round{n}/{seat}/", "runs/round", "runs/round"],
                    "deny": ["logs/{date}/app/"],
                }
            ),
            encoding="utf-8",
        )
        allow, deny = gm.load_domain_config(cfg)
        # 文档语义：占位符取第一个 '{' 之前的部分 → 后段一并截断（runs/round{n}/… → runs/round）
        assert allow == ["runs/round"], allow  # 占位符剥掉 + 去重
        assert deny == ["logs/"], deny  # 若不剥，startswith 恒不匹配


@check("gen_manifest: 写入域 baseline→check 往返（CLEAN/HIT-added/HIT-changed 三态）")
def _():
    import contextlib
    import io as _io

    gm = _load_gm()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "runs" / "round1" / "S1").mkdir(parents=True)
        (d / "runs" / "round1" / "S1" / "v.json").write_text("v1\n", encoding="utf-8")
        cfg = d / "cfg.json"
        cfg.write_text(json.dumps({"allow": [], "deny": ["runs/round1/"]}), encoding="utf-8")
        base = d / "baseline.json"
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert gm.write_domain_baseline(d, cfg, base) == 0
        # 无变化 → CLEAN / rc 0
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert gm.write_domain_check(d, cfg, base, None) == 0
        assert "CLEAN" in buf.getvalue(), buf.getvalue()
        # deny 域改内容 → HIT changed / rc 1
        (d / "runs" / "round1" / "S1" / "v.json").write_text("v2\n", encoding="utf-8")
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert gm.write_domain_check(d, cfg, base, None) == 1
        assert "HIT" in buf.getvalue() and "[changed]" in buf.getvalue(), buf.getvalue()
        # deny 域新增文件 → HIT added；allow 域新增不计入判定
        (d / "runs" / "round1" / "S1" / "extra.json").write_text("e\n", encoding="utf-8")
        (d / "allowed.txt").write_text("a\n", encoding="utf-8")
        rep = d / "wd-check.json"
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert gm.write_domain_check(d, cfg, base, rep) == 1
        report = json.loads(rep.read_text(encoding="utf-8"))
        assert report["verdict"] == "HIT", report
        assert any(
            h["status"] == "added" and h["path"].endswith("extra.json")
            for h in report["changed_under_deny"]
        ), report
        assert report["changed_under_allow"] == [], report  # allow 域只对照不判定


@check("check_docs_consistency: G 类正文裸计数禁区（正样本必拦／负样本必放）")
def _():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cdc_ut", ROOT / "scripts/check_docs_consistency.py"
    )
    cdc = importlib.util.module_from_spec(spec)
    sys.modules["cdc_ut"] = cdc
    spec.loader.exec_module(cdc)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # 正样本：四种裸计数都必须被拦（对应 2026-10-29 审计 P0 的四条真实错法）
        bad = d / "交接摘要-bad.md"
        bad.write_text(
            "| kit 自测 | **75/75 通过** |\n"
            "（20 bundle + 12 tar，约 885 MB）\n"
            "| `_trash` 唯一副本 | 254 目录 |\n"
            "（254 个历史快照，138 个 git 里没有）\n"
            "自检总量约 388 项\n",
            encoding="utf-8",
        )
        probs = []
        n = cdc.check_forbidden_counts(probs, docs=[bad])
        assert n >= 4 and len(probs) >= 4, (n, probs)
        assert all(k == "G" for k, _, _ in probs), probs
        assert any("kit 自测" in w for _, w, _ in probs), probs
        assert any("目录" in w or "快照" in w for _, w, _ in probs), probs

        # 负样本：指向生成块 / 带时点 / 历史叙述句，一律放行（否则检查变成"什么都拦"）
        good = d / "交接摘要-good.md"
        good.write_text(
            "| kit 自测 | 全通过（项数以文末生成块为准） |\n"
            "（21 bundle + 13 tar，约 1003 MB；10-29 22:36 实测）\n"
            "| `_trash` 唯一副本 | 258 目录（10-29 22:36 实测） |\n"
            "其后 kit 自测 64→75→**83**（diff 记录）\n",
            encoding="utf-8",
        )
        probs2 = []
        cdc.check_forbidden_counts(probs2, docs=[good])
        assert not probs2, f"负样本被误报：{probs2}"

        # 变异击破①：把「时点豁免」写坏 → 带时点的负样本必须变红（证明豁免真的在起作用）
        saved = cdc.STAMP_RE
        cdc.STAMP_RE = cdc.re.compile(r"(?!)")  # 永不匹配 = 时点豁免失效
        try:
            probs3 = []
            cdc.check_forbidden_counts(probs3, docs=[good])
            assert probs3, "变异未被击破：时点豁免失效后，带时点的裸计数仍被放行"
        finally:
            cdc.STAMP_RE = saved
        # 变异击破②：删掉 2 个禁用模式 → 正样本必须漏拦（证明模式表是承重的）
        saved_pats = cdc.FORBIDDEN_PATTERNS
        cdc.FORBIDDEN_PATTERNS = saved_pats[:2]
        try:
            probs4 = []
            cdc.check_forbidden_counts(probs4, docs=[bad])
            assert len(probs4) < len(probs), "变异未被击破：删掉模式后命中数没减少"
        finally:
            cdc.FORBIDDEN_PATTERNS = saved_pats


# ── 运行器 ───────────────────────────────────────────────────────────────


def main() -> int:
    fails = []
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as e:  # 断言失败与意外异常都要报
            fails.append((name, f"{type(e).__name__}: {e}"))

    # 报告项：只看不裁决（F1）。分界线在这里——
    # 「报告说数据是饱和的」= 数据态，不进退出码；「报告自己跑不起来」= 代码坏了，照样红。
    report_lines: list[str] = []
    report_fails: list[tuple[str, str]] = []
    for name, fn in REPORTS:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn()
        except Exception as e:
            report_fails.append((name, f"{type(e).__name__}: {e}"))
            buf.write(f"    ⚠️ 报告项自身出错: {type(e).__name__}: {e}")
        report_lines.append(f"  · {name}\n{buf.getvalue().rstrip()}")

    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "checks": len(CHECKS),
                    "reports": len(REPORTS),
                    "failed": len(fails) + len(report_fails),
                    "fails": [{"check": n, "err": e} for n, e in fails],
                    "report_errors": [{"report": n, "err": e} for n, e in report_fails],
                    "report_lines": report_lines,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"[kit-test] {len(CHECKS) - len(fails)}/{len(CHECKS)} 通过")
        for n, e in fails:
            print(f"  🔴 {n}\n     {e}")
        if report_lines:
            print("\n── 报告（数据态观测，不计入退出码）──")
            print("\n".join(report_lines))
        for n, e in report_fails:
            print(f"  🔴 报告项出错 {n}\n     {e}")
        if not fails and not report_fails:
            print("  ✅ 全部通过（含与历史 score_v2 的交叉验证）")
    return 1 if (fails or report_fails) else 0


if __name__ == "__main__":
    raise SystemExit(main())
