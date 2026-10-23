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
  ✅ 会改：`MANIFEST **1095 条 / 7,885,557 字节**`（条数与字节数一起改成当前值）
  ⏭️ 跳过：`（原始为 7,840,273）`
  ⏭️ 跳过：`（审计时 7,883,426；此后共 +1,084）`
  ⏭️ 跳过：`# 期望：{'file_count': 1095, 'total_bytes': 7883426}`（代码块里的历史快照）

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
# 文件数（形如「1095 条」）。只在**本行含当前字节数**时才改 ——
# 字节数是"当前值"的天然锚点：历史行的字节数不是当前值，所以文件数也不会被误改。
# 这条规则是 N2 缺口的补实现（2026-10-23）：此前只有字节数被同步，
# 于是文档出现「1095 条 / 当代字节数」这种自相矛盾的组合。
FILE_COUNT_PAT = re.compile(r"(\d[\d,]*)(\s*条)")

# 代码行数行：`| `xxx.py` | 2364 | 说明 |`。只认第二格是纯数字的行 ——
# ARCHITECTURE 里还有「谁依赖谁」的表，第二格是文件列表，不是行数。
CODE_ROW_PAT = re.compile(r"^(\|\s*`([\w./]+\.py)`[^|]*\|\s*)(\d+)(\s*\|)")
STATUS_DOCS = ("ARCHITECTURE.md", "README.md", "DEPENDENCIES.md")


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


def _fmt_count(m, real_n):
    """文件数替换：沿用原文的千分位风格（`1095 条` ↔ `1,099 条` 都见过）。

    第一版写成 `(\\d{3,5})` 是个真 bug：遇到带千分位的 `1,099 条` 会只匹配
    后三位 `099`，替换后得到 `1,1099 条`（2026-10-23 当场咬到）。
    """
    raw = m.group(1)
    if raw.replace(",", "") == str(real_n):
        return m.group(0)
    out = fmt(real_n) if "," in raw else str(real_n)
    return f"{out}{m.group(2)}"


def resolve_script(name):
    """按「源码树优先」定位脚本：scripts/ → 根 → migration-package/。

    表里同时有 `judges/seat_check.py`（源树内相对路径）与 `precheck.py（包内）`
    （只在包里存在）两种写法，按此顺序解析能同时命中。
    """
    for base in (ROOT / "scripts", ROOT, ROOT / "migration-package"):
        p = base / name
        if p.is_file():
            return p
    return None


def sync_code_rows():
    """把 `| `xxx.py` | NNN |` 的行数同步为实际行数。

    2026-10-23 实测：ARCHITECTURE 的这张表 28 行里 16 行与实际不符
    （`check_docs_consistency` 差 119 行、`snapshot_sources` 差 164 行）。
    表是给人「这文件多大、改它值不值得」用的，数字错了等于给错依据。
    """
    changes, stale = [], []
    for name in STATUS_DOCS:
        p = ROOT / name
        if not p.is_file():
            continue
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        out, touched = [], False
        for ln in lines:
            m = CODE_ROW_PAT.match(ln)
            target = resolve_script(m.group(2)) if m else None
            if m and target is not None:
                real = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
                if int(m.group(3)) != real:
                    ln = f"{m.group(1)}{real}{m.group(4)}"
                    stale.append(f"{name}: {m.group(2)} {m.group(3)} → {real}")
                    touched = True
            out.append(ln)
        if touched:
            changes.append((p, out))
    return changes, stale


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
            if fmt(real_b) in new:
                new = FILE_COUNT_PAT.sub(lambda m: _fmt_count(m, real_n), new)
            if new != ln:
                stale.append(f"{p.name}: {ln.strip()[:60]}")
                touched = True
            out.append(new)
        if touched:
            changes.append((p, out))

    arch_changes, arch_stale = sync_code_rows()
    changes += arch_changes
    stale += arch_stale

    if not changes:
        print(f"[PASS] 全部一致（当前 {fmt(real_n)} 条 / {fmt(real_b)} 字节；代码行数表一致）")
        return 0

    if args.check:
        print(f"[FAIL] {len(stale)} 处过期（字节数当前应为 {fmt(real_b)}）：", file=sys.stderr)
        for s in stale[:12]:
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
    print(f"[OK] 已同步 {len(changes)} 份文档 → {fmt(real_b)} 字节；代码行数表 {len(arch_stale)} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
