#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_fixtures.py —— 生成合成核对任务的输入、真值与【分层判据】

任务：跨平台数据核对与差异归因
产出：
    ledger.csv            台账
    bill.csv              账单
    truth.json            真值 + 单层判据（legacy，保留向后兼容）
    spec/arithmetic.json  纯算术锚点（自动裁决，无解释空间）
    spec/adjudication.json 判断项（交人类闸门，**不含 expected**）

设计要点：
  * 全部为**合成数据**，不涉及任何真实业务数据
  * 真值由脚本计算得出，**不手填**（对应铁律：报告关键数字由脚本取数）
  * **金额一律用 Decimal**，不用 float —— 实测 float 会产生
    (2500.01-2500.00)+(3210.51-3210.50) = 0.020000000000436557 ≠ 0.02
  * 判据分层：可自动裁决的算术锚点与需人裁决的判断项**物理分离**，
    防止判断项被塞进算术层冒充客观事实

依赖：仅 Python 标准库。可在 Windows / macOS / Linux 运行。

用法：
    python3 make_fixtures.py --out ../fixtures
"""

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

# 固定种子式构造：不引入随机，确保可复现
MONTH = (2026, 9)
TAX_RATE = Decimal("0.06")

# 正常匹配行（双方完全一致）
NORMAL_COUNT = 20
NORMAL_BASE_AMOUNT = Decimal("1000.00")

CENT = Decimal("0.01")


# ---------------------------------------------------------------- 金额工具


def money(x):
    """统一两位小数。用 Decimal + ROUND_HALF_EVEN 对齐 Python round 语义。"""
    if isinstance(x, Decimal):
        d = x
    else:
        d = Decimal(str(x))
    return d.quantize(CENT, rounding=ROUND_HALF_EVEN)


def dec(x):
    """任意数值 → Decimal（经字符串，规避 float 二进制误差）。"""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def money_str(x):
    """输出固定两位小数的字符串（用于 CSV）。"""
    return f"{money(x):.2f}"


class DecEncoder(json.JSONEncoder):
    """json 不能直接序列化 Decimal。转 float 落盘 —— 与历史输出 repr 一致。

    已实测：float(Decimal("37490.50")) == 37490.5，json 写 "37490.5"，
    与改造前的 float 路径逐字节相同。
    """

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


# ---------------------------------------------------------------- 数据构造


def build():
    ledger = []  # 台账
    bill = []  # 账单（可能含重复、口径不同）

    def add(
        txn_id,
        d,
        amount,
        tax_incl,
        party,
        note,
        to_bill=True,
        bill_amount=None,
        bill_date=None,
        dup=False,
    ):
        ledger.append(
            {
                "txn_id": txn_id,
                "date": d.isoformat(),
                "amount": money_str(amount),
                "tax_included": tax_incl,
                "party": party,
                "note": note,
            }
        )
        if to_bill:
            b_amt = money(bill_amount if bill_amount is not None else amount)
            b_date = (bill_date or d).isoformat()
            bill.append(
                {
                    "txn_id": txn_id,
                    "date": b_date,
                    "amount": money_str(b_amt),
                    "tax_included": tax_incl,
                    "party": party,
                    "note": note,
                }
            )
            if dup:
                bill.append(
                    {
                        "txn_id": txn_id,
                        "date": b_date,
                        "amount": money_str(b_amt),
                        "tax_included": tax_incl,
                        "party": party,
                        "note": note + "(重复导入)",
                    }
                )

    # ---- 正常匹配行 ----
    d0 = date(MONTH[0], MONTH[1], 1)
    for i in range(1, NORMAL_COUNT + 1):
        d = d0 + timedelta(days=i - 1)
        amt = NORMAL_BASE_AMOUNT + Decimal(i) * Decimal("10")
        add(f"T{i:03d}", d, amt, "Y", f"渠道{(i % 4) + 1}", "正常结算")

    # ---- D1 舍入差：金额相差 0.01（两笔，均为 +0.01）----
    add(
        "T101",
        d0 + timedelta(days=21),
        Decimal("2500.00"),
        "Y",
        "渠道1",
        "舍入差样本①",
        bill_amount=Decimal("2500.01"),
    )
    add(
        "T102",
        d0 + timedelta(days=22),
        Decimal("3210.50"),
        "Y",
        "渠道2",
        "舍入差样本②",
        bill_amount=Decimal("3210.51"),
    )

    # ---- D2 重复计：账单里同一笔出现两次 ----
    add("T103", d0 + timedelta(days=23), Decimal("1200.00"), "Y", "渠道3", "重复导入样本", dup=True)

    # ---- D3 口径差异：台账含税、账单不含税，差额恰为税额 ----
    for idx, (txn, gross, party) in enumerate(
        [("T104", Decimal("2120.00"), "渠道4"), ("T105", Decimal("1060.00"), "渠道1")], start=1
    ):
        tax = money(gross * TAX_RATE / (Decimal(1) + TAX_RATE))
        add(
            txn,
            d0 + timedelta(days=23 + idx),
            gross,
            "Y",
            party,
            f"口径差异样本{idx}",
            bill_amount=money(gross - tax),
            bill_date=d0 + timedelta(days=23 + idx),
        )
        # 账单侧标记为不含税（口径不同）
        bill[-1]["tax_included"] = "N"

    # ---- D4 跨月归属：同一笔，台账 9/30、账单 10/1，金额一致 ----
    add(
        "T106",
        date(2026, 9, 30),
        Decimal("4800.00"),
        "Y",
        "渠道2",
        "跨月归属样本",
        bill_amount=Decimal("4800.00"),
        bill_date=date(2026, 10, 1),
    )

    # ---- D5 真实缺失：台账有、账单无 ----
    add(
        "T107",
        d0 + timedelta(days=27),
        Decimal("500.00"),
        "Y",
        "渠道3",
        "真实缺失样本",
        to_bill=False,
    )

    return ledger, bill


def build_truth(ledger, bill):
    """真值全部由脚本计算，禁止手填。金额全程 Decimal。"""
    L = {r["txn_id"]: r for r in ledger}
    bill_by_id = {}
    for r in bill:
        bill_by_id.setdefault(r["txn_id"], []).append(r)

    # 去重：账单中同一 txn_id 出现多次 → 只保留首条
    dedup_removed = {k: len(v) - 1 for k, v in bill_by_id.items() if len(v) > 1}
    dedup_amount = money(
        sum((dec(L[k]["amount"]) * n for k, n in dedup_removed.items()), Decimal(0))
    )

    # 逐笔归因
    categories = {"舍入差": [], "重复计": [], "口径差": [], "跨月归属": [], "真实缺失": []}
    rounding_net = Decimal(0)
    missing_net = Decimal(0)
    caliber_gap = Decimal(0)

    for tid, l in L.items():
        bl = bill_by_id.get(tid)
        if bl is None:
            categories["真实缺失"].append(tid)
            missing_net += dec(l["amount"])
            continue
        if len(bl) > 1:
            categories["重复计"].append(tid)
        b = bl[0]
        la, ba = dec(l["amount"]), dec(b["amount"])
        if la == ba and l["date"] != b["date"]:
            categories["跨月归属"].append(tid)
        elif la != ba:
            tax = money(la * TAX_RATE / (Decimal(1) + TAX_RATE))
            if abs((la - ba) - tax) <= Decimal("0.02") and l["tax_included"] != b["tax_included"]:
                categories["口径差"].append(tid)
                caliber_gap += money(la - ba)
            else:
                categories["舍入差"].append(tid)
                rounding_net += money(ba - la)

    diff_ids = sorted(t for v in categories.values() for t in v)
    amount_diff_total = money(rounding_net + missing_net)

    # 纯算术锚点：账单总额 − 台账总额（无任何解释空间）
    ledger_total = money(sum((dec(r["amount"]) for r in ledger), Decimal(0)))
    bill_total = money(sum((dec(r["amount"]) for r in bill), Decimal(0)))
    raw_total_gap = money(bill_total - ledger_total)

    # 被排除项的「差额绝对值」合计（口径差 180 + 跨月 0 = 180）
    excluded_gap = Decimal(0)
    for tid in categories["口径差"] + categories["跨月归属"]:
        excluded_gap += money(abs(dec(L[tid]["amount"]) - dec(bill_by_id[tid][0]["amount"])))
    excluded_gap = money(excluded_gap)

    return {
        "task_id": "SYNTH-RECON-001",
        "task_name": "跨平台数据核对与差异归因",
        "data_note": "全部为合成数据，不含任何真实业务信息",
        "rules": {
            "dedup": "账单中同一 txn_id 出现多次 → 去重，仅保留首条",
            "rounding": "金额差为小额（≤0.05）且非税额 → 归为「舍入差」，计入金额差异",
            "caliber": "含税/不含税口径不一致且差额恰等于税额 → 归为「口径差」，**标注但不计入金额差异**",
            "cross_month": "同一笔 txn_id 日期跨月、金额一致 → 归为「跨月归属」，按各自月份归属，**不剔除**",
            "missing": "台账有、账单无 → 归为「真实缺失」，计入金额差异",
        },
        "expected": {
            "ledger_rows": len(ledger),
            "bill_rows": len(bill),
            "dedup_removed_txn_count": len(dedup_removed),
            "dedup_amount": dedup_amount,
            "diff_count": len(diff_ids),
            "diff_ids": diff_ids,
            "amount_diff_total": amount_diff_total,
            "excluded_amount_total": excluded_gap,
            "ledger_total": ledger_total,
            "bill_total": bill_total,
            "raw_total_gap": raw_total_gap,
            "excluded_categories": ["口径差", "跨月归属"],
            "categories": {k: sorted(v) for k, v in categories.items()},
        },
        "checks": [
            {
                "name": "台账行数",
                "type": "numeric_equals",
                "path": "summary.ledger_rows",
                "expected": len(ledger),
            },
            {
                "name": "账单原始行数",
                "type": "numeric_equals",
                "path": "summary.bill_rows",
                "expected": len(bill),
            },
            {
                "name": "去重笔数",
                "type": "numeric_equals",
                "path": "summary.dedup_removed_txn_count",
                "expected": len(dedup_removed),
            },
            {
                "name": "去重金额",
                "type": "numeric_equals",
                "path": "summary.dedup_amount",
                "expected": dedup_amount,
                "tol": 0.01,
            },
            {
                "name": "差异总条数",
                "type": "numeric_equals",
                "path": "summary.diff_count",
                "expected": len(diff_ids),
            },
            {
                "name": "差异清单命中（txn 无序集合，与类别命名无关）",
                "type": "set_equals",
                "path": "diffs",
                "key": "id",
                "expected": diff_ids,
            },
            {
                "name": "金额差异合计（仅计应计入的类别）",
                "type": "numeric_equals",
                "path": "summary.amount_diff_total",
                "expected": amount_diff_total,
                "tol": 0.01,
            },
            {
                "name": "被排除项的差额合计",
                "type": "numeric_equals",
                "path": "summary.excluded_amount_total",
                "expected": excluded_gap,
                "tol": 0.01,
            },
            {
                "name": "台账总额（纯算术锚点）",
                "type": "numeric_equals",
                "path": "summary.ledger_total",
                "expected": ledger_total,
                "tol": 0.01,
            },
            {
                "name": "账单总额（纯算术锚点）",
                "type": "numeric_equals",
                "path": "summary.bill_total",
                "expected": bill_total,
                "tol": 0.01,
            },
            {
                "name": "总额差（纯算术锚点）",
                "type": "numeric_equals",
                "path": "summary.raw_total_gap",
                "expected": raw_total_gap,
                "tol": 0.01,
            },
            {
                "name": "必须声明哪些类别被排除在金额差异之外",
                "type": "present",
                "path": "summary.excluded_categories",
            },
            {"name": "必须给出类别归属", "type": "present", "path": "categories"},
            {
                "name": "必须给出证伪条件（不可裁决的结论一律不合格）",
                "type": "present",
                "path": "falsification",
            },
        ],
        # 分层判据的构造素材（不写进 legacy truth.json）
        "_layered": {
            "rounding_net": rounding_net,
            "missing_net": missing_net,
            "caliber_gap": caliber_gap,
            "dedup_amount": dedup_amount,
            "ledger_total": ledger_total,
            "bill_total": bill_total,
            "raw_total_gap": raw_total_gap,
            "amount_diff_total": amount_diff_total,
            "excluded_gap": excluded_gap,
            "diff_ids": diff_ids,
            "categories": {k: sorted(v) for k, v in categories.items()},
            "ledger_rows": len(ledger),
            "bill_rows": len(bill),
            "dedup_removed_txn_count": len(dedup_removed),
        },
    }


# ---------------------------------------------------------------- 分层判据


def emit_layered_specs(truth):
    """把单层判据拆成 arithmetic（自动裁决）与 adjudication（人类闸门）两层。

    分层的意义（对应结构缺陷 F1）：
      原 truth.json 把「口径差应排除」这一**归类判断**编码成了 expected 值，
      导致硬裁判拿着被争议的判断去裁决争议 —— 它只能复述，无法仲裁。
      分层后：算术层只留无解释空间的锚点；判断项不含 expected，交人类闸门。
    """
    L = truth["_layered"]

    # ---- 算术层：只有「仅凭输入数据 + 已声明规则即可唯一算出」的量 ----
    arithmetic = {
        "task_id": truth["task_id"],
        "layer": "arithmetic",
        "layer_note": (
            "纯算术锚点。本层判据的 expected 全部可由输入数据唯一算出，不含任何归类判断。"
            "verify.py 对 path 实行白名单：比对型判据的 path 必须落在 ARITHMETIC_ANCHOR_PATHS 内，"
            "否则拒绝加载。"
        ),
        "checks": [
            {
                "name": "台账行数",
                "type": "numeric_equals",
                "path": "summary.ledger_rows",
                "expected": L["ledger_rows"],
            },
            {
                "name": "账单原始行数",
                "type": "numeric_equals",
                "path": "summary.bill_rows",
                "expected": L["bill_rows"],
            },
            {
                "name": "去重笔数（去重规则由 TASK.md §B 声明，非价值判断）",
                "type": "numeric_equals",
                "path": "summary.dedup_removed_txn_count",
                "expected": L["dedup_removed_txn_count"],
            },
            {
                "name": "去重金额（多出行金额合计）",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.dedup_amount",
                "expected": L["dedup_amount"],
            },
            {
                "name": "台账总额",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.ledger_total",
                "expected": L["ledger_total"],
            },
            {
                "name": "账单总额",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.bill_total",
                "expected": L["bill_total"],
            },
            {
                "name": "总额差（账单−台账）",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.raw_total_gap",
                "expected": L["raw_total_gap"],
            },
            {
                "name": "必须声明哪些类别被排除在金额差异之外",
                "type": "present",
                "path": "summary.excluded_categories",
            },
            {"name": "必须给出类别归属", "type": "present", "path": "categories"},
            {"name": "必须给出证伪条件", "type": "present", "path": "falsification"},
        ],
    }

    # ---- 判断层：只给选项与「在该选项下应有的值」，不给 expected ----
    #
    # ★ F15 修复（本轮核心）：ADJ-002 与 ADJ-003 曾各自改写 diff_count/diffs，
    #   而 implies 是静态值 → 4 种组合里只有 (as_diff, as_diff) 不自相冲突；
    #   且「跨月与口径差都不算差异」应得 4 条，而 4 根本不在选项空间里。
    #   → 人的真实判断无法表达，闸门只能承载机器已有的那个答案。
    #   这与 L-0002（硬裁判携带被争议判断则无法仲裁）是同一病的第二种形态。
    #
    #   现合并为【一个裁决项、两个判断点、四个组合】，且清单由【定义式】生成，
    #   不再手写四组 id 列表 —— 手写正是漂移来源。
    all_ids = L["diff_ids"]

    DIFF_POINTS = [
        {
            "key": "cross_month",
            "ids": ["T106"],
            "text": "「跨月归属」T106（同一 txn_id、金额一致、日期跨月）",
        },
        {
            "key": "caliber",
            "ids": ["T104", "T105"],
            "text": "「口径差」T104/T105（台账含税、账单不含税、差额恰为税额）",
        },
    ]

    def _diffs(decides):
        """定义式：从全部差异中剔除「判为不列入」的判断点所含的单号。

        ★ 这是 F15 的修法本体：选项 id（4/5/6/7）不是手写的，而是这条式子的结果。
          只有这样才能保证「选项 id」与「清单条数」不可能不一致。
        """
        drop = set()
        for p in DIFF_POINTS:
            if not decides[p["key"]]:
                drop.update(p["ids"])
        return [t for t in all_ids if t not in drop]

    DIFFSET_OPTIONS = [
        (
            "7",
            {"cross_month": True, "caliber": True},
            "两项都列入：跨月 T106 + 口径差 T104/T105 都算差异",
        ),
        ("6", {"cross_month": False, "caliber": True}, "只列入口径差：跨月 T106 不算差异"),
        ("5", {"cross_month": True, "caliber": False}, "只列入跨月：口径差 T104/T105 不算差异"),
        ("4", {"cross_month": False, "caliber": False}, "两项都不列入：跨月与口径差都不算差异"),
    ]

    diffset_options = []
    for oid, decides, label in DIFFSET_OPTIONS:
        ids = _diffs(decides)
        # ★ 防漂移闸门：将来夹具数据变了（差异类型/单号改了），这里会直接拒绝出包，
        #   而不是静默产出一个「id 说 4、清单却是 5 条」的选项。
        if len(ids) != int(oid):
            raise SystemExit(
                f"[ERR] DIFFSET 选项 '{oid}' 由定义式算出 {len(ids)} 条 —— 选项 id 必须等于条数。"
                f"若夹具数据已变，请同步修正 DIFF_POINTS，不得产出说谎的 id。"
            )
        diffset_options.append(
            {
                "id": oid,
                "label": label,
                "decides": decides,
                "implies": {"summary.diff_count": len(ids), "diffs": ids},
            }
        )

    adjudication = {
        "task_id": truth["task_id"],
        "layer": "adjudication",
        "layer_note": (
            "判断项。**不含 expected** —— 机器侧不存在可被误当『真值』的量。"
            "本层判据不参与自动 pass/fail；未裁决时 verdict=pending_adjudication（退出码 3）。"
            "裁决必须由人类填写 adjudication-record.json 后经 --record 传入。"
        ),
        "items": [
            {
                "id": "ADJ-001",
                "question": "「口径差」（台账含税、账单不含税，且差额恰等于税额）是否计入金额差异？",
                "context_evidence": {
                    "T104": "台账 2120.00(含税) vs 账单 2000.00(不含税)，差 120.00",
                    "T105": "台账 1060.00(含税) vs 账单 1000.00(不含税)，差 60.00",
                    "ratio": "2120/2000 = 1060/1000 = 1.06（n=2，均为 6% 税率）",
                },
                "paths_affected": ["summary.amount_diff_total", "summary.excluded_amount_total"],
                "affected_ids": ["T104", "T105"],
                "why_it_matters": (
                    "这是两条真实存在的金额落差（合计 180.00）。计入，则对账报告会报出 180.00 的"
                    "「应收缺口」；不计入，则这 180.00 只作为「已解释的口径差异」留在附注里。"
                ),
                "risk_asymmetry": (
                    "多报差异的代价是一次人工复核；少报差异的代价是它永久从账上消失、再无人回看。"
                ),
                "options": [
                    {
                        "id": "count",
                        "label": "计入金额差异（视为应收缺口 180.00）",
                        "implies": {
                            "summary.amount_diff_total": money(
                                L["amount_diff_total"] + L["caliber_gap"]
                            ),
                            "summary.excluded_amount_total": money(Decimal("0.00")),
                        },
                    },
                    {
                        "id": "exclude",
                        "label": "不计入（视为同一经济价值的两种口径表述）",
                        "implies": {
                            "summary.amount_diff_total": L["amount_diff_total"],
                            "summary.excluded_amount_total": L["excluded_gap"],
                        },
                    },
                ],
                "status": "unresolved",
            },
            {
                "id": "ADJ-002",
                "question": (
                    "差异清单（`summary.diff_count` 与 `diffs`）应包含哪些条目？请就两件事分别表态、"
                    "由一个选项同时决定：(a)「跨月归属」T106（同一 txn_id、金额一致、日期跨月）是否列入；"
                    "(b)「口径差」T104/T105（台账含税、账单不含税、差额恰为税额）是否列入。"
                ),
                "context_evidence": {
                    "T106": "台账 2026-09-30 vs 账单 2026-10-01，金额均 4800.00（跨月，金额一致）",
                    "T104": "台账 2120.00(含税) vs 账单 2000.00(不含税)，差 120.00",
                    "T105": "台账 1060.00(含税) vs 账单 1000.00(不含税)，差 60.00",
                    "invariant": "T101/T102（舍入差）、T103（重复计）、T107（真实缺失）在任何选项下都列入",
                },
                "paths_affected": ["summary.diff_count", "diffs"],
                "affected_ids": ["T104", "T105", "T106"],
                "why_it_matters": (
                    "清单长度决定了「需要跟进的条数」。把已能完全解释的项也列进去，会稀释真正需要"
                    "追查的项；把它们剔除，则一旦解释不成立，它们再也不会被复查。"
                ),
                "risk_asymmetry": (
                    "列进去的代价是每条多一次复核；剔除掉的代价是它从清单里消失。"
                    "★ 注意：本项的四个选项是【并列】的，没有一个「更正确」的默认值。"
                ),
                "options": diffset_options,
                "status": "unresolved",
            },
        ],
        "adjudication_required": True,
        "record_format": {
            "source": "human_adjudication",
            "recorded_by": "<人类署名，不得为空；机器不得代填>",
            "recorded_at": "<ISO8601>",
            "decisions": [
                {"id": "ADJ-001", "chosen": "<count 或 exclude>", "rationale": "<理由>"},
                {"id": "ADJ-002", "chosen": "<7 / 6 / 5 / 4>", "rationale": "<理由>"},
            ],
            "must_cover": (
                "decisions 必须恰好覆盖 spec.items 的全部 id（当前为 ADJ-001、ADJ-002）。"
                "缺项或出现 spec 之外的 id（如已退役的 ADJ-003）都会被拒载，退出码 2。"
            ),
        },
    }

    return arithmetic, adjudication


# ---------------------------------------------------------------- 输出


def write_csv(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="生成合成核对任务的输入、真值与分层判据")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument(
        "--no-layered", action="store_true", help="只产出 legacy truth.json，不产出分层 spec"
    )
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    ledger, bill = build()
    truth = build_truth(ledger, bill)

    fields = ["txn_id", "date", "amount", "tax_included", "party", "note"]
    write_csv(out / "ledger.csv", ledger, fields)
    write_csv(out / "bill.csv", bill, fields)

    # legacy truth.json —— 剥离 _layered 私有键，保持与历史输出逐字节一致
    truth_out = {k: v for k, v in truth.items() if not k.startswith("_")}
    with open(out / "truth.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(truth_out, f, ensure_ascii=False, indent=2, cls=DecEncoder)
        f.write("\n")

    # 分层 spec
    if not args.no_layered:
        spec_dir = out / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        arithmetic, adjudication = emit_layered_specs(truth)
        for name, payload in (("arithmetic.json", arithmetic), ("adjudication.json", adjudication)):
            with open(spec_dir / name, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, cls=DecEncoder)
                f.write("\n")

    e = truth["expected"]
    print(f"[OK] 已生成到 {out.resolve()}")
    print(f"     ledger.csv  {len(ledger)} 行")
    print(f"     bill.csv    {len(bill)} 行")
    print(f"     truth.json  {len(truth['checks'])} 条判据（legacy 单层）")
    if not args.no_layered:
        print(f"     spec/arithmetic.json   {len(arithmetic['checks'])} 条（自动裁决）")
        print(f"     spec/adjudication.json {len(adjudication['items'])} 条判断轴（人类闸门）")
    print("-" * 60)
    print("  真值（脚本计算，非手填，全程 Decimal）：")
    print(f"    差异总条数        = {e['diff_count']}")
    print(f"    金额差异合计      = {e['amount_diff_total']}（仅计应计入的类别）")
    print(
        f"    未计入金额差异     = {e['excluded_amount_total']}（类别：{e['excluded_categories']}）"
    )
    print(f"    去重笔数/金额     = {e['dedup_removed_txn_count']} 笔 / {e['dedup_amount']}")
    print("    各类别：")
    for k, v in e["categories"].items():
        print(f"      {k:6s} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
