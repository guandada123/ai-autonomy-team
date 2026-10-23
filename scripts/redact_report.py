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
    python3 scripts/redact_report.py --self-test

`--check` 审的是**输入文件的现状**（2026-10-23 前会先脱敏再自检，等于自己查自己，
任何脏文件都 PASS）。

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

# ── 持仓代码兜底（2026-10-23 补）─────────────────────────────────────────
# 上面的显式清单只认"写它时"那三只；换一次持仓就漏，而且 --check 照样报 PASS。
# 实测：`真实对账报告-对外版.md` 残留 600036（当轮唯一持仓）四处，check 全绿 —— 假自信。
# 改为「上下文 + 六位代码形状」识别：只在出现持仓语境的**行**里替换，
# 形状限定 A 股前缀，避免把六位数金额（100000）误当代码。
# 边界取 `[0-9,]` 与 `[0-9]`：代码会写成 `positions.600036.pnl` 或 `600036.pnl`，
# 前后带点很常见，但不能让点成为边界条件（第一版用 `[\d.]` 当场漏掉 3/4 处）。
CODE_PAT = re.compile(r"(?<![0-9,])((?:60|68|00|30|43|83|87)\d{4})(?![0-9])")
CODE_CTX = re.compile(r"持仓|有仓|清仓|标的|positions|\.pnl|\.shares|\.market_value")
KNOWN_CODES = {"600584": "标的A", "588170": "标的B", "002185": "标的C"}
_FALLBACK_LETTERS = "DEFGHIJKLMNOPQRSTUVWXYZ"


def _code_token(code, assigned):
    """已知三只沿用 A/B/C；新代码按首次出现顺序发 D/E/F…，保持彼此可区分。"""
    if code in KNOWN_CODES:
        return KNOWN_CODES[code]
    if code not in assigned:
        i = len(assigned)
        assigned[code] = f"标的{_FALLBACK_LETTERS[i]}" if i < len(_FALLBACK_LETTERS) else "标的?"
    return assigned[code]

def banner(mask_amounts: bool) -> str:
    """抬头按**实际**是否打码金额来写。

    旧版是一段常量，无论有无 `--mask-amounts` 都声称"金额已抹掉精度"，
    实测对外版里 `41,651.98` 原样保留 —— 文件在自我描述上说了谎。
    """
    if mask_amounts:
        rule = "账户级金额保留量级、抹掉精度"
    else:
        rule = ("⚠️ **账户金额未打码**（生成时未加 `--mask-amounts`）：可内部传阅，**勿公开**")
    return f"""> 🔒 **本文件为对外版**，由 `scripts/redact_report.py` 从正本自动生成，**请勿手工修改**。
> 脱敏口径：绝对路径与券商名打码、持仓代码代号化（含清单外的六位代码兜底）；{rule}。
> **差额 / 倍数 / 比率一律保留**（它们是结论本身，单独不具识别性）。
> 正本含完整数字，仅在本机留存。
"""


def redact(text: str, mask_amounts: bool = False) -> str:
    for old, new in LINKAGE + (AMOUNTS if mask_amounts else []):
        text = text.replace(old, new)
    # 兜底：显式清单之外、但形状像 A 股代码且处在持仓语境里的六位数
    assigned = {}
    out_lines = []
    for ln in text.splitlines(keepends=True):
        if CODE_CTX.search(ln):
            ln = CODE_PAT.sub(lambda m: _code_token(m.group(1), assigned), ln)
        out_lines.append(ln)
    text = "".join(out_lines)
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
    # 兜底：仍以代码形状出现在持仓语境里的六位数
    left = sum(len(CODE_PAT.findall(ln)) for ln in text.splitlines() if CODE_CTX.search(ln))
    if left:
        hits.append(("持仓语境里的六位代码", left))
    return hits


def self_test() -> int:
    """自检：四条必须成立的属性。任何一条破了，对外版就可能在说谎。"""
    cases = []

    # 1) 清单外的代码（600036）在持仓语境里必须被代号化
    got = redact("| I2 持仓股数 | 仅 600036 有仓（100 股） |")
    cases.append(("清单外代码被兜底", "600036" not in got and "标的" in got, got.strip()))

    # 2) `positions.<code>.pnl` 这种点包围的形态也要命中（第一版漏过 3/4 处）
    got = redact("## 不闭合项：`positions.600036.pnl`\n600036.pnl: 7870.0\n")
    cases.append(("点边界形态命中", "600036" not in got, got.strip()))

    # 3) 金额不能被误当代码（无持仓语境的行原样保留）
    got = redact("| I1 现金恒等 | 46,829.30 == 46,829.30 |")
    cases.append(("金额不被误伤", got.count("46,829.30") == 2, got.strip()))

    # 4) --check 审的是输入现状：脏输入必须 FAIL
    dirty = find_residue("仅 600036 有仓\n", mask_amounts=False)
    clean = find_residue("仅 标的D 有仓\n", mask_amounts=False)
    cases.append(("脏输入可被检出 / 净输入放行", bool(dirty) and not clean, f"dirty={dirty} clean={clean}"))

    # 5) 抬头必须与实际是否打码一致
    ok_banner = ("未打码" in banner(False)) and ("未打码" not in banner(True))
    cases.append(("抬头与打码开关一致", ok_banner, "banner(False)/banner(True)"))

    bad = 0
    for name, ok, detail in cases:
        if not ok:
            bad += 1
        print(f"  {'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f"  ← {detail}"))
    print(f"\n[{'PASS' if not bad else 'FAIL'}] 脱敏自检：{len(cases) - bad}/{len(cases)} 项")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description="生成报告的对外版（脱敏 + 自检）")
    ap.add_argument("--in", dest="src", default="")
    ap.add_argument("--out", dest="dst", default="")
    ap.add_argument("--check", action="store_true", help="只检查输入文件的残留，不写文件")
    ap.add_argument("--self-test", action="store_true", help="跑内置自检（不读写任何报告）")
    ap.add_argument("--mask-amounts", action="store_true",
                    help="同时打码账户金额（含逐仓明细；默认不打码，见文件头说明）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.src:
        print("[ERR] 需要 --in（或加 --self-test）", file=sys.stderr)
        return 2

    try:
        raw = Path(args.src).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        print(f"[ERR] 读取失败：{e}", file=sys.stderr)
        return 2

    # ★ --check 必须审"**输入文件的现状**"，不能先脱敏再审 ——
    #   老代码先 redact() 再 find_residue()，等于自己查自己：任何脏文件都会 PASS。
    #   （2026-10-23 实测：对外版残留 600036 四处，--check 仍报"无残留"。）
    if args.check:
        residue = find_residue(raw, args.mask_amounts)
        if residue:
            print(f"[FAIL] 残留 {len(residue)} 类：{residue}")
            return 1
        print("[PASS] 无残留")
        return 0

    out = redact(raw, args.mask_amounts)
    ban = banner(args.mask_amounts)
    if not out.startswith("#"):
        out = ban + "\n" + out
    else:
        head, rest = out.split("\n", 1)
        out = head + "\n\n" + ban + rest

    residue = find_residue(out, args.mask_amounts)
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
