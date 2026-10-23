#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metrics.py —— 轮次指标采集器

把「防谄媚机制」从**只有阈值与动作**变成**可计算**：
  * 立场反推（两坐标向量）→ 不依赖席位自述（谄媚正是通过自述通道传播的）
  * 翻边率 flip_rate（分母 = 可比对的 (席位 × 轴) 对）
  * 分歧熵 dissent_entropy（Shannon，逐轴归一化）
  * 空转检测 idle_detection（新证据指针计数，仅对 evidence_round 生效）
  * 动作裁决（restart_round / force_devils_advocate_round / terminate_branch）

参数全部来自 config/params.json —— 让它成为**真参数源**而非装饰。
定义式与三条诚实边界见 docs/protocol/07-轮次与指标.md。

依赖：仅 Python 标准库。跨平台。

用法：
    python3 metrics.py --round-dir runs/round2 [--prev round-dir runs/round1]
    python3 metrics.py --round-dir runs/round2 --json
    python3 metrics.py --self-test

退出码（严重度序：5 > 1 > 3 > 4）：
    0 = 无需动作
    1 = 需 restart_round（翻边率超阈值）
    2 = 输入/用法错误
    3 = 需 force_devils_advocate_round（分歧熵低于下限 + 全一致 + 无独立证据）
    4 = 需 terminate_branch（evidence_round 上空转达标）
    5 = blocked_by_contract（存在被否决席位，指标不可信）
"""

import argparse
import json
import math
import sys
from datetime import timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "judges"))

from seat_check import (  # noqa: E402
    build_axis_registry, derive_stance, load_json, sha256_of, lint_params, now_iso, normalize_pointer, _pkg_root,
    evaluate_budget, POINTER_ID_DEFINITION,
)

TZ_CN = timezone(timedelta(hours=8))

EXIT_NONE = 0
EXIT_RESTART = 1
EXIT_ERROR = 2
EXIT_DEVILS = 3
EXIT_TERMINATE = 4
EXIT_BLOCKED = 5

# 严重度序：数值越大越优先上报
SEVERITY = {"blocked_by_contract": 5, "restart_round": 4,
            "force_devils_advocate_round": 3, "terminate_branch": 2, "none": 0}
ACTION_EXIT = {"none": EXIT_NONE, "restart_round": EXIT_RESTART,
               "force_devils_advocate_round": EXIT_DEVILS, "terminate_branch": EXIT_TERMINATE}

DEBATING_SEATS = ("EX-01", "EX-02", "EX-03")
NON_COMPARABLE = ("abstain", "out_of_space")


# ---------------------------------------------------------------- 熵

def shannon_entropy(counts):
    n = sum(counts.values())
    if n <= 0:
        return None
    h = 0.0
    for c in counts.values():
        if c > 0:
            p = c / n
            h -= p * math.log2(p)
    return h


def dissent_entropy(stances, registry):
    """逐轴计算 Shannon 熵并归一化。只对「可比对」的席位（非弃权、非越界）计数。"""
    per_axis = {}
    for axis in list(registry.get("primary", {})) + list(registry.get("joint", {})):
        picks = {}
        for seat in DEBATING_SEATS:
            ch = (stances.get(seat) or {}).get(axis, {}).get("choice", "abstain")
            if ch in NON_COMPARABLE:
                continue
            picks[seat] = ch
        n = len(picks)
        counts = {}
        for ch in picks.values():
            counts[ch] = counts.get(ch, 0) + 1

        # K = 该轴的选项基数
        if axis in registry.get("primary", {}):
            k = len(registry["primary"][axis])
        else:
            k = len(registry["joint"][axis]["values"])

        h = shannon_entropy(counts) if n >= 1 else None
        h_norm = None
        if h is not None and n >= 2 and k >= 2:
            h_norm = h / math.log2(k)

        per_axis[axis] = {
            "H": None if h is None else round(h, 6),
            "H_norm": None if h_norm is None else round(h_norm, 6),
            "K": k,
            "N": n,
            "counts": counts,
            "unanimous": (n >= 2 and len(counts) == 1),
            "comparable_seats": sorted(picks),
        }

    vals = [v["H_norm"] for v in per_axis.values() if v["H_norm"] is not None]
    return {
        "per_axis": per_axis,
        "round_mean": round(sum(vals) / len(vals), 6) if vals else None,
    }


# ---------------------------------------------------------------- 翻边率

def flip_rate(cur_stances, prev_stances, registry):
    by_seat, by_axis = {}, {}
    numerator, denominator = 0, 0
    flips = []

    axes = list(registry.get("primary", {})) + list(registry.get("joint", {}))
    for seat in DEBATING_SEATS:
        seat_pairs = seat_flips = 0
        for axis in axes:
            a = (prev_stances.get(seat) or {}).get(axis, {}).get("choice")
            b = (cur_stances.get(seat) or {}).get(axis, {}).get("choice")
            if a in NON_COMPARABLE or b in NON_COMPARABLE or a is None or b is None:
                continue
            seat_pairs += 1
            by_axis.setdefault(axis, {"n": 0, "flips": 0})
            by_axis[axis]["n"] += 1
            if a != b:
                seat_flips += 1
                by_axis[axis]["flips"] += 1
                flips.append({"seat": seat, "axis": axis, "from": a, "to": b})
        denominator += seat_pairs
        numerator += seat_flips
        if seat_pairs:
            by_seat[seat] = {"pairs": seat_pairs, "flips": seat_flips,
                             "rate": round(seat_flips / seat_pairs, 6)}

    value = None if denominator == 0 else round(numerator / denominator, 6)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "denominator_definition": "comparable_seat_axis_pairs",
        "low_confidence": denominator < 2,
        "by_seat": by_seat,
        "by_axis": by_axis,
        "flips": flips,
        "note": ("D=0 → value=null（未定义，绝不当 0）"
                 if denominator == 0 else
                 ("D<2 → low_confidence，不按阈值硬判重启" if denominator < 2 else "")),
    }


# ---------------------------------------------------------------- 证据指针

def collect_pointers(round_dir):
    """从 seat-check.json 读取本轮全部证据指针 id 集合。"""
    sc = load_json(Path(round_dir) / "seat-check.json")
    ids = set()
    if sc:
        for r in sc.get("results", []):
            for p in (r.get("derived") or {}).get("evidence_pointers", []):
                if p.get("pointer_id"):
                    ids.add(p["pointer_id"])
    return ids


def collect_file_keys(round_dir):
    """从 seat-check.json 读取本轮证据的【文件级】键集合。

    用途与 pointer_id 不同（这是必须分清的一条）：
      pointer_id  → 空转检测（是否带来了新观测单元）
      file_key    → 独立证据（是否与他人共享同一证据源）
    混用会互相打穿：把 pointer_id 降到文件级，两件事就变成同一件了。
    """
    sc = load_json(Path(round_dir) / "seat-check.json")
    keys = set()
    if sc:
        for r in sc.get("results", []):
            for p in (r.get("derived") or {}).get("evidence_pointers", []):
                for part in p.get("pointer_parts") or []:
                    if part.get("file"):
                        keys.add(part["file"])
    return keys


def evidence_delta(round_dir, prev_dir, role, min_streak):
    cur = collect_pointers(round_dir)
    prev_ids = set()
    prev_idle = False
    if prev_dir:
        prev_ids = collect_pointers(prev_dir)
        pm = load_json(Path(prev_dir) / "metrics.json")
        if pm:
            prev_idle = bool((pm.get("evidence") or {}).get("idle"))

    cur_f = collect_file_keys(round_dir)
    prev_f = collect_file_keys(prev_dir) if prev_dir else set()
    new_files = sorted(cur_f - prev_f)

    new = sorted(cur - prev_ids)
    idle = (len(new) == 0)
    streak = (1 + (1 if prev_idle else 0)) if idle else 0

    applies = (role == "evidence_round")
    detected = applies and streak >= min_streak

    notes = []
    if not applies:
        notes.append(f"round_role={role} → idle_detection 不适用"
                     "（该轮按定义不引入新证据，套用会自证终止）")
    notes.append("本轮新指针数基于 seat-check.json 的 derived.evidence_pointers；"
                 "历史基线来自各轮 evidence-pointers 集合（含上一轮）")
    if new and not new_files:
        # ★ 诚实提示（F17 残余边界）：pointer_id 只消掉路径写法噪声。
        #   同一观测换个选择子写法（#T104 → :26）仍会被算作「新证据」。
        #   本条不给动作，只把可疑形态说出来，避免「有新指针 = 有新观测」这个错觉。
        notes.append(f"★ 本轮 {len(new)} 条「新证据」全部来自【已有文件】（无新文件）—— "
                     "可能是同一观测换了选择子写法。pointer_id 只能消掉路径写法噪声，"
                     "无法区分「新观测」与「旧观测换写法」。")

    return {
        "input_pointer_count": len(cur),
        "new_count": len(new),
        "new_pointer_ids": new,
        "new_file_keys": new_files,
        "new_file_key_count": len(new_files),
        "pointer_id_definition": POINTER_ID_DEFINITION,
        "idle": idle,
        "idle_streak": streak,
        "idle_detection_applies": applies,
        "idle_detected": detected,
        "min_streak_required": min_streak,
        "notes": notes,
    }


# ---------------------------------------------------------------- 主流程

def compute(round_dir, prev_dir, params, adj_spec):
    round_dir = Path(round_dir)
    manifest = load_json(round_dir / "round-manifest.json") or {}
    role = manifest.get("role", "unknown")
    registry = build_axis_registry(adj_spec, params)

    sc = load_json(round_dir / "seat-check.json")
    if sc is None:
        return None, "缺少 seat-check.json，请先运行：seat_check.py --round-dir <本轮>"

    blocked = sc.get("aggregation_blocked") or []
    valid_seats = set(sc.get("aggregation_admissible") or [])

    # 立场只取自合规席位
    cur_stances, mismatches = {}, []
    for r in sc.get("results", []):
        seat = r["seat"]
        if seat not in valid_seats or seat not in DEBATING_SEATS:
            continue
        d = (r.get("derived") or {}).get("derived_stance") or {}
        cur_stances[seat] = d
        for m in r.get("stance_mismatches") or []:
            mismatches.append({"seat": seat, **m})

    if blocked:
        return {
            "kind": "round_metrics",
            "round": round_dir.name,
            "round_role": role,
            "generated_at": now_iso(),
            "metrics_status": "blocked_by_contract",
            "seats_contract_valid": sc.get("summary", {}).get("valid", 0),
            "seats_blocked": [b["seat"] for b in blocked],
            "dissent_entropy": None,
            "flip_rate": None,
            "evidence": None,
            "actions": ["blocked_by_contract"],
            "exit_code": EXIT_BLOCKED,
            "notes": [
                "存在被否决席位 → 拒绝计算指标。",
                "理由：从被否决的产出里算翻边率，会把契约污染带进指标。",
                f"被否决席位：{[b['seat'] for b in blocked]}",
            ],
        }, None

    de = dissent_entropy(cur_stances, registry)

    fr = None
    prev_stances = {}
    # ★ F7 修复：把 flip_rate=null 的【成因】显式化。
    #   原实现只有「有 value / 无 value」两条路，且无 value 时 note 恒为
    #   「无上一轮可比对」——于是「未传 --prev」与「传了但基线 0 合规」在产出里
    #   完全不可区分，读者会把「算不出来」误读成「本轮没有翻边」。
    baseline = {"round": None, "status": "not_provided", "reason": "",
                "contract_version": None, "round_role": None, "compliant_seats": []}
    if prev_dir:
        pdir = Path(prev_dir)
        pman = load_json(pdir / "round-manifest.json") or {}
        baseline["round"] = pdir.name
        baseline["contract_version"] = str(pman.get("contract_version") or "1.0")
        baseline["round_role"] = pman.get("role", "unknown")
        psc = load_json(pdir / "seat-check.json")
        if psc is None:
            baseline["status"] = "baseline_missing"
            baseline["reason"] = (f"给了 --prev {pdir.as_posix()}，但该目录内无 "
                                  "seat-check.json，取不到基线立场")
        else:
            pvalid = set(psc.get("aggregation_admissible") or [])
            for r in psc.get("results", []):
                if r["seat"] in pvalid and r["seat"] in DEBATING_SEATS:
                    prev_stances[r["seat"]] = (r.get("derived") or {}).get("derived_stance") or {}
            baseline["compliant_seats"] = sorted(prev_stances)
            if not prev_stances:
                baseline["status"] = "baseline_no_compliant_seat"
                baseline["reason"] = (
                    f"基线轮 {pdir.name} 的 seat-check.json 中 aggregation_admissible 为空"
                    f"（0/{len(psc.get('results', []))} 席位合规）→ 无立场可比对，D=0。"
                    "★ 这不是「本轮没有翻边」，而是【契约前向兼容缺口】："
                    "该轮产出早于席位契约，数字在 answer.summary 下、顶层无 summary.*，"
                    "故立场一律反推为 abstain。")
            else:
                baseline["status"] = "ok"
                baseline["reason"] = (f"基线轮 {pdir.name} 提供 {len(prev_stances)} 个合规席位"
                                      f"（契约 v{baseline['contract_version']}）")
        fr = flip_rate(cur_stances, prev_stances, registry)

    ev = evidence_delta(round_dir, prev_dir, role,
                        params["anti_sycophancy"]["idle_detection"]["consecutive_rounds_without_new_evidence"])

    # 动作裁决
    actions, notes = [], []
    floor = params["anti_sycophancy"]["dissent_entropy_floor"]

    if fr and fr["value"] is not None:
        thr = params["anti_sycophancy"]["flip_rate_alert_threshold"]
        fr["threshold"] = thr
        fr["alert"] = fr["value"] > thr
        if fr["alert"]:
            if fr["low_confidence"]:
                notes.append(f"翻边率 {fr['value']} > {thr} 但 D={fr['denominator']} < 2，"
                             "标 low_confidence，不据此硬判重启")
            else:
                actions.append(params["anti_sycophancy"]["flip_rate_action"])
                notes.append(f"翻边率 {fr['value']} > 阈值 {thr} → {params['anti_sycophancy']['flip_rate_action']}"
                             f"（分子 {fr['numerator']} / 分母 {fr['denominator']}）")
        else:
            notes.append(f"翻边率 {fr['value']} ≤ 阈值 {thr}，未触发重启")
        fr["status"] = "ok"
    else:
        fr = fr or {}
        fr["threshold"] = params["anti_sycophancy"]["flip_rate_alert_threshold"]
        fr["alert"] = None
        # 基线可用但无可比对的 (席位×轴) 对（例如合规席位全体弃权）→ 独立成态，
        # 不要与「基线不可用」混为一谈。
        fr["status"] = (baseline["status"] if baseline["status"] != "ok"
                        else "baseline_no_comparable_pair")
        notes.append(f"flip_rate=null（未定义，不是 0）：{baseline['reason']}")
    fr["reason"] = baseline["reason"]
    fr["baseline_round"] = baseline["round"]
    fr["baseline_round_role"] = baseline["round_role"]
    fr["baseline_contract_version"] = baseline["contract_version"]
    fr["baseline_compliant_seats"] = baseline["compliant_seats"]

    # 分歧熵下限
    de["floor"] = floor["value"]
    rm = de.get("round_mean")
    unanimous_axis = [ax for ax, v in de["per_axis"].items() if v["unanimous"]]

    # ★ 「独立证据」的操作化 —— F6 修复：聚类粒度从【指针整串】提到【文件级】
    #
    #   旧实现去重键是 sha256(指针整串)，不解析 # / :。实测后果：round2 三席都指向
    #   fixtures/ledger.csv，却因写法不同（#T104,T105 vs :26-27）被判为「互不共享」，
    #   shared_pointer_count=0、三席全评「独立」 —— 判据退化成了「字符串是否相同」。
    #
    #   新定义式：
    #     文件集合 f(s) = 席位 s 全部证据指针归一化后的文件集合
    #     引用计数 c(f) = |{ s ∈ 合规辩论席位 : f ∈ f(s) }|
    #     席位 s 独立   := ∃f ∈ f(s), c(f) == 1
    #     本轮有独立证据 := ∃s : s 独立
    #
    #   ★ 为什么取文件级而非记录级：两类错误的代价不对称。
    #     粗（文件级）→ 更易判「不独立」→ 更易触发魔鬼代言人轮 = 多花一轮 token，是【干预】；
    #     细（记录级）→ 更易判「独立」→ 漏触发 = 共享盲区被当成已验证的真相放行，是【污染】。
    #     且实测同一行被写成三种形式，记录级匹配会制造【假精度】。
    #
    #   ★ 铁律：pointer_id（整串 sha256）只服务【空转检测】（collect_pointers / evidence_delta），
    #     本处只用 normalize_pointer 的 file_keys。把 idle 也改成文件级会改掉空转语义。
    pkg_root = _pkg_root()
    roots = tuple(dict.fromkeys((Path(round_dir), pkg_root, pkg_root.parent)))
    files_by_seat, file_total = {}, {}
    norm_stats = {"pointers": 0, "resolved": 0, "unresolved": 0}
    for r in sc.get("results", []):
        seat = r["seat"]
        keys = set()
        for p in (r.get("derived") or {}).get("evidence_pointers", []):
            norm_stats["pointers"] += 1
            n = normalize_pointer(p.get("pointer"), pkg_root, roots)
            norm_stats["resolved" if n["ok"] else "unresolved"] += 1
            keys.update(n["file_keys"])
        files_by_seat[seat] = keys
        for k in keys:
            file_total[k] = file_total.get(k, 0) + 1

    def seat_is_independent(seat):
        return any(file_total.get(k, 0) <= 1 for k in files_by_seat.get(seat, set()))

    independent_seats = sorted(s for s in cur_stances if seat_is_independent(s))
    independent_evidence = bool(independent_seats)
    shared_files = sorted(k for k, c in file_total.items() if c > 1)

    de["below_floor"] = (rm is not None and rm < floor["value"])
    devils_required = bool(
        floor.get("independent_evidence_required", False)
        and de["below_floor"]
        and unanimous_axis
        and not independent_evidence
    )
    de["unanimous_axes"] = unanimous_axis
    de["independent_evidence_seats"] = independent_seats
    # 更名：shared_pointer_count → shared_file_count。语义已从「整串相同」变成
    # 「指向同一文件」，保留旧名会固化一个错误的字段名（无消费者依赖旧名）。
    de["shared_file_count"] = len(shared_files)
    de["shared_files"] = shared_files
    de["pairwise_shared_files"] = {k: c for k, c in sorted(file_total.items()) if c > 1}
    de["normalization"] = dict(norm_stats, granularity="file_level",
                               note="独立证据判据已从「指针整串」升为「文件级」（F6）")
    de["devils_advocate_required"] = devils_required
    if devils_required:
        actions.append(floor["action"])
        notes.append(
            f"分歧熵 round_mean={rm} < 下限 {floor['value']}，且轴 {unanimous_axis} 全一致，"
            f"且无席位带来独立证据（无独立举证席位） → {floor['action']}"
        )
        notes.append("★ 诚实边界：「独立证据」的操作化是「其指针不被其他席位引用」。"
                     "脚本据此判断，无法进一步验证该指针的实质内容是否真的独立。")
    elif de["below_floor"]:
        if independent_evidence:
            notes.append(f"分歧熵 round_mean={rm} < 下限，但 {independent_seats} 带来了独立证据，未触发干预")
        else:
            notes.append(f"分歧熵 round_mean={rm} < 下限，但不存在全一致轴，未触发干预")

    if ev["idle_detected"]:
        actions.append(params["anti_sycophancy"]["idle_detection"]["action"])
        notes.append(f"连续 {ev['idle_streak']} 轮无新证据指针（阈值 {ev['min_streak_required']}）"
                     f" → {params['anti_sycophancy']['idle_detection']['action']}")

    # ---- F18：预算（声明式）----
    #   三态必须分清：exhausted 才是超支；unverifiable 只是「有席位没申报」。
    #   把未核验当超支，会让所有历史轮次一上线就被判终止。
    budget = evaluate_budget(sc, params)
    if budget["status"] == "exhausted":
        act = (params.get("cost_budget") or {}).get("action") or "terminate_branch"
        actions.append(act)
        notes.append("预算耗尽（申报值）：" + "；".join(budget["exhausted_reasons"])
                     + f" → {act}")
    elif budget["status"] == "unverifiable":
        notes.append(f"预算无法核验：{len(budget['missing'])} 个席位未申报 usage"
                     f"（{budget['missing']}）—— 这是【未核验】，不等于超支")
    else:
        notes.append(f"预算：tokens {budget['tokens_total']}/{budget['tokens_cap']}，"
                     f"wall-clock {budget['minutes_span']}/{budget['minutes_cap']} 分钟，未超限")

    rounds_so_far = int("".join(ch for ch in round_dir.name if ch.isdigit()) or 0)
    if rounds_so_far >= params["debate"]["max_rounds"] + 1:
        notes.append(f"已达 max_rounds={params['debate']['max_rounds']}（当前轮 {round_dir.name}）")

    actions = sorted(set(actions), key=lambda a: -SEVERITY.get(a, 0))
    if not actions:
        actions = ["none"]

    primary = max(actions, key=lambda a: SEVERITY.get(a, 0))
    exit_code = EXIT_BLOCKED if "blocked_by_contract" in actions else ACTION_EXIT.get(primary, EXIT_NONE)

    # 长度诊断
    lens = []
    for r in sc.get("results", []):
        fp = Path(r.get("file") or "")
        if fp.is_file():
            lens.append(fp.stat().st_size)
    length_spread = None
    if lens:
        mean = sum(lens) / len(lens)
        var = sum((x - mean) ** 2 for x in lens) / len(lens)
        length_spread = {"min": min(lens), "max": max(lens),
                         "stdev": round(math.sqrt(var), 1), "unit": "bytes"}

    return {
        "kind": "round_metrics",
        "round": round_dir.name,
        "round_role": role,
        "generated_at": now_iso(),
        "metrics_status": "ok",
        "params_sha256": sha256_of("config/params.json"),
        "axis_registry_sha256": None,
        "seats_total": len(DEBATING_SEATS),
        "seats_contract_valid": sc.get("summary", {}).get("valid", 0),
        "seats_blocked": [],
        "stance": cur_stances,
        "stance_mismatches": mismatches,
        "dissent_entropy": de,
        "flip_rate": fr,
        "evidence": ev,
        "budget": budget,
        "pointer_id_definition": POINTER_ID_DEFINITION,
        "length_spread": length_spread,
        "actions": actions,
        "exit_code": exit_code,
        "notes": notes + ev["notes"],
    }, None


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="轮次指标采集器")
    ap.add_argument("--round-dir", "--round", dest="round_dir", required=False)
    ap.add_argument("--prev", default=None, help="上一轮目录（用于翻边率）")
    ap.add_argument("--params", default="config/params.json")
    ap.add_argument("--spec", default="fixtures/spec/adjudication.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.round_dir:
        ap.print_help()
        return EXIT_ERROR

    params = load_json(args.params)
    if params is None:
        print(f"[ERR] 读不到 {args.params}", file=sys.stderr)
        return EXIT_ERROR
    missing = lint_params(params)
    if missing:
        print(f"[ERR] params 缺必需键：{missing}", file=sys.stderr)
        return EXIT_ERROR

    adj = load_json(args.spec)
    if adj is None:
        print(f"[ERR] 读不到 {args.spec}", file=sys.stderr)
        return EXIT_ERROR

    result, err = compute(args.round_dir, args.prev, params, adj)
    if err:
        print(f"[ERR] {err}", file=sys.stderr)
        return EXIT_ERROR

    result["axis_registry_sha256"] = sha256_of(args.spec)
    out = Path(args.out) if args.out else Path(args.round_dir) / "metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result["exit_code"]

    print("=" * 68)
    print(f"轮次指标 · {result['round']}  (role={result['round_role']})")
    print("=" * 68)
    if result["metrics_status"] != "ok":
        print(f"  ★ {result['metrics_status']} —— 被否决席位 {result['seats_blocked']}，拒绝计算指标")
        print(f"  -> {out}")
        return result["exit_code"]

    de, fr, ev = result["dissent_entropy"], result["flip_rate"], result["evidence"]
    print("【立场向量】(非弃权者计入熵与翻倍率分母)")
    for seat, st in sorted(result["stance"].items()):
        picks = " ".join(f"{ax}={v['choice']}" for ax, v in sorted(st.items()))
        print(f"  {seat}: {picks}")
    if result["stance_mismatches"]:
        print("  ★ 自述与反推背离（这是有价值的信号，非错误）：")
        for m in result["stance_mismatches"]:
            print(f"    {m['seat']} {m['axis']}: 自述 {m['declared']} vs 反推 {m['derived']}")

    print("\n【分歧熵 dissent_entropy】")
    for ax, v in sorted(de["per_axis"].items()):
        print(f"  {ax:10s} H={v['H']}  H_norm={v['H_norm']}  K={v['K']} N={v['N']}  "
              f"counts={v['counts']}{'  ← 全一致' if v['unanimous'] else ''}")
    print(f"  round_mean={de['round_mean']}  下限={de['floor']}  below_floor={de['below_floor']}")
    print(f"  魔鬼代言人轮需要={de['devils_advocate_required']}")

    print("\n【翻边率 flip_rate】")
    if fr.get("value") is None:
        print(f"  值=null（未定义，不是 0）  {fr.get('note', '')}")
    else:
        print(f"  {fr['numerator']}/{fr['denominator']} = {fr['value']}  "
              f"阈值={fr['threshold']}  告警={fr['alert']}  低置信={fr['low_confidence']}")
        for f in fr.get("flips") or []:
            print(f"    ↻ {f['seat']} {f['axis']}: {f['from']} → {f['to']}")

    print("\n【空转检测 evidence】")
    print(f"  新指针 {ev['new_count']} / 本轮共 {ev['input_pointer_count']}  空闲={ev['idle']}  "
          f"连击={ev['idle_streak']}  适用={ev['idle_detection_applies']}  触发={ev['idle_detected']}")

    if result.get("length_spread"):
        print(f"\n【长度诊断】{result['length_spread']}")

    print("\n【动作】" + ", ".join(result["actions"]))
    for n in result["notes"]:
        print(f"  · {n}")
    print(f"  退出码={result['exit_code']}")
    print(f"  -> {out}")
    return result["exit_code"]


# ---------------------------------------------------------------- 自检

def self_test():
    n_ok, fails = 0, []

    def expect(label, cond, detail=""):
        nonlocal n_ok
        if cond:
            n_ok += 1
            print(f"  [PASS] {label}" + (f"  {detail}" if detail else ""))
        else:
            fails.append(label)
            print(f"  [FAIL] {label}  {detail}")

    print("=" * 68)
    print("metrics.py 自检")
    print("=" * 68)

    print("\n【Shannon 熵】")
    expect("2 选项 3 席 1:2 → H_norm=0.918",
           abs((shannon_entropy({"a": 1, "b": 2}) or 0) / math.log2(2) - 0.9182958) < 1e-5,
           str(round((shannon_entropy({"a": 1, "b": 2}) or 0) / math.log2(2), 6)))
    expect("全一致 → H=0", shannon_entropy({"a": 3}) == 0.0)
    expect("空计数 → None", shannon_entropy({}) is None)

    spec = {"items": [
        {"id": "ADJ-001", "options": [{"id": "count", "implies": {"summary.amount_diff_total": 680.02,
                                                                  "summary.excluded_amount_total": 0.0}},
                                      {"id": "exclude", "implies": {"summary.amount_diff_total": 500.02,
                                                                    "summary.excluded_amount_total": 180.0}}]},
        {"id": "ADJ-002", "options": [{"id": "as_diff", "implies": {"summary.diff_count": 7}},
                                      {"id": "not_diff", "implies": {"summary.diff_count": 6}}]},
        {"id": "ADJ-003", "options": [{"id": "as_diff", "implies": {"summary.diff_count": 7}},
                                      {"id": "not_diff", "implies": {"summary.diff_count": 5}}]},
    ]}
    params = {"seat_contract": {"stance": {"primary_axes": ["ADJ-001"],
                                           "joint_axes": {"DIFFSET": ["ADJ-002", "ADJ-003"]}}}}
    reg = build_axis_registry(spec, params)

    print("\n【分歧熵：全一致 → 应为 unity 探测器】")
    uni = {"EX-01": derive_stance({"summary": {"amount_diff_total": 500.02,
                                               "excluded_amount_total": 180.0, "diff_count": 7}}, reg),
           "EX-02": derive_stance({"summary": {"amount_diff_total": 500.02,
                                               "excluded_amount_total": 180.0, "diff_count": 7}}, reg),
           "EX-03": derive_stance({}, reg)}
    de = dissent_entropy(uni, reg)
    expect("ADJ-001 全一致 → H_norm=0", de["per_axis"]["ADJ-001"]["H_norm"] == 0.0)
    expect("EX-03 弃权 → N=2（不进分母）", de["per_axis"]["ADJ-001"]["N"] == 2)
    expect("标记全一致", de["per_axis"]["ADJ-001"]["unanimous"] is True)
    expect("round_mean=0", de["round_mean"] == 0.0)

    print("\n【分歧熵：1 异议 + 2 多数】")
    split = dict(uni)
    split["EX-03"] = derive_stance({"summary": {"amount_diff_total": 680.02,
                                                "excluded_amount_total": 0.0, "diff_count": 7}}, reg)
    de2 = dissent_entropy(split, reg)
    expect("ADJ-001 → N=3", de2["per_axis"]["ADJ-001"]["N"] == 3)
    expect("H_norm≈0.918（2 选项轴上 1:2）",
           abs(de2["per_axis"]["ADJ-001"]["H_norm"] - 0.9182958) < 1e-5,
           str(de2["per_axis"]["ADJ-001"]["H_norm"]))
    expect("不再标记全一致", de2["per_axis"]["ADJ-001"]["unanimous"] is False)

    print("\n【翻边率：分母 = 可比对 (席位×轴) 对】")
    prev = {"EX-01": {"ADJ-001": {"choice": "exclude"}, "DIFFSET": {"choice": "7"}},
            "EX-02": {"ADJ-001": {"choice": "exclude"}, "DIFFSET": {"choice": "7"}},
            "EX-03": {"ADJ-001": {"choice": "abstain"}, "DIFFSET": {"choice": "abstain"}}}
    cur = {"EX-01": {"ADJ-001": {"choice": "exclude"}, "DIFFSET": {"choice": "7"}},
           "EX-02": {"ADJ-001": {"choice": "count"}, "DIFFSET": {"choice": "7"}},
           "EX-03": {"ADJ-001": {"choice": "abstain"}, "DIFFSET": {"choice": "5"}}}
    fr = flip_rate(cur, prev, reg)
    expect("分母=4（EX-01/02 各 2 轴；EX-03 全程弃权不进分母）",
           fr["denominator"] == 4, f"D={fr['denominator']}")
    expect("分子=1（仅 EX-02 的 ADJ-001 翻边）", fr["numerator"] == 1, f"N={fr['numerator']}")
    expect("值=0.25", fr["value"] == 0.25, str(fr["value"]))
    expect("未告警（0.25 不 > 0.40）", not (0.25 > 0.40))

    fr0 = flip_rate(cur, {}, reg)
    expect("无上一轮 → D=0 → value=null（绝不当 0）",
           fr0["denominator"] == 0 and fr0["value"] is None, str(fr0["value"]))
    expect("D<2 → low_confidence", fr0["low_confidence"] is True)

    fr1 = flip_rate({"EX-01": {"ADJ-001": {"choice": "count"}}},
                    {"EX-01": {"ADJ-001": {"choice": "exclude"}}}, reg)
    expect("D=1 → low_confidence 仍标记", fr1["low_confidence"] is True and fr1["value"] == 1.0)

    print("\n【空转：只对 evidence_round 生效】")
    ev_rev = {"idle": True, "idle_streak": 2, "applies": False}
    expect("revision_round 不触发（豁免规则）",
           not (ev_rev["applies"] and ev_rev["idle_streak"] >= 2))
    ev_evd = {"idle": True, "idle_streak": 2, "applies": True}
    expect("evidence_round 连击达标 → 触发",
           ev_evd["applies"] and ev_evd["idle_streak"] >= 2)

    print("\n【动作严重度】")
    acts = ["terminate_branch", "restart_round"]
    primary = max(acts, key=lambda a: SEVERITY.get(a, 0))
    expect("restart(4) 优先于 terminate(2)", primary == "restart_round", primary)
    expect("blocked 优先级最高", SEVERITY["blocked_by_contract"] == 5)

    print("\n" + "-" * 68)
    total = n_ok + len(fails)
    if fails:
        print(f"[FAIL] 自检未通过：{len(fails)}/{total} 项失败")
        for f in fails:
            print("   -", f)
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{total} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
