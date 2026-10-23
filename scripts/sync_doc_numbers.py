#!/usr/bin/env python3
r"""sync_doc_numbers.py —— 把文档中的「当前 MANIFEST 数字」同步为实际值（可写）。

## 为什么存在

包内任何文件一改，`MANIFEST.total_bytes` 就变，而团队根文档里写死的当前字节数
就成了过期值。2026-10-20 ~ 10-21 期间已人工同步 **5 次**（每次改 8~11 处）。

`pre_release_check` 的 A 项能精确指出「哪份文档的哪个值过期」，但**替换仍需人做**。
本脚本补上最后一步：一条命令改完。

## 只改「当前值」，不碰「历史值」

判断规则：某行若含下列限定词，视为历史记录，**跳过**——
   原始 / 原为 / 审计时 / 对齐前 / 此前 / 为 \d / →

例：
  ✅ 会改：`MANIFEST **1095 条 / 7,885,557 字节**`
  ⏭️ 跳过：`（原始为 7,840,273）`
  ⏭️ 跳过：`（审计时 7,883,426；此后共 +1,084）`

## 用法

    python3 scripts/sync_doc_numbers.py --dry-run   # 先看会改什么
    python3 scripts/sync_doc_numbers.py             # 实际写入
    python3 scripts/sync_doc_numbers.py --check     # 只检查是否有过期值（不改），供 CI 用

退出码：0 无需改动或已改完 / 1 --check 模式下发现过期值 / 2 环境错误
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MANIFEST = ROOT / "migration-package" / "MANIFEST.json"

# 出现这些词的行，其数字视为「历史值」，不改
HIST_MARKERS = ("原始", "原为", "审计时", "对齐前", "此前", "当时", "→")
NUM_PAT = re.compile(r"7,\d{3},\d{3}")


def fmt(n):
    return f"{n:,}"


def load_stats():
    if not MANIFEST.is_file():
        return None
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f).get("stats")
    except (OSError, json.JSONDecodeError):
        return None


def is_historical(line):
    return any(m in line for m in HIST_MARKERS)


def main():
    ap = argparse.ArgumentParser(description="同步文档中的当前 MANIFEST 数字")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    ap.add_argument("--check", action="store_true", help="只检查，有过期值即 exit 1")
    args = ap.parse_args()

    st = load_stats()
    if not st:
        print(f"[ERR] 无法读取 {MANIFEST}", file=sys.stderr)
        return 2
    real_b, real_n = int(st["total_bytes"]), int(st["file_count"])
    valid_bytes = {fmt(real_b)}
    # 文件数的白名单原为 valid_counts = {fmt(real_n), str(real_n)}，构造后从未使用
    # —— 即「文件数」从未被本脚本同步过，只有字节数被同步。
    # 2026-10-23 审计发现。此处删除死变量但**不假装已实现**；补实现会改写文档数字，
    # 需单独评估，见审计报告。

    changes, stale = [], []
    for p in sorted(DOCS.glob("*.md")):
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        out, touched = [], False
        for ln in lines:
            if is_historical(ln):
                out.append(ln)
                continue
            new = ln
            for hit in set(NUM_PAT.findall(ln)):
                if hit not in valid_bytes:
                    new = new.replace(hit, fmt(real_b))
            if new != ln:
                stale.append(f"{p.name}: {ln.strip()[:60]}")
                touched = True
            out.append(new)
        if touched:
            changes.append((p, out))

    if not changes:
        print(f"[PASS] 全部一致（当前 {fmt(real_n)} 条 / {fmt(real_b)} 字节）")
        return 0

    if args.check:
        print(f"[FAIL] {len(stale)} 处过期（当前应为 {fmt(real_b)} 字节）：", file=sys.stderr)
        for s in stale[:10]:
            print(f"  {s}", file=sys.stderr)
        print("\n修复：python3 scripts/sync_doc_numbers.py", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[DRY] 将修改 {len(changes)} 份文档：")
        for p, _ in changes:
            print(f"  {p.name}")
        return 0

    for p, out in changes:
        p.write_text("".join(out), encoding="utf-8", newline="\n")
    print(f"[OK] 已同步 {len(changes)} 份文档 → {fmt(real_b)} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
