#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_item_review.py —— item_review.py（出题质检机检闸 v1）A1–A9 验收套件。

## 为什么必须有它

item_review 的三个退出码对应「放行 / 拦批 / 打回」，判错方向代价不对称：
放行坏题 = D-0063/D-0064 事故复现（坏题进题库，出分后才发现）；打回好题 = 管线哑火
（出题人改无可改）。更要紧的是它**把判据写死在代码里、不随批次放宽**——判据一旦
悄悄变了，没有任何下游会报警，只有这套件能钉住。

故所有用例都走**真实 CLI**（subprocess 实跑，不 mock 内部函数，与 test_route_gate.py
/ test_kit.py 的 item_gate 组同款惯例），且断言到**数值**而非只断言退出码：
退出码同为零，reject 从 22 变成 3 也是判据被放宽了。

## 覆盖矩阵

| 组 | 验收项 | 内容 |
|---|---|---|
| 真实档案 | A1 | dossier-v2.json → exit 1 / reject 22 / warn 13 / 批次 PASS 88%，逐 code 计数 + 逐 qid 钉点 + Fraction 独立复核 |
| 正样本 | A2 | 长度均衡 + r1 全 corroborated + r3 全 false + blind 全对 → exit 0（仅 C6 饱和 WARN） |
| 可证伪 | A3 | 正样本注入超长正确项 → exit 1 且 code==C3（C3 不是永远不命中） |
| 正交性 | A4 | 同题同时命中 C3 与 C4 → rejects 与 warns 里两条都在（首版「命中即停」会把 WARN 吃成 0） |
| 否决轴 | A5 | 只命中 C1 / 只命中 C2（basis=null）/ 三码齐命中只记一码（优先级 C1>C2>C3） |
| 批次 C6 | A6 | 盲答 0/25 → 批次 REJECT；25/25 → 批次 WARN；恰好 0.3 → PASS（下限为开区间） |
| schema | A7 | 缺 r3 / correct 越界 / options 单项 / 空批 / 坏 JSON / 文件不存在 / 顶层非对象 / veto 字符串 / 非法枚举 → 全 exit 2，且**不产出半份判定** |
| C0 | A8 | 正确 sha256 → 正常判定；错误 sha256 → exit 2；缺字段 → 正常判定且人读输出含「未绑定题面版本」 |
| 边界 | A9 | C3 比值恰 1.3 判命中（≥ 而非 >）、恰小于不命中、合取两半边（最长但倍数不足 / 倍数达标但非最长）、n_others=1、大长度不丢精度 |
| 盲答口径 | A10 | `blind.verdict ∈ options`：A–D 合法通过、`E`/`D.`/`选项D`/`d` → exit 2（格式错不得诊成"不可解"）、带空白归一化、【白盒】脏题不得进 clean |

## 用法

    python3 audit/kit/test_item_review.py                      # 独立运行（本环境无 pytest，主通道）
    python3 -m pytest audit/kit/test_item_review.py -q         # pytest 可用时同样全兼容

退出码：0 全过 ｜ 1 有失败
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from collections import Counter
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
GATE = HERE / "item_review.py"
DOSSIER_V2 = ROOT / "audit/baseline/v2/dossier-v2.json"

# 判据常量：从契约抄一份到测试里，**故意不复用被测模块**——复用就变成"自己验自己"
META_WORDS = ("人工复核", "闭环", "待验证", "持续跟踪", "持续暴露", "跟进")
LEN_RATIO = Fraction(13, 10)   # C3 阈值 1.3，用分数避免浮点
BLIND_MIN_ACC = Fraction(3, 10)

CHECKS: list = []


def case(label: str):
    """记录用例并保留中文标签；返回原函数，pytest 仍按 test_* 收集。"""
    def deco(fn):
        fn._label = label
        CHECKS.append(fn)
        return fn
    return deco


# ── 工具：真实 CLI 实跑 ────────────────────────────────────────────────────

def _run(dossier: Path, json_mode: bool = True) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE), "--dossier", str(dossier)]
    if json_mode:
        cmd.append("--json")
    return subprocess.run(cmd, capture_output=True, text=True)


def _run_obj(obj=None, raw: str | None = None, json_mode: bool = True):
    """写临时 dossier → 实跑 → 返回 (returncode, 解析后的 JSON | None, 合并输出)。"""
    with tempfile.TemporaryDirectory(prefix="ir-") as td:
        p = Path(td) / "dossier.json"
        p.write_text(raw if raw is not None else json.dumps(obj, ensure_ascii=False),
                     encoding="utf-8")
        r = _run(p, json_mode)
    data = None
    if json_mode:
        try:
            data = json.loads(r.stdout)
        except Exception:
            data = None
    return r.returncode, data, r.stdout + r.stderr


# ── 夹具构造（临时目录，不入仓库）─────────────────────────────────────────

def _pad(n: int, prefix: str = "") -> str:
    """造恰好 n 个字符的选项文本；prefix 计入长度。不含任何元话语词。"""
    assert n >= len(prefix), f"长度 {n} 装不下前缀 {prefix!r}"
    return prefix + "字" * (n - len(prefix))


def _sha(options: dict) -> str:
    """C0 契约公式：ensure_ascii=False, sort_keys=True, separators=(",",":") 的 sha256。"""
    canon = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _item(qid: str, texts: dict, correct: str, *, blind: str | None = None,
          r1: str = "corroborated", r1_detail: str | None = None,
          r2: str = "none", veto: bool = False, basis: str | None = None,
          r3_reason: str | None = None, bind_sha: str | None = None) -> dict:
    """造一道合法题目。bind_sha: None=不写该字段；"ok"=写正确值；其他=原样写入（错值用例）。"""
    it = {
        "qid": qid,
        "options": dict(texts),
        "correct": correct,
        "r1": {"verdict": r1},
        "r2": {"type": r2},
        "r3": {"veto": veto, "basis": basis},
        "blind": {"verdict": correct if blind is None else blind},
    }
    if r1_detail is not None:
        it["r1"]["detail"] = r1_detail
    if r3_reason is not None:
        it["r3"]["reason"] = r3_reason
    if bind_sha == "ok":
        it["options_sha256"] = _sha(it["options"])
    elif bind_sha is not None:
        it["options_sha256"] = bind_sha
    return it


def _plain(qid: str, *, correct: str = "A", blind: str | None = None,
           length: int = 12) -> dict:
    """中性题：四选项等长、无元话语、无否决 —— C1/C2/C3/C4 全不命中。"""
    return _item(qid, {k: _pad(length) for k in "ABCD"}, correct, blind=blind)


def _batch(n: int, hits: int) -> dict:
    """n 题中性批，前 hits 题盲答正确（其余盲答 B）—— 用于精确控制 C6 正确率。"""
    items = [_plain(f"B{i:02d}", blind="A" if i <= hits else "B") for i in range(1, n + 1)]
    return {"batch": f"test-{hits}of{n}", "items": items}


# ── 独立判据复核（Fraction 精确算，与被测代码的整数交叉相乘式**不同实现**）──

def _c3_hit(options: dict, correct: str) -> bool:
    lens = {k: len(v) for k, v in options.items()}
    c = lens[correct]
    others = [n for k, n in lens.items() if k != correct]
    if c <= 0 or c != max(lens.values()):
        return False
    avg = Fraction(sum(others), len(others))
    return Fraction(c) >= avg * LEN_RATIO


def _c4_hit(options: dict, correct: str) -> bool:
    if not any(w in options[correct] for w in META_WORDS):
        return False
    return not any(w in t for k, t in options.items() if k != correct for w in META_WORDS)


def _expect_reject_code(it: dict) -> str | None:
    """否决轴优先级 C1>C2>C3，一题只记一码（契约原文）。"""
    if it["r1"]["verdict"] == "contradicted":
        return "C1"
    if it["r3"].get("veto") is True:
        return "C2"
    return "C3" if _c3_hit(it["options"], it["correct"]) else None


def _reject_map(d: dict) -> dict:
    return {r["qid"]: r["code"] for r in d["rejects"]}


# ── A1：真实档案（逐项数值，不接受只看退出码）──────────────────────────────

@case("A1: dossier-v2.json → exit 1、reject 22、warn 13、批次 PASS 88%，逐 code 与逐 qid 钉死")
def test_a1_real_dossier_exact_numbers():
    r = _run(DOSSIER_V2)
    assert r.returncode == 1, f"期望 exit 1，实为 {r.returncode}\n{r.stdout}{r.stderr}"
    d = json.loads(r.stdout)
    assert d["exit"] == 1, d["exit"]
    assert d["n_items"] == 25, f"n_items 应为 25，实为 {d['n_items']}"
    assert d["errors"] == [], f"真实档案不得有 schema 错误：{d['errors']}"
    # summary 含 @batch 条目（C6 命中时）；本批 C6=PASS 故 summary 恰等于题级计数。
    # 别把 summary 当"题级缺陷数"用——A6 里它就含 @batch。
    assert d["summary"] == {"reject": 22, "warn": 13}, \
        f"summary 应为 reject22/warn13，实为 {d['summary']}"
    assert len(d["rejects"]) == 22, f"rejects 长度 22，实为 {len(d['rejects'])}"
    assert len(d["warns"]) == 13, f"warns 长度 13，实为 {len(d['warns'])}"
    bc = d["batch_checks"]
    assert bc["blind_acc"] == 0.88, f"盲答正确率应为 0.88，实为 {bc['blind_acc']}"
    assert bc["verdict"] == "PASS", f"批次应为 PASS，实为 {bc['verdict']}（{bc['reason']}）"
    # 逐 code 计数：C1 1 / C2 4 / C3 17（C3 只记未被 C1/C2 吃掉的题）
    codes = Counter(x["code"] for x in d["rejects"])
    assert dict(codes) == {"C1": 1, "C2": 4, "C3": 17}, f"reject 分码应为 C1:1/C2:4/C3:17，实为 {dict(codes)}"
    wcodes = Counter(x["code"] for x in d["warns"])
    assert dict(wcodes) == {"C4": 13}, f"warn 应全为 C4:13，实为 {dict(wcodes)}"
    assert "@batch" not in wcodes and "@batch" not in codes, "批次 PASS 时不应有 @batch 条目"


@case("A1: 逐 qid 钉点（V2-20→C1、V2-11/13/15/16→C2），并与 Fraction 独立复核逐题一致")
def test_a1_real_dossier_per_qid_oracle():
    d = json.loads(_run(DOSSIER_V2).stdout)
    doc = json.loads(DOSSIER_V2.read_text(encoding="utf-8"))
    got = _reject_map(d)
    assert got.get("V2-20") == "C1", f"V2-20 应判 C1（r1=contradicted），实为 {got.get('V2-20')}"
    for q in ("V2-11", "V2-13", "V2-15", "V2-16"):
        assert got.get(q) == "C2", f"{q} 应判 C2（r3.veto=true），实为 {got.get(q)}"
    # 独立 oracle：不用被测代码的整数式，用 Fraction 精确算，逐题比码
    want = {it["qid"]: c for it in doc["items"] if (c := _expect_reject_code(it))}
    assert got == want, "\n".join(
        ["reject 表与独立复核不一致："]
        + [f"  {q}: 实现 {got.get(q)} vs 复核 {want.get(q)}"
           for q in sorted(set(got) | set(want)) if got.get(q) != want.get(q)])
    # C4 记档同样逐题复核（13 条）
    want_w = {it["qid"] for it in doc["items"] if _c4_hit(it["options"], it["correct"])}
    got_w = {w["qid"] for w in d["warns"]}
    assert got_w == want_w, f"C4 记档不一致——多 {sorted(got_w - want_w)}，少 {sorted(want_w - got_w)}"
    assert len(want_w) == 13, f"C4 原始命中应为 13 题，实为 {len(want_w)}"
    # 正交性口径要分清「判据命中」与「记码」两个层次：
    #   原始 C3 判据命中 22 题，其中 17 题记码 C3、5 题被 C1/C2 抢先记码（C3 判据同样成立）；
    #   13 题 C4 全部同时满足 C3 判据，但只有 10 题记码为 C3（V2-11/13/15 被 C2 抢先）。
    raw_c3 = {it["qid"] for it in doc["items"] if _c3_hit(it["options"], it["correct"])}
    assert len(raw_c3) == 22, f"C3 原始命中应为 22 题，实为 {len(raw_c3)}"
    assert raw_c3 == set(want), \
        f"rejects 集合应恰等于 C3 判据命中集（全部 22 题都应被拦下）：" \
        f"多 {sorted(set(want) - raw_c3)}，少 {sorted(raw_c3 - set(want))}"
    assert want_w <= raw_c3, f"C4 命中但 C3 不命中的题（正交性会失效）：{sorted(want_w - raw_c3)}"
    c3_coded_warned = {q for q, c in want.items() if c == "C3"} & want_w
    assert len(c3_coded_warned) == 10, \
        f"记码 C3 且同时 C4 的应为 10 题（另 3 题被 C2 抢先），实为 {len(c3_coded_warned)}"


# ── A2：正样本（好批必须放行）────────────────────────────────────────────

@case("A2: 正样本（长度均衡/r1 全 corroborated/r3 全 false/blind 全对）→ exit 0，仅 C6 饱和 WARN")
def test_a2_positive_sample_exit0():
    rc, d, out = _run_obj(_batch(5, hits=5))
    assert rc == 0, f"正样本期望 exit 0，实为 {rc}\n{out}"
    assert d["errors"] == [], d["errors"]
    assert d["rejects"] == [], f"正样本不得有 REJECT：{d['rejects']}"
    assert [w["code"] for w in d["warns"]] == ["C6"], \
        f"5/5 盲答全对应只留批次饱和 WARN（无题级 C4），实为 {d['warns']}"
    assert d["warns"][0]["qid"] == "@batch", d["warns"][0]
    assert d["batch_checks"]["blind_acc"] == 1.0, d["batch_checks"]
    assert d["batch_checks"]["verdict"] == "WARN", d["batch_checks"]


@case("A2: 正样本（盲答 2/5 未饱和）→ exit 0 且 rejects / warns 全空（C3/C4 不误伤）")
def test_a2_positive_sample_no_warn():
    rc, d, out = _run_obj(_batch(5, hits=2))
    assert rc == 0, f"期望 exit 0，实为 {rc}\n{out}"
    assert d["rejects"] == [] and d["warns"] == [], (d["rejects"], d["warns"])
    assert d["batch_checks"]["verdict"] == "PASS", d["batch_checks"]


# ── A3：C3 可证伪（正样本注入超长正确项必须变红）──────────────────────────

@case("A3: 正样本注入超长正确项（40 字 vs 其余 10 字）→ exit 1 且 code==C3")
def test_a3_c3_falsifiable():
    items = [_plain(f"P{i:02d}", blind="A" if i <= 3 else "B") for i in range(1, 6)]
    texts = {"A": _pad(10), "B": _pad(10), "C": _pad(10), "D": _pad(40)}   # 无元话语，只泄漏长度
    items.append(_item("LONG", texts, "D", blind="B"))
    rc, d, out = _run_obj({"batch": "a3", "items": items})
    assert rc == 1, f"注入超长正确项后应 exit 1，实为 {rc}\n{out}"
    assert len(d["rejects"]) == 1, f"只应命中 1 条，实为 {d['rejects']}"
    r0 = d["rejects"][0]
    assert r0["qid"] == "LONG" and r0["code"] == "C3", r0
    assert "长度泄漏" in r0["reason"], r0["reason"]
    assert d["batch_checks"]["blind_acc"] == 0.5, d["batch_checks"]   # 不靠批次项搅局


# ── A4：C3 与 C4 正交（同题两条都要在）────────────────────────────────────

@case("A4: 同题同时命中 C3 与 C4 → rejects 含 C3 且 warns 含 C4（同题双轴并存）")
def test_a4_c3_c4_orthogonal():
    texts = {"A": _pad(10), "B": _pad(10), "C": _pad(10), "D": _pad(40, "人工复核")}
    items = [_item("BOTH", texts, "D", blind="A")]          # 盲答错，拉低 C6 避免饱和
    items += [_plain(f"P{i:02d}", blind="A" if i <= 2 else "B") for i in range(1, 4)]
    rc, d, out = _run_obj({"batch": "a4", "items": items})
    assert rc == 1, f"期望 exit 1，实为 {rc}\n{out}"
    rej = _reject_map(d)
    wmap = {w["qid"]: w["code"] for w in d["warns"]}
    assert rej.get("BOTH") == "C3", f"BOTH 应进 rejects 且 code==C3，实为 {rej}"
    assert wmap.get("BOTH") == "C4", f"BOTH 应同时进 warns 且 code==C4（正交性），实为 {wmap}"
    assert d["batch_checks"]["verdict"] == "PASS", d["batch_checks"]   # 3/4=0.75，批次不搅局
    assert len(d["rejects"]) == 1 and len(d["warns"]) == 1, (d["rejects"], d["warns"])


# ── A5：C1 / C2 独立命中 + 否决轴优先级 ──────────────────────────────────

@case("A5: 只命中 C1（r1=contradicted）→ exit 1 且 code==C1，理由引用 r1.detail")
def test_a5_c1_only():
    items = [_item("C1ONLY", {k: _pad(12) for k in "ABCD"}, "A",
                   r1="contradicted", r1_detail="题面写合计 87.40，规则写 52.00，二者矛盾",
                   blind="A")]
    items += [_plain(f"P{i:02d}", blind="A" if i == 1 else "B") for i in range(1, 4)]
    rc, d, out = _run_obj({"batch": "a5c1", "items": items})
    assert rc == 1, f"期望 exit 1，实为 {rc}\n{out}"
    assert _reject_map(d) == {"C1ONLY": "C1"}, f"应只命中 C1，实为 {d['rejects']}"
    assert "矛盾" in d["rejects"][0]["reason"], f"C1 理由应引用 r1.detail：{d['rejects'][0]['reason']}"
    assert d["warns"] == [], f"2/4=0.5 未饱和，不该有批次 WARN：{d['warns']}"


@case("A5: 只命中 C2（r3.veto=true，basis 为 null）→ exit 1 且 code==C2 且理由可追溯")
def test_a5_c2_only_with_null_basis():
    items = [_item("C2ONLY", {k: _pad(12) for k in "ABCD"}, "A", veto=True, basis=None, blind="A")]
    items += [_plain(f"P{i:02d}", blind="A" if i == 1 else "B") for i in range(1, 4)]
    rc, d, out = _run_obj({"batch": "a5c2", "items": items})
    assert rc == 1, f"期望 exit 1，实为 {rc}\n{out}"
    assert d["errors"] == [], f"basis=null 是合法值，不该报 schema 错：{d['errors']}"
    assert _reject_map(d) == {"C2ONLY": "C2"}, f"应只命中 C2，实为 {d['rejects']}"
    reason = d["rejects"][0]["reason"]
    assert "R3 否决" in reason and "None" in reason, \
        f"无 r3.reason 时应回退到 'R3 否决（basis=...）'，basis=null 也要打出来：{reason}"


@case("A5: 一题同时满足 C1+C2+C3 → 只记一个码，且优先级为 C1（否决轴顺序即理由优先级）")
def test_a5_priority_c1_over_c2_over_c3():
    texts = {"A": _pad(40), "B": _pad(10), "C": _pad(10), "D": _pad(10)}
    items = [_item("TRIPLE", texts, "A", r1="contradicted", r1_detail="题面自相矛盾",
                   veto=True, basis="ambiguous_gate", blind="A")]
    items += [_plain(f"P{i:02d}", blind="A" if i == 1 else "B") for i in range(1, 4)]
    rc, d, out = _run_obj({"batch": "a5prio", "items": items})
    assert rc == 1, f"期望 exit 1，实为 {rc}\n{out}"
    assert len(d["rejects"]) == 1, f"一题只能记一个否决码，实为 {d['rejects']}"
    assert d["rejects"][0]["code"] == "C1", f"三码齐命中应记 C1，实为 {d['rejects'][0]}"


# ── A6：C6 两个边界（0/25 与 25/25）+ 下限开区间 ────────────────────────

@case("A6: 盲答 0/25（<0.3）→ 批次 REJECT、exit 1、rejects 里多一条 @batch C6")
def test_a6_blind_zero_batch_reject():
    rc, d, out = _run_obj(_batch(25, hits=0))
    assert rc == 1, f"批次 REJECT 也必须 exit 1，实为 {rc}\n{out}"
    assert len(d["rejects"]) == 1, f"题级无缺陷，只应有 @batch 一条，实为 {d['rejects']}"
    b = d["rejects"][0]
    assert b["qid"] == "@batch" and b["code"] == "C6", b
    assert "不可解" in b["reason"], b["reason"]
    bc = d["batch_checks"]
    assert bc["blind_acc"] == 0.0 and bc["verdict"] == "REJECT", bc


@case("A6: 盲答 25/25（=1.0）→ 批次 WARN、exit 0、warns 里多一条 @batch C6")
def test_a6_blind_full_batch_warn():
    rc, d, out = _run_obj(_batch(25, hits=25))
    assert rc == 0, f"仅批次 WARN 时 exit 应为 0（可含 WARN），实为 {rc}\n{out}"
    assert d["rejects"] == [], d["rejects"]
    assert len(d["warns"]) == 1, f"只应有 @batch 一条，实为 {d['warns']}"
    w = d["warns"][0]
    assert w["qid"] == "@batch" and w["code"] == "C6", w
    assert "饱和" in w["reason"], w["reason"]
    bc = d["batch_checks"]
    assert bc["blind_acc"] == 1.0 and bc["verdict"] == "WARN", bc


@case("A6: 盲答恰 3/10 == 0.3 → 批次 PASS（判据是 <0.3 才 REJECT，下限为开区间）")
def test_a6_blind_boundary_exactly_03():
    rc, d, out = _run_obj(_batch(10, hits=3))
    assert rc == 0, f"恰 0.3 不该拦批，期望 exit 0，实为 {rc}\n{out}"
    bc = d["batch_checks"]
    assert bc["blind_acc"] == 0.3 and bc["verdict"] == "PASS", bc
    assert d["rejects"] == [] and d["warns"] == [], (d["rejects"], d["warns"])


# ── A7：schema 负样本（全部 exit 2，且不产出半份判定）────────────────────

def _mutations() -> list[tuple[str, dict | str, str]]:
    """(标签, dossier 对象或原始 JSON 文本, errors 里应出现的关键词)。"""
    def base(**kw) -> dict:
        it = _plain("N01")
        it.update(kw)
        return {"batch": "neg", "items": [it]}

    no_r3 = _plain("N01")
    del no_r3["r3"]
    no_blind = _plain("N01")
    del no_blind["blind"]
    no_r2 = _plain("N01")
    del no_r2["r2"]
    no_qid = _plain("N01")
    del no_qid["qid"]
    return [
        ("缺 r3", {"batch": "neg", "items": [no_r3]}, "r3"),
        ("缺 r2", {"batch": "neg", "items": [no_r2]}, "r2"),
        ("缺 blind", {"batch": "neg", "items": [no_blind]}, "blind"),
        ("缺 qid", {"batch": "neg", "items": [no_qid]}, "qid"),
        ("correct 不在 options", base(correct="D", options={k: _pad(12) for k in "ABC"}),
         "不在 options"),
        ("options 单项", base(correct="A", options={"A": _pad(12)}), "少于 2 项"),
        ("options 非对象", base(options=["A", "B"]), "options"),
        ("options 文本非字符串", base(options={"A": 1, "B": _pad(12)}), "字符串"),
        ("空 items", {"batch": "neg", "items": []}, "items 为空"),
        ("缺 items", {"batch": "neg"}, "items"),
        ("r3.veto 用字符串 'true'", base(r3={"veto": "true", "basis": None}), "布尔"),
        ("r1.verdict 非法", base(r1={"verdict": "ok"}), "r1.verdict"),
        ("r2.type 非法", base(r2={"type": "weird"}), "r2.type"),
        ("blind.verdict 空串", base(blind={"verdict": "  "}), "blind.verdict"),
        ("坏 JSON", "{items: [不是JSON]]", "无法解析"),
        ("顶层非对象", '["顶层是数组"]', "顶层"),
    ]


@case("A7: schema 负样本 16 例 → 全部 exit 2 且 errors 非空、不产出任何判定（fail-safe）")
def test_a7_schema_negatives():
    bad = []
    for label, payload, kw in _mutations():
        rc, d, out = _run_obj(None if isinstance(payload, str) else payload,
                              raw=payload if isinstance(payload, str) else None)
        if rc != 2:
            bad.append(f"{label}: 期望 exit 2，实为 {rc}") 
            continue
        if not (d and d["exit"] == 2 and d["errors"]):
            bad.append(f"{label}: exit 2 但 --json 里无 errors / exit 不符：{d}")
            continue
        if not any(kw in e for e in d["errors"]):
            bad.append(f"{label}: errors 未命中关键词 {kw!r}：{d['errors']}")
        if d["rejects"] or d["warns"]:
            bad.append(f"{label}: exit 2 却产出了判定（半份结果）rejects={d['rejects']} warns={d['warns']}")
        # exit 2 也必须是三值枚举闭包内的一致结论，不能是空/缺键（下游按枚举分支）
        bc = d.get("batch_checks") or {}
        if bc.get("verdict") not in ("PASS", "WARN", "REJECT"):
            bad.append(f"{label}: exit 2 时 batch_checks.verdict 不在枚举内：{bc}")
        elif bc["verdict"] != "REJECT":
            bad.append(f"{label}: exit 2 是 fail-safe，batch_checks.verdict 应为 REJECT：{bc}")
    assert not bad, "\n".join(["schema 负样本未按契约处置："] + [f"  {b}" for b in bad])


@case("A7: 文件不存在 → exit 2 且 errors 说明文件不存在（不是 traceback）")
def test_a7_missing_file():
    with tempfile.TemporaryDirectory(prefix="ir-") as td:
        r = _run(Path(td) / "no-such-dossier.json")
    assert r.returncode == 2, f"文件不存在期望 exit 2，实为 {r.returncode}\n{r.stdout}{r.stderr}"
    assert "Traceback" not in r.stderr, f"不得抛 traceback：{r.stderr}"
    d = json.loads(r.stdout)
    assert d["errors"] and any("文件不存在" in e for e in d["errors"]), d["errors"]
    assert d["rejects"] == [] and d["warns"] == [], d
    assert d["batch_checks"]["verdict"] == "REJECT", d["batch_checks"]   # fail-safe 不放过


# ── A8：C0 题面版本绑定（options_sha256）─────────────────────────────────

@case("A8: 测试侧 sha256 公式与真实档案逐题一致（先自证夹具公式没抄错）")
def test_a8_sha_formula_matches_real_dossier():
    doc = json.loads(DOSSIER_V2.read_text(encoding="utf-8"))
    bad = [it["qid"] for it in doc["items"]
           if it.get("options_sha256") != _sha(it["options"])]
    assert not bad, f"档案里这些题的 options_sha256 与契约公式不符：{bad}（本套件公式需复核）"


@case("A8: 正确 options_sha256 → 正常判定（exit 0、无 errors、判定与未绑定一致）")
def test_a8_c0_correct_sha_normal():
    bare = [_plain(f"S{i:02d}", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    bound = [dict(it, options_sha256=_sha(it["options"])) for it in bare]
    rc, d, out = _run_obj({"batch": "a8ok", "items": bound})
    assert rc == 0, f"绑定了正确 sha256 应正常判定 exit 0，实为 {rc}\n{out}"
    assert d["errors"] == [], d["errors"]
    assert d["rejects"] == [] and d["warns"] == [], (d["rejects"], d["warns"])
    # 与不带 sha 的同批判定完全等价（C0 只做版本校验，不得改变判据）
    rc2, d2, out2 = _run_obj({"batch": "a8ok", "items": bare})
    assert (rc2, d2["summary"], d2["batch_checks"]) == (rc, d["summary"], d["batch_checks"]), \
        f"绑定 sha 与否的判定必须一致：{(rc2, d2['summary'])} vs {(rc, d['summary'])}"


@case("A8: 错误 options_sha256（题面被改）→ exit 2，errors 定位到 qid，不产出判定")
def test_a8_c0_wrong_sha_exit2():
    items = [_plain("S01", blind="A")]
    items[0]["options_sha256"] = "0" * 64
    rc, d, out = _run_obj({"batch": "a8bad", "items": items})
    assert rc == 2, f"sha256 不符必须 exit 2（题面被改却仍判分＝最危险），实为 {rc}\n{out}"
    assert d["errors"], d
    hit = [e for e in d["errors"] if "S01" in e]
    assert hit, f"errors 应定位到 qid S01：{d['errors']}"
    assert any(("sha" in e.lower()) or ("题面" in e) for e in hit), \
        f"errors 应说明是题面版本不符：{hit}"
    assert d["rejects"] == [] and d["warns"] == [], d


@case("A8: 缺 options_sha256 → 正常判定，且人读输出提示「未绑定题面版本」")
def test_a8_c0_missing_field_hint():
    items = [_plain(f"S{i:02d}", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    rc, d, out = _run_obj({"batch": "a8miss", "items": items}, json_mode=False)
    assert rc == 0, f"缺字段不该拦批（向后兼容旧档案），期望 exit 0，实为 {rc}\n{out}"
    assert "未绑定题面版本" in out, f"人读输出缺「未绑定题面版本」提示：\n{out}"
    assert "（4 题）" in out, f"提示应给出未绑定的题数 4：\n{out}"


@case("A8: 等长篡改（选项改一个字、长度不变）→ 只有 C0 能发现，必须 exit 2")
def test_a8_c0_equal_length_tamper_detected():
    # C3/C4 都看不出来（长度与措辞都没变），这正是 C0 存在的唯一理由
    it = _plain("T01", blind="A")
    it["options_sha256"] = _sha(it["options"])          # 先按原题面绑定
    it["options"]["A"] = _pad(12).replace("字", "词")   # 改字不改长
    assert len(it["options"]["A"]) == 12, "篡改后长度必须不变，否则用例混入了 C3 因素"
    rc, d, out = _run_obj({"batch": "a8tamper", "items": [it]})
    assert rc == 2, f"等长篡改必须被 C0 拦住，实为 exit {rc}\n{out}"
    assert any("T01" in e and "sha256" in e.lower() for e in d["errors"]), d["errors"]


@case("A8: 选项键序不同/哈希大写 → 同一题面必须判为未改动（C0 不得误杀合法档案）")
def test_a8_c0_key_order_and_uppercase_not_false_positive():
    base = _plain("K01", blind="A")
    sh = _sha(base["options"])
    permuted = dict(reversed(list(base["options"].items())))   # 键序反转，内容不变
    it = dict(base, options=permuted, options_sha256=sh)
    rc, d, out = _run_obj({"batch": "a8order", "items": [it]})
    assert rc == 0, f"键序变化不该被判篡改（哈希按 sort_keys 归一），实为 exit {rc}\n{out}"
    it2 = dict(base, options_sha256=sh.upper())
    rc2, d2, out2 = _run_obj({"batch": "a8upper", "items": [it2]})
    assert rc2 == 0, f"大写十六进制是同值哈希，不该被判篡改，实为 exit {rc2}\n{out2}"
    assert d2["errors"] == [], d2["errors"]


@case("A8: options_sha256 为 null / 空串 / 全空白 → 一律视同未绑定（缺 ≠ 声明了错哈希）")
def test_a8_c0_blank_values_treated_as_missing():
    # 三种「没填」的常见占位：出题管线用空串占位时不得被一律判成篡改
    for blank in (None, "", "   ", "\t\n"):
        items = [_plain(f"S{i:02d}", blind="A" if i <= 2 else "B") for i in range(1, 5)]
        for it in items:                              # 先全部绑定，只把一题改成空白值
            it["options_sha256"] = _sha(it["options"])
        items[1]["options_sha256"] = blank
        rc, d, out = _run_obj({"batch": "a8blank", "items": items}, json_mode=False)
        assert rc == 0, f"占位值 {blank!r} 不该 exit 2（不是声明了错哈希），实为 {rc}\n{out}"
        assert "未绑定题面版本（1 题）" in out, \
            f"{blank!r} 应只计 1 题未绑定：\n{out}"


@case("A8: 非字符串 options_sha256（如 12345）→ 视同不符 exit 2（不能静默跳过校验）")
def test_a8_c0_non_string_sha_rejected():
    for bad in (12345, 0, True, ["x"], {"h": "x"}):
        it = _plain("N01", blind="A")
        it["options_sha256"] = bad
        rc, d, out = _run_obj({"batch": "a8type", "items": [it]})
        assert rc == 2, f"非字符串哈希值 {bad!r} 应视同不符 → exit 2，实为 {rc}\n{out}"
        assert any("N01" in e and "sha256" in e.lower() for e in d["errors"]), \
            f"{bad!r} 未报出哈希不符：{d['errors']}"
        assert d["rejects"] == [] and d["warns"] == [], f"{bad!r} 不得产出判定：{d}"


@case("A8: 正确哈希但带首尾空白 → 视为匹配（同一哈希值的不同书写）")
def test_a8_c0_whitespace_padded_sha_accepted():
    bare = [_plain(f"S{i:02d}", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    padded = [dict(it, options_sha256=f"  {_sha(it['options'])}\n") for it in bare]
    rc, d, out = _run_obj({"batch": "a8pad", "items": padded})
    assert rc == 0, f"首尾空白不影响哈希取值，不该 exit 2，实为 {rc}\n{out}"
    assert d["errors"] == [], d["errors"]
    rc2, d2, out2 = _run_obj({"batch": "a8pad", "items": bare})
    assert (rc2, d2["batch_checks"]) == (rc, d["batch_checks"]), "带空白的判定应与不带完全一致"


@case("A8: 混合批（2 绑定 + 2 未绑定）→ 正常判定，提示只数未绑定的 2 题")
def test_a8_c0_mixed_batch_counts_unbound_only():
    items = [_plain(f"S{i:02d}", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    for it in items[:2]:
        it["options_sha256"] = _sha(it["options"])
    rc, d, out = _run_obj({"batch": "a8mix", "items": items}, json_mode=False)
    assert rc == 0, f"混合批不该拦，实为 exit {rc}\n{out}"
    assert "未绑定题面版本（2 题）" in out, f"提示应只数未绑定的 2 题（已绑定的不算）：\n{out}"


@case("A8: 真实档案已全题绑定 → 人读输出不得出现「未绑定题面版本」提示")
def test_a8_real_dossier_fully_bound():
    r = _run(DOSSIER_V2, json_mode=False)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "未绑定题面版本" not in r.stdout, \
        f"dossier-v2.json 25 题都带 options_sha256，不该提示未绑定：\n{r.stdout}"


# ── A9：C3 边界（整数长度精确构造，恰 1.3 必须命中）─────────────────────

@case("A9: C3 比值恰为 1.3（13 字 vs 其余各 10 字）→ 必须命中（判据是 ≥ 不是 >）")
def test_a9_ratio_exactly_13_boundary_hit():
    # 前置自证：这是真边界（13/10.0 恰好压线），实现若用浮点 avg*1.3 比较也必须给同一答案
    assert Fraction(13) == Fraction(10) * LEN_RATIO, "夹具长度没落在 1.3 压线上"
    texts = {"A": _pad(13), "B": _pad(10), "C": _pad(10), "D": _pad(10)}
    items = [_item(f"E{i:02d}", texts, "A", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    rc, d, out = _run_obj({"batch": "a9eq", "items": items})
    assert rc == 1, f"恰 1.3 应判命中 → exit 1，实为 {rc}\n{out}"
    assert [r["code"] for r in d["rejects"]] == ["C3"] * 4, \
        f"4 题应全命中 C3，实为 {[(r['qid'], r['code']) for r in d['rejects']]}"
    assert "1.3×" in d["rejects"][0]["reason"], \
        f"理由应带预注册阈值 1.3×（不断言整句/派生数值，避免文案微调即脆断）：" \
        f"{d['rejects'][0]['reason']}"


@case("A9: C3 是合取——最长但倍数不足（12 vs 10/10/10，1.2×）→ 不命中")
def test_a9_ratio_below_threshold_miss():
    # 合取的另一半（「倍数达标但非最长」见 test_a9_conjunction_not_longest_miss）：
    # 两个前置断言让「这个负样本究竟在测哪一半」不可能被后人改坏。
    texts = {"A": _pad(12), "B": _pad(10), "C": _pad(10), "D": _pad(10)}
    assert 12 == max(len(v) for v in texts.values()), "夹具前提：正确项须最长"
    assert not Fraction(12) >= Fraction(10) * LEN_RATIO, "夹具前提：倍数须不达标"
    items = [_item(f"F{i:02d}", texts, "A", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    rc, d, out = _run_obj({"batch": "a9lt", "items": items})
    assert rc == 0, f"1.2× 不该命中 → exit 0，实为 {rc}\n{out}"
    assert d["rejects"] == [], f"1.2× 被误判为长度泄漏（阈值被下探）：{d['rejects']}"


@case("A9: C3 是合取——仅倍数达标但非最长（50 vs 其余 100/1/1，均值 34）→ 不命中")
def test_a9_conjunction_not_longest_miss():
    # 协议 §5 反例：50/34 = 1.47× ≥ 1.3 达标，但 100 字才是最长项 → 数长度猜不到答案，不算泄漏。
    # 实现若把「最长」与「比值」写成 or，此例会红。
    texts = {"A": _pad(50), "B": _pad(100), "C": _pad(1), "D": _pad(1)}
    avg = Fraction(100 + 1 + 1, 3)
    assert Fraction(50) >= avg * LEN_RATIO, "夹具前提：倍数须达标（否则测不到「非最长」这一半）"
    assert 50 != max(len(v) for v in texts.values()), "夹具前提：正确项不能是最长"
    items = [_item(f"J{i:02d}", texts, "A", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    rc, d, out = _run_obj({"batch": "a9and", "items": items})
    assert rc == 0, f"仅倍数达标但非最长不该命中 C3，期望 exit 0，实为 {rc}\n{out}"
    assert d["rejects"] == [], \
        f"C3 被写成析取了（倍数达标就拦）：{d['rejects']}"


@case("A9: n_others==1（两选项，13 字 vs 10 字）恰 1.3 → 命中；大长度 130 vs 100 同样命中")
def test_a9_two_options_and_large_lengths():
    two = {"A": _pad(13), "B": _pad(10)}
    items = [_item(f"G{i:02d}", two, "A", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    rc, d, out = _run_obj({"batch": "a9n1", "items": items})
    assert rc == 1, f"两选项恰 1.3 应命中，实为 {rc}\n{out}"
    assert [r["code"] for r in d["rejects"]] == ["C3"] * 4, d["rejects"]
    assert d["errors"] == [], f"两选项是合法 schema，不该报错：{d['errors']}"

    big = {"A": _pad(130), "B": _pad(100), "C": _pad(100), "D": _pad(100)}
    items = [_item(f"H{i:02d}", big, "A", blind="A" if i <= 2 else "B") for i in range(1, 5)]
    rc, d, out = _run_obj({"batch": "a9big", "items": items})
    assert rc == 1, f"三位数长度恰 1.3 也应命中（不得因精度丢失漏判），实为 {rc}\n{out}"
    assert [r["code"] for r in d["rejects"]] == ["C3"] * 4, d["rejects"]


@case("A8: exit 2 时不得打「未绑定题面版本」提示（没有结论可对齐，同屏自相矛盾）")
def test_a8_no_unbound_hint_on_exit2():
    # D1 钉死：脏题不参与判定，其未绑定计数也不该出现在「本报告的结论…」这种措辞里。
    clean_it = _plain("U01", blind="A")                 # 干净但未绑定
    dirty_it = _plain("U02", blind="A")
    del dirty_it["r3"]                                  # 脏题（也未绑定）
    rc, d, out = _run_obj({"batch": "a8d1", "items": [clean_it, dirty_it]}, json_mode=False)
    assert rc == 2, f"含脏题应 exit 2，实为 {rc}\n{out}"
    assert "未产出任何判定" in out, f"exit 2 必须明确声明未产出判定：\n{out}"
    assert "未绑定题面版本" not in out, \
        f"exit 2 时不该出现「未绑定题面版本」提示（没有结论可对齐）：\n{out}"


# ── A10：blind.verdict 必须是选项键之一（格式错误不得当成可解性信号）────────

@case("A10: blind.verdict 取自 A–D 且都在 options 键内 → 正常判定（不得误报）")
def test_a10_blind_verdict_valid_letters_pass():
    # 覆盖四条合法值各一次，且半数盲答错使 C6=PASS（批次条目不掺进断言）
    items = []
    for i, key in enumerate("ABCD", 1):
        items.append(_item(f"L{i:02d}", {k: _pad(12) for k in "ABCD"}, key, blind=key))
    for i, key in enumerate("ABCD", 5):
        items.append(_item(f"L{i:02d}", {k: _pad(12) for k in "ABCD"}, key, blind="A" if key != "A" else "B"))
    rc, d, out = _run_obj({"batch": "a10ok", "items": items})
    assert rc == 0, f"合法 blind 值不该拦批，期望 exit 0，实为 {rc}\n{out}"
    assert d["errors"] == [], f"合法 blind 值不得报 schema 错：{d['errors']}"
    assert d["rejects"] == [] and d["warns"] == [], (d["rejects"], d["warns"])
    assert d["batch_checks"]["verdict"] == "PASS", d["batch_checks"]


@case("A10: blind.verdict 为 'E' / 'D.' / '选项D' / 'd' → exit 2 并定位 qid（格式错≠不可解）")
def test_a10_blind_verdict_illegal_exit2():
    bad = []
    for value in ("E", "D.", "选项D", "选项A", "d", "DD"):
        it = _item("B01", {k: _pad(12) for k in "ABCD"}, "D", blind="A")
        it["blind"]["verdict"] = value
        rc, d, out = _run_obj({"batch": "a10bad", "items": [it]})
        if rc != 2:
            bad.append(f"blind={value!r}: 期望 exit 2（格式错误不得当作可解性信号），实为 {rc}")
            continue
        msg = [e for e in d["errors"] if "B01" in e and "blind" in e]
        if not msg:
            bad.append(f"blind={value!r}: errors 未定位到 B01 的 blind.verdict：{d['errors']}")
        elif not any("不在 options" in e for e in msg):
            bad.append(f"blind={value!r}: 未说明「不在 options 键内」：{msg}")
        if d["rejects"] or d["warns"]:
            bad.append(f"blind={value!r}: exit 2 却产出了判定：{d['rejects']}{d['warns']}")
    assert not bad, "\n".join(["blind.verdict 合法性校验未按契约处置："] + [f"  {b}" for b in bad])


@case("A10: 两选项题 blind 写 'D'（键不存在）→ exit 2（不能靠选项个数兜住）")
def test_a10_blind_verdict_two_option_item():
    # 两选项批最容易漏：校验若写成「盲答必须是 A–D 之一」就会放过 'D'，
    # 但该题根本没有 D 键 → C6 会算 miss 并把它诊成"不可解"。必须按 options 键校验。
    it = _item("T02", {"A": _pad(12), "B": _pad(12)}, "A", blind="D")
    rc, d, out = _run_obj({"batch": "a10two", "items": [it]})
    assert rc == 2, f"两选项题的 blind='D' 应 exit 2，实为 {rc}\n{out}"
    assert any("T02" in e and "blind" in e for e in d["errors"]), d["errors"]
    assert d["rejects"] == [] and d["warns"] == [], d


@case("A10: 值归一化统一——correct=' A' 与 blind=' A' 必须同规则（不能一个 strip 一个原样）")
def test_a10_value_normalization_unified():
    # 这条守的是"同一文件里不许有两套归一化规则"：修前 correct=' A' → exit 2 而
    # blind=' A' → exit 0，同一类输入两个相反结论，没有任何判据依据。
    # 契约（协议 §4 归一化规则）：值（qid/correct/blind.verdict/options_sha256）一律 strip 再比对。
    for field in ("correct", "blind"):
        it = _plain("V01", blind="A")
        if field == "correct":
            it["correct"] = " A"
        else:
            it["blind"]["verdict"] = " A"
        rc, d, out = _run_obj({"batch": "a10norm", "items": [it]})
        assert rc == 0, f"{field}=' A' 属书写差异、应归一化后合法，实为 exit {rc}\n{out}"
        assert d["errors"] == [], f"{field}=' A' 不该报 schema 错：{d['errors']}"
        assert d["rejects"] == [], f"{field}=' A' 不该有题级 REJECT：{d['rejects']}"
        assert d["batch_checks"]["blind_acc"] == 1.0, \
            f"{field}=' A' 归一化后应命中（盲答 1/1）：{d['batch_checks']}"
        assert [w["code"] for w in d["warns"]] == ["C6"], \
            f"单题批 1/1 必饱和 WARN，显式承认（免得 exit 0 被误读成没命中 C6）：{d['warns']}"


@case("A10: 语义差异仍拒——correct='D.' / blind='D.' → exit 2（归一化不是放宽校验）")
def test_a10_semantic_error_still_rejected():
    # 归一化只吸收空白，不吸收语义错。文案「格式错误，不得当作可解性信号」已写进协议，故可断言。
    it = _plain("S01", blind="D")
    it["correct"] = "D."
    rc, d, out = _run_obj({"batch": "a10sem", "items": [it]})
    assert rc == 2, f"correct='D.' 不该被归一化放过，实为 exit {rc}\n{out}"
    assert any("S01" in e and "不在 options" in e for e in d["errors"]), d["errors"]

    it = _plain("S02", blind="D")
    it["blind"]["verdict"] = "D."
    rc, d, out = _run_obj({"batch": "a10sem2", "items": [it]})
    assert rc == 2, f"blind='D.' 应 exit 2，实为 {rc}\n{out}"
    msg = [e for e in d["errors"] if "S02" in e]
    assert msg and any("格式错误" in e and "可解性信号" in e for e in msg), \
        f"协议冻结的文案应含「格式错误」「可解性信号」：{msg}"


@case("A10: options 的键不归一化——键含空格时 correct=' A' 必须 exit 2（键是冻结件）")
def test_a10_option_keys_not_normalized():
    # 证伪形态：只有当 options 里**不存在**纯 "A" 键时才有判别力。
    # 若实现连键一起 strip，则键集会变成 {A,B}、correct ' A'→'A' 能找到 → exit 0（错）。
    # 契约：键是 P0 冻结件，原样比对——改一个空格就是另一道题。
    # blind 故意用合法键 "B"，让 K01 只可能报 correct 一条错——否则两条错都能让校验通过，
    # 断言就分不清是哪条判据在起作用。
    it = _item("K01", {" A": _pad(12), "B": _pad(12)}, " A", blind="B")
    rc, d, out = _run_obj({"batch": "a10key", "items": [it]})
    assert rc == 2, f"键含空格时 correct=' A' 应 exit 2（键不被 strip），实为 {rc}\n{out}"
    assert any("K01" in e and "不在 options" in e for e in d["errors"]), \
        f"errors 应说明 correct 不在 options 的键内：{d['errors']}"
    assert d["rejects"] == [] and d["warns"] == [], d


@case("A10: blind.verdict 带首尾空白（' D' / 'D\\n'）→ 按 strip 归一化后合法（与 qid/correct 惯例一致）")
def test_a10_blind_verdict_whitespace_normalized():
    # 裁决只规定「非法值 → exit 2」；带空白的合法值是「同一个值的不同书写」，与 qid / correct /
    # r3.basis 的 strip 惯例保持一致（不是把 'D.' 这类语义拼错放过去——那类必须 exit 2）。
    # 若将来裁决改为「一律严格原样匹配」，把本用例翻成 exit 2 即可，实现改动是一行。
    for value in (" D", "D\n", "\tD "):
        rc, d, out = _run_obj({"batch": "a10ws", "items": [
            _item("W01", {k: _pad(12) for k in "ABCD"}, "D", blind=value)]})
        assert rc == 0, f"blind={value!r} 归一化后是合法值，不该拦批，实为 {rc}\n{out}"
        assert d["errors"] == [], f"blind={value!r} 不该报 schema 错：{d['errors']}"
        assert d["rejects"] == [], f"blind={value!r} 不该有题级/批次 REJECT：{d['rejects']}"
        assert d["batch_checks"]["blind_acc"] == 1.0, \
            f"blind={value!r} 应被当作 'D' 命中（盲答 1/1）：{d['batch_checks']}"
        # 单题批 1/1 必触发 C6 饱和 WARN —— 显式断言，免得将来有人以为这里的 exit 0 是"没命中 C6"
        assert [w["code"] for w in d["warns"]] == ["C6"], \
            f"单题批盲答 1/1 应命中 C6 饱和 WARN：{d['warns']}"


@case("A10: 真实档案 25 题 blind 值全部落在 options 键内（新增校验不得打回好档案）")
def test_a10_real_dossier_blind_values_valid():
    doc = json.loads(DOSSIER_V2.read_text(encoding="utf-8"))
    offenders = [(it["qid"], it["blind"]["verdict"]) for it in doc["items"]
                 if it["blind"]["verdict"] not in it["options"]]
    assert not offenders, f"真实档案里有 blind 值不属于 options 键：{offenders}（A1 会因此变红）"
    # 顺带确认新增校验落地后 A1 的三个数值不变
    d = json.loads(_run(DOSSIER_V2).stdout)
    assert (d["exit"], d["summary"]["reject"], d["summary"]["warn"]) == (1, 22, 13), \
        f"真实档案结论应为 exit1/22/13，实为 {(d['exit'], d['summary']['reject'], d['summary']['warn'])}"


@case("A10: [白盒] 任一 item 报错时 validate_items 不得放出可用的 clean（D2 结构堵死）")
def test_a10_dirty_item_never_reaches_clean():
    """唯一的白盒用例：D2 在 CLI 层不可观测（exit 2 不判定，脏题自然不参与）。

    但"clean 只在无 errors 时可用"是个结构性不变量，CLI 测不到，只能直调函数钉死；
    否则将来有人复用返回值（加 --emit、或把批次统计改成基于 clean）就会带着
    占位 qid 'items[0]' 的脏题进判定。若哪天 validate_items 的签名/返回形式改了，
    本用例报"请同步"而不是静默通过。
    """
    sys.path.insert(0, str(HERE))
    import importlib
    ir = importlib.import_module("item_review")

    only_missing_qid = {"options": {"A": _pad(12), "B": _pad(12)}, "correct": "A",
                        "r1": {"verdict": "corroborated"}, "r2": {"type": "none"},
                        "r3": {"veto": False, "basis": None}, "blind": {"verdict": "A"}}
    errs: list = []
    try:
        res = ir.validate_items([only_missing_qid], errs)
    except Exception as e:            # 接口被重构也要明说，而不是报个不相干的 TypeError
        raise AssertionError(f"validate_items 调用失败（{type(e).__name__}: {e}）；"
                             f"若已重构，请同步本用例") from e
    clean = res[0] if isinstance(res, tuple) else res
    assert errs, "夹具前提：这道题缺 qid，必须报错"
    assert clean == [], \
        f"只缺 qid 的脏题被放进了 clean（qid 回退占位）：{clean}——D2 未堵死"


# ── 独立运行器（pytest 不可用时的主通道；风格随 test_route_gate.py）──────

def main() -> int:
    if not GATE.exists():
        print(f"[item-review-test] 被测文件不存在：{GATE}")
        return 1
    fails = []
    for fn in CHECKS:
        try:
            fn()
        except Exception as e:                   # 断言失败与意外异常都要报
            fails.append((fn._label, f"{type(e).__name__}: {e}"))
    print(f"[item-review-test] {len(CHECKS) - len(fails)}/{len(CHECKS)} 通过")
    for n, e in fails:
        print(f"  🔴 {n}\n     {e}")
    if not fails:
        print("  ✅ 全部通过（A1–A10 验收项 + fail-safe 负样本全绿）")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
