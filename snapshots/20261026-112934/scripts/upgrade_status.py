#!/usr/bin/env python3
"""★ 升级候选台账核对：哪些经验真的进了跨项目规则层。

## 为什么需要这个

2026-10-26 发现：`🔄 跨项目经验升级(全自动)`（周日 23:57）被当作"会把 ★ 经验
自动捞走"的既定事实，但实际**双重不匹配**（读其 prompt 后定案）：

1. **本仓不在扫描清单**。它只扫 6 个项目——Claw / QuantTradingSystem /
   StockInsight / engineering-audit-kit / project-monitor-fusion / wechat_api；
2. **格式不符**。它读 `.learnings/LEARNINGS.md` + `ERRORS.md` 两个**汇总文件**，
   而本仓是一课一文件（`L-0026-*.md`），`.learnings/` 下汇总文件数为 **0**。

   → 即使把本仓加进清单，仍扫不到任何东西。

旁证：用户级 `MEMORY.md` 里 `L-` 编号出现 **0 次**（从未有 provenance）；
2026-10-25 夜同批周日自动化（磁盘巡检 / 下周前瞻 / 巡检中枢周报 / 多仓库周报 /
绩效周报）全部留痕 → 调度器健康，不是平台故障。（`/Users/guan/WorkBuddy` 是
`/Volumes/ZHITAI/WorkBuddy` 的符号链接，路径本身没问题，六目标均可解析。）

结论：该链路对那 6 个项目健康，**但从未覆盖本仓**；本仓经验的"升级"目前**没有
任何自动通路**，只能靠本台账显式登记。

## 台账口径（预注册，不得事后放宽）

一条经验记「已升级」必须同时满足：

- `docs/learnings-upgraded.json` 里有该 L-ID 的条目；
- 条目必须带 `rule_line`：跨项目规则层里**可 grep 到的原句**（不是"加过了"的口头声明）；
- 条目必须带 `verified_at`（人工核对日期）。

缺任一条 → 记「未升级」。

## 用法与退出码

    python3 scripts/upgrade_status.py             # 只报告，永远 exit 0
    python3 scripts/upgrade_status.py --strict    # ★ 且龄 > 7 天仍未升级 → exit 1
    python3 scripts/upgrade_status.py --json      # 机器可读

为什么默认不 fail：「有 ★ 未升级」是常态（人工核对有滞后），让它每天红 =
生产"狼来了"、消耗真实告警的可信度（L-0028 第 3 条）。`--strict` 供周度巡检调用。

## 可证伪性

- 阳性：把某条 L-ID 写进台账但 `rule_line` 在 MEMORY.md 里 grep 不到 → 必须判
  「台账与规则层不一致」并单列（这是台账本身的可信度检查）。
- 阴性：台账为空时，应把全部 ★ 列为未升级，不得报"全部已升级"。
"""
import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEARN = ROOT / ".learnings"
LEDGER = ROOT / "docs" / "learnings-upgraded.json"
# 跨项目规则层：用户级 MEMORY.md。允许用环境变量覆盖（换机 / 测试）。
RULE_LAYER = Path(
    __import__("os").environ.get(
        "UPGRADE_RULE_LAYER",
        str(Path.home() / ".workbuddy" / "user-24688a34-03ff-4299-8736-08d5a6585c6b-personal" / "MEMORY.md"),
    )
)


def parse_lesson(p: Path) -> dict:
    txt = p.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"(L-\d{4})", p.name)
    lid = m.group(1) if m else p.stem
    title = txt.splitlines()[0].lstrip("# ").strip() if txt.strip() else p.stem
    title = re.sub(r"^L-\d{4}\s*", "", title)
    dm = re.search(r"\*\*日期\*\*\s*[：:]\s*(\d{4}-\d{2}-\d{2})", txt)
    if dm:
        d = date.fromisoformat(dm.group(1))
        date_src = "文件内声明"
    else:
        d = datetime.fromtimestamp(p.stat().st_mtime).date()
        date_src = "mtime 兜底"
    return {
        "id": lid,
        "file": p.name,
        "title": title,
        "date": d.isoformat(),
        "date_src": date_src,
        "star": "★" in txt,
    }


def load_ledger() -> dict:
    if not LEDGER.exists():
        return {"upgraded": []}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def check_ledger(ledger: dict, rule_text: str) -> list:
    """台账自身的可信度：登记的 rule_line 必须能在规则层里 grep 到。"""
    bad = []
    for e in ledger.get("upgraded", []):
        line = (e.get("rule_line") or "").strip()
        if not line:
            bad.append((e.get("id", "?"), "缺 rule_line"))
        elif line not in rule_text:
            bad.append((e.get("id", "?"), f"rule_line 在规则层 grep 不到：{line[:40]}…"))
        elif not e.get("verified_at"):
            bad.append((e.get("id", "?"), "缺 verified_at"))
    for e in ledger.get("covered_locally", []):
        if not (e.get("reason") or "").strip():
            bad.append((e.get("id", "?"), "covered_locally 缺 reason（须指出执行体）"))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="★ 且龄>7 天未升级 → exit 1")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-age", type=int, default=7, help="严格模式的年龄阈值（天）")
    a = ap.parse_args()

    if not LEARN.is_dir():
        print(f"[ERR] 找不到 {LEARN}", file=sys.stderr)
        return 2

    lessons = sorted((parse_lesson(p) for p in LEARN.glob("L-*.md")), key=lambda x: x["id"])
    ledger = load_ledger()
    rule_text = RULE_LAYER.read_text(encoding="utf-8", errors="replace") if RULE_LAYER.exists() else ""
    done = {e.get("id") for e in ledger.get("upgraded", [])}
    local = {e.get("id") for e in ledger.get("covered_locally", [])}
    defer = set((ledger.get("deferred") or {}).get("ids") or [])
    today = date.today()

    rows = []
    for it in lessons:
        age = (today - date.fromisoformat(it["date"])).days
        rows.append({**it, "age_days": age,
                     "upgraded": it["id"] in done, "covered_locally": it["id"] in local,
                     "deferred": it["id"] in defer})

    stars = [r for r in rows if r["star"]]
    # 待处置 = 既没升级、也没有本地强制机制兜着
    pending = [r for r in stars
               if not r["upgraded"] and not r["covered_locally"] and not r["deferred"]]
    stale = [r for r in pending if r["age_days"] > a.max_age]
    bad = check_ledger(ledger, rule_text)

    if a.json:
        print(json.dumps({
            "total": len(rows), "star": len(stars), "upgraded": len(done),
            "covered_locally": len(local), "deferred": len(defer),
            "pending": [r["id"] for r in pending], "stale": [r["id"] for r in stale],
            "ledger_errors": [{"id": i, "why": w} for i, w in bad],
            "rule_layer": str(RULE_LAYER), "rule_layer_exists": RULE_LAYER.exists(),
        }, ensure_ascii=False, indent=1))
        return 1 if (a.strict and stale) else 0

    print(f"规则层：{RULE_LAYER}")
    print(f"          {'存在' if RULE_LAYER.exists() else '❌ 不存在（升级链路必然空转）'}")
    print(f"经验总数 {len(rows)}｜★ 候选 {len(stars)}｜已升级 {len(done)}｜"
          f"本地强制 {len(local)}｜待升(预算阻塞) {len(defer)}｜待处置 {len(pending)}")
    print()
    if bad:
        print("🔴 台账自身不一致（登记的 rule_line 在规则层找不到）：")
        for i, w in bad:
            print(f"   {i}: {w}")
        print()
    if pending:
        print("待处置的 ★ 候选（按龄降序；已升级与本地强制的不列入）：")
        for r in sorted(pending, key=lambda x: -x["age_days"]):
            flag = "🔴超期" if r["age_days"] > a.max_age else "  "
            print(f"   {flag} {r['id']}  {r['age_days']:>3}天  {r['title'][:52]}")
    elif defer:
        print(f"✅ 无未处置项。另有 {len(defer)} 条已判定值得升，但受规则层余量阻塞（见台账 deferred）。")
    else:
        print("✅ 全部 ★ 候选均已升级或已有本地强制机制兜底。")

    if a.strict and stale:
        print(f"\n[FAIL] {len(stale)} 条 ★ 超过 {a.max_age} 天既未升级也无本地兜底")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
