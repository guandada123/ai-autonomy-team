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

  A. MANIFEST 字节数：文档里出现的 `total_bytes`（形如 7,883,426）是否等于 `MANIFEST.json#stats`
     ⚠️ `file_count` **不在本项校验范围内**：下面的正则只匹配 7 位带千分位的字节数，
        4 位文件数（如 1099）永远命不中。原文写作「`file_count` / `total_bytes`」，
        但 file_count 从未实现校验 —— 2026-10-23 审计发现，此处如实收窄声明，
        不假装已覆盖。要补需另写一条只匹配文件数的规则。
  B. 索引行数：`docs/*.md` 中被 `| NNN |` 引用的行数是否等于该文件实际行数
  C. 契约版本：文档里的 `vX.Y` 声明是否与 `config/params.json` 一致
  D. 计数声明：**当前状态文档**（README / ARCHITECTURE / DEPENDENCIES）里的
     「N 条经验 / N 份决策 / N 轮运行 / N 份协议」是否等于磁盘实际条数。
     `docs/*.md` **不在**本项范围：那是历史审计记录，写的是当时的事实，
     拿今天的磁盘去判它错，会把"历史正确"报成红，反而掩盖真问题。

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

# D 项扫描范围：只扫「当前状态」类文档。docs/*.md 是历史审计，不参与（见文件头）。
STATUS_DOCS = ["README.md", "ARCHITECTURE.md", "DEPENDENCIES.md"]

# D 项规则：正则 → (人类可读标签, 计数函数)。正则刻意写得窄（"条经验"而不是"条"），
# 宽匹配会把「3 条铁律」这类无关说法一起抓进来。
COUNT_RULES = [
    (re.compile(r"(\d+)\s*条经验"), "经验条数",
     lambda: len(list((ROOT / ".learnings").glob("*.md")))),
    (re.compile(r"(\d+)\s*份决策"), "决策份数",
     lambda: len(list((DOCS / "decisions").glob("D-*.md")))),
    (re.compile(r"(\d+)\s*轮运行"), "运行轮数",
     lambda: len([d for d in (PKG / "runs").glob("round*") if d.is_dir()])),
    (re.compile(r"(\d+)\s*份协议"), "协议份数",
     lambda: len(list((DOCS / "protocol").glob("*.md")))),
]


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
    tb = st["total_bytes"]
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


def check_count_claims(problems):
    """D. 当前状态文档里的计数声明 vs 磁盘实际。"""
    checked = 0
    for name in STATUS_DOCS:
        p = ROOT / name
        if not p.exists():
            problems.append(("D", f"{name} 缺失", "状态文档不存在"))
            continue
        text = p.read_text(encoding="utf-8")
        for rx, label, counter in COUNT_RULES:
            hits = set(rx.findall(text))
            if not hits:
                continue
            actual = counter()
            for declared in hits:
                checked += 1
                if int(declared) == actual:
                    continue
                # 同行带历史限定词 = 当时事实，跳过（与 A 项同一口径）
                line = next((ln for ln in text.splitlines() if declared in ln), "")
                if re.search(r"原始|原为|审计时|对齐前|此前|当时", line):
                    continue
                problems.append(
                    ("D", f"{name}: {label}声明 {declared}", f"磁盘实际 {actual}"))
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
    d = check_count_claims(problems)

    if not args.quiet:
        print("[check_docs_consistency] 文档 ↔ 磁盘一致性")
        print(f"  A. MANIFEST 数字   : 校验 {a} 处")
        print(f"  B. 索引行数        : 校验 {b} 处")
        print(f"  C. 契约版本        : 校验 {c} 处")
        print(f"  D. 计数声明        : 校验 {d} 处")
        print()

    if problems:
        print(f"[FAIL] 发现 {len(problems)} 处不一致：", file=sys.stderr)
        for kind, what, why in problems:
            print(f"  [{kind}] {what}  →  {why}", file=sys.stderr)
        # 给出可直接执行的修复命令（A 类可自动修，B/C 类需人工）
        kinds = {k for k, _, _ in problems}
        if "A" in kinds:
            print("\n[A 类可自动修复] python3 scripts/sync_doc_numbers.py", file=sys.stderr)
        if kinds & {"B", "C", "D"}:
            print("[B/C/D 类需人工] B=索引行数（改文档后重跑索引同步），C=契约版本声明，"
                  "D=计数声明（改文档数字以匹配磁盘，别改磁盘去凑文档）",
                  file=sys.stderr)
        if args.strict:
            return BAD
        print("\n（非 --strict 模式：仅报告，不返回失败码）", file=sys.stderr)
        return OK

    print(f"[PASS] 全部一致（合计校验 {a + b + c + d} 处）")
    return OK


if __name__ == "__main__":
    sys.exit(main())
