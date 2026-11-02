#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare_v13.py —— v1.3 题库的同条件配对比较（单席 vs 多席），只读

## 与 v1.2 版的差别（2026-10-28，D-0079）

- 题库换成 v1.3（`truth-v1.3.json`，25 题不变：T03 题面写死 ÷1048576、T05 升级可重算锚点）；
- 重算闸门换成同目录 `recompute_truth.py`（v1.3，新增 `regress_items_at_commit`）；
- **其余一律不变**：判分仍复用 `paired_compare.grade`（单一真源），非劣效判据仍是 (b−c)/n ≥ −margin，margin 默认 0.05；
- v1.2 的分数不得与 v1.3 混算（`D-0057` 换卷规则）。

## 与 v1.1 版的差别（2026-10-27，D-0074）

- 题库换成 v1.2（`truth-v1.2.json`，25 题：回补 Q17/Q25/Q26、移出 Q31）；
- 重算闸门换成同目录 `recompute_truth.py`（v1.2，含 `doc_line_num`）；
- **其余一律不变**：判分仍复用 `paired_compare.grade`（单一真源），非劣效判据仍是 (b−c)/n ≥ −margin，margin 默认 0.05；
- Q31 已移出（真值保留在 `moved_out`），不进判分，故 n=25。

## 与 `paired_compare.py` 的区别（为什么另写一个）

| | `paired_compare.py`（v1） | 本脚本（v1.1） |
|---|---|---|
| 对照臂 | **冻结的**单 agent 作答（凭会话记忆） | **同条件**重跑的单席（与多席访问权限相同） |
| 题库 | v1（27 题，已过期 8 题） | v1.1（26 题，带时点与锚点） |
| 前置检查 | 自校验复现 20/27 | **先跑 `recompute_truth.py`，不通过就拒绝出分** |

D-0059 的结论是：v1 的两臂信息条件不等价，`c` 测的是信息差。所以 v1.1 的对照臂必须**重跑**，
并且与多席**用同一份提示词、同一套访问权限**。

## 硬闸

1. **题库没过重算检查 → exit 3，一个字都不算**（`L-0018`：冻结必须配重算）；
2. 判分函数直接复用 `paired_compare.grade`，**不复制一份**——口径必须单一真源；
3. 任一臂缺题按"错"计，并在输出里单列（`missing`）。

## 用法

    python3 audit/baseline/v1.3/compare_v13.py \
        --single audit/baseline/v1.3/arm-M1.json \
        --multi  audit/baseline/v1.3/multi-answers.json

    --margin 0.05（默认）  --json  --skip-recompute（仅调试，报告里必须注明）

退出码：0 非劣效成立 ｜ 1 不成立 ｜ 2 输入错误 ｜ 3 题库未通过重算检查
"""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent

# ── 判分口径：从 v1 的判定器导入，绝不复制 ────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    "paired_compare", ROOT / "audit" / "baseline" / "paired_compare.py"
)
_pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pc)
grade = _pc.grade


def load_arm(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    ans = d.get("answers", d)
    return {k: v for k, v in ans.items() if not str(k).startswith("_")}


def run_recompute():
    r = subprocess.run(
        [sys.executable, str(HERE / "recompute_truth.py")], capture_output=True, text=True
    )
    return r.returncode, (r.stdout + r.stderr)


def main():
    ap = argparse.ArgumentParser(description="v1.3 同条件配对比较")
    ap.add_argument("--single", required=True)
    ap.add_argument("--multi", required=True)
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-recompute", action="store_true")
    args = ap.parse_args()

    if not args.skip_recompute:
        rc, out = run_recompute()
        if rc != 0:
            print("[ERR] 题库 v1.3 未通过重算检查，拒绝出分：", file=sys.stderr)
            print(out, file=sys.stderr)
            return 3
    else:
        print("[WARN] 已跳过重算检查——本报告的分数不可用于决策", file=sys.stderr)

    for label, path in (("--single", args.single), ("--multi", args.multi)):
        if not Path(path).exists():
            print(f"[ERR] {label} 指向的文件不存在：{path}", file=sys.stderr)
            return 2

    doc = json.loads((HERE / "truth-v1.3.json").read_text(encoding="utf-8"))
    truth = {k: v for k, v in doc["items"].items() if k not in doc.get("excluded", {})}
    single, multi = load_arm(args.single), load_arm(args.multi)

    rows, b, c, both_ok, both_bad, missing_s, missing_m = [], 0, 0, 0, 0, [], []
    for q in sorted(truth, key=lambda s: (s[0], int("".join(ch for ch in s if ch.isdigit()) or 0))):
        t = truth[q]["v"]
        if q not in single:
            missing_s.append(q)
            s_ok = False
        else:
            s_ok = grade(single[q], t)
        if q not in multi:
            missing_m.append(q)
            m_ok = False
        else:
            m_ok = grade(multi[q], t)
        if s_ok and m_ok:
            both_ok += 1
        elif not s_ok and m_ok:
            b += 1
        elif s_ok and not m_ok:
            c += 1
        else:
            both_bad += 1
        rows.append(
            {
                "q": q,
                "truth": t,
                "single": single.get(q),
                "multi": multi.get(q),
                "single_ok": s_ok,
                "multi_ok": m_ok,
                "delta": (
                    "多席赢"
                    if m_ok and not s_ok
                    else "多席输"
                    if s_ok and not m_ok
                    else "同对"
                    if s_ok
                    else "同错"
                ),
            }
        )

    n = len(rows)
    diff = (b - c) / n if n else 0.0
    ok = diff >= -args.margin
    res = {
        "n": n,
        "b": b,
        "c": c,
        "both_ok": both_ok,
        "both_bad": both_bad,
        "single_pass": both_ok + c,
        "multi_pass": both_ok + b,
        "diff": round(diff, 4),
        "margin": args.margin,
        "non_inferior": ok,
        "verdict": "非劣效成立" if ok else "非劣效不成立",
        "missing_single": missing_s,
        "missing_multi": missing_m,
        "note": (
            "零个不一致对 = 零信息：只能说未观察到差异，不能宣称相等" if b == 0 and c == 0 else ""
        ),
        "rows": rows,
    }

    if args.json:
        print(
            json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False, indent=2)
        )
    else:
        print(f"[v1.3] n={n}  单席对={res['single_pass']}  多席对={res['multi_pass']}")
        print(f"  一致：都对 {both_ok} / 都错 {both_bad}")
        print(f"  不一致：b（多席赢）={b}  c（多席输）={c}")
        print(f"  差值 (b−c)/n = {diff:+.3f}   判据 ≥ −{args.margin:.2f} → **{res['verdict']}**")
        if missing_s:
            print(f"  ⚠️ 单席未作答 {len(missing_s)} 题：{', '.join(missing_s)}")
        if missing_m:
            print(f"  ⚠️ 多席未作答 {len(missing_m)} 题：{', '.join(missing_m)}")
        if res["note"]:
            print(f"  ⚠️ {res['note']}")
        dis = [r for r in rows if r["delta"] in ("多席赢", "多席输")]
        if dis:
            print("  分歧题：")
            for r in dis:
                print(
                    f"      {r['q']}: 真值={r['truth']} 单席={r['single']} 多席={r['multi']} → {r['delta']}"
                )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
