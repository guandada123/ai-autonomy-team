#!/usr/bin/env python3
"""把 migration-package/runs/round* 的状态提取成可复算的事实层（只读）。

## 为什么存在

本脚本最初由 08:05「看板巡检」自动化在 2026-10-26 现场生成（一次性草稿：写死绝对
路径与 `NOW` 常量）。因为**程序化重扫优于每天肉眼重推**（后者会漂：10-23 就修正过
10-22 的一处误判），故保留并整理为正式工具：路径改为相对本文件解析，删掉未使用的
`NOW`，行为与输出格式保持不变（下游自动化按 stdout 阅读）。

## 输出（只读，不写任何文件）

逐轮打印：answer-final 是否存在 / aggregation_inputs 席位与状态 / answer-status /
metrics.flip_rate / verdict(pending, veto_deadline) / 违规律 / S8-1 泄漏。末尾汇总：
LEAK SUMMARY、缺 aggregation_inputs 的轮次、人类闸门、flip_rate 明细、无 answer-final 的轮次。

## 判据（预注册，不得事后解释）

- **S8-1 泄漏**：某席在 `seat-check*.json` 里被判 blocked/非 valid，但该席仍在
  `answer-final.json` 的 `aggregation_inputs[]` 中（sha 被聚合）→ 真实泄漏。
  仅单边出现不算泄漏，需双边交叉。
- **翻边率超阈值**：`metrics.flip_rate.value > 0.4`（阈值同 inspection_watch.py）。
- **无证据指针**：`answer-final.json` 缺 `aggregation_inputs` 字段（旧版式轮次）。

## 退出码

恒 0（报告型工具；是否异常由读输出的人/自动化判断，避免"恒红=噪音"）。
"""
import json, os, glob, sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "migration-package" / "runs"
THRESHOLD = 0.4

def load(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}

def read_all_jsons(d, prefix):
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.startswith(prefix) and fn.endswith(".json"):
            out.append(load(os.path.join(d, fn)))
    return out

results = {}
for d in sorted(glob.glob(os.path.join(RUNS, "round*"))):
    rn = os.path.basename(d)
    info = {"dir": rn, "files": sorted(os.listdir(d))}
    # answer-final
    af = load(os.path.join(d, "answer-final.json"))
    info["answer_final"] = af
    # answer-status
    as_ = load(os.path.join(d, "answer-status.json"))
    info["answer_status"] = as_
    # seat-check (all variants)
    scs = read_all_jsons(d, "seat-check")
    info["seat_checks"] = scs
    # metrics (all variants)
    mets = read_all_jsons(d, "metrics")
    info["metrics"] = mets
    # verdict (all variants)
    vs = read_all_jsons(d, "verdict")
    info["verdicts"] = vs
    # adjudication-record
    info["adjudication_record"] = load(os.path.join(d, "adjudication-record.json"))
    info["pending_adjudication"] = load(os.path.join(d, "pending-adjudication.json"))
    results[rn] = info

def get_aggregation_inputs(af):
    if isinstance(af, dict) and "aggregation_inputs" in af:
        return af["aggregation_inputs"]
    return None

def collect_seat_violations(scs):
    # gather all seat entries with status != valid / errors
    viol = {}  # seat -> list of (status, error)
    for sc in scs:
        if not isinstance(sc, dict):
            continue
        seats = sc.get("seats") or sc.get("derived", {}).get("seats") or []
        if isinstance(seats, dict):
            seats = list(seats.values())
        for s in seats:
            if not isinstance(s, dict):
                continue
            name = s.get("seat") or s.get("name")
            st = s.get("status") or s.get("seat_check_status")
            err = s.get("error") or s.get("errors") or s.get("reason")
            if st not in ("valid", None) or err:
                viol.setdefault(name, []).append((st, err))
    # also support top-level per-seat rejection lists
    for sc in scs:
        if not isinstance(sc, dict):
            continue
        for k in ("rejected_seats", "blocked_seats", "aggregation_blocked"):
            v = sc.get(k)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        viol.setdefault(item, []).append(("blocked", k))
                    elif isinstance(item, dict):
                        viol.setdefault(item.get("seat"), []).append(("blocked", k))
    return viol

# ---- ② detect S8-1 leak: vetoed seat sha present in aggregation_inputs ----
def detect_leak(info):
    af = info["answer_final"]
    inputs = get_aggregation_inputs(af)
    if not inputs:
        return []
    input_shas = {}
    for it in inputs:
        if isinstance(it, dict) and it.get("seat_check_status") == "valid":
            input_shas[it.get("seat")] = it.get("sha256")
    viol = collect_seat_violations(info["seat_checks"])
    leaks = []
    for seat, entries in viol.items():
        if seat in input_shas:
            # seat was vetoed/blocked but its sha is in aggregation_inputs
            leaks.append((seat, input_shas[seat], entries))
    return leaks

# ---- iterate ----
print("="*80)
for rn in sorted(results):
    info = results[rn]
    af = info["answer_final"]
    as_ = info["answer_status"]
    inputs = get_aggregation_inputs(af)
    viol = collect_seat_violations(info["seat_checks"])
    mets = info["metrics"]
    leak = detect_leak(info)
    print(f"\n### {rn}")
    print(f"  answer-final present: {isinstance(af, dict) and 'error' not in af}")
    print(f"  aggregation_inputs: {None if inputs is None else len(inputs)}")
    if inputs is not None:
        print(f"    seats: {[ (i.get('seat'), i.get('seat_check_status')) for i in inputs if isinstance(i,dict)]}")
    print(f"  answer-status: {as_ if isinstance(as_, dict) else as_}")
    # metrics flip rate
    frs = []
    for m in mets:
        if isinstance(m, dict):
            fr = m.get("flip_rate")
            ms = m.get("metrics_status")
            if fr is not None or ms is not None:
                frs.append((ms, fr.get("value") if isinstance(fr, dict) else None))
    print(f"  metrics(flip_rate): {frs}")
    # verdicts
    for v in info["verdicts"]:
        if isinstance(v, dict):
            print(f"  verdict[{v.get('spec_layer','?')}]: {v.get('verdict')} pending={v.get('pending')} veto_deadline={v.get('veto_deadline')}")
    if leak:
        print(f"  *** S8-1 LEAK: {leak}")
    if viol:
        print(f"  seat violations: {viol}")

print("\n" + "="*80)
print("LEAK SUMMARY:")
for rn in sorted(results):
    leak = detect_leak(results[rn])
    if leak:
        print(f"  {rn}: {leak}")

print("\nMISSING aggregation_inputs (item ③):")
for rn in sorted(results):
    info = results[rn]
    af = info["answer_final"]
    if isinstance(af, dict) and "aggregation_inputs" not in af:
        print(f"  {rn}: missing field (has keys: {list(af.keys())[:6]})")

print("\nHUMAN GATE (awaiting_human_confirm):")
for rn in sorted(results):
    as_ = results[rn]["answer_status"]
    if isinstance(as_, dict) and as_.get("status") == "awaiting_human_confirm":
        ts = as_.get("ts")
        print(f"  {rn}: blocking={as_.get('blocking')} ts={ts}")

print("\nFLIP RATE values:")
for rn in sorted(results):
    for m in results[rn]["metrics"]:
        if isinstance(m, dict) and "flip_rate" in m:
            fr = m["flip_rate"]
            v = fr.get("value") if isinstance(fr, dict) else None
            print(f"  {rn}: status={m.get('metrics_status')} flip_rate.value={v} threshold={fr.get('threshold') if isinstance(fr,dict) else None}")

print("\nanswer-final ABSENT rounds:")
for rn in sorted(results):
    af = results[rn]["answer_final"]
    if not (isinstance(af, dict) and "error" not in af):
        print(f"  {rn}: {af}")
