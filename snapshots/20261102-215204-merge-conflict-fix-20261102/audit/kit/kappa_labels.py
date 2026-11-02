#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kappa_labels.py —— 盲标 Kappa 计算（破「标注集只能验实现」的唯一杠杆）

## 为什么要有它

`label_saturation.py` 证明：原校准集 36 条 + 留出 10 条都被规则打到 1.00 满分、
且对阈值不敏感 → 标签是「照规则标的」，只能证明「实现符合规格」，不能证明「规格正确」。

唯一的出路（D-0065 §四 / 审计 §四-事项1）：让**独立于实现者的人**盲标同一批卡，
再与参考标签比 Kappa。若 Kappa < 0.6，先怀疑标注标准没写清，而不是改题。

## 输入

- 参考标签：`premise-ref-labels.json`（从 premise_probe 校准集冻结，True=拦/False=过）。
- 盲标文件：按 `premise-labeling-sheet.md` 的格式回传，例如：

        1拦 2过 3拦 4过 ... 36过

  也接受 JSON：`{"T01":"拦","N01":"过",...}` 或 `[{"id":"T01","label":"拦"},...]`。
  可传多个文件 → 算 Fleiss κ（多人盲标）；单文件 → 算 Cohen's κ（你 vs 参考）。

## 标签归一

拦 / block / 1 / B / 报警 / hit  → 1（该拦）
过 / pass  / 0 / P / 放过 / no   → 0（该过）

## 判据

- Cohen's / Fleiss κ < 0.6 → 🔴 标注者间一致性不足，先回头改标注标准，不要动题。
- 0.6 ≤ κ < 0.8 → 🟡 可接受，但边界卡需人工复核。
- κ ≥ 0.8 → 🟢 标注标准站得住。

## 用法

    python3 audit/kit/kappa_labels.py --labels labels-user.txt
    python3 audit/kit/kappa_labels.py --labels raterA.txt raterB.txt raterC.txt
    python3 audit/kit/kappa_labels.py --labels labels-user.txt --ref audit/baseline/v3/premise-ref-labels.json

退出码：0 正常 ｜ 2 输入/对齐错误 ｜ 3 文件为空或无有效配对
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REF = HERE.parent / "baseline" / "v3" / "premise-ref-labels.json"


# ── 归一 ──────────────────────────────────────────────────────────────────
_BLOCK = {"拦", "block", "1", "b", "报警", "hit", "y", "true"}
_PASS = {"过", "pass", "0", "p", "放过", "no", "n", "false"}


def norm(token: str) -> int | None:
    t = token.strip().lower()
    if t in _BLOCK:
        return 1
    if t in _PASS:
        return 0
    return None


# ── 解析盲标文件 ─────────────────────────────────────────────────────────
def parse_labels(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    out: dict[str, int] = {}

    # 1) JSON
    stripped = text.lstrip()
    if stripped.startswith("{"):
        data = json.loads(text)
        for k, v in data.items():
            n = norm(str(v))
            if n is not None:
                out[str(k).upper()] = n
        return out
    if stripped.startswith("["):
        for item in json.loads(text):
            if isinstance(item, dict):
                cid = str(item.get("id") or item.get("ID")).upper()
                n = norm(str(item.get("label") or item.get("verdict")))
                if n is not None:
                    out[cid] = n
        return out

    # 2) 自由格式：`1拦 2过 3拦 ...` 或 `T01拦 N01过`
    #    先找 `Tnn`/`Nnn` 显式 id，再退化到「数字序号」按盲标表顺序映射。
    explicit = re.findall(r"(T\d{2}|N\d{2})\s*([拦过A-Za-z0-9]+)", text)
    if explicit:
        for cid, lab in explicit:
            n = norm(lab)
            if n is not None:
                out[cid.upper()] = n
        return out

    # 3) 纯序号：按盲标表顺序 T01..T18, N01..N18
    seq = re.findall(r"(\d+)\s*([拦过A-Za-z0-9]+)", text)
    order = [f"T{i:02d}" for i in range(1, 19)] + [f"N{i:02d}" for i in range(1, 19)]
    for idx, (num, lab) in enumerate(seq):
        n = norm(lab)
        if n is None:
            continue
        if idx < len(order):
            out[order[idx]] = n
    return out


# ── Kappa ────────────────────────────────────────────────────────────────
def cohen_kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return (po - pe) / (1 - pe) if (1 - pe) else 1.0


def fleiss_kappa(matrix: list[list[int]]) -> float:
    """matrix[item][cat] = 该标注者在该卡上给该类的人数计数。二类情形直接传 0/1。"""
    n_items = len(matrix)
    n_raters = sum(matrix[0])
    if n_raters < 2 or n_items == 0:
        return float("nan")
    p_bar = 0.0
    for row in matrix:
        s2 = sum(x * x for x in row)
        p_bar += (s2 - n_raters) / (n_raters * (n_raters - 1))
    p_bar /= n_items
    n_cats = len(matrix[0])
    p_e = 0.0
    for j in range(n_cats):
        col = sum(row[j] for row in matrix) / (n_items * n_raters)
        p_e += col * col
    return (p_bar - p_e) / (1 - p_e) if (1 - p_e) else 1.0


def band(k: float) -> str:
    if k != k:  # nan
        return "⚪ 无法计算"
    if k < 0.6:
        return "🔴 不足（先改标注标准，不要动题）"
    if k < 0.8:
        return "🟡 可接受（边界卡需人工复核）"
    return "🟢 站得住"


# ── 主流程 ────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="盲标 Kappa 计算")
    ap.add_argument("--ref", default=str(DEFAULT_REF), help="参考标签 JSON")
    ap.add_argument("--labels", required=True, nargs="+", help="盲标文件（可多个）")
    args = ap.parse_args()

    ref_path = Path(args.ref)
    if not ref_path.exists():
        print(f"[kappa] 参考标签不存在：{ref_path}", file=sys.stderr)
        return 2
    ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
    ref_labels = {
        k.upper(): (1 if v["label"] == "拦" else 0) for k, v in ref_data["labels"].items()
    }

    raters: list[dict[str, int]] = []
    for lp in args.labels:
        p = Path(lp)
        if not p.exists():
            print(f"[kappa] 盲标文件不存在：{lp}", file=sys.stderr)
            return 2
        lab = parse_labels(p)
        if not lab:
            print(f"[kappa] 盲标文件未解析出任何标签：{lp}", file=sys.stderr)
            return 2
        raters.append(lab)

    # 对齐到参考 id 集合
    ids = [i for i in ref_labels if all(i in r for r in raters)]
    if not ids:
        print("[kappa] 无任何 id 同时出现在参考与所有盲标文件中", file=sys.stderr)
        return 3
    missing = len(ref_labels) - len(ids)
    if missing:
        print(f"[kappa] ⚠ {missing} 个 id 在盲标中缺失，仅用 {len(ids)} 个对齐计算")

    ref_vec = [ref_labels[i] for i in ids]
    rater_vecs = [[r[i] for i in ids] for r in raters]

    print(f"[kappa] 参考分布 拦/过 = {ref_data['counts']}")
    print(f"[kappa] 有效配对 n = {len(ids)}，标注者数 = {len(raters)}")
    print()

    for ri, vec in enumerate(rater_vecs):
        agree = sum(1 for x, y in zip(ref_vec, vec) if x == y) / len(ids)
        print(f"  标注者 {ri + 1}（{args.labels[ri]}）：与参考一致率 = {agree:.3f}")

    if len(raters) == 1:
        k = cohen_kappa(ref_vec, rater_vecs[0])
        print(f"\n  Cohen's κ（你 vs 参考）= {k:.3f} → {band(k)}")
    else:
        # Fleiss：matrix[item][cat] = 该卡上给 cat 的标注者数
        matrix = []
        for i in range(len(ids)):
            row = [0, 0]
            for rv in rater_vecs:
                row[rv[i]] += 1
            matrix.append(row)
        k = fleiss_kappa(matrix)
        print(f"\n  Fleiss κ（{len(raters)} 名标注者间）= {k:.3f} → {band(k)}")

    print(
        "\n★ κ < 0.6：先回头改「premise-labeling-sheet.md」的标注标准，"
        "不要动题或改探针规则。κ ≥ 0.8：标注标准站得住，可据此重算 D-0052。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
