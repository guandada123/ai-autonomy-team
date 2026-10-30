#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_route_gate.py —— route_gate.py 的 A1–A6 验收套件（DESIGN-v0 §2.4）。

## 为什么必须有它

route_gate 的三个退出码分别对应「放行 / 拦批 / 打回」，判错方向的代价不对称：
把偏斜批放行（exit 1 误判成 0）= v3 死因复现；把好批打回（exit 2 误报）= 管线哑火。
所以正样本（A2/A3）、判据负样本（L3/误标 verification）、fail-safe（空批/坏 JSON/
文件不存在）必须逐条钉死，且全部走**真实 CLI**（subprocess 实跑，不 mock 内部函数，
与 test_kit.py 的 item_gate 组同款惯例）。

## 覆盖矩阵（对应 DESIGN §2.4）

| 组 | 验收项 | 内容 |
|---|---|---|
| 正样本 | A2 | manifest-v1.3-L1 → exit 0，routes.L1==25 |
| 正+负 | A3 | manifest-v3-L2 → exit 1，routes.L2==10，skew.max_share==0.7 且 RED |
| 一致性 | A4 | 两个 manifest 的 qid 集合逐 id 对比 truth-v1.3.json；v3 十题对 QUESTIONS.md Q01–Q10 |
| 接线 | A5 | manifest-v1.3 的 qid 逐个字面出现在 questions-v1.3.md（缺失即 fail 并列出） |
| fuzz | A6 | 合法 manifest 逐个删除必填字段（qid/level/route/ref_label/verification）→ 每次 exit 2 |
| 负样本 | A1 | L3 / 误标 verification / route 矛盾 / 空批 / 坏 JSON / 文件不存在 / 顶层非对象 → exit 2；均衡 L2 → exit 0 且 skew PASS |
| 输出 | A7 | exit 0 批写出 decision-table（25 题逐题含 level/route/answer/veto/debate + aggregate 契约） |
| 输出 | A8 | exit 1/2 批拒绝写出 decision-table（不产出脏表） |
| 真源 | A9 | skew 占比与 kit.item_gate.constant_baseline 同源同值（不是本地复制的第二份判据） |
| 计数 | A10 | 脏 item（缺 route）报错且不计入 routes 计数 |

A5 裁决记录（2026-10-30，主理人裁决「豁免区间写法，检查器展开」）：questions-v1.3.md
的 A 组用「T06–T09、Q14、Q15」区间写法，T07/T08 字面不存在。裁决为检查器先把文本中
「T+数字 [–—-] T+数字」规范格式区间（正则 T\\d+\\s*[–—-]\\s*T\\d+）展开为逐题 id
再比对（历史产出不回溯改判）；区间外任何 qid 仍须逐字匹配，展开后仍缺失照常报红
——契约只认写法，不认缺题。

## 用法

    python3 audit/routing/test_route_gate.py            # 独立运行（本环境无 pytest，主通道）
    python3 -m pytest audit/routing/test_route_gate.py -q   # pytest 可用时同样全兼容

退出码：0 全过 ｜ 1 有失败
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
GATE = HERE / "route_gate.py"
M_L1 = HERE / "manifest-v1.3-L1.json"
M_L2 = HERE / "manifest-v3-L2.json"
TRUTH = ROOT / "audit/baseline/v1.3/truth-v1.3.json"
Q_V13 = ROOT / "audit/baseline/v1.3/questions-v1.3.md"
Q_V3 = ROOT / "audit/baseline/v3/QUESTIONS.md"

CHECKS: list = []


def case(label: str):
    """记录用例并保留中文标签；返回原函数，pytest 仍按 test_* 收集。"""
    def deco(fn):
        fn._label = label
        CHECKS.append(fn)
        return fn
    return deco


# ── 工具：真实 CLI 实跑（不 mock，沿用 test_kit.py item_gate 组惯例）────────

def _run_gate(manifest: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), "--manifest", str(manifest),
                           "--json"], capture_output=True, text=True)


def _mk(tmp: Path, obj=None, raw: str | None = None) -> Path:
    """临时 manifest：raw 直接写原文（坏 JSON 用），否则序列化 obj。"""
    p = tmp / "manifest.json"
    p.write_text(raw if raw is not None else json.dumps(obj, ensure_ascii=False),
                 encoding="utf-8")
    return p


def _run_obj(obj=None, raw: str | None = None):
    """写临时 manifest → 实跑 route_gate --json → 返回 (returncode, 解析后的 JSON)。"""
    with tempfile.TemporaryDirectory(prefix="rg-") as td:
        r = _run_gate(_mk(Path(td), obj, raw))
    try:
        data = json.loads(r.stdout)
    except Exception:
        data = None
    return r.returncode, data, r.stdout + r.stderr


# 合法样板（fuzz 的基底）
L1_ITEM = {"qid": "Q99", "level": "L1", "route": "single", "verification": "machine"}
L2_ITEM = {"qid": "Q98", "level": "L2", "route": "multi-role", "ref_label": "A"}


# ── A2：v1.3 正样本 ───────────────────────────────────────────────────────

@case("A2: manifest-v1.3-L1 → exit 0 且 routes.L1==25（好批必须放行）")
def test_a2_v13_all_l1_exit0():
    r = _run_gate(M_L1)
    assert r.returncode == 0, f"期望 exit 0，实为 {r.returncode}\n{r.stdout}{r.stderr}"
    d = json.loads(r.stdout)
    assert d["exit"] == 0, d
    assert d["routes"]["L1"] == 25, f"routes.L1 应为 25，实为 {d['routes']}"
    assert d["n_items"] == 25, d
    assert d["errors"] == [], d["errors"]
    assert d["skew"] is None, d["skew"]          # 无 L2 → 无 S1 结果


# ── A3：v3 正+负样本（S1 报红）────────────────────────────────────────────

@case("A3: manifest-v3-L2 → exit 1、routes.L2==10、max_share==0.7 且 RED（v3 死因必须被拦）")
def test_a3_v3_l2_red_exit1():
    r = _run_gate(M_L2)
    assert r.returncode == 1, f"期望 exit 1，实为 {r.returncode}\n{r.stdout}{r.stderr}"
    d = json.loads(r.stdout)
    assert d["exit"] == 1, d
    assert d["routes"]["L2"] == 10, f"routes.L2 应为 10，实为 {d['routes']}"
    assert d["n_items"] == 10, d
    sk = d["skew"]
    assert sk is not None, "S1 结果不得为 null"
    assert sk["max_label"] == "A" and sk["max_share"] == 0.7, sk
    assert sk["verdict"] == "RED", sk
    assert sk["threshold"] == 0.5, sk
    assert sk["distribution"] == {"A": 7, "B": 1, "C": 2}, sk


# ── A4：标注一致性（逐 id 对比，不做集合近似）────────────────────────────

@case("A4: manifest-v1.3 的 qid 集合与 truth-v1.3.json items 完全一致，且 19 machine + 6 attested")
def test_a4_v13_qids_match_truth():
    truth_ids = set(json.loads(TRUTH.read_text(encoding="utf-8"))["items"])
    m = json.loads(M_L1.read_text(encoding="utf-8"))
    m_ids = [it["qid"] for it in m["items"]]
    assert len(m_ids) == len(set(m_ids)), "manifest 内 qid 有重复"
    only_m = sorted(set(m_ids) - truth_ids)
    only_t = sorted(truth_ids - set(m_ids))
    assert not only_m and not only_t, \
        f"qid 集合不一致——manifest 多出 {only_m}，truth 多出 {only_t}"
    # DESIGN §三：19 machine（逐题记 recompute_kind）+ 6 attested
    kinds = [it.get("verification") for it in m["items"]]
    assert kinds.count("machine") == 19, f"machine 应 19，实为 {kinds.count('machine')}"
    assert kinds.count("attested") == 6, f"attested 应 6，实为 {kinds.count('attested')}"


@case("A4: manifest-v3 的 10 个 qid 与 QUESTIONS.md 的 Q01–Q10 一致")
def test_a4_v3_qids_match_questions_md():
    v3_text = Q_V3.read_text(encoding="utf-8")
    want = [f"Q{i:02d}" for i in range(1, 11)]
    m = json.loads(M_L2.read_text(encoding="utf-8"))
    got = [it["qid"] for it in m["items"]]
    assert sorted(got) == want, f"v3 manifest qid 应为 Q01–Q10，实为 {sorted(got)}"
    for q in got:
        assert q in v3_text, f"{q} 未出现在 QUESTIONS.md 文本中"


# ── A5：源文件接线核对（只读；区间写法按 2026-10-30 主理人裁决在检查器内展开）──

_RANGE_RE = re.compile(r"(T)(\d+)\s*[–—-]\s*T(\d+)")


def _expand_ranges(text: str) -> set[str]:
    """把 questions-v1.3.md 的规范区间写法（如 T06–T09）展开为逐题 id。

    只认 `T\\d+\\s*[–—-]\\s*T\\d+`（全角/半角连接符均收），起点>终点或前缀不一致
    的区间一律不展开——豁免的是「写法」，不是缺题，契约不放松。
    """
    expanded: set[str] = set()
    for m in _RANGE_RE.finditer(text):
        prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        if lo > hi:
            continue
        width = max(len(m.group(2)), len(m.group(3)))
        expanded.update(f"{prefix}{n:0{width}d}" for n in range(lo, hi + 1))
    return expanded


@case("A5: manifest-v1.3 每个 qid 出现在 questions-v1.3.md（区间写法按裁决展开；缺失即 fail 并列出）")
def test_a5_v13_qids_wired_to_questions_md():
    # 裁决记录（2026-10-30，主理人）：豁免「T06–T09」区间写法，检查器展开后比对；
    # 展开范围外任何 qid 仍须逐字匹配，展开后仍缺失照常报红。
    text = Q_V13.read_text(encoding="utf-8")
    wired = {q for q in re.findall(r"[TQ]\d+", text)} | _expand_ranges(text)
    m = json.loads(M_L1.read_text(encoding="utf-8"))
    missing = [it["qid"] for it in m["items"] if it["qid"] not in wired]
    assert not missing, \
        f"以下 {len(missing)} 个 qid 未出现在 questions-v1.3.md（含区间展开）中：" \
        f"{', '.join(missing)}（A5 接线契约违反）"


# ── A6：字段完整性 fuzz（逐个删除必填字段）────────────────────────────────

@case("A6: L2 样板逐个删除 qid/level/route/ref_label → 每次 exit 2 且 errors 带 qid 定位")
def test_a6_fuzz_l2_missing_required_fields():
    for field in ("qid", "level", "route", "ref_label"):
        item = dict(L2_ITEM)
        del item[field]
        rc, d, out = _run_obj({"items": [item]})
        assert rc == 2, f"删 {field} 后期望 exit 2，实为 {rc}\n{out}"
        assert d and d["exit"] == 2 and d["errors"], (field, d)
        # 交付契约：errors 元素是 "qid: 消息" 字符串（缺 qid 时回退 items[idx] 定位）
        locator = "items[0]" if field == "qid" else L2_ITEM["qid"]
        assert any(locator in e for e in d["errors"]), \
            f"删 {field} 后 errors 未定位到 {locator}：{d['errors']}"


@case("A6: L1 样板删除 verification → exit 2（误标判断题的入口必须堵死）")
def test_a6_fuzz_l1_missing_verification():
    item = dict(L1_ITEM)
    del item["verification"]
    rc, d, out = _run_obj({"items": [item]})
    assert rc == 2, f"期望 exit 2，实为 {rc}\n{out}"
    assert d and d["exit"] == 2, d
    assert any("verification" in e and "Q99" in e for e in d["errors"]), d["errors"]


# ── 额外负样本（注入式，tempfile 构造，不入仓库）──────────────────────────

@case("负样本: L3 题 → exit 2 且 skew 为 null（判据未定不许悄悄过）")
def test_neg_l3_item_exit2():
    rc, d, out = _run_obj({"items": [{"qid": "Q97", "level": "L3", "route": "full"}]})
    assert rc == 2, f"L3 期望 exit 2，实为 {rc}\n{out}"
    assert any("Q97" in e and "L3" in e for e in d["errors"]), d["errors"]
    assert d["skew"] is None, d["skew"]


@case("负样本: L1 误标 verification='judged' → exit 2（DESIGN §2.2 可证伪负样本）")
def test_neg_l1_wrong_verification_exit2():
    item = dict(L1_ITEM, verification="judged")
    rc, d, out = _run_obj({"items": [item]})
    assert rc == 2, f"期望 exit 2，实为 {rc}\n{out}"
    assert any("Q99" in e for e in d["errors"]), d["errors"]


@case("负样本: level 与 route 矛盾（L1 配 multi-role）→ exit 2")
def test_neg_route_level_mismatch_exit2():
    item = dict(L1_ITEM, route="multi-role")
    rc, d, out = _run_obj({"items": [item]})
    assert rc == 2, f"期望 exit 2，实为 {rc}\n{out}"
    assert any("Q99" in e and "multi-role" in e for e in d["errors"]), d["errors"]


@case("负样本: 空批次 / 缺 items → exit 2（fail-safe 不放行）")
def test_neg_empty_batch_exit2():
    for obj in ({"items": []}, {}):
        rc, d, out = _run_obj(obj)
        assert rc == 2, f"{obj} 期望 exit 2，实为 {rc}\n{out}"
        assert d and d["errors"], d


@case("负样本: 坏 JSON / 文件不存在 / 顶层非对象 → exit 2（fail-safe）")
def test_neg_bad_input_exit2():
    rc, d, out = _run_obj(raw="{items: [不是JSON]]")
    assert rc == 2, f"坏 JSON 期望 exit 2，实为 {rc}\n{out}"
    with tempfile.TemporaryDirectory(prefix="rg-") as td:
        rc2 = _run_gate(Path(td) / "no-such-file.json").returncode
    assert rc2 == 2, f"文件不存在期望 exit 2，实为 {rc2}"
    rc3, d3, out3 = _run_obj(raw='["顶层是数组"]')
    assert rc3 == 2, f"顶层非对象期望 exit 2，实为 {rc3}\n{out3}"
    assert any("顶层" in e for e in d3["errors"]), d3["errors"]


@case("负样本: 均衡分布 L2（A/B/C/D 各 2）→ exit 0 且 skew PASS（S1 不误伤好批）")
def test_neg_balanced_l2_pass_exit0():
    labels = ["A", "A", "B", "B", "C", "C", "D", "D"]
    items = [dict(L2_ITEM, qid=f"Q{i:02d}", ref_label=lab)
             for i, lab in enumerate(labels, 1)]
    rc, d, out = _run_obj({"items": items})
    assert rc == 0, f"均衡批期望 exit 0，实为 {rc}\n{out}"
    sk = d["skew"]
    assert sk["verdict"] == "PASS" and sk["max_share"] == 0.25, sk
    assert d["routes"]["L2"] == 8 and d["errors"] == [], d


# ── v0.1 增补：decision-table 输出 / 判据真源 / 脏 item 计数 ──────────────

@case("A7: --emit-decision-table 在 exit 0 批写出下游可消费的表（每题含 level/route/answer/veto/debate）")
def test_a7_decision_table_emitted_on_exit0():
    with tempfile.TemporaryDirectory(prefix="rg-dt-") as td:
        out = Path(td) / "decision-table.json"
        r = subprocess.run([sys.executable, str(GATE), "--manifest", str(M_L1),
                            "--json", "--emit-decision-table", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        d = json.loads(r.stdout)
        assert d["decision_table"] == str(out), d.get("decision_table")
        assert out.exists(), "decision-table 未写出"
        dt = json.loads(out.read_text(encoding="utf-8"))
        assert dt["manifest"].endswith("manifest-v1.3-L1.json"), dt["manifest"]
        assert len(dt["items"]) == 25, len(dt["items"])
        for qid, row in dt["items"].items():
            assert row["level"] == "L1" and row["route"] == "single", (qid, row)
            assert row["answer"] is None and row["veto"] is None and row["debate"] is False, (qid, row)
            assert "aggregate_seats.py" in dt["aggregate_contract"]["seats_input"], dt["aggregate_contract"]


@case("A8: 红批/坏批拒绝写出 decision-table（exit 非 0 不产出脏表）")
def test_a8_decision_table_refused_when_not_exit0():
    with tempfile.TemporaryDirectory(prefix="rg-dt-") as td:
        out = Path(td) / "dt-red.json"
        # exit 1：v3 偏斜批
        r1 = subprocess.run([sys.executable, str(GATE), "--manifest", str(M_L2),
                             "--json", "--emit-decision-table", str(out)],
                            capture_output=True, text=True)
        assert r1.returncode == 1, r1.stdout + r1.stderr
        assert not out.exists(), "偏斜批不得写出 decision-table"
        # exit 2：缺字段批
        rc2, d2, out2 = _run_obj({"items": [{"qid": "Q90", "level": "L2", "route": "multi-role"}]})
        assert rc2 == 2, out2
        assert not out.exists(), "坏输入批不得写出 decision-table"


@case("A9: S1 占比与 kit.item_gate.constant_baseline 同源（不是本地复制的第二份判据）")
def test_a9_skew_reuses_constant_baseline():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kit"))
    from item_gate import constant_baseline
    labels = ["A"] * 7 + ["C"] * 2 + ["B"]
    base = constant_baseline(labels)
    assert base["top_label"] == "A" and round(base["top_share"], 4) == 0.7, base
    # 同一批标签交给 route_gate，两份结果必须一致
    items = [dict(L2_ITEM, qid=f"Q{i:02d}", ref_label=lab) for i, lab in enumerate(labels, 1)]
    rc, d, out = _run_obj({"items": items})
    assert rc == 1, out
    sk = d["skew"]
    assert sk["max_label"] == base["top_label"], (sk, base)
    assert sk["max_share"] == round(base["top_share"], 4), (sk, base)
    assert sk["distribution"] == base["distribution"], (sk, base)


@case("A10: 脏 item 不计入 routes 计数（缺 route 的 item 报错且不计档）")
def test_a10_dirty_item_not_counted():
    bad = {"qid": "Q91", "level": "L1", "verification": "machine"}   # 缺 route
    rc, d, out = _run_obj({"items": [dict(L1_ITEM), bad]})
    assert rc == 2, out
    assert any("缺 route" in e for e in d["errors"]), d["errors"]
    assert d["routes"]["L1"] == 1, f"只有 1 个干净 item 应被计数：{d['routes']}"


# ── 独立运行器（pytest 不可用时的主通道；风格随 test_kit.py）──────────────

def main() -> int:
    fails = []
    for fn in CHECKS:
        try:
            fn()
        except Exception as e:                   # 断言失败与意外异常都要报
            fails.append((fn._label, f"{type(e).__name__}: {e}"))
    print(f"[route-gate-test] {len(CHECKS) - len(fails)}/{len(CHECKS)} 通过")
    for n, e in fails:
        print(f"  🔴 {n}\n     {e}")
    if not fails:
        print("  ✅ 全部通过（A1–A10 验收项 + fail-safe 负样本全绿）")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

# ── 遗留（验收时点记录，非本套件职责）────────────────────────────────────
# 1. pytest 本环境不可用：本套件写成 pytest 兼容（函数式 test_*，无模块级副作用），
#    但 pytest 通道未实测，独立运行为主通道。
# 2. 已闭环（2026-10-30 v0.1）：原「脏 item 仍计入 routes」已修并由 A10 钉死；
#    原 S1 判据本地实现已改为复用 kit.item_gate.constant_baseline（A9 钉死同源）。
