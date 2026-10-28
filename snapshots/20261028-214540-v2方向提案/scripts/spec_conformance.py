#!/usr/bin/env python3
"""规格符合性核对：实现 vs 规则文本，以及规则 vs 语义（分歧即盲区清单）。

## 为什么需要它（2026-10-26，D-0070）

原校准集（36 张）的标签与卡号前缀完全同构（T 组全拦、N 组全过），
于是「卡号以 T 开头即拦」这条**不看内容**的规则也能拿 F1=1.00 —— 该集无区分力。
本脚本配合 `audit/probes/premise/spec-cases.json`（20 张、id 已打散）：

- **spec**：由 P-01/P-02/P-03 三条规则**文本**推导的期望值，不跑代码；
- **semantic**：不带规则知识的评审者会怎么判（作者判断，**不是独立真值**）；
- **actual**：探针实际输出。

三层比对给出三类结论：

1. `actual vs spec` 不一致 → **实现与规格不符**（真 bug，必须修）；
2. `actual vs semantic` 不一致 → **规则盲区**（不是 bug，是规格的选择，须登记）；
   本套件刻意包含 8 张这类卡：短但具体（老张/王工）、长但空泛、键齐但内容空、
   基线荒谬、边界一字之差（团队发布值班 vs 团队发布值班人）。
3. 元信息基线（id 奇偶 / id 顺序）若仍得高分 → 命名未打散，本套件同样不可用。

## 退出码

0 = actual 与 spec 完全一致（实现符合规格）
1 = 存在实现与规格不符（须修代码或修规格）
2 = 套件文件缺失/解析失败
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import premise_probe as pp                                    # noqa: E402

CASES = ROOT / "audit" / "probes" / "premise" / "spec-cases.json"


def verdict(hits) -> str:
    return "拦" if hits else "过"


def f1(pairs) -> float:
    """pairs: [(pred_is_hit, truth_is_hit)]"""
    tp = sum(1 for p, t in pairs if p and t)
    fp = sum(1 for p, t in pairs if p and not t)
    fn = sum(1 for p, t in pairs if not p and t)
    if tp == 0:
        return 0.0
    prec, rec = tp / (tp + fp), tp / (tp + fn)
    return 2 * prec * rec / (prec + rec)


def main() -> int:
    if not CASES.exists():
        print(f"[ERR] 找不到 {CASES}", file=sys.stderr)
        return 2
    data = json.loads(CASES.read_text(encoding="utf-8"))
    cases = data["cases"]

    mis_impl, blind = [], []
    rows = []
    for c in cases:
        hits = pp.run_card(c["card"])
        act = verdict(hits)
        rows.append((c["id"], c["spec"], act, c["semantic"], [h["id"] for h in hits], c.get("note", "")))
        if act != c["spec"]:
            mis_impl.append((c["id"], c["spec"], act))
        if act != c["semantic"]:
            blind.append((c["id"], act, c["semantic"], c.get("note", "")))

    print(f"套件 {len(cases)} 张｜spec 拦 {sum(1 for c in cases if c['spec']=='拦')}"
          f" / 过 {sum(1 for c in cases if c['spec']=='过')}")
    print(f"\n{'id':<5}{'spec':<6}{'actual':<8}{'semantic':<10}{'命中':<26}说明")
    for cid, sp, ac, se, hh, note in rows:
        flag = " " if ac == sp else "❗"
        print(f"{cid:<5}{sp:<6}{ac:<8}{se:<10}{str(hh):<26}{flag}{note[:34]}")

    print()
    if mis_impl:
        print(f"🔴 实现与规格不符 {len(mis_impl)} 张（须修）：{mis_impl}")
    else:
        print("✅ 实现与规格完全一致（spec-conformance 通过）")

    # 元信息基线：验证 id 未与标签同构
    neg = sum(1 for c in cases if c["spec"] == "拦") / len(cases)
    print(f"\n元信息基线检验（防 D-0070 重演）：spec 正类比例 = {neg:.0%}")
    for name, fn_pred in [
        ("id 为奇数 → 拦", lambda i, n: i % 2 == 1),
        ("id 前一半 → 拦", lambda i, n: i <= n // 2),
    ]:
        pairs = [(fn_pred(i, len(cases)), cases[i - 1]["spec"] == "拦") for i in range(1, len(cases) + 1)]
        print(f"  {name}: F1 = {f1(pairs):.2f}（应 << 1.00）")

    print(f"\n规则盲区（actual ≠ semantic）{len(blind)} 张 —— 这是本套件的主要产出：")
    for cid, ac, se, note in blind:
        print(f"  {cid}: 规则={ac} 语义={se}  {note[:52]}")

    return 1 if mis_impl else 0


if __name__ == "__main__":
    sys.exit(main())
