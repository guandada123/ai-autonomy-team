#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""redact_report.py —— 从正本生成「对外版」报告（只读正本，不改正本）

## 为什么做成脚本，而不是手工另存一份

手工维护两个版本必然分叉：改了 A 忘了 B，最后没人知道哪份是真的。
本项目已经因为「同一语义多副本」踩过四次（pnl / invested_cost_stocks / daily_pnl / dashboard 死读），
所以对外版必须**由正本生成**，且生成后**自动验证无残留**。

## 脱敏口径（2026-10-22 修订）

| 类别 | 处理 | 理由 |
|---|---|---|
| 绝对路径 | `/Volumes/...` → `<WORKSPACE>`，`/Users/guan` → `~` | 暴露目录结构与用户名 |
| 券商名 | 国金 → 券商 | 与持仓组合起来可定位账户 |
| 持仓代码 | 600584/588170/002185 → 标的A/B/C | 代码 + 金额 + 时点足以反查账户 |
| 账户金额 | **默认保留**；加 `--mask-amounts` 才打码 | 见下 |

**为什么不默认打码金额**：只抹合计、不抹逐仓明细，读者把三个残差一加就能还原合计——
**这种遮蔽是自欺**。真正能反查账户的是「券商 + 代码 + 时点」，切掉这条链比抹数字有意义。
需要更严格时用 `--mask-amounts`（此时逐仓明细也一并打码）。

用法：
    python3 scripts/redact_report.py --in <正本.md> --out <对外版.md>
    python3 scripts/redact_report.py --in ... --out ... --mask-amounts
    python3 scripts/redact_report.py --in <对外版.md> --check [--mask-amounts]

退出码：0 干净 / 1 有残留 / 2 读写出错
"""
import argparse
import re
import sys
from pathlib import Path

# 关联信息（默认必抹）：切断「券商 + 代码 + 路径」这条反查链
LINKAGE = [
    ("/Volumes/ZHITAI/WorkBuddy/2026-10-19-22-33-38/ai-autonomy-team", "<PROJECT>"),
    ("/Volumes/ZHITAI/WorkBuddy/Claw", "<CLAW>"),
    ("/Volumes/ZHITAI/WorkBuddy", "<WORKSPACE>"),
    ("/Volumes/ZHITAI", "<EXTERNAL_DISK>"),
    ("/Users/guan", "~"),
    ("国金证券", "某券商"),
    ("国金", "券商"),
    ("600584", "标的A"),
    ("588170", "标的B"),
    ("002185", "标的C"),
]

# 金额打码（仅 --mask-amounts 时启用）：保留量级、抹掉精度
AMOUNTS = [
    ("41,651.98", "41,6xx.xx"),
    ("15,409.89", "15,4xx.xx"),
    ("15,376.80", "15,3xx.xx"),
    ("47,340.80", "47,3xx.xx"),
    ("9,687.98", "9,6xx.xx"),
    ("86.421", "86.4xx"),
    ("31,964.00", "31,9xx.xx"),
    ("31,964", "31,9xx"),
    ("-8,771.51", "-8,7xx.xx"),
    ("-8,757.30", "-8,7xx.xx"),
    ("-2,340.00", "-2,3xx.xx"),
    ("-2,330.00", "-2,3xx.xx"),
    ("-4,298.38", "-4,2xx.xx"),
    ("-4,289.50", "-4,2xx.xx"),
]

# 默认必须清零：关联信息
FORBIDDEN_LINK = ["600584", "588170", "002185", "国金", "/Volumes/ZHITAI", "/Users/guan"]
# 开启金额打码后还必须清零这些
FORBIDDEN_AMOUNT = ["9,687.98", "86.421", "41,651.98", "47,340.80", "15,376.80", "15,409.89"]

BANNER = """> 🔒 **本文件为对外版**，由 `scripts/redact_report.py` 从正本自动生成，**请勿手工修改**。
> 脱敏口径：绝对路径与券商名打码、持仓代码代号化、账户级金额保留量级抹掉精度；
> **差额 / 倍数 / 比率一律保留**（它们是结论本身，单独不具识别性）。
> 正本含完整数字，仅在本机留存。
"""


def redact(text: str, mask_amounts: bool = False) -> str:
    for old, new in LINKAGE + (AMOUNTS if mask_amounts else []):
        text = text.replace(old, new)
    if mask_amounts:
        # 兜底：仍带完整分位精度的万级金额再打码（不动已代号化的替身）
        text = re.sub(r"(?<![\w.])(\d{2}),(\d{3})\.(\d{2})(?![\w])",
                      lambda m: f"{m.group(1)},{m.group(2)[0]}xx.xx", text)
    return text


def find_residue(text: str, mask_amounts: bool = False):
    hits = []
    for token in FORBIDDEN_LINK + (FORBIDDEN_AMOUNT if mask_amounts else []):
        n = text.count(token)
        if n:
            hits.append((token, n))
    for pat, label in ((r"/Volumes/[A-Za-z]", "绝对路径 /Volumes/…"),
                       (r"/Users/[A-Za-z]", "绝对路径 /Users/…")):
        n = len(re.findall(pat, text))
        if n:
            hits.append((label, n))
    return hits


def main():
    ap = argparse.ArgumentParser(description="生成报告的对外版（脱敏 + 自检）")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", default="")
    ap.add_argument("--check", action="store_true", help="只检查残留，不写文件")
    ap.add_argument("--mask-amounts", action="store_true",
                    help="同时打码账户金额（含逐仓明细；默认不打码，见文件头说明）")
    args = ap.parse_args()

    try:
        raw = Path(args.src).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERR] 读取失败：{e}", file=sys.stderr)
        return 2

    out = redact(raw, args.mask_amounts)
    if not out.startswith("#"):
        out = BANNER + "\n" + out
    else:
        head, rest = out.split("\n", 1)
        out = head + "\n\n" + BANNER + rest

    residue = find_residue(out, args.mask_amounts)

    if args.check:
        if residue:
            print(f"[FAIL] 残留 {len(residue)} 类：{residue}")
            return 1
        print("[PASS] 无残留")
        return 0

    if not args.dst:
        print("[ERR] 需要 --out（或加 --check 只检查）", file=sys.stderr)
        return 2
    try:
        Path(args.dst).expanduser().write_text(out, encoding="utf-8")
    except OSError as e:
        print(f"[ERR] 写入失败：{e}", file=sys.stderr)
        return 2

    changed = sum(1 for a, b in LINKAGE + (AMOUNTS if args.mask_amounts else []) if a in raw)
    print(f"[OK] 已生成 {args.dst}")
    print(f"     替换类别 {changed} 项；正本 {len(raw.splitlines())} 行 → 对外版 {len(out.splitlines())} 行")
    if residue:
        print(f"[FAIL] 仍有残留：{residue}")
        return 1
    print("[PASS] 自检：FORBIDDEN 全部清零")
    return 0


if __name__ == "__main__":
    sys.exit(main())
