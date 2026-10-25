#!/usr/bin/env python3
"""★ 升级候选台账核对：哪些经验真的进了跨项目规则层。

## 为什么需要这个

2026-10-26 发现：`🔄 跨项目经验升级(全自动)`（周日 23:57）被当作"会把 ★ 经验
自动捞走"的既定事实，但实际上——

1. 用户级 `MEMORY.md`（跨项目规则层）里 **`L-` 编号出现 0 次**，从未记录 provenance；
2. 2026-10-25 夜首次机会，本仓库零产出，而同批周日自动化（磁盘巡检 / 下周前瞻 /
   巡检中枢周报 / 多仓库周报 / 绩效周报）全部留痕 → 调度器活着，不是平台故障；
3. 该自动化 id 为遗留格式（`automation-1786439069962`），`automation_update`
   **view 读不到其 prompt** → 它的扫描范围**不可审计**。

结论：升级链路处于「**不可证明曾工作过**」状态。所以不能靠记忆宣称，要有台账。

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
    today = date.today()

    rows = []
    for it in lessons:
        age = (today - date.fromisoformat(it["date"])).days
        rows.append({**it, "age_days": age, "upgraded": it["id"] in done})

    stars = [r for r in rows if r["star"]]
    pending = [r for r in stars if not r["upgraded"]]
    stale = [r for r in pending if r["age_days"] > a.max_age]
    bad = check_ledger(ledger, rule_text)

    if a.json:
        print(json.dumps({
            "total": len(rows), "star": len(stars), "upgraded": len(stars) - len(pending),
            "pending": [r["id"] for r in pending], "stale": [r["id"] for r in stale],
            "ledger_errors": [{"id": i, "why": w} for i, w in bad],
            "rule_layer": str(RULE_LAYER), "rule_layer_exists": RULE_LAYER.exists(),
        }, ensure_ascii=False, indent=1))
        return 1 if (a.strict and stale) else 0

    print(f"规则层：{RULE_LAYER}")
    print(f"          {'存在' if RULE_LAYER.exists() else '❌ 不存在（升级链路必然空转）'}")
    print(f"经验总数 {len(rows)}｜★ 候选 {len(stars)}｜已登记升级 {len(stars) - len(pending)}｜未升级 {len(pending)}")
    print()
    if bad:
        print("🔴 台账自身不一致（登记的 rule_line 在规则层找不到）：")
        for i, w in bad:
            print(f"   {i}: {w}")
        print()
    if pending:
        print("未升级的 ★ 候选（按龄降序）：")
        for r in sorted(pending, key=lambda x: -x["age_days"]):
            flag = "🔴超期" if r["age_days"] > a.max_age else "  "
            print(f"   {flag} {r['id']}  {r['age_days']:>3}天  {r['title'][:52]}")
    else:
        print("✅ 全部 ★ 候选均已登记升级。")

    if a.strict and stale:
        print(f"\n[FAIL] {len(stale)} 条 ★ 超过 {a.max_age} 天未升级")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
