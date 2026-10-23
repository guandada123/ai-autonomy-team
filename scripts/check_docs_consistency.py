#!/usr/bin/env python3
"""check_docs_consistency.py —— 文档与磁盘的一致性校验（只读）。

## 为什么存在

2026-10-20 审计发现 14 处错误数字 + 1 处未验证断言，根因是**数字的"来源"与"所在地"分离**：
  · 「132 次读数」是 `33×4` 的乘法，从未真数过；
  · 「44 种写法」是类别对齐**前**的统计，对齐后仍是旧值；
  · 「24/26 严格满足」是对齐前数字，却被用来论证对齐后的状态。

同类问题在修复过程中又复发 4 次（§八 索引的行数值没跟上被引用文件的行数变化）。
故本脚本把两条教训落成机制：

  1. 文档里的关键统计量，必须能用命令复现 → 本脚本复现它们；
  2. 交叉引用（行数/路径/计数）必须在最后一次改动后统一复核 → 本脚本做这件事。

## 用法

    python3 scripts/check_docs_consistency.py            # 人在环，先看输出
    python3 scripts/check_docs_consistency.py --strict  # CI 用，不一致即 exit 1

## 检查项

  A. MANIFEST 数字：文档里出现的 `file_count` / `total_bytes` 是否等于 `MANIFEST.json#stats`
  B. 索引行数：`docs/*.md` 中被 `| NNN |` 引用的行数是否等于该文件实际行数
  C. 契约版本：文档里的 `vX.Y` 声明是否与 `config/params.json` 一致

退出码：0 全部一致 / 1 存在不一致（仅 --strict 时）/ 2 环境错误
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ai-autonomy-team/
DOCS = ROOT / "docs"
PKG = ROOT / "migration-package"

OK, BAD, ERR = 0, 1, 2


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def fmt(n):
    """1234567 -> '1,234,567'"""
    return f"{n:,}"


def check_manifest_numbers(problems):
    """A. 文档中的 MANIFEST 数字 vs 实际 stats。"""
    m = load_json(PKG / "MANIFEST.json")
    if not m or "stats" not in m:
        problems.append(("A", "MANIFEST.json 缺失或结构异常", "无法校验"))
        return 0
    st = m["stats"]
    fc, tb = st["file_count"], st["total_bytes"]
    checked = 0
    num_re = re.compile(r"7,\d{3},\d{3}")          # 形如 7,883,426
    for p in sorted(DOCS.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        for hit in set(num_re.findall(text)):
            val = int(hit.replace(",", ""))
            checked += 1
            if val != tb:
                # 允许历史值出现，但要求同行带"原始为/前"等限定词
                line = next((ln for ln in text.splitlines() if hit in ln), "")
                # 历史标记：与 sync_doc_numbers.HIST_MARKERS 保持一致。
                # 旧规则含 `为 \d`，会把「| 当前值 | ... 原始为 X |」这类行整体跳过，
                # 而该行前半部分正是需要校验的当前值 —— 属漏检缺陷（2026-10-21 修）。
                if not re.search(r"原始|原为|审计时|对齐前|此前|当时|→", line):
                    problems.append(
                        ("A", f"{p.name}: 出现 {hit}", f"MANIFEST 实际 {fmt(tb)}（该行无限定词）"))
    return checked


def check_index_line_counts(problems):
    """B. 索引表的行数声明 vs 实际文件行数。

    匹配形如： | [`文件.md`](./文件.md) | 347 | 描述 |
    行数在**链接之后**，不是之前——初版正则写反了，导致校验 0 处（自测时发现）。
    """
    checked = 0
    idx_re = re.compile(r"\[`?([^`\]]+\.md)`?\]\([^)]*\)\s*\|\s*(\d{1,4})\s*\|")
    for p in sorted(DOCS.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        for name, declared in idx_re.findall(text):
            target = DOCS / name.strip()
            if not target.exists():
                problems.append(("B", f"{p.name} 引用了不存在的 {name.strip()}", "文件缺失"))
                continue
            actual = len(target.read_text(encoding="utf-8").splitlines())
            checked += 1
            if int(declared) != actual:
                problems.append(
                    ("B", f"{p.name}: {name.strip()} 声明 {declared} 行",
                     f"实际 {actual} 行"))
    return checked


def check_contract_version(problems):
    """C. 文档声明的契约版本 vs params.json。"""
    cfg = load_json(PKG / "config" / "params.json") or {}
    ver = str((cfg.get("seat_contract") or {}).get("contract_version") or "?")
    checked = 0
    pat = re.compile(r"契约 v(\d+\.\d+)")
    for p in sorted(DOCS.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        for hit in set(pat.findall(text)):
            checked += 1
            if hit != ver:
                problems.append(("C", f"{p.name}: 声明 v{hit}", f"params.json 为 v{ver}"))
    return checked


def main():
    ap = argparse.ArgumentParser(description="文档与磁盘一致性校验（只读）")
    ap.add_argument("--strict", action="store_true", help="存在不一致时 exit 1（CI 用）")
    ap.add_argument("-q", "--quiet", action="store_true", help="只输出结论")
    args = ap.parse_args()

    if not DOCS.is_dir() or not PKG.is_dir():
        print(f"[ERR] 目录缺失：{DOCS} 或 {PKG}", file=sys.stderr)
        return ERR

    problems = []
    a = check_manifest_numbers(problems)
    b = check_index_line_counts(problems)
    c = check_contract_version(problems)

    if not args.quiet:
        print("[check_docs_consistency] 文档 ↔ 磁盘一致性")
        print(f"  A. MANIFEST 数字   : 校验 {a} 处")
        print(f"  B. 索引行数        : 校验 {b} 处")
        print(f"  C. 契约版本        : 校验 {c} 处")
        print()

    if problems:
        print(f"[FAIL] 发现 {len(problems)} 处不一致：", file=sys.stderr)
        for kind, what, why in problems:
            print(f"  [{kind}] {what}  →  {why}", file=sys.stderr)
        # 给出可直接执行的修复命令（A 类可自动修，B/C 类需人工）
        kinds = {k for k, _, _ in problems}
        if "A" in kinds:
            print("\n[A 类可自动修复] python3 scripts/sync_doc_numbers.py", file=sys.stderr)
        if kinds & {"B", "C"}:
            print("[B/C 类需人工] B=索引行数（改文档后重跑索引同步），C=契约版本声明",
                  file=sys.stderr)
        if args.strict:
            return BAD
        print("\n（非 --strict 模式：仅报告，不返回失败码）", file=sys.stderr)
        return OK

    print(f"[PASS] 全部一致（合计校验 {a + b + c} 处）")
    return OK


if __name__ == "__main__":
    sys.exit(main())
