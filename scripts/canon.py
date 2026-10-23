#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""canon.py —— 标识与词表的规范化（只读工具 + 可被别处 import 的函数）

## 解决什么问题

同一个对象存在多种"官方写法"，导致跨轮统计必须靠猜：

  task_id    7 种形态：T-NN / TASK-BROKER-NNN / TASK-BROKER-NNN/T-NN /
                       tasks/T-NN / SYNTH-RECON-NNN / TASK-BROKER-RECON-NNN / 裸数字
  category   48 种写法（含全角括号、不同分隔符、同义前缀）

`new_round.py` 已在 2026-10-20 起写入 `task_id_normalized`，但
`adjudication-record` / `answer-status` 等产物没跟着用，跨产物仍要靠猜。

## 设计原则：**归一化在读取侧，不改写历史**

项目的硬原则是「历史已签字产出不回溯改判」。因此本模块：
  - **只提供解析函数**（读取侧统一口径）
  - **不修改任何历史文件**
  - 历史里的旧写法通过别名表映射，而不是被重写

## 用法

    python3 scripts/canon.py --check migration-package     # 报告现状（含未解析项）
    python3 scripts/canon.py --tid "TASK-BROKER-013/T-13"  # 单值解析
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------- task_id
_NUM20 = 20  # T-01..T-20 是本包内的任务族；超出范围的 TASK-BROKER-NNN 视为外部编号


def canonical_task_id(raw):
    """把任意历史写法解析为规范 task_id。

    返回 (canonical, resolvable)。resolvable=False 表示无法安全归一，调用方应原样保留并记一笔。
    规范形式：`T-NN`（两位数字，与夹具目录 `fixtures/tasks/T-NN` 一致）。
    """
    s = str(raw or "").strip()
    if not s:
        return "", False

    # 复合形态 TASK-BROKER-013/T-13 → 取 T- 段（它就是夹具目录名）
    m = re.search(r"(?:^|/)T-(\d{2})$", s)
    if m:
        return f"T-{m.group(1)}", True

    # tasks/T-13
    m = re.search(r"T-(\d{2})", s)
    if m and s.endswith(m.group(0)):
        return f"T-{m.group(1)}", True

    # TASK-BROKER-001 → T-01（仅当落在本包 20 个任务范围内）
    m = re.fullmatch(r"TASK-BROKER-(\d{3})", s)
    if m:
        n = int(m.group(1))
        return (f"T-{n:02d}", True) if 1 <= n <= _NUM20 else (s, False)

    # 其它任务族（合成/模板/外部）：不属于本包 20 任务，原样保留但标记不可归入 T- 族
    return s, False


# ---------------------------------------------------------------- category
_FULLWIDTH = str.maketrans({"（": "(", "）": ")", "，": ",", "：": ":", "　": " "})
_SEP = re.compile(r"[－—–~〜至]|->|→")


def canonical_category(raw):
    """类别名的**机械**归一：全角转半角、分隔符统一、去空白、合并重复分隔。

    只做**可证伪的等价变换**（同一语义的排版差异），不做同义归并——
    同义归并需要领域判断，交给人工（见 --check 输出的「疑似同义组」）。
    """
    s = str(raw or "").strip().translate(_FULLWIDTH)
    s = _SEP.sub("-", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"-+$", "", s)
    return s


def _scan(root: Path):
    """扫描包内产物，统计 task_id 与 category 的写法分布。"""
    tid_forms, tid_unresolved = Counter(), Counter()
    cat_forms = Counter()
    examples = {}

    for p in sorted(root.rglob("*.json")):
        if "_selfcheck" in p.name:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        vals = []
        if isinstance(d, dict):
            for k in ("task_id", "task"):
                if isinstance(d.get(k), str):
                    vals.append(d[k])
            for c in ((d.get("summary") or {}).get("excluded_categories") or []):
                if isinstance(c, str):
                    cat_forms[c] += 1
        for v in vals:
            canon, ok = canonical_task_id(v)
            key = re.sub(r"\d+", "N", v)
            tid_forms[key] += 1
            examples.setdefault(key, str(p.relative_to(root)))
            if not ok:
                tid_unresolved[canon] += 1

    return tid_forms, tid_unresolved, cat_forms, examples


def main():
    ap = argparse.ArgumentParser(description="标识与词表规范化（只读）")
    ap.add_argument("--check", default="", help="扫描目录（通常是 migration-package）")
    ap.add_argument("--tid", default="", help="解析单个 task_id")
    ap.add_argument("--cat", default="", help="解析单个类别名")
    args = ap.parse_args()

    if args.tid:
        canon, ok = canonical_task_id(args.tid)
        print(f"{args.tid!r} → {canon!r}  resolvable={ok}")
        return 0 if ok else 1

    if args.cat:
        print(f"{args.cat!r} → {canonical_category(args.cat)!r}")
        return 0

    if not args.check:
        ap.print_help()
        return 2

    root = Path(args.check).expanduser()
    if not root.is_dir():
        print(f"[ERR] 目录不存在：{root}", file=sys.stderr)
        return 2

    tid_forms, tid_unresolved, cat_forms, examples = _scan(root)

    print(f"[canon] {root.name}")
    print(f"\n① task_id 写法 {len(tid_forms)} 种：")
    for k, n in tid_forms.most_common():
        print(f"   {k:<26} x{n:<4} 例: {examples[k]}")
    if tid_unresolved:
        print(f"   无法归入 T- 族 {sum(tid_unresolved.values())} 处：")
        for k, n in tid_unresolved.most_common():
            print(f"     {k:<28} x{n}")

    canon_cats = Counter()
    for c, n in cat_forms.items():
        canon_cats[canonical_category(c)] += n
    merged = len(cat_forms) - len(canon_cats)
    print(f"\n② excluded_categories：原文 {len(cat_forms)} 种 → 机械归一并掉 {merged} 种 → {len(canon_cats)} 种")
    if merged:
        groups = {}
        for c in cat_forms:
            groups.setdefault(canonical_category(c), set()).add(c)
        for k, vs in sorted(groups.items()):
            if len(vs) > 1:
                print(f"   合并 {len(vs)} 种 → {k!r}: {sorted(vs)}")
    print(f"   剩余 {len(canon_cats)} 种需人工判断是否同义（本工具不做同义归并）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
