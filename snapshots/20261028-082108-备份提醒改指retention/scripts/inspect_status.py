#!/usr/bin/env python3
"""把 migration-package/runs/round* 的状态提取成可复算的事实层（只读）。

## 为什么存在

本脚本最初由 08:05「看板巡检」自动化在 2026-10-26 现场生成（一次性草稿：写死绝对
路径与 `NOW` 常量）。因为**程序化重扫优于每天肉眼重推**（后者会漂：10-23 就修正过
10-22 的一处误判），故保留并整理为正式工具：路径改为相对本文件解析，删掉未使用的
`NOW`，行为与输出格式保持不变（下游自动化按 stdout 阅读）。

## 输出（只读，不写任何文件）

逐轮打印：answer-final 是否存在 / aggregation_inputs 席位与状态 / answer-status /
**current state（当前状态）** / metrics.flip_rate / verdict(pending, veto_deadline) /
违规律 / S8-1 泄漏。末尾汇总：
LEAK SUMMARY、缺 aggregation_inputs 的轮次、人类闸门、flip_rate 明细、无 answer-final 的轮次。

## 2026-10-28（D-0078）：新增 `current state` 行，verdict 行标「判据侧快照」

**病**：`verdict-layered.json` / `pending-adjudication.json` 是 `verify.py` **判据侧**的产物，
其 `verdict=pending_adjudication` 的语义是「当时有 N 个判断项等人签字」（协议 05 §3：
未裁决≠失败）。裁决落盘后当前状态在 `answer-status.json` 的 `adjudication_state`，
而这两份旧产物**按设计不改写**（改它等于把「当时待裁什么」改写成事后结论）。
结果是本脚本只打 verdict 行时，**29 轮已裁决的轮次看起来全是未裁决**（round13 只是被
人眼发现的其中一个，不是特例）——「聚合噪声掩盖单点」的典型。

**修**：新增 `current state:` 行，**以 `answer-status.json` 为唯一真源**；verdict 行保留
（它是历史事实），但标注 `(判据侧快照 @ts)`，与当前态冲突时追加 `← 已被 ... 取代`。
**不覆盖任何文件**，只改显示口径。

★ 这条不是「round13 的特例处理」：同一缺陷覆盖全部 29 轮，特例化会留下 28 个同形隐患。

## 判据（预注册，不得事后解释）

- **S8-1 泄漏**：某席在 `seat-check*.json` 里被判 blocked/非 valid，但该席仍在
  `answer-final.json` 的 `aggregation_inputs[]` 中（sha 被聚合）→ 真实泄漏。
  仅单边出现不算泄漏，需双边交叉。
- **翻边率超阈值**：`metrics.flip_rate.value > 0.4`（阈值同 inspection_watch.py）。
- **无证据指针**：`answer-final.json` 缺 `aggregation_inputs` 字段（旧版式轮次）。

## 退出码

0 = 正常输出（报告型工具；是否异常由读输出的人/自动化判断，避免"恒红=噪音"）。
2 = 参数/目录错误（`--runs` 指向不存在目录）—— 加了参数就要有参数错误，
    否则「路径写错」会静默变成「0 轮、一切正常」。
"""
import argparse, json, os, glob, sys
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "migration-package" / "runs"
THRESHOLD = 0.4

# ★ `--runs`（2026-10-28，D-0078）：默认仍是真实 runs 目录，行为不变。
#   存在的理由是**可证伪**：`current state` 的判据必须能在合成样本上被击破
#   （已裁决+verdict 自称 pending / 无 answer-status / 非 verify.py 版式三种），
#   否则只能靠「今天正好有一轮是这样」来验证 —— 那等于没有判据。
#   与 inspection_watch.py 的 `--runs` 同一先例（回归第 54 项亦因此而生）。
_ap = argparse.ArgumentParser(description="把 runs/round* 的状态提取成可复算的事实层（只读）")
_ap.add_argument("--runs", default=str(RUNS), help=f"runs 目录（默认 {RUNS}）")
_args = _ap.parse_args()
RUNS = Path(_args.runs).expanduser()
if not RUNS.is_dir():
    print(f"[ERR] runs 目录不存在：{RUNS}", file=sys.stderr)
    sys.exit(2)

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

# ---- ①b current state: answer-status.json 是当前状态的唯一真源 ----
# ★ 两套版式并存（2026-10-28 实测）：
#   (a) verify.py 输出：`adjudication_state`（adjudicated / fail / pending / not_applicable）
#       + provenance + answer/spec/record 三方 sha —— 29 轮是这个；
#   (b) 人工闸门记录：round34 手写 `status: resolved_by_human` + resolution{}。
#   只认 (a) 会把 round34 误报成「无 answer-status」——两套都要认，且要标明是哪套，
#   否则「有文件」和「字段名不同」会混成同一个「未终态」。
RESOLVED_STATES = ("adjudicated", "resolved_by_human", "not_applicable")


def current_state(info):
    """返回 (state|None, detail)。state=None 表示确实拿不到当前状态（不是"未裁决"）。"""
    as_ = info.get("answer_status")
    if not isinstance(as_, dict) or "__error__" in as_:
        return (None, "无 answer-status.json —— 未终态")
    st = as_.get("adjudication_state")
    if st:
        return (st, f"provenance={as_.get('provenance')}, ts={as_.get('ts')}，"
                    f"源 answer-status.json[adjudication_state]")
    st2 = as_.get("status")
    if st2:
        return (st2, f"ts={as_.get('ts')}，源 answer-status.json[status]"
                     "（非 verify.py 版式：人工闸门记录，见其 resolution 段）")
    return (None, "answer-status.json 存在但无 adjudication_state / status 字段（版式未知）")


def annotate_verdict(v, cur_state):
    """verdict 行的后缀：标明它是判据侧快照，并在与当前态冲突时点名取代者。

    冲突判定只看「verdict 自称 pending_adjudication 且当前态已 resolved」这一种，
    其余不改写 —— 少改一个字，就少一次误读。
    """
    ts = v.get("ts") or "?"
    tag = f"（判据侧快照 @{ts}）"
    if str(v.get("verdict")) == "pending_adjudication" and cur_state in RESOLVED_STATES:
        tag += f" ← 已被 answer-status.json 的 {cur_state} 取代（非当前状态，勿据此人判未裁决）"
    return tag


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
    cur_state, cur_detail = current_state(info)
    print(f"  current state: {cur_state or '未终态'}（{cur_detail}）")
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
            print(f"  verdict[{v.get('spec_layer','?')}]: {v.get('verdict')} pending={v.get('pending')} veto_deadline={v.get('veto_deadline')} {annotate_verdict(v, cur_state)}")
    if leak:
        print(f"  *** S8-1 LEAK: {leak}")
    if viol:
        print(f"  seat violations: {viol}")

print("\n" + "="*80)
print("CURRENT STATE SUMMARY（当前状态以 answer-status.json 为准，非 verdict 行）:")
_adj = [rn for rn in sorted(results) if current_state(results[rn])[0] in RESOLVED_STATES]
_unterm = [rn for rn in sorted(results) if not current_state(results[rn])[0]]
_stale = [rn for rn in sorted(results)
          if current_state(results[rn])[0] in RESOLVED_STATES
          and any(isinstance(v, dict) and str(v.get("verdict")) == "pending_adjudication"
                  for v in results[rn]["verdicts"])]
print(f"  已 resolved（adjudicated / resolved_by_human / not_applicable）: {len(_adj)} 轮")
print(f"  未终态（拿不到 answer-status 状态）: {len(_unterm)} 轮 → {_unterm or '无'}")
print(f"  verdict 行自称 pending 但当前态已裁决: {len(_stale)} 轮 → {_stale or '无'}")
if _stale:
    print("    ★ 这是**已知显示现象**，不是待办：verdict-layered.json 是判据侧快照，裁决后不回写。")
    print("      勿据此把任何一轮报成未裁决；要看异常去看 runs/<rn>/integrity-notes.json。")

print("\nLEAK SUMMARY:")
for rn in sorted(results):
    leak = detect_leak(results[rn])
    if leak:
        # ★ 2026-10-27：若该轮已留痕，在此显式标出，避免每日巡检把已定性的问题反复当"新缺陷"。
        #   留痕**优先读旁挂文件** `runs/<rn>/integrity-notes.json` —— 刻意不写进 answer-final.json，
        #   因为那是被 answer-status.sha256 锚定的产物，为加注释而重写它会破坏哈希链
        #   （第一版曾这么做并产生 297 行无关 diff，已回滚）。
        #   兼容旧格式：answer-final.integrity_notes 仍会被读到。
        #   只加标注，不改判定：leak 照样列出。
        af = results[rn].get("answer_final")
        notes = []
        side = RUNS / rn / "integrity-notes.json"
        if side.exists():
            try:
                notes = json.loads(side.read_text(encoding="utf-8")).get("findings") or []
            except (OSError, json.JSONDecodeError):
                notes = []
        if not notes and isinstance(af, dict):
            notes = af.get("integrity_notes") or []
        tag = ""
        if notes:
            n0 = notes[0]
            tag = (f"  ← 已留痕({n0.get('severity','?')}/{n0.get('id','?')}"
                   f"/disposition={n0.get('disposition','?')})，见 integrity-notes.json")
            if len(notes) > 1:
                tag += f" 等 {len(notes)} 项"
        print(f"  {rn}: {leak}{tag}")

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
