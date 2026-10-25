#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""veto_sweep.py —— 24h 否决窗口的载体（只读为主；写只写 verdict 的否决字段）

## 它解决什么

《自治团队方案》写着「日常产出默认通过，24h 内可否决，超时视为接受」，
但实测 33 轮的 `verdict-layered.json` 里 `veto_deadline` **0 处存在**，
`params` 只有 `veto_window_hours: 24`，没有任何脚本读它 ——
"超时视为接受"是一条没有载体的口头约定。

本脚本给它一个载体，并**按用户明确要求保留否决权**设计：

1. **否决权不会因为没人跑扫描而悄悄过期**。"超时视为接受"改为：
   必须显式 `--settle` 才算接受；未 settle 的一直留在待裁清单里。
2. **不需要常驻进程/调度器**：`--list` 是人想看的时候跑的（同 `make backup-list`），
   `--reject` 在 settle 之前随时可用。没有"造了没人开的闹钟"问题。
3. **写入面极小**：只写 verdict 里的 `veto_deadline / human_veto / accepted_at` 三个字段，
   不动判据结论本身；用原子写，避免截断 JSON。

## 用法

    python3 scripts/veto_sweep.py --list                      # 列出待裁（默认动作）
    python3 scripts/veto_sweep.py --stamp path/to/v.json      # 给结论盖上 24h 截止戳
    python3 scripts/veto_sweep.py --reject v.json --who 关 --reason "口径错了"   # 行使否决权
    python3 scripts/veto_sweep.py --settle v.json             # 到期且不否决 → 记为接受

退出码：0 无待裁 / 1 有待裁（窗口内）/ 2 输入或参数错误
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fsutil import write_json_atomic

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "migration-package"
PARAMS = PKG / "config" / "params.json"
RUNDIR = PKG / "runs"

OK, PENDING, ERR = 0, 1, 2
VERDICT_SUFFIX = ("verdict", "adjudicated")


# ── 参数真源：否决窗口小时数只认 params ──────────────────────────────────
def window_hours():
    try:
        d = json.loads(PARAMS.read_text(encoding="utf-8"))
        v = d.get("review", {}).get("veto_window_hours")
        return int(v) if v is not None else 24
    except (OSError, json.JSONDecodeError, ValueError):
        return 24


def _now():
    return datetime.now()


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _parse(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _is_verdict(p):
    return p.is_file() and p.suffix == ".json" and any(k in p.name for k in VERDICT_SUFFIX)


def iter_verdicts(paths):
    for raw in paths:
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else Path.cwd() / p
        if p.is_dir():
            for f in sorted(p.rglob("*.json")):
                if _is_verdict(f):
                    yield f
        elif _is_verdict(p):
            yield p


def load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] 读不了（跳过）：{p} — {e}", file=sys.stderr)
        return None


def state(d, wh):
    """返回 (状态, 剩余小时数)。状态：settled / rejected / open / overdue"""
    if d.get("human_veto"):
        return "rejected", None
    if d.get("accepted_at"):
        return "settled", None
    dl = _parse(d.get("veto_deadline"))
    if dl is None:
        return "unstamped", None          # 没盖戳：无法判断，必须显式暴露
    left = (dl - _now()).total_seconds() / 3600.0
    return ("open", left) if left > 0 else ("overdue", left)


def fmt_row(p, st, left):
    rel = p.relative_to(ROOT) if p.is_relative_to(ROOT) else p
    if st == "open":
        return f"  ⏳ {left:6.1f}h 内可否决   {rel}"
    if st == "overdue":
        return f"  ⌛ 已过期未 settle   {rel}（仍可 --reject；--settle 记为接受）"
    if st == "rejected":
        return f"  🚫 已否决           {rel}"
    if st == "settled":
        return f"  ✅ 已接受           {rel}"
    return f"  ❓ 未盖 veto 戳       {rel}（--stamp 补上）"


def main():
    ap = argparse.ArgumentParser(description="24h 否决窗口载体（保留人工否决权）")
    ap.add_argument("paths", nargs="*", default=[str(RUNDIR)],
                    help="verdict 文件或目录（默认 migration-package/runs）")
    ap.add_argument("--list", action="store_true", help="列出待裁（默认动作）")
    ap.add_argument("--stamp", action="store_true", help="给 verdict 盖上 24h 截止戳")
    ap.add_argument("--reject", action="store_true", help="行使否决权（需 --who/--reason）")
    ap.add_argument("--settle", action="store_true", help="到期且不否决 → 记为接受")
    ap.add_argument("--who", default="", help="否决人")
    ap.add_argument("--reason", default="", help="否决理由（必填）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    acts = [a for a, on in (("--list", args.list), ("--stamp", args.stamp),
                            ("--reject", args.reject), ("--settle", args.settle)) if on]
    if len(acts) > 1:
        print("[ERR] --list / --stamp / --reject / --settle 一次只能选一个", file=sys.stderr)
        return ERR
    if args.reject and (not args.who or not args.reason):
        print("[ERR] --reject 必须同时给 --who 与 --reason（否决要留痕）", file=sys.stderr)
        return ERR

    wh = window_hours()
    files = list(iter_verdicts(args.paths))
    if acts and acts[0] in ("--stamp", "--reject", "--settle") and len(files) != 1:
        print(f"[ERR] {acts[0]} 只接受单个 verdict（收到 {len(files)} 个）", file=sys.stderr)
        return ERR

    if acts and acts[0] == "--stamp":
        p = files[0]
        d = load(p)
        if d is None:
            return ERR
        d["veto_deadline"] = _iso(_now() + timedelta(hours=wh))
        write_json_atomic(p, d)
        print(f"[stamp] {p.name} → veto_deadline {d['veto_deadline']}（窗口 {wh}h）")
        return OK

    if acts and acts[0] == "--reject":
        p, d = files[0], load(files[0])
        if d is None:
            return ERR
        st, _ = state(d, wh)
        if st == "settled":
            print(f"[ERR] 已 settle = 已接受，不能再否决：{p.name}（要改就走新决策）", file=sys.stderr)
            return ERR
        if st == "rejected":
            # 一条结论只记一次否决：第二次否决要么走新决策，要么先撤销记录——
            # 静默覆盖会把"第一次为什么否"的证据抹掉。
            print(f"[ERR] 已否决过（{d['human_veto'].get('at')} by {d['human_veto'].get('who')}），"
                  f"不再覆盖：{p.name}", file=sys.stderr)
            return ERR
        d["human_veto"] = {"who": args.who, "reason": args.reason, "at": _iso(_now())}
        write_json_atomic(p, d)
        print(f"[reject] {p.name} → human_veto 已记录（{args.who}：{args.reason}）")
        return OK

    if acts and acts[0] == "--settle":
        p, d = files[0], load(files[0])
        if d is None:
            return ERR
        st, _ = state(d, wh)
        if st != "overdue":
            print(f"[ERR] 只有『已过期未 settle』才可 settle（当前 {st}）", file=sys.stderr)
            return ERR
        d["accepted_at"] = _iso(_now())
        write_json_atomic(p, d)
        print(f"[settle] {p.name} → accepted_at {d['accepted_at']}")
        return OK

    # 默认：列出
    rows, pending = [], 0
    for p in files:
        d = load(p)
        if d is None:
            continue
        st, left = state(d, wh)
        rows.append({"file": p.name, "state": st, "hours_left": left})
        if st == "open":
            pending += 1
    if args.json:
        print(json.dumps({"window_hours": wh, "pending": pending, "rows": rows},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[veto] 窗口 {wh}h；共 {len(rows)} 个结论，{pending} 个仍在窗口内")
        for p, r in zip([f for f in files if load(f) is not None], rows):
            st = {"open": None, "overdue": None, "rejected": None, "settled": None,
                  "unstamped": None}[r["state"]]
            print(fmt_row(p, r["state"], r["hours_left"]))
        if pending:
            print("  → 跑 `--reject <file> --who 你 --reason …` 行使否决权；"
                  "`--settle <file>` 记为接受")
    return PENDING if pending else OK


if __name__ == "__main__":
    sys.exit(main())
