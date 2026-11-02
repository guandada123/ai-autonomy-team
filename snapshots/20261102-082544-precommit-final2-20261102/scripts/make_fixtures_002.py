#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_fixtures_002.py —— 生成【第二个】合成核对任务的输入、真值与分层判据

任务：TASK-BROKER-RECON-002 · 券商交割单 ↔ 自记账（A股）

为什么要有第二个任务（对应指南「扩样」项）：
    001 是「含税/不含税」的通用对账，签署人对该领域没有真实判断权责 ——
    他会问「哪个是真的」。判断项之所以交给人，前提是【人能承担这个归类决定】；
    没有判断权的签名会退化成形式（见 L-0002 / D-0008）。
    002 换到签署人真正有判断权的域：A股交易成本。
    「佣金+印花税+过户费算不算成本差异」——判错了有真实代价（收益归因会偏乐观）。

设计要点（与 001 同构，保证机制侧零改动）：
  * 全部合成数据，不含任何真实账户信息
  * 真值由脚本从两份 CSV 用 Decimal 算出，**不手填**
  * 判据分层：arithmetic 层可自动裁决；adjudication 层不含 expected
  * 四选项 7/6/5/4 的结构与 001 一致：恒在项 4 + 权益类 2 + 跨日类 1

依赖：仅 Python 标准库。

用法：
    python3 make_fixtures_002.py --out ../migration-package/fixtures/002
"""

import argparse
import csv
import json
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

TASK_ID = "TASK-BROKER-RECON-002"
TASK_NAME = "券商交割单与自记账核对（A股）"

# ---------------------------------------------------------------- 输入数据
# 自记账（我方记录）：txn_id, date, code, name, side, shares, price, amount, fee_total, note
LEDGER = [
    [
        "T201",
        "2026-09-01",
        "600519",
        "贵州茅台",
        "买入",
        "100",
        "1680.00",
        "168000.00",
        "43.68",
        "正常记账",
    ],
    [
        "T202",
        "2026-09-03",
        "601318",
        "中国平安",
        "买入",
        "500",
        "52.00",
        "26000.00",
        "6.50",
        "只记佣金，过户费未记",
    ],
    [
        "T204",
        "2026-09-08",
        "600036",
        "招商银行",
        "卖出",
        "2000",
        "38.00",
        "76000.00",
        "0.00",
        "费用未入账",
    ],
    [
        "T205",
        "2026-09-30",
        "000858",
        "五粮液",
        "买入",
        "100",
        "138.00",
        "13800.00",
        "5.00",
        "按交割日入账",
    ],
    [
        "T206",
        "2026-09-12",
        "600519",
        "贵州茅台",
        "送转",
        "0",
        "0.00",
        "0.00",
        "0.00",
        "10送3，未更新持仓股数",
    ],
    [
        "T208",
        "2026-09-15",
        "000333",
        "美的集团",
        "买入",
        "2000",
        "15.00",
        "30000.00",
        "7.50",
        "分批成交合并记一行",
    ],
    [
        "T209",
        "2026-09-18",
        "000001",
        "平安银行",
        "买入",
        "1000",
        "45.00",
        "45000.00",
        "11.25",
        "按委托价记账",
    ],
]

# 券商交割单：txn_id, trade_date, settle_date, code, side, shares, price, amount,
#             commission, stamp_tax, transfer_fee, fee_total, note
BILL = [
    [
        "T201",
        "2026-09-01",
        "2026-09-02",
        "600519",
        "买入",
        "100",
        "1680.00",
        "168000.00",
        "42.00",
        "0.00",
        "1.68",
        "43.68",
        "正常",
    ],
    [
        "T202",
        "2026-09-03",
        "2026-09-04",
        "601318",
        "买入",
        "500",
        "52.00",
        "26000.00",
        "6.50",
        "0.00",
        "0.26",
        "6.76",
        "含过户费",
    ],
    [
        "T204",
        "2026-09-08",
        "2026-09-09",
        "600036",
        "卖出",
        "2000",
        "38.00",
        "76000.00",
        "19.00",
        "38.00",
        "0.76",
        "57.76",
        "含印花税",
    ],
    [
        "T205",
        "2026-09-29",
        "2026-09-30",
        "000858",
        "买入",
        "100",
        "138.00",
        "13800.00",
        "5.00",
        "0.00",
        "0.03",
        "5.03",
        "成交日 09-29",
    ],
    [
        "T206",
        "2026-09-12",
        "2026-09-12",
        "600519",
        "送转",
        "30",
        "0.00",
        "0.00",
        "0.00",
        "0.00",
        "0.00",
        "0.00",
        "权益变动，股数 +30",
    ],
    [
        "T207",
        "2026-09-22",
        "2026-09-23",
        "001979",
        "中签买入",
        "500",
        "8.20",
        "4100.00",
        "5.00",
        "0.00",
        "0.04",
        "5.04",
        "新股中签，自记账无",
    ],
    [
        "T208",
        "2026-09-15",
        "2026-09-16",
        "000333",
        "买入",
        "1000",
        "15.00",
        "15000.00",
        "3.75",
        "0.00",
        "0.02",
        "3.77",
        "分批成交 1/2",
    ],
    [
        "T208",
        "2026-09-15",
        "2026-09-16",
        "000333",
        "买入",
        "1000",
        "15.00",
        "15000.00",
        "3.75",
        "0.00",
        "0.02",
        "3.77",
        "分批成交 2/2",
    ],
    [
        "T209",
        "2026-09-18",
        "2026-09-19",
        "000001",
        "买入",
        "1000",
        "45.12",
        "45120.00",
        "11.28",
        "0.00",
        "0.05",
        "11.33",
        "实际成交价",
    ],
    [
        "T210",
        "2026-09-25",
        "2026-09-25",
        "600519",
        "股息入账",
        "0",
        "0.00",
        "260.00",
        "0.00",
        "0.00",
        "0.00",
        "0.00",
        "现金分红，自记账无",
    ],
]

LEDGER_HEADER = [
    "txn_id",
    "date",
    "code",
    "name",
    "side",
    "shares",
    "price",
    "amount",
    "fee_total",
    "note",
]
BILL_HEADER = [
    "txn_id",
    "trade_date",
    "settle_date",
    "code",
    "side",
    "shares",
    "price",
    "amount",
    "commission",
    "stamp_tax",
    "transfer_fee",
    "fee_total",
    "note",
]

# 差异分类（用于 truth 的 rules 说明；不参与判据）
INVARIANT_IDS = ["T202", "T204", "T207", "T209"]  # 恒在项：四类选项都出现
EQUITY_IDS = ["T206", "T210"]  # 权益/分红类（判断点 a）
SETTLE_IDS = ["T205"]  # 交割跨日类（判断点 b）


def d(s):
    return Decimal(str(s)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def compute_truth():
    """真值全部由两份 CSV 算出，不手填。"""
    # 行数
    ledger_rows, bill_rows = len(LEDGER), len(BILL)
    ledger_total = sum(d(r[7]) for r in LEDGER)
    bill_total = sum(d(r[7]) for r in BILL)

    # 按单号聚合
    def agg(rows, idx_amount, idx_fee, idx_shares, idx_date):
        out = {}
        for r in rows:
            t = r[0]
            e = out.setdefault(
                t,
                {
                    "n": 0,
                    "amount": Decimal("0"),
                    "fee": Decimal("0"),
                    "shares": 0,
                    "date": r[idx_date],
                },
            )
            e["n"] += 1
            e["amount"] += d(r[idx_amount])
            e["fee"] += d(r[idx_fee])
            e["shares"] += int(r[idx_shares])
        return out

    L = agg(LEDGER, 7, 8, 5, 1)
    B = agg(BILL, 7, 11, 5, 1)

    # 重复项：单号在任一侧出现 >1 次
    dup_ids = sorted(
        {t for t, e in B.items() if e["n"] > 1} | {t for t, e in L.items() if e["n"] > 1}
    )
    dedup_count = len(dup_ids)
    dedup_amount = Decimal("0")
    for t in dup_ids:
        # 多出的那些行的金额之和（按单号计一次，不是按行计）
        for side in (B, L):
            n = side.get(t, {"n": 0})["n"]
            if n > 1:
                per = side[t]["amount"] / n
                dedup_amount += side[t]["amount"] - per
                break

    # 差异单号：股数 / 成交金额 / 费用 / 日期 四列不完全相同
    all_ids = sorted(set(L) | set(B))
    diff_ids, amount_gap, fee_gap = [], Decimal("0"), Decimal("0")
    for t in all_ids:
        if t in dup_ids:
            continue
        l, b = L.get(t), B.get(t)
        if l is None or b is None:  # 单侧缺失
            src = b if l is None else l
            diff_ids.append(t)
            amount_gap += src["amount"]
            fee_gap += src["fee"]
            continue
        same = (
            l["shares"] == b["shares"]
            and l["amount"] == b["amount"]
            and l["fee"] == b["fee"]
            and l["date"] == b["date"]
        )
        if not same:
            diff_ids.append(t)
            amount_gap += abs(l["amount"] - b["amount"])
            fee_gap += abs(l["fee"] - b["fee"])

    diff_ids.sort()
    return {
        "ledger_rows": ledger_rows,
        "bill_rows": bill_rows,
        "ledger_total": float(ledger_total),
        "bill_total": float(bill_total),
        "raw_total_gap": float(bill_total - ledger_total),
        "dedup_removed_txn_count": dedup_count,
        "dedup_amount": float(dedup_amount),
        "diff_count": len(diff_ids),
        "diff_ids": diff_ids,
        "amount_diff_total": float(amount_gap),  # 默认口径 exclude：费用不计入
        "excluded_amount_total": float(fee_gap),
        "amount_diff_total_if_count": float(amount_gap + fee_gap),
        # 判断项组合下的清单长度（由 spec 结构机械推导）
        "diff_count_by_option": {
            "7": len(INVARIANT_IDS) + len(EQUITY_IDS) + len(SETTLE_IDS),
            "6": len(INVARIANT_IDS) + len(EQUITY_IDS),
            "5": len(INVARIANT_IDS) + len(SETTLE_IDS),
            "4": len(INVARIANT_IDS),
        },
    }


def build_arithmetic(t):
    return {
        "task_id": TASK_ID,
        "layer": "arithmetic",
        "layer_note": (
            "纯算术锚点。expected 全部可由输入数据唯一算出，不含任何归类判断。"
            "verify.py 对 path 实行白名单：比对型判据的 path 必须落在 ARITHMETIC_ANCHOR_PATHS 内，否则拒绝加载。"
        ),
        "checks": [
            {
                "name": "自记账行数",
                "type": "numeric_equals",
                "path": "summary.ledger_rows",
                "expected": t["ledger_rows"],
            },
            {
                "name": "交割单原始行数",
                "type": "numeric_equals",
                "path": "summary.bill_rows",
                "expected": t["bill_rows"],
            },
            {
                "name": "去重笔数（按单号计，规则由 TASK.md §B 声明，非价值判断）",
                "type": "numeric_equals",
                "path": "summary.dedup_removed_txn_count",
                "expected": t["dedup_removed_txn_count"],
            },
            {
                "name": "去重金额（多出行金额合计）",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.dedup_amount",
                "expected": t["dedup_amount"],
            },
            {
                "name": "自记账成交金额合计",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.ledger_total",
                "expected": t["ledger_total"],
            },
            {
                "name": "交割单成交金额合计",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.bill_total",
                "expected": t["bill_total"],
            },
            {
                "name": "总额差（交割单−自记账）",
                "type": "decimal_equals",
                "scale": 2,
                "path": "summary.raw_total_gap",
                "expected": t["raw_total_gap"],
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


def build_adjudication(t):
    inv = "、".join(INVARIANT_IDS)
    eq = "、".join(EQUITY_IDS)
    se = "、".join(SETTLE_IDS)
    return {
        "task_id": TASK_ID,
        "layer": "adjudication",
        "layer_note": (
            "判断项。**不含 expected** —— 机器侧不存在可被误当『真值』的量。"
            "本层判据不参与自动 pass/fail；未裁决时 verdict=pending_adjudication（退出码 3）。"
            "裁决必须由人类填写 adjudication-record.json 后经 --record 传入。"
        ),
        "items": [
            {
                "id": "ADJ-001",
                "question": "交易费用（佣金 + 印花税 + 过户费）是否计入「成本差异」金额？",
                "context_evidence": {
                    "T202": "自记账 6.50 vs 交割单 6.76（差 0.26：过户费漏记）",
                    "T204": "自记账 0.00 vs 交割单 57.76（差 57.76：佣金 19.00 + 印花税 38.00 + 过户费 0.76 全部未入账）",
                    "T205": "自记账 5.00 vs 交割单 5.03（差 0.03）",
                    "T207": "交割单 5.04（新股中签，自记账无此笔）",
                    "T209": "自记账 11.25 vs 交割单 11.33（差 0.08）",
                    "fee_gap_total": f"{t['excluded_amount_total']:.2f}",
                },
                "paths_affected": ["summary.amount_diff_total", "summary.excluded_amount_total"],
                "affected_ids": ["T202", "T204", "T205", "T207", "T209"],
                "why_it_matters": (
                    "这是一个真实的成本归属问题，不是算术问题：同一笔买卖，券商按含费净额结算，"
                    "自记账可能只记成交价。计入 → 每笔差异按真实成本放大；不计入 → "
                    "交易成本在对账里消失，收益归因系统性偏乐观。"
                ),
                "risk_asymmetry": (
                    "计入的代价是对账差额变大、需要逐笔核对费用明细；"
                    "不计入的代价是真实摩擦成本永久从收益计算里消失 —— 而这正是长期最容易被低估的一项。"
                ),
                "options": [
                    {
                        "id": "count",
                        "label": "计入成本差异（费用算作真实成本）",
                        "implies": {
                            "summary.amount_diff_total": t["amount_diff_total_if_count"],
                            "summary.excluded_amount_total": 0.0,
                        },
                    },
                    {
                        "id": "exclude",
                        "label": "不计入（费用视为已解释的结算口径差异）",
                        "implies": {
                            "summary.amount_diff_total": t["amount_diff_total"],
                            "summary.excluded_amount_total": t["excluded_amount_total"],
                        },
                    },
                ],
                "status": "unresolved",
            },
            {
                "id": "ADJ-002",
                "question": (
                    "差异清单（`summary.diff_count` 与 `diffs`）应包含哪些条目？请就两件事分别表态、"
                    "由一个选项同时决定：(a)「权益变动」"
                    + eq
                    + "（送转股 / 现金分红，金额 0 或小额）是否列入；"
                    "(b)「交割跨日」"
                    + se
                    + "（同一笔，自记账按交割日、券商按成交日，金额一致）是否列入。"
                ),
                "context_evidence": {
                    "T206": "送转股：自记账股数 0 vs 交割单 30（金额均为 0）",
                    "T210": "现金分红 260.00：仅交割单有，自记账无",
                    "T205": "自记账 2026-09-30 vs 成交日 2026-09-29，股数金额一致",
                    "invariant": f"{inv} 出现在全部 4 个选项中",
                },
                "paths_affected": ["summary.diff_count", "diffs"],
                "affected_ids": EQUITY_IDS + SETTLE_IDS,
                "why_it_matters": (
                    "清单长度决定「需要跟进的条数」。权益变动（送转/分红）与交割跨日都属于"
                    "『能解释但需确认』的项：列进去稀释真正要追的项，剔除掉则一旦解释不成立就再无人复查。"
                ),
                "risk_asymmetry": (
                    "列进去的代价是每条多一次复核；剔除掉的代价是它从清单里消失。"
                    "★ 四个选项是【并列】的，没有「更正确」的默认值。"
                ),
                "options": [
                    {
                        "id": "7",
                        "label": f"两类都列入：权益变动 {eq} + 交割跨日 {se} 都算差异",
                        "implies": {"summary.diff_count": t["diff_count_by_option"]["7"]},
                    },
                    {
                        "id": "6",
                        "label": f"只列入权益变动：交割跨日 {se} 不算差异",
                        "implies": {"summary.diff_count": t["diff_count_by_option"]["6"]},
                    },
                    {
                        "id": "5",
                        "label": f"只列入交割跨日：权益变动 {eq} 不算差异",
                        "implies": {"summary.diff_count": t["diff_count_by_option"]["5"]},
                    },
                    {
                        "id": "4",
                        "label": "两类都不列入：权益变动与交割跨日都不算差异",
                        "implies": {"summary.diff_count": t["diff_count_by_option"]["4"]},
                    },
                ],
                "status": "unresolved",
            },
        ],
        "adjudication_required": True,
        "record_format": {
            "_note": "由人填写，工具链不得自动生成。生成模板可以，填值不行。",
            "required_fields": ["task_id", "source", "recorded_by", "recorded_at", "decisions"],
            "decision_fields": ["id", "chosen", "rationale"],
            "constraint": "decisions 必须恰好覆盖本文件 items 的全部 id；含 spec 之外的 id 或已退役 id 会被拒载（退出码 2）。",
        },
    }


def main():
    ap = argparse.ArgumentParser(description="生成 002 任务夹具（券商交割单 ↔ 自记账）")
    ap.add_argument("--out", default="../migration-package/fixtures/002", help="输出目录")
    ap.add_argument("--json", action="store_true", help="只打印计算出的真值")
    args = ap.parse_args()

    t = compute_truth()
    if args.json:
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0

    out = Path(args.out)
    (out / "spec").mkdir(parents=True, exist_ok=True)
    write_csv(out / "ledger.csv", LEDGER_HEADER, LEDGER)
    write_csv(out / "bill.csv", BILL_HEADER, BILL)

    truth = {
        "task_id": TASK_ID,
        "task_name": TASK_NAME,
        "data_note": "全部为合成数据，不含任何真实账户或持仓信息",
        "rules": {
            "dedup": "交割单中同一 txn_id 出现多次（分批成交）→ 去重，仅保留首条；多出行的金额计入 dedup_amount",
            "fee": "佣金 万分之2.5（最低 5 元）/ 印花税 卖出千分之0.5 / 过户费 万分之0.1，双向",
            "net_vs_gross": "自记账按成交金额记账、费用单列；券商交割单同样单列 —— 本夹具【不】把净额差异伪装成成交金额差异",
            "settlement_day": "自记账按交割日入账、券商按成交日列示 → 产生日期差异（T+1）",
            "equity": "送转股（金额 0、股数变化）与现金分红（仅券商侧）→ 归为权益变动",
            "missing": "仅券商侧存在（新股中签、现金分红）→ 归为真实缺失，计入金额差异",
        },
        "expected": {
            "ledger_rows": t["ledger_rows"],
            "bill_rows": t["bill_rows"],
            "dedup_removed_txn_count": t["dedup_removed_txn_count"],
            "dedup_amount": t["dedup_amount"],
            "ledger_total": t["ledger_total"],
            "bill_total": t["bill_total"],
            "raw_total_gap": t["raw_total_gap"],
            "diff_count": t["diff_count"],
            "diff_ids": t["diff_ids"],
            "amount_diff_total": t["amount_diff_total"],
            "excluded_amount_total": t["excluded_amount_total"],
        },
        "checks": [],
        "note": (
            "★ 本文件的 expected 沿用【默认口径】：ADJ-001=exclude（费用不计入成本差异）、ADJ-002=7。"
            "它只是一条历史口径的锚，不是真值 —— 签署人可另行裁决，判 fail 是正确结果。"
        ),
    }
    (out / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (out / "spec" / "arithmetic.json").write_text(
        json.dumps(build_arithmetic(t), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out / "spec" / "adjudication.json").write_text(
        json.dumps(build_adjudication(t), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"[OK] 002 夹具 -> {out}")
    print(
        f"     ledger_rows={t['ledger_rows']} bill_rows={t['bill_rows']} "
        f"ledger_total={t['ledger_total']} bill_total={t['bill_total']} gap={t['raw_total_gap']}"
    )
    print(
        f"     dedup={t['dedup_removed_txn_count']}/{t['dedup_amount']} "
        f"diff_count={t['diff_count']} diff_ids={t['diff_ids']}"
    )
    print(
        f"     金额差(不含费)={t['amount_diff_total']} 费用差={t['excluded_amount_total']} "
        f"合计(若计入)={t['amount_diff_total_if_count']}"
    )
    print(f"     四选项清单长度: {t['diff_count_by_option']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
