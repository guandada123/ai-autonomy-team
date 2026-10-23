#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_tasks_20.py —— 批量生成 20 条留出任务（扩样，设计见 docs/扩样设计草案-20条留出任务.md）

任务域：券商交割单 ↔ 自记账（A股），以 TASK-BROKER-RECON-002 为模板参数化。

每个任务 = 1 组夹具，落在 fixtures/tasks/T-XX/：
    TASK.md / ledger.csv / bill.csv / truth.json
    spec/{arithmetic.json, adjudication.json, fact-schema.json, interpretations.json}

★ 五条硬规则（docs/扩样设计草案 §二，全部来自实测教训）：
    1. 判断轴人类必须有判断权，判错有真实代价
    2. ADJ-002 四选项的组合计数互不相同（D-0006）
    3. truth 的 expected 标注「历史默认口径，非真值」
    4. fact-schema.json 随任务生成（多列比对，D-0012）
    5. 算术/判断分层物理分离

确定性：随机数由 --seed 驱动，同 seed 逐字节可重现。

用法：
    python3 make_tasks_20.py --out ../migration-package/fixtures/tasks            # 生成 20 组
    python3 make_tasks_20.py --out ../migration-package/fixtures/tasks --self-check
        # 自检：对全部 20 组合成默认口径答案 → verify → 必须算术全过 + 判断层 pending
    python3 make_tasks_20.py --patch-read-domains
        # 把 20 组的 truth/spec 写入 read_domains.json 的 deny_absolute（幂等）

依赖：仅 Python 标准库。
"""

import argparse
import csv
import json
import random
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

SEED = 20261020
N_TASKS = 20
N_HOLDOUT = 4
PILOT_N = 3

# ---------------------------------------------------------------- 费率与工具
COMM_RATE = Decimal("0.00025")   # 佣金 万 2.5
COMM_MIN = Decimal("5.00")
STAMP_RATE = Decimal("0.0005")   # 印花税 卖出 0.05%
TRANSFER_RATE = Decimal("0.00001")


def d(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def fees(amount, side):
    """A股费率口径：佣金 万2.5（最低 5 元）/ 印花税 卖出 0.05% / 过户费 万 0.1 双向。"""
    amount = d(amount)
    comm = max(amount * COMM_RATE, COMM_MIN)
    stamp = amount * STAMP_RATE if side == "卖出" else Decimal("0")
    transfer = amount * TRANSFER_RATE
    return d(comm), d(stamp), d(transfer)


# ---------------------------------------------------------------- 差异类型实现
# 每类返回 (ledger 行 or None, bill 行s)。行格式：
#   ledger: [txn, date, code, name, side, shares, price, amount, fee_total, note]
#   bill:   [txn, trade_date, settle_date, code, side, shares, price, amount,
#            commission, stamp_tax, transfer_fee, fee_total, note]
SEQ = {"n": 0}


def next_id():
    SEQ["n"] += 1
    return f"T{SEQ['n']:03d}"


def normal_rows(rng, day, code, name, side, shares, price):
    """两侧完全一致的一笔。"""
    t = next_id()
    amount = d(shares * price)
    comm, stamp, tf = fees(amount, side)
    ftotal = d(comm + stamp + tf)
    date = f"2026-09-{day:02d}"
    note = "正常"
    return (
        [t, date, code, name, side, str(shares), f"{price:.2f}", f"{amount}", f"{ftotal}", note],
        [[t, date, f"2026-09-{min(day + 1, 30):02d}", code, side, str(shares), f"{price:.2f}",
          f"{amount}", f"{comm}", f"{stamp}", f"{tf}", f"{ftotal}", note]],
    )


def fee_omission_rows(rng, day, code, name, side, shares, price):
    """费用漏记 ×2：自记账只记佣金（漏过户费/印花税），成交金额两侧一致。"""
    out = []
    for k in range(2):
        t = next_id()
        amount = d(shares * price)
        comm, stamp, tf = fees(amount, side)
        ftotal = d(comm + stamp + tf)
        date = f"2026-09-{day:02d}"
        out.append((
            [t, date, code, name, side, str(shares), f"{price:.2f}", f"{amount}", f"{comm}",
             f"只记佣金（{'漏印花税' if side == '卖出' else '漏过户费'}）"],
            [[t, date, f"2026-09-{min(day + 1, 30):02d}", code, side, str(shares), f"{price:.2f}",
              f"{amount}", f"{comm}", f"{stamp}", f"{tf}", f"{ftotal}", "含全部费用"]],
        ))
        day += 2
    return out


def slippage_rows(rng, day, code, name, side, shares, price):
    """滑点 ×1：自记账按委托价，交割单按实际成交价。"""
    t = next_id()
    slip = Decimal("0.12") if side == "买入" else Decimal("-0.12")
    deal = d(f"{price:.2f}") + slip
    amount_l = d(shares * price)
    amount_b = d(shares * deal)
    comm_l, stamp_l, tf_l = fees(amount_l, side)
    comm_b, stamp_b, tf_b = fees(amount_b, side)
    date = f"2026-09-{day:02d}"
    return (
        [t, date, code, name, side, str(shares), f"{price:.2f}", f"{amount_l}", f"{d(comm_l + stamp_l + tf_l)}",
         "按委托价记账"],
        [[t, date, f"2026-09-{min(day + 1, 30):02d}", code, side, str(shares), f"{deal:.2f}",
          f"{amount_b}", f"{comm_b}", f"{stamp_b}", f"{tf_b}", f"{d(comm_b + stamp_b + tf_b)}",
          "实际成交价"]],
    )


def equity_rows(rng, day, code, name):
    """权益变动 ×2：送转股（金额 0、股数差）+ 现金分红（仅交割单侧）。"""
    t1, t2 = next_id(), next_id()
    day2 = min(day + 3, 28)
    return [
        ([t1, f"2026-09-{day:02d}", code, name, "送转", "0", "0.00", "0.00", "0.00",
          "10送3，未更新持仓股数"],
         [[t1, f"2026-09-{day:02d}", f"2026-09-{day:02d}", code, "送转", "30", "0.00", "0.00",
           "0.00", "0.00", "0.00", "0.00", "权益变动，股数 +30"]]),
        ([],
         [[t2, f"2026-09-{day2:02d}", f"2026-09-{day2:02d}", code, "股息入账", "0", "0.00", "260.00",
           "0.00", "0.00", "0.00", "0.00", "现金分红，自记账无"]]),
    ]


def settle_day_rows(rng, day, code, name, side, shares, price):
    """交割跨日 ×1：自记账按交割日入账，交割单按成交日列示，金额一致。"""
    t = next_id()
    amount = d(shares * price)
    comm, stamp, tf = fees(amount, side)
    ftotal = d(comm + stamp + tf)
    trade = f"2026-09-{day:02d}"
    settle = f"2026-09-{min(day + 1, 30):02d}"
    return (
        [t, settle, code, name, side, str(shares), f"{price:.2f}", f"{amount}", f"{ftotal}",
         "按交割日入账"],
        [[t, trade, settle, code, side, str(shares), f"{price:.2f}", f"{amount}",
          f"{comm}", f"{stamp}", f"{tf}", f"{ftotal}", "按成交日列示"]],
    )


def one_side_rows(rng, day, code, name, side, shares, price):
    """单侧缺失 ×2：新股中签 + 现金分红，仅交割单存在。"""
    t1, t2 = next_id(), next_id()
    amount = d(shares * price)
    comm, stamp, tf = fees(amount, "买入")
    day2 = min(day + 4, 28)
    return [
        ([],
         [[t1, f"2026-09-{day:02d}", f"2026-09-{min(day + 1, 30):02d}", code, "中签买入",
           str(shares), f"{price:.2f}", f"{amount}", f"{comm}", f"{stamp}", f"{tf}",
           f"{d(comm + stamp + tf)}", "新股中签，自记账无"]]),
        ([],
         [[t2, f"2026-09-{day2:02d}", f"2026-09-{day2:02d}", "600519", "股息入账", "0", "0.00",
           "260.00", "0.00", "0.00", "0.00", "0.00", "现金分红，自记账无"]]),
    ]


def batch_fill_rows(rng, day, code, name, side, shares, price):
    """分批成交 ×1：交割单两行 vs 自记账一行（金额一致，费用有小额尾差）。"""
    t = next_id()
    half_s = shares // 2
    amount = d(shares * price)
    comm, stamp, tf = fees(amount, side)
    ftotal = d(comm + stamp + tf)
    date = f"2026-09-{day:02d}"
    settle = f"2026-09-{min(day + 1, 30):02d}"
    half_a = d(amount / 2)
    half_c = d(comm / 2)
    half_s2 = d(stamp / 2)
    half_t = d(tf / 2)
    bill_rows = []
    for k in (1, 2):
        bill_rows.append([t, date, settle, code, side, str(half_s), f"{price:.2f}", f"{half_a}",
                          f"{half_c}", f"{half_s2}", f"{half_t}", f"{d(half_c + half_s2 + half_t)}",
                          f"分批成交 {k}/2"])
    return (
        [t, date, code, name, side, str(shares), f"{price:.2f}", f"{amount}", f"{ftotal}",
         "分批成交合并记一行"],
        bill_rows,
    )


TYPE_IMPL = {
    "fee_omission": fee_omission_rows,
    "slippage": slippage_rows,
    "equity": equity_rows,
    "settle_day": settle_day_rows,
    "one_side": one_side_rows,
    "batch_fill": batch_fill_rows,
}

STOCKS = [("600519", "贵州茅台"), ("000651", "格力电器"), ("601318", "中国平安"),
          ("600036", "招商银行"), ("000858", "五粮液"), ("000001", "平安银行"),
          ("000333", "美的集团"), ("601888", "中国中免")]


def _as_pairs(res):
    """TYPE_IMPL 返回 (l, b) 或 [(l, b), ...] —— 统一成 [(l, b), ...]。"""
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], list) and (
            not res[0] or not isinstance(res[0][0], list)):
        return [res]
    return list(res)

# ---------------------------------------------------------------- 组合规则
# 每任务：slippage（恒在 1）+ ADJ001 类型（恒在 2，金额口径翻转）+ groupB1（可选 2）
#        + groupB2（可选 1）→ diff_count 四选项 = k+2+1 / k+2 / k+1 / k（k=3 → 6/5/4/3）
ADJ001_POOL = ("fee_omission", "one_side")
GROUPB1_POOL = ("equity",)             # 唯一能稳定出 2 单号的可选类
GROUPB2_POOL = ("settle_day",)
INVARIANT_POOL = ("slippage",)


def pick_types(rng):
    adj001 = rng.choice(ADJ001_POOL)
    rest = [t for t in ("equity", "settle_day", "batch_fill", "fee_omission", "one_side")
            if t != adj001]
    g1 = rng.choice(GROUPB1_POOL)
    g2 = rng.choice([t for t in GROUPB2_POOL if t != g1 and t != adj001] or GROUPB2_POOL)
    inv = rng.choice([t for t in rest if t not in (g1, g2)] or INVARIANT_POOL)
    return adj001, g1, g2, inv


# ---------------------------------------------------------------- 构建与真值
LEDGER_HEADER = ["txn_id", "date", "code", "name", "side", "shares", "price", "amount", "fee_total", "note"]
BILL_HEADER = ["txn_id", "trade_date", "settle_date", "code", "side", "shares", "price", "amount",
               "commission", "stamp_tax", "transfer_fee", "fee_total", "note"]


def build_task(seed, idx):
    rng = random.Random(f"{SEED}-{idx}")
    adj001, g1, g2, inv = pick_types(rng)
    ledger, bill = [], []
    day = rng.randint(2, 6)
    code, name = rng.choice(STOCKS)

    # 恒在：2 笔正常 + slippage + ADJ001 类型（2 单号）
    for _ in range(2):
        shares, price = rng.choice([(100, 1680.00), (500, 52.00), (1000, 45.00), (2000, 15.00)])
        l, b = normal_rows(rng, day, code, name, rng.choice(["买入", "卖出"]), shares, price)
        ledger.append(l)
        bill.extend(b)
        day += 2
    l, b = slippage_rows(rng, day, code, name, "买入", 1000, 45.00)
    ledger.append(l)
    bill.extend(b)
    day += 2

    shares, price = rng.choice([(500, 52.00), (2000, 38.00), (100, 138.00), (1000, 15.00)])
    side = rng.choice(["买入", "卖出"])
    adj001_ids = []
    for ll, bb in _as_pairs(TYPE_IMPL[adj001](rng, day, code, name, side, shares, price)):
        if ll:
            ledger.append(ll)
        bill.extend(bb)
        adj001_ids += sorted({x[0] for x in bb} | ({ll[0]} if ll else set()))
    adj001_ids = sorted(set(adj001_ids))
    day += 3

    g1_ids, g2_ids = [], []
    res = TYPE_IMPL[g1](rng, day, code, name)
    for ll, bb in _as_pairs(res):
        if ll:
            ledger.append(ll)
        bill.extend(bb)
        g1_ids += sorted({x[0] for x in bb} | ({ll[0]} if ll else set()))
    g1_ids = sorted(set(g1_ids))
    day += 3

    shares, price = rng.choice([(100, 138.00), (100, 8.20)])
    res = TYPE_IMPL[g2](rng, day, code, name, "买入", shares, price)
    for ll, bb in _as_pairs(res):
        if ll:
            ledger.append(ll)
        bill.extend(bb)
        g2_ids += sorted({x[0] for x in bb} | ({ll[0]} if ll else set()))
    g2_ids = sorted(set(g2_ids))
    day += 2

    inv_ids = []

    # 排序（ledger/bill 按出现序即可；真值按 key 聚合）
    def agg(rows, idx_amount, idx_fee, idx_shares, idx_date):
        out = {}
        for r in rows:
            t = r[0]
            e = out.setdefault(t, {"n": 0, "amount": Decimal("0"), "fee": Decimal("0"),
                                   "shares": 0, "date": r[idx_date]})
            e["n"] += 1
            e["amount"] += d(r[idx_amount])
            e["fee"] += d(r[idx_fee])
            e["shares"] += int(r[idx_shares])
        return out

    L = agg(ledger, 7, 8, 5, 1)
    B = agg(bill, 7, 11, 5, 1)

    dup_ids = sorted({t for t, e in B.items() if e["n"] > 1} | {t for t, e in L.items() if e["n"] > 1})
    dedup_amount = Decimal("0")
    for t in dup_ids:
        for side in (B, L):
            n = side.get(t, {"n": 0})["n"]
            if n > 1:
                per = side[t]["amount"] / n
                dedup_amount += (side[t]["amount"] - per)
                break

    diff_ids, amount_gap, fee_gap = [], Decimal("0"), Decimal("0")
    # ★ 2026-10-20 修：interpretations.applies_to 原先填的是宽泛集合
    #   （fee_omission→全部 diff_ids、one_side_missing→adj001_ids），
    #   与实测不符（20/20 任务都对不上）。改为按数据实算两个精确集合。
    one_side_ids, fee_diff_ids = [], []
    one_side_amount = Decimal("0")          # ★ 2026-10-21 新增：全部单侧金额合计
    for t in sorted(set(L) | set(B)):
        if t in dup_ids:
            continue
        l, b = L.get(t), B.get(t)
        if l is None or b is None:
            src = b if l is None else l
            diff_ids.append(t)
            one_side_ids.append(t)
            amount_gap += src["amount"]
            fee_gap += src["fee"]
            one_side_amount += src["amount"]   # ★ 与 one_side_ids 同源，避免两处口径分叉
            continue
        same = (l["shares"] == b["shares"] and l["amount"] == b["amount"]
                and l["fee"] == b["fee"] and l["date"] == b["date"])
        if not same:
            diff_ids.append(t)
            amount_gap += abs(l["amount"] - b["amount"])
            fee_gap += abs(l["fee"] - b["fee"])
            if l["fee"] != b["fee"]:
                fee_diff_ids.append(t)
    diff_ids.sort()
    one_side_ids.sort()
    fee_diff_ids.sort()

    ledger_total = sum(d(r[7]) for r in ledger)
    bill_total = sum(d(r[7]) for r in bill)

    expected = {
        "ledger_rows": len(ledger), "bill_rows": len(bill),
        "ledger_total": float(ledger_total), "bill_total": float(bill_total),
        "raw_total_gap": float(bill_total - ledger_total),
        "dedup_removed_txn_count": len(dup_ids), "dedup_amount": float(dedup_amount),
        "diff_count": len(diff_ids), "diff_ids": diff_ids,
        "amount_diff_total": float(amount_gap),           # 默认 exclude：费用不计入
        "excluded_amount_total": float(fee_gap),
        # ★ 3.0 新增：费用差独立承载（契约 3.0 §fee_diff_total）。
        #   此前费用差被塞进 excluded_amount_total，违反 D-0016「费用不并入金额」；
        #   且导致 fee_omission 场景下 ADJ-001 两选项 implies 完全相同（9/20 任务），
        #   人闸门签字失去机器可验证性。本字段承载 fee 列差合计（含单侧缺失单的整笔费用）。
        #   excluded_amount_total 保持原值仅为历史口径留痕，3.0 起应读作 0（见 truth.note）。
        "fee_diff_total": float(fee_gap),
    }

    # ---- ADJ-001（金额口径）：ADJ001 类型的费用/金额计入与否 ----
    if adj001 == "fee_omission":
        q1 = "交易费用（佣金 + 印花税 + 过户费）是否计入「成本差异」金额？"
        # ★ D-0016 修复：费用差在 summary 六字段中无落点（amount_diff_total/excluded_
        #   amount_total 均定义在 amount 列差上，费用属 fee_total 列）。旧 implies 把
        #   fee_gap 加进/移出 amount 字段，违反 TASK.md「费用不并入金额」易错点 1。
        #   修复：两选项的 implies 相同（amount_gap/0）——人类选择照常入档，但
        #   金额字段不受该轴影响；费用差异的呈报缺口记入待办（候选新字段
        #   fee_diff_total，属契约 3.0 范畴）。
        # ★ 3.0：fee_diff_total 提供可辨维度 —— count 记费用差，exclude 记 0。
        #   两选项从此不再中性（D-0016 的妥协已由新字段解除），签字可被机器验证。
        opts1 = [
            {"id": "count", "label": "计入成本差异（费用算作真实成本）",
             "implies": {"summary.amount_diff_total": float(amount_gap),
                         "summary.excluded_amount_total": 0.0,
                         "summary.fee_diff_total": float(fee_gap)}},
            {"id": "exclude", "label": "不计入（费用视为已解释的结算口径差异）",
             "implies": {"summary.amount_diff_total": float(amount_gap),
                         "summary.excluded_amount_total": 0.0,
                         "summary.fee_diff_total": 0.0}},
        ]
    else:
        q1 = "单侧缺失条目（仅交割单存在的记录）是否计入「成本差异」金额？"
        # ★ D-0015 修复：implies 必须与选项语义一致。单侧金额 = adj001 单号的 amount 差合计；
        #   count → 单侧留在计入侧（amount_gap 含它，excluded=0）；
        #   exclude → 单侧整类移出（amount=amount_gap−单侧，excluded=单侧）。
        #   旧版把 fee_gap 当移动量（仅 6.79），与「单侧缺失」语义无关且违反字段定义。
        # ★ 2026-10-21 修「implies 一对多」（复核报告 §5-4 / 证据链复核 §一）：
        #   原实现用 `adj001_ids` 求单侧金额——那是**注入场景**的 id 集合，
        #   而数据里还有自然发生的单侧记录（实测 T-13：注入只覆盖 T100+一笔股息
        #   = 14060，实际单侧合计 14320，漏了第二笔同额股息 T103）。
        #   后果：exclude 的 implies 记成 380/14060，与「单侧缺失整类移出」的
        #   语义不符；席位按语义实算得 120/14320 或 13920/520，**谁都对不上**，
        #   35/39 处「声明 × 算术」冲突由此产生。
        #   修法：改按全部单侧记录（one_side_amount，与 one_side_ids 同源）计算，
        #   使 implies 成为选项 label 的必然推论。
        one_side_amounts = one_side_amount
        opts1 = [
            {"id": "count", "label": "计入（漏记的资产/收益是真实差异）",
             "implies": {"summary.amount_diff_total": float(amount_gap),
                         "summary.excluded_amount_total": 0.0,
                         "summary.fee_diff_total": float(fee_gap)}},
            {"id": "exclude", "label": "不计入（整笔漏记另册管理，不与金额不符混算）",
             "implies": {"summary.amount_diff_total": float(amount_gap - one_side_amounts),
                         "summary.excluded_amount_total": float(one_side_amounts),
                         "summary.fee_diff_total": 0.0}},
        ]

    # ---- ADJ-002（清单范围）：四选项计数互异 ----
    # ★ 2026-10-20 修命名漂移：内部类别键用简称（settle_day），对外文本用全名
    #   （settlement_day）。此前 question/label 直接把内部键暴露给席位，与
    #   interpretations.json 的 key「settlement_day」不一致，同一概念两个名字。
    #   修法：仅在【展示层】映射，不动内部键（改内部键会牵动函数名
    #   settle_day_rows / GROUPB2_POOL / 内部映射，风险高而收益低）。
    CATEGORY_DISPLAY = {"settle_day": "settlement_day"}
    d1, d2 = CATEGORY_DISPLAY.get(g1, g1), CATEGORY_DISPLAY.get(g2, g2)
    k = len([t for t in diff_ids if t not in g1_ids and t not in g2_ids])
    counts = {"all": k + len(g1_ids) + len(g2_ids),
              "g1": k + len(g1_ids), "g2": k + len(g2_ids), "none": k}
    vals = sorted(counts.values())
    assert len(set(vals)) == 4, f"四选项计数不互异: {counts} (task {idx})"
    q2 = ("差异清单（`summary.diff_count` 与 `diffs`）应包含哪些条目？由一个选项同时决定："
          f"(a)「{d1}」类（{len(g1_ids)} 条）是否列入；(b)「{d2}」类（{len(g2_ids)} 条）是否列入。")
    opts2 = [
        {"id": str(counts["all"]), "label": f"两类都列入（{d1} + {d2}）",
         "implies": {"summary.diff_count": counts["all"]}},
        {"id": str(counts["g1"]), "label": f"只列入 {d1} 类",
         "implies": {"summary.diff_count": counts["g1"]}},
        {"id": str(counts["g2"]), "label": f"只列入 {d2} 类",
         "implies": {"summary.diff_count": counts["g2"]}},
        {"id": str(counts["none"]), "label": "两类都不列入",
         "implies": {"summary.diff_count": counts["none"]}},
    ]

    task_id = f"TASK-BROKER-{idx:03d}"
    return {
        "task_id": task_id, "adj001": adj001, "g1": g1, "g2": g2, "inv": inv,
        "ledger": ledger, "bill": bill, "expected": expected,
        "diff_ids": diff_ids, "adj001_ids": adj001_ids, "g1_ids": g1_ids, "g2_ids": g2_ids,
        # interpretations.applies_to 的精确数据源（2026-10-20 修：原用宽泛集合导致标注失真）
        "one_side_ids": one_side_ids, "fee_diff_ids": fee_diff_ids,
        "q1": q1, "opts1": opts1, "q2": q2, "opts2": opts2,
    }


# ---------------------------------------------------------------- 落盘
def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")


TASK_MD = """# 任务 {task_id} · 券商交割单与自记账核对（A股）

> 本任务使用**全合成数据**，不含任何真实账户、持仓或成交信息。
> 与 `TASK-BROKER-RECON-002` 同构：字段、口径、易错点完全一致，仅数据不同。

## 背景

自记账（我方记录，按入账日记账）与券商交割单（按成交日列示，费用单列）之间存在若干不一致。
请完成核对，输出**差异归因结果**。

## 输入

| 文件 | 说明 |
|---|---|
| `ledger.csv` | 自记账（我方记录） |
| `bill.csv` | 券商交割单（对方返回） |

自记账字段：`txn_id`、`date`（入账日）、`code`、`name`、`side`、`shares`、`price`、`amount`、`fee_total`、`note`

交割单字段：`txn_id`、`trade_date`（成交日）、`settle_date`（交割日）、`code`、`side`、`shares`、`price`、`amount`、`commission`、`stamp_tax`、`transfer_fee`、`fee_total`、`note`

费率口径（仅用于理解数据，**不得据此反推期望值**）：佣金 万分之2.5（最低 5 元）／印花税 卖出千分之0.5／过户费 万分之0.1，双向。

## 输出

严格 JSON，写入 `answer.json`：

```json
{{
  "summary": {{
    "ledger_rows": 0, "bill_rows": 0, "ledger_total": 0, "bill_total": 0,
    "raw_total_gap": 0, "dedup_removed_txn_count": 0, "dedup_amount": 0,
    "diff_count": 0, "amount_diff_total": 0, "excluded_amount_total": 0,
    "fee_diff_total": 0, "excluded_categories": []
  }},
  "diffs": [{{"id": "T000", "category": "你定义的类别名"}}],
  "categories": {{"你定义的类别名": ["T000"]}},
  "falsification": "在什么条件下我这个结论是错的"
}}
```

### 字段定义

> **契约只定义每个字段的计算方法，不预设你对「该不该算差异」的判断。**
> 金额一律取 CSV 原始值，**不做含税/净额换算**。

#### A. 纯算术字段

| 字段 | 计算方法 |
|---|---|
| `ledger_rows` | `ledger.csv` 数据行数（不含表头） |
| `bill_rows` | `bill.csv` 数据行数（不含表头） |
| `ledger_total` | `ledger.csv` 的 `amount` 列合计 |
| `bill_total` | `bill.csv` 的 `amount` 列合计 |
| `raw_total_gap` | `bill_total − ledger_total` |

#### B. 重复项字段

| 字段 | 计算方法 |
|---|---|
| `dedup_removed_txn_count` | 判定为**重复**的单号个数（**按单号计，不按行计**） |
| `dedup_amount` | 上述重复单号所对应的**多出行**的金额合计 |

> 同一 `txn_id` 在一侧出现多次（如分批成交）即为重复。

#### C. 差异分类字段

| 字段 | 计算方法 |
|---|---|
| `diff_count` | 差异清单条数，须等于 `diffs` 数组长度 |
| `amount_diff_total` | 归入「**计入**金额差异」类别的**非重复**单号，取 `abs(自记账 amount − 交割单 amount)`，求和 |
| `excluded_amount_total` | 归入「**不计入**金额差异」类别的**非重复**单号，取差额，求和 |
| `fee_diff_total` | 费用列（`fee_total`）的差额合计：逐个**非重复**单号取 `abs(自记账 fee_total − 交割单 fee_total)` 求和；**单侧缺失时取整笔费用**。费用**不并入** `amount_diff_total` / `excluded_amount_total`（那两者只算 `amount` 列） |
| `excluded_categories` | 被排除在金额差异之外的**类别名**列表，必须是 `diffs[].category` 的**子集**（若无则填 `[]`，**并必须在 `excluded_categories_note` 说明为何为空**） |
| `excluded_categories_note` | 当 `excluded_categories` 为 `[]` 时，说明「为何没有任何类别被排除」；非空时可省略 |

> 📌 **`excluded_categories` 的语义（易误读，务必按此理解）**：
> 它列的是「**被排除出金额差异**的类别」，因此天然是 `diffs[].category` 的**真子集**：
> - `diffs` 列**全部**差异（含被排除的类别）；
> - `excluded_categories` 只列其中**不计入金额差异**的那几类。
>
> 所以「`excluded_categories` 非空而 `excluded_amount_total == 0`」**不是矛盾**——
> 日期差、股数差、费用差这些类别的 `amount` 差本就是 0。
> 机器校验：写入的类别必须出现在 `diffs[].category` 中（凭空新增会被 `W_CATEGORY_NOT_IN_DIFFS` 提示）。

> ⚠️ 四个易错点，严格按定义执行：
> 1. `amount` 列是**成交金额**，`fee_total` 是**费用**，两者分开取，不要相加后再比对。
> 2. 两个金额差异字段都是**差额**（`abs(自记账 − 交割单)`），不是交易全额。
> 3. **重复项不参与** `amount_diff_total` 与 `excluded_amount_total`。
> 4. 一律取原始值，**不得**做净额/含费换算后再计算。
>
> 📌 **空数组条款（D-0024 收口）**：`excluded_categories: []` 是**合法**的，但必须配 `excluded_categories_note`
> 解释原因（例：「本任务全部差异均为金额类，无类别需要排除」）。机器判据为二选一：
> 要么列表非空，要么列表为空且有说明。**"空" 与 "漏填" 必须可区分。**

#### D. 差异的判定口径

「差异单号」= 两侧在**股数 / 成交金额 / 费用 / 日期**四列上不完全相同的单号。
仅单侧存在的单号亦为差异单号，差额取该单号在该侧的 `amount`。

## 约束

1. **不得引入外部数据**，只能用给定两份文件。
2. **所有数字必须由数据算出**，不得估计或手填。
3. 类别名由你自行定义，但**必须在整个输出中一致使用**。
4. `diffs` 中每条须含 `id` 与 `category`；`categories` 须与 `diffs` 自洽。
5. `falsification` 字段**不可省略**——缺失即视为结论不可裁决。
"""

FACT_SCHEMA = {
    "note": "任务级事实表列 schema（D-0012）。缺失 = 001 默认布局。比对列必须两侧 CSV 都存在。",
    "key": "txn_id",
    "sides": [
        {"file": "ledger.csv", "name": "自记账", "date_col": "date", "amount_col": "amount",
         "compare": [{"col": "fee_total", "label": "费用", "agg": "sum", "fmt": "money"},
                     {"col": "shares", "label": "股数", "agg": "sum", "fmt": "int"}]},
        {"file": "bill.csv", "name": "交割单", "date_col": "trade_date", "amount_col": "amount",
         "compare": [{"col": "fee_total", "label": "费用", "agg": "sum", "fmt": "money"},
                     {"col": "shares", "label": "股数", "agg": "sum", "fmt": "int"}]},
    ],
}


def emit(out_dir, idx, t):
    tdir = Path(out_dir) / f"T-{idx:02d}"
    (tdir / "spec").mkdir(parents=True, exist_ok=True)
    write_csv(tdir / "ledger.csv", LEDGER_HEADER, t["ledger"])
    write_csv(tdir / "bill.csv", BILL_HEADER, t["bill"])
    (tdir / "TASK.md").write_text(TASK_MD.format(task_id=t["task_id"]),
                                  encoding="utf-8", newline="\n")
    truth = {
        "task_id": t["task_id"],
        "data_note": "全部为合成数据，不含任何真实账户或持仓信息",
        "rules": {"dedup": "同一 txn_id 多行 → 去重保留首条",
                  "fee": "佣金 万2.5（最低5元）/ 印花税 卖出 0.05% / 过户费 万0.1 双向",
                  "missing": "仅单侧存在 → 计入金额差异（默认口径）",
                  "settlement_day": "自记账按交割日、券商按成交日 → 日期差异（默认计入）",
                  # ★ 契约 3.0 裁定：日期比对基准写死 trade_date。
                  #   实测依据：ledger.csv 的 date 列存的是成交日；若按 settle_date 比对，
                  #   会消除 1 条真差异（settlement_day 类）又引入 2 条伪差异
                  #   （正常单 ledger.date==trade_date≠settle_date），T-09/T-15 的
                  #   diff_count 会从 6 变成 7。故 settle_date 不作为等价口径。
                  "date_basis": "trade_date"},
        "expected": t["expected"],
        "checks": [],
        "note": ("★ expected 沿用【历史默认口径】（费用不计入/单侧计入、清单全列），它不是真值 —— "
                 "签署人可另行裁决，判 fail 是正确结果。"
                 " ★ 契约 3.0 起：excluded_amount_total 中的费用部分应读作 0，"
                 "费用差改由 fee_diff_total 独立承载（本 expected 保留原值仅为历史留痕）。"),
    }
    write_json(tdir / "truth.json", truth)
    arith = {
        "task_id": t["task_id"], "layer": "arithmetic",
        "layer_note": "纯算术锚点。expected 全部可由输入数据唯一算出。",
        "checks": [
            {"name": "自记账行数", "type": "numeric_equals", "path": "summary.ledger_rows",
             "expected": t["expected"]["ledger_rows"]},
            {"name": "交割单原始行数", "type": "numeric_equals", "path": "summary.bill_rows",
             "expected": t["expected"]["bill_rows"]},
            {"name": "去重笔数", "type": "numeric_equals", "path": "summary.dedup_removed_txn_count",
             "expected": t["expected"]["dedup_removed_txn_count"]},
            {"name": "去重金额", "type": "decimal_equals", "scale": 2,
             "path": "summary.dedup_amount", "expected": t["expected"]["dedup_amount"]},
            {"name": "自记账合计", "type": "decimal_equals", "scale": 2,
             "path": "summary.ledger_total", "expected": t["expected"]["ledger_total"]},
            {"name": "交割单合计", "type": "decimal_equals", "scale": 2,
             "path": "summary.bill_total", "expected": t["expected"]["bill_total"]},
            {"name": "总额差", "type": "decimal_equals", "scale": 2,
             "path": "summary.raw_total_gap", "expected": t["expected"]["raw_total_gap"]},
            # ★ D-0024 收口：present → present_or_note（双路径）。
            #   TASK.md 允许 excluded_categories 为 []，原 present 断言会把空数组判 fail，
            #   逼人编造类别。改为「非空 或 空且有说明」二选一，使"空"与"漏填"可区分。
            {"name": "必须声明排除类别或说明为何为空", "type": "present_or_note",
             "path": "summary.excluded_categories",
             "note_path": "summary.excluded_categories_note"},
            {"name": "必须给出类别归属", "type": "present", "path": "categories"},
            {"name": "必须给出证伪条件", "type": "present", "path": "falsification"},
        ],
    }
    write_json(tdir / "spec" / "arithmetic.json", arith)
    adj = {
        "task_id": t["task_id"], "layer": "adjudication",
        # ★ 2026-10-22（交接 §5-7）：同一个任务在本包里有 4 种写法
        #   （T-NN / TASK-BROKER-NNN / TASK-BROKER-NNN/T-NN / tasks/T-NN）。
        #   这里显式给出规范号，读取侧不必再猜；历史产物保持原样（不回溯改写）。
        "task_id_canonical": f"T-{idx:02d}",
        # ★ 2026-10-21：ADJ 的答案已由 TASK.md 明确规定（20/20 任务），故两项均标「合规确认」。
        #   这不是「两种合理口径待裁决」，而是「检查是否遵守文档」——
        #   加前缀以免读者误以为此处存在判断空间。见 docs/ADJ判断空间分析-20261021.md。
        "layer_note": ("合规确认项（非开放判断）。正确口径已写在 TASK.md，"
                       "未裁决时 verdict=pending_adjudication（退出码 3）。"),
        "items": [
            # ★ 2026-10-22（交接 §5-5）：新增 `kind` 区分两类判断项。
            #   compliance = 契约唯一确定，implies 可反推 -> 机器能验「签字与算出自洽」；
            #   judgment   = 契约内多解，需人类裁量 -> 记录必须附 rationale（见 verify.lint_record）。
            #   本包 20 个 ADJ 全部是 compliance（选项由口径条款唯一确定）。
            {"id": "ADJ-001", "kind": "compliance", "question": "【合规确认】" + t["q1"],
             "paths_affected": ["summary.amount_diff_total", "summary.excluded_amount_total"],
             "options": t["opts1"], "status": "unresolved"},
            {"id": "ADJ-002", "kind": "compliance", "question": "【合规确认】" + t["q2"],
             "paths_affected": ["summary.diff_count", "diffs"],
             "options": t["opts2"], "status": "unresolved"},
        ],
        "adjudication_required": True,
        "record_format": {
            "_note": "由人填写，工具链不得自动生成。",
            "required_fields": ["task_id", "source", "recorded_by", "recorded_at", "decisions"],
            "decision_fields": ["id", "chosen", "rationale"],
        },
    }
    write_json(tdir / "spec" / "adjudication.json", adj)
    write_json(tdir / "spec" / "fact-schema.json", FACT_SCHEMA)
    interp = {
        "task_id": t["task_id"],
        "note": "候选解释 —— 是【解释】不是【事实】更不是判据。",
        "items": [
            {"key": "fee_omission", "name": "费用漏记",
             "basis": "同单号成交金额一致但 fee_total 不一致",
             "applies_to": t["fee_diff_ids"],
             "caveat": "漏记与费率记错在数据上不可区分；是否计入由 ADJ-001 裁决。"},
            {"key": "slippage", "name": "委托价 vs 成交价（滑点）",
             "basis": "price 列两侧不同", "applies_to": t["diff_ids"],
             "caveat": "n=1 支撑的归纳；也可能只是手工录入误差。"},
            {"key": "equity", "name": "权益变动（送转/分红）",
             "basis": "股数差（金额 0）或仅交割单侧的股息", "applies_to": t["g1_ids"],
             "caveat": "账户事实是否算「对账差异」没有数据判据；由 ADJ-002 裁决。"},
            {"key": "settlement_day", "name": "T+1 交割跨日",
             "basis": "date 与 trade_date 差一天，金额一致", "applies_to": t["g2_ids"],
             "caveat": "依赖日期比对基准的隐含假设（trade_date vs settle_date）；契约 3.0 已裁定基准为 trade_date。"},
            {"key": "one_side_missing", "name": "单侧缺失",
             "basis": "仅交割单存在",
             "applies_to": t["one_side_ids"],
             "caveat": "漏记资产还是券商误入账，方向不同处置完全不同；由 ADJ-001 裁决。"},
            {"key": "batch_fill", "name": "分批成交合并记账",
             "basis": "同单号多行 vs 单行，合计一致但费用有小额尾差",
             "applies_to": t["g2_ids"], "caveat": "「多出行」指称未消歧；重复单号能否入 diffs 属契约空白。"},
        ],
    }
    write_json(tdir / "spec" / "interpretations.json", interp)
    return tdir


# ---------------------------------------------------------------- 自检
def self_check(out_dir, tasks):
    """对全部 20 组：合成默认口径答案 → verify → 算术全过 + 判断层 pending。"""
    import subprocess
    # ★ 自检产物只写 _trash/tmp-regress/（回归第 20 项：删除唯一去向是 _trash，
    #   故这里【不做任何删除】—— 工作副本由回归流程统一处置）。
    pkg = Path(__file__).resolve().parent.parent / "migration-package"
    verify = pkg / "judges" / "verify.py"
    work = Path(__file__).resolve().parent.parent / "_trash" / "tmp-regress" / "t20-selfcheck"
    work.mkdir(parents=True, exist_ok=True)
    bad = []
    for idx, t in tasks:
        e = t["expected"]
        ans = {"summary": {**{k: e[k] for k in ("ledger_rows", "bill_rows", "ledger_total",
                                                "bill_total", "raw_total_gap",
                                                "dedup_removed_txn_count", "dedup_amount",
                                                "diff_count", "amount_diff_total",
                                                "excluded_amount_total")},
                           "excluded_categories": ["费用差异"]},
               "diffs": [{"id": i, "category": "待裁决"} for i in e["diff_ids"]],
               "categories": {"待裁决": e["diff_ids"]},
               "falsification": "若费率口径一致且无漏记，本结论不成立。"}
        ap = work / f"T-{idx:02d}-answer.json"
        write_json(ap, ans)
        spec = Path(out_dir) / f"T-{idx:02d}" / "spec"
        r = subprocess.run([sys.executable, str(verify),
                            "--spec-dir", str(spec), "--answer", str(ap),
                            "--out", str(work / f"T-{idx:02d}-verdict.json")],
                           capture_output=True, text=True, encoding="utf-8")
        if "pending_adjudication" not in (r.stdout or "") or "10/10" not in (r.stdout or ""):
            bad.append((idx, (r.stdout or "") + (r.stderr or "")))
    return bad


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="批量生成 20 条留出任务")
    ap.add_argument("--out", default="../migration-package/fixtures/tasks")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--patch-read-domains", action="store_true",
                    help="把各组 truth/spec 写入 read_domains.json 的 deny_absolute（幂等）")
    args = ap.parse_args()

    seed = args.seed
    SEQ["n"] = 0

    rng = random.Random(f"{seed}-plan")
    holdout = sorted(rng.sample(range(1, N_TASKS + 1), N_HOLDOUT))
    pilot = [i for i in range(1, N_TASKS + 1) if i not in holdout][:PILOT_N]

    tasks = []
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(1, N_TASKS + 1):
        t = build_task(seed, idx)
        emit(out_dir, idx, t)
        tasks.append((idx, t))

    manifest = {
        "generated_at": "2026-10-20", "seed": SEED,
        "holdout": [f"T-{i:02d}" for i in holdout],
        "pilot": [f"T-{i:02d}" for i in pilot],
        "full_order": [f"T-{i:02d}" for i in range(1, N_TASKS + 1) if i not in holdout],
        "note": "holdout 任务不进任何设计迭代，仅终测首跑。同 seed 重跑逐字节一致。",
    }
    write_json(Path(args.out) / "tasks-manifest.json", manifest)

    print(f"[OK] {N_TASKS} 组夹具 -> {out_dir}")
    print(f"     holdout: {[f'T-{i:02d}' for i in holdout]}")
    print(f"     pilot:   {[f'T-{i:02d}' for i in pilot]}")
    for idx, t in tasks:
        c = t["expected"]
        print(f"     T-{idx:02d} adj001={t['adj001']:12s} g1={t['g1']:12s} g2={t['g2']:12s} "
              f"diffs={c['diff_count']:2d} 选项={[o['id'] for o in t['opts2']]}")

    if args.patch_read_domains:
        rd_path = Path(args.out).parent.parent / "config" / "read_domains.json"
        rd = json.loads(rd_path.read_text(encoding="utf-8"))
        deny = rd.get("deny_absolute") or []
        added = 0
        for idx in range(1, N_TASKS + 1):
            for sub in (f"fixtures/tasks/T-{idx:02d}/truth.json",
                        f"fixtures/tasks/T-{idx:02d}/spec/"):
                if sub not in deny:
                    deny.append(sub)
                    added += 1
        rd["deny_absolute"] = deny
        rd.setdefault("_why_each_entry", {})["fixtures/tasks/*（扩样批量，2026-10-20）"] = (
            "★ 新增批量任务必须逐组补 deny_absolute：deny 是前缀匹配，"
            "'fixtures/truth.json' 盖不住 'fixtures/tasks/T-XX/truth.json'。"
            "allow 不加（D-0010 教训：allow 全局直拼进提示词，会令所有轮次列出全部任务的输入）。")
        write_json(rd_path, rd)
        print(f"[OK] read_domains.json deny_absolute +{added}")

    if args.self_check:
        bad = self_check(args.out, tasks)
        if bad:
            for idx, msg in bad:
                print(f"[FAIL] T-{idx:02d}: {msg[:300]}", file=sys.stderr)
            return 1
        print(f"[OK] 自检通过：{N_TASKS}/{N_TASKS} 组 算术 10/10 + 判断层 pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
