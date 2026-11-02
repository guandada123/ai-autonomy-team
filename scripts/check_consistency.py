#!/usr/bin/env python3
"""check_consistency.py —— 判断声明 × 算术产出 的自洽校验（只读）。

## 为什么存在

2026-10-21 发现：ADJ 的答案写在 TASK.md 里，所以「ADJ 选对」不保证「算术按它执行」。

实证（T-13 的单席）：
    判断层  ADJ-001 = count        （520 应计入金额差）
    算术层  amount_diff_total = 13920（520 被排除，等于 exclude）
    → 答对、算错。

## 三类检查

1. **implies 比对**（原有）：声明的选项，其 `implies` 各路径应等于 summary 实际值。
2. **分桶交叉校验**（2026-10-21 新增）：`excluded_categories` 是「被排除在金额差异之外的类别」，
   因此按 CSVs 逐单号算差额、按 `diffs[].category` 归组后：
       Σ(类别 ∈ excluded_categories) 应等于 excluded_amount_total
       Σ(类别 ∉ excluded_categories) 应等于 amount_diff_total
   这类矛盾 `implies` 比对看不见——r18/T-13 就是这样漏掉的：amount 含股息 520，
   而 excluded_categories 把该类别声明为「已排除」。
3. **勾稽闭合**（新增）：`amount_diff_total + excluded_amount_total == raw_total_gap`。

> 2、3 两项在 `dedup_removed_txn_count > 0` 时自动跳过——
> 有重复行时「逐单号取一次差额」与 summary 的口径无法直接对齐，跳过好过误报。

## 声明来源（按优先级）

    --adj <record.json>       人签记录（chosen）
    answer.declared_adjudication
    answer.stance[]           席位自报（六席产出用这个）

## 用法

    # 单个产出
    python3 scripts/check_consistency.py --task T-13 --answer <answer.json> [--adj <rec.json>]

    # 席位层（自报 stance × 自身算术）
    python3 scripts/check_consistency.py --task T-13 --answer runs/round18/EX-01.json

    # 批量扫描（自动识别任务；scope=final 只扫 answer-final，scope=seat 扫全部席位产出）
    python3 scripts/check_consistency.py --sweep migration-package/runs --scope final
    python3 scripts/check_consistency.py --sweep migration-package/runs --scope seat

    # 历史上已签字的产出：只看报告，不以失败码阻断
    ... --warn-only

退出码：0 自洽 / 1 不自洽或有违规 / 2 环境错误 / 3 无声明可查（跳过）
"""

import argparse
import csv
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "migration-package" / "fixtures" / "tasks"

SEAT_NAMES = ("EX-01", "EX-02", "EX-03", "PM-01", "AD-01", "XJ-01", "JD-01")
ZERO = Decimal("0")


# ---------------------------------------------------------------- 基础工具
def load(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def resolve(obj, path):
    """按 'summary.amount_diff_total' 取值。"""
    cur = obj
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def equal(a, b):
    try:
        return abs(Decimal(str(a)) - Decimal(str(b))) < Decimal("0.000001")
    except (InvalidOperation, TypeError, ValueError):
        return a == b


def dec(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def norm_task(raw):
    m = re.search(r"T-\d{2}", str(raw or ""))
    return m.group(0) if m else None


# ---------------------------------------------------------------- 分桶交叉校验
def load_txns(task_dir):
    """返回 {txn_id: (ledger_amount, bill_amount)}；金额缺失侧为 None。"""
    out = {}
    for side, fn in (("L", "ledger.csv"), ("B", "bill.csv")):
        p = task_dir / fn
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    tid = row.get("txn_id")
                    if not tid:
                        continue
                    slot = out.setdefault(tid, {})
                    slot[side] = dec(row.get("amount"))
        except OSError:
            return None
    return out


def row_amount(txns, tid):
    """单个单号的金额差：两侧都有取差的绝对值；单侧缺失取该侧整笔。"""
    slot = txns.get(tid)
    if not slot:
        return None
    l, b = slot.get("L"), slot.get("B")
    if l is None and b is None:
        return None
    if l is None:
        return abs(b)
    if b is None:
        return abs(l)
    return abs(l - b)


def check_partition(answer, task_dir):
    """分桶交叉校验 + 勾稽闭合。返回 (problems, notes)。"""
    problems, notes = [], []
    summ = answer.get("summary") or {}
    diffs = answer.get("diffs") or []
    if not summ or not diffs:
        return problems, notes

    if dec(summ.get("dedup_removed_txn_count") or 0) not in (ZERO, None):
        notes.append("dedup_removed_txn_count > 0，跳过分桶与闭合校验（口径无法直接对齐）")
        return problems, notes

    txns = load_txns(task_dir)
    if txns is None:
        notes.append("缺少 ledger.csv/bill.csv，跳过分桶校验")
        return problems, notes

    excluded_cats = set(summ.get("excluded_categories") or [])
    in_amt, in_exc, unknown = ZERO, ZERO, []
    for d in diffs:
        tid = d.get("id") if isinstance(d, dict) else None
        cat = d.get("category") if isinstance(d, dict) else None
        amt = row_amount(txns, tid)
        if amt is None:
            unknown.append(tid)
            continue
        if cat in excluded_cats:
            in_exc += amt
        else:
            in_amt += amt

    if unknown:
        notes.append(f"{len(unknown)} 个差异单号不在 CSV 中，分桶校验结果不可信：{unknown[:5]}")
    else:
        got_amt, got_exc = summ.get("amount_diff_total"), summ.get("excluded_amount_total")
        if got_exc is not None and not equal(in_exc, got_exc):
            problems.append(
                "X_PARTITION: excluded_categories 下逐单号差额合计 = %s，"
                "但 excluded_amount_total = %r（该字段是「被排除」类别的金额，应相等）"
                % (in_exc, got_exc)
            )
        if got_amt is not None and not equal(in_amt, got_amt):
            problems.append(
                "X_PARTITION: excluded_categories 之外的逐单号差额合计 = %s，"
                "但 amount_diff_total = %r（该字段应只含「计入」类别）" % (in_amt, got_amt)
            )

    # 勾稽闭合：与声明无关，纯字段间恒等
    a, e, g = (
        dec(summ.get("amount_diff_total")),
        dec(summ.get("excluded_amount_total")),
        dec(summ.get("raw_total_gap")),
    )
    if None not in (a, e, g) and not equal(a + e, g):
        problems.append(
            "X_GAP_NOT_CLOSED: amount_diff_total(%s) + excluded_amount_total(%s) = %s，"
            "但 raw_total_gap = %s" % (a, e, a + e, g)
        )
    return problems, notes


# ---------------------------------------------------------------- 声明 × implies
def declared_from(answer, adj_path):
    if adj_path:
        rec = load(Path(adj_path).expanduser()) or {}
        d = {
            x["id"]: x.get("chosen")
            for x in (rec.get("decisions") or [])
            if isinstance(x, dict) and x.get("id")
        }
        if d:
            return d, f"裁决记录 {Path(adj_path).name}"
    if isinstance(answer.get("declared_adjudication"), dict):
        return dict(answer["declared_adjudication"]), "answer.declared_adjudication"
    stance = answer.get("stance")
    if isinstance(stance, list):
        d = {
            s.get("axis"): str(s.get("choice"))
            for s in stance
            if isinstance(s, dict) and s.get("axis")
        }
        if d:
            return d, "answer.stance（席位自报）"
    return {}, ""


def check_one(task, answer_path, adj_path="", quiet=False, note_holder=None):
    """返回 (status, problems, notes)；status ∈ {'pass','fail','skip','error'}"""
    task_dir = TASKS / task
    spec = load(task_dir / "spec" / "adjudication.json")
    answer = load(Path(answer_path).expanduser())
    if not spec or not answer:
        return "error", [f"无法读取 spec 或 answer（{task} / {answer_path}）"], []

    declared, src = declared_from(answer, adj_path)
    problems, notes = [], []

    # —— 检查 1：implies 比对
    checked = 0
    if declared:
        for it in spec.get("items") or []:
            aid = it.get("id")
            if aid not in declared:
                continue
            chosen = str(declared[aid])
            opt = next((o for o in it.get("options") or [] if str(o.get("id")) == chosen), None)
            if opt is None:
                problems.append(f"{aid}: 声明选了 {chosen!r}，但该选项不在 spec 中")
                continue
            for path, expect in (opt.get("implies") or {}).items():
                got = resolve(answer, path)
                if got is None:
                    continue  # 字段缺失不算矛盾（历史席位普遍缺 fee_diff_total）
                checked += 1
                if not equal(got, expect):
                    problems.append(
                        f"{aid} 声称选 {chosen!r}，但 {path} = {got!r}（应为 {expect!r}）"
                    )

    # —— 检查 2/3：分桶 + 闭合
    p2, n2 = check_partition(answer, task_dir)
    problems += p2
    notes += n2

    if not quiet:
        print(f"[check_consistency] {task}  {Path(answer_path).name}")
        print(f"  声明来源: {src or '（无）'}   implies 比对字段: {checked}")
        for n in notes:
            print(f"  · {n}")

    if note_holder is not None:
        note_holder.extend(notes)

    if not declared and not problems:
        return "skip", [], notes
    return ("fail" if problems else "pass"), problems, notes


# ---------------------------------------------------------------- 批量扫描
def sweep(root, scope, quiet, warn_only):
    root = Path(root).expanduser()
    rows = []
    for rd in sorted(
        root.glob("round*"), key=lambda p: int(re.search(r"(\d+)$", p.stem).group(1) or 0)
    ):
        man = load(rd / "round-manifest.json") or {}
        task = norm_task(man.get("task_id") or man.get("task"))
        if not task:
            continue
        if scope == "final":
            cands = [rd / "answer-final.json"] if (rd / "answer-final.json").exists() else []
        else:
            cands = [rd / f"{s}.json" for s in SEAT_NAMES if (rd / f"{s}.json").exists()]
        for fp in cands:
            adj = rd / "adjudication-record.json" if scope == "final" else ""
            status, problems, _ = check_one(task, fp, str(adj) if adj else "", quiet=True)
            rows.append((rd.name, fp.name, task, status, problems))
            if not quiet:
                mark = {"pass": "✅", "fail": "🔴", "skip": "·", "error": "ERR"}[status]
                print(f"{mark} {rd.name}/{fp.name}  ({task})")
                for p in problems:
                    print(f"     - {p}")

    agg = {}
    for r in rows:
        agg[r[3]] = agg.get(r[3], 0) + 1
    print()
    print(
        f"[sweep] scope={scope}  共 {len(rows)} 份  "
        f"通过 {agg.get('pass', 0)} / 不自洽 {agg.get('fail', 0)} / "
        f"跳过 {agg.get('skip', 0)} / 读取失败 {agg.get('error', 0)}"
    )
    if agg.get("fail"):
        print("  不以失败码阻断（历史已签字产出不回溯改判）" if warn_only else "")
    return 0 if (warn_only or not agg.get("fail")) else 1


def self_test():
    """用现成样本正反两向验证检查本身。

    正样本（必须被抓出）：r18/T-13 聚合层——excluded_categories 声明了 520 所属类别，
                         但 amount 把 520 也算了进去。
    负样本（必须放过）：  r18/EX-02——声明 count 且算术确实按 count 落。
    区分样本（只该报 implies，不该报分桶）：r18/EX-01——分桶自洽、声明与算术相反。
    """
    runs = ROOT / "migration-package" / "runs" / "round18"
    cases = [
        (
            "r18/T-13 answer-final 应被抓出分桶矛盾",
            "T-13",
            runs / "answer-final.json",
            runs / "adjudication-record.json",
            "fail",
            "X_PARTITION",
        ),
        ("r18/EX-02 应完全通过", "T-13", runs / "EX-02.json", "", "pass", None),
        (
            "r18/EX-01 应只因 implies 失败、分桶无问题",
            "T-13",
            runs / "EX-01.json",
            "",
            "fail",
            "NO_PARTITION",
        ),
    ]
    ok = 0
    for name, task, ans, adj, want, mode in cases:
        status, problems, _ = check_one(task, ans, str(adj) if adj else "", quiet=True)
        joined = " | ".join(problems)
        if mode == "X_PARTITION":
            good = status == "fail" and "X_PARTITION" in joined
        elif mode == "NO_PARTITION":
            good = status == "fail" and "X_PARTITION" not in joined
        else:
            good = status == want
        print(f"  [{'PASS' if good else 'FAIL'}] {name}  → 实测 {status}")
        if not good:
            for p in problems:
                print(f"        {p}")
        ok += good
    print(f"[self-test] {ok}/{len(cases)} 项通过")
    return 0 if ok == len(cases) else 1


def main():
    ap = argparse.ArgumentParser(description="判断声明 × 算术产出的自洽校验")
    ap.add_argument("--task", help="任务名，如 T-13（--sweep 时可省）")
    ap.add_argument("--answer", help="answer/席位产出 JSON 路径")
    ap.add_argument("--adj", default="", help="adjudication-record.json 路径（可选）")
    ap.add_argument("--sweep", default="", help="批量扫描目录（如 migration-package/runs）")
    ap.add_argument(
        "--scope",
        choices=["final", "seat"],
        default="final",
        help="sweep 范围：final=answer-final；seat=席位产出",
    )
    ap.add_argument("--warn-only", action="store_true", help="只报告，不以失败码阻断")
    ap.add_argument("--self-test", action="store_true", help="用正负样本验证检查本身")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.sweep:
        return sweep(args.sweep, args.scope, args.quiet, args.warn_only)

    if not args.task or not args.answer:
        print("[ERR] 需要 --task 与 --answer，或用 --sweep", file=sys.stderr)
        return 2

    status, problems, _ = check_one(args.task, args.answer, args.adj, quiet=args.quiet)
    if status == "error":
        print(f"[ERR] {problems[0]}", file=sys.stderr)
        return 2
    if status == "skip":
        print(
            f"[SKIP] 无声明可查（{args.task}）——未提供 --adj，answer 也无 declared_adjudication/stance"
        )
        return 3
    if status == "fail":
        print(f"[FAIL] 判断与算术不自洽（{len(problems)} 处）：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 0 if args.warn_only else 1
    print("[PASS] 判断声明与算术产出自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
