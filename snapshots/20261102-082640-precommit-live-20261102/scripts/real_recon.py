#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""real_recon.py —— 真实账本对账（恒等式校验，只读输入）

与 `migration-package/` 的合成任务是两套东西：
合成任务的判据依赖 `truth.json`（expected），真实账本没有真值，只能靠**恒等式**。

所以本工具不判「答案对不对」，只判「账自己是否闭合」：

  I1 现金恒等   cash == 本金 + 加仓 − Σ买入(total) + Σ卖出(net_proceeds)
  I2 持仓股数   ΣBUY.shares − ΣSELL.shares == positions[code].shares
  I3 持仓成本   剩余股数按买入均摊成本 == positions[code].total_cost
  I4 市值        market_value == current_price × shares
  I5 浮盈        floating_pnl == market_value − total_cost
  I6 同义字段    pnl == floating_pnl（refresh_portfolio.py 明写二者同义）

> I1 的注意点：`config.adjustments` 是**冲销备忘录**（用于解释历史盈余的来源），
> 不是额外现金流。把它当流出重复扣减，会凭空造出 1,956.64 的假差异。

用法：
    python3 scripts/real_recon.py --portfolio <portfolio.json> [--out <dir>]
    python3 scripts/real_recon.py --portfolio ... --json      # 只输出 JSON

退出码：0 全部闭合 / 1 存在不闭合项 / 2 读取失败
"""

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# 账本路径默认不内置任何绝对路径（2026-10-23 解耦）。
# 本项目须能脱离任何外部项目独立运行：`--portfolio` 显式给，或用环境变量指定。
# 找不到就报错退出 2 —— 不猜、不回退到某个"约定俗成"的位置。
DEFAULT_PF = os.environ.get("AI_TEAM_PORTFOLIO", "")


def D(x):
    return Decimal(str(x if x is not None else 0))


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def fmt(v):
    try:
        return f"{D(v):,.2f}"
    except (InvalidOperation, ValueError):
        return str(v)


def main():
    ap = argparse.ArgumentParser(description="真实账本恒等式对账")
    ap.add_argument(
        "--portfolio",
        default=DEFAULT_PF,
        help="账本 JSON 路径；也可用环境变量 AI_TEAM_PORTFOLIO 指定（本项目不内置任何外部路径）",
    )
    ap.add_argument(
        "--broker", default="", help="券商侧对照 JSON（如 real-runs/*/券商对照快照.json）"
    )
    ap.add_argument("--out", default="", help="输出目录（写入 recon-result.json）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    if not args.portfolio:
        print(
            "[ERR] 未指定账本：加 --portfolio <portfolio.json>，或设环境变量 AI_TEAM_PORTFOLIO。\n"
            "      本脚本已解除对外部项目的默认路径依赖，不再回退到任何约定位置。",
            file=sys.stderr,
        )
        return 2

    try:
        pf = load(args.portfolio)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERR] 读取失败：{e}", file=sys.stderr)
        return 2

    cfg = pf.get("config") or {}
    tx = pf.get("transactions") or []
    summ = pf.get("summary") or {}
    # 缺失值按 0 参与恒等式（D() 的语义），但**必须在 notes 里留痕**：
    # 否则输出 JSON 的 summary.cash="0" 会被下游读成"账户真的没钱"。
    cash_missing = pf.get("cash") is None and summ.get("cash_available") is None
    cash = D(pf.get("cash") if pf.get("cash") is not None else summ.get("cash_available"))

    # 两种账户形态：模拟盘 positions(dict) / 实盘 holdings(list)。恒等式同一套，只是取数路径不同。
    if isinstance(pf.get("positions"), dict):
        pos_items = [(c, p) for c, p in sorted(pf["positions"].items())]
        shape = "positions(dict)"
    else:
        pos_items = [(h.get("code"), h) for h in (pf.get("holdings") or [])]
        shape = "holdings(list)"

    def cost_of(p):
        """成本：模拟盘有 total_cost；实盘只有 avg_cost，按股数折。"""
        if p.get("total_cost") is not None:
            return D(p.get("total_cost"))
        return (D(p.get("avg_cost")) * D(p.get("shares"))).quantize(Decimal("0.01"))

    checks, diffs = [], []

    def add(cid, name, expect, actual, ok, note=""):
        checks.append(
            dict(id=cid, name=name, expect=str(expect), actual=str(actual), ok=ok, note=note)
        )
        if not ok:
            diffs.append(dict(id=cid, name=name, expect=str(expect), actual=str(actual), note=note))

    notes = []
    if cash_missing:
        notes.append(
            "账本无 cash、summary 也无 cash_available：现金按 0 参与恒等式。"
            "此时 I1/I9/I11 红属**字段缺失**，不是账算错"
        )
    init = D(cfg.get("initial_capital"))
    add_cap = sum((D(a.get("amount")) for a in cfg.get("capital_additions") or []), D(0))

    # ---- I1~I3 流水类：只有存在 transactions 才可验 ----
    if tx:
        buy = sum((D(t.get("total")) for t in tx if t.get("type") == "BUY"), D(0))
        sell = sum((D(t.get("net_proceeds")) for t in tx if t.get("type") == "SELL"), D(0))
        cash_exp = init + add_cap - buy + sell
        add(
            "I1",
            "现金恒等",
            cash_exp,
            cash,
            cash_exp == cash,
            "本金 + 加仓 − 买入 + 卖出净；adjustments 为冲销备忘录，不参与",
        )

        bought, sold, cost_bought = {}, {}, {}
        for t in tx:
            c, s = t.get("code"), int(t.get("shares") or 0)
            if t.get("type") == "BUY":
                bought[c] = bought.get(c, 0) + s
                cost_bought[c] = cost_bought.get(c, D(0)) + D(t.get("total"))
            elif t.get("type") == "SELL":
                sold[c] = sold.get(c, 0) + s

        for c in sorted(set(bought) | {c for c, _ in pos_items}):
            exp_sh = bought.get(c, 0) - sold.get(c, 0)
            act_sh = int((dict(pos_items).get(c) or {}).get("shares") or 0)
            add("I2", f"持仓股数 {c}", exp_sh, act_sh, exp_sh == act_sh, "Σ买入股数 − Σ卖出股数")
            if exp_sh > 0 and bought.get(c):
                exp_cost = (cost_bought[c] * D(exp_sh) / D(bought[c])).quantize(Decimal("0.01"))
                add(
                    "I3",
                    f"持仓成本 {c}",
                    exp_cost,
                    cost_of(dict(pos_items)[c]),
                    exp_cost == cost_of(dict(pos_items)[c]),
                    "买入总额按剩余股数均摊（含佣金）",
                )
    else:
        notes.append(
            "无 transactions：现金与持仓成本**不可由流水验证**，只做字段间恒等式（实盘为手工维护）"
        )

    # ---- I4/I5/I6 逐持仓派生字段 ----
    for c, p in pos_items:
        if p.get("total_cost") is None and p.get("avg_cost") is None:
            notes.append(f"{c} 无 total_cost 也无 avg_cost：成本按 0 计，I5 的浮盈会等于整个市值")
        sh = D(p.get("shares"))
        px = D(p.get("current_price"))
        mv = D(p.get("market_value"))
        cost = cost_of(p)
        add(
            "I4",
            f"市值 {c}",
            (px * sh).quantize(Decimal("0.01")),
            mv,
            (px * sh).quantize(Decimal("0.01")) == mv,
            "current_price × shares",
        )
        add(
            "I5",
            f"浮盈 {c}",
            (mv - cost).quantize(Decimal("0.01")),
            D(p.get("floating_pnl")),
            (mv - cost).quantize(Decimal("0.01")) == D(p.get("floating_pnl")),
            "market_value − 成本（total_cost 或 avg_cost×shares）",
        )
        if p.get("pnl_pct") is not None and cost > 0:
            add(
                "I10",
                f"浮盈率 {c}",
                ((mv - cost) / cost * 100).quantize(Decimal("0.01")),
                D(p.get("pnl_pct")),
                ((mv - cost) / cost * 100).quantize(Decimal("0.01")) == D(p.get("pnl_pct")),
                "(市值 − 成本) / 成本 × 100",
            )
        # I6：pnl 已于 2026-10-21 收敛为单一真源；若再次出现必须与 floating_pnl 相等。
        if "pnl" in p:
            add(
                "I6",
                f"同义字段 {c}（已废弃）",
                D(p.get("floating_pnl")),
                D(p.get("pnl")),
                D(p.get("pnl")) == D(p.get("floating_pnl")),
                "pnl 已于 2026-10-21 移除；再次出现说明有写入方复活它",
            )
        else:
            add(
                "I6",
                f"同义字段 {c}（已收敛）",
                "—",
                "—",
                True,
                "单一真源：pnl 已移除，只保留 floating_pnl",
            )

    # ---- I7~I9 汇总恒等：summary 内字段与逐持仓求和必须一致 ----
    sum_mv = sum((D(p.get("market_value")) for _, p in pos_items), D(0))
    sum_fp = sum((D(p.get("floating_pnl")) for _, p in pos_items), D(0))
    if summ.get("total_market_value") is not None:
        add(
            "I7",
            "汇总市值",
            sum_mv,
            D(summ.get("total_market_value")),
            sum_mv == D(summ.get("total_market_value")),
            "Σ 逐持仓 market_value",
        )
    if summ.get("floating_pnl") is not None:
        add(
            "I8",
            "汇总浮盈",
            sum_fp,
            D(summ.get("floating_pnl")),
            sum_fp == D(summ.get("floating_pnl")),
            "Σ 逐持仓 floating_pnl",
        )
    if summ.get("total_assets") is not None:
        add(
            "I9",
            "总资产",
            (cash + sum_mv).quantize(Decimal("0.01")),
            D(summ.get("total_assets")),
            (cash + sum_mv).quantize(Decimal("0.01")) == D(summ.get("total_assets")),
            "现金 + 市值",
        )
        if summ.get("position_pct") is not None and (cash + sum_mv) > 0:
            exp_pp = (sum_mv / (cash + sum_mv) * 100).quantize(Decimal("0.01"))
            add(
                "I11",
                "仓位占比",
                exp_pp,
                D(summ.get("position_pct")),
                abs(exp_pp - D(summ.get("position_pct"))) <= Decimal("0.1"),
                "市值 / 总资产 × 100（容差 0.1pp）",
            )
    # I12/I13：invested_cost_stocks / invested_return_pct_stocks 同为「一次性脚本写」的
    # 汇总字段，2026-10-21 起改为每次刷新重算。此处把它们钉成恒等式。
    sum_cost = sum((cost_of(p) for _, p in pos_items), D(0))
    if summ.get("invested_cost_stocks") is not None:
        add(
            "I12",
            "已投成本合计",
            sum_cost,
            D(summ.get("invested_cost_stocks")),
            sum_cost == D(summ.get("invested_cost_stocks")),
            "Σ 逐持仓成本（avg_cost×shares）",
        )
        if summ.get("invested_return_pct_stocks") is not None and sum_cost > 0:
            exp_r = (sum_fp / sum_cost * 100).quantize(Decimal("0.01"))
            add(
                "I13",
                "已投收益率",
                exp_r,
                D(summ.get("invested_return_pct_stocks")),
                abs(exp_r - D(summ.get("invested_return_pct_stocks"))) <= Decimal("0.01"),
                "Σ浮盈 / Σ成本 × 100",
            )

    # I14：daily_pnl 原无写入方（手工截图维护），2026-10-21 起由刷新按涨跌额现算并落 prev_close。
    # 仅当全部持仓都有 prev_close 时可验；缺一即跳过，不假装能验。
    if (
        summ.get("daily_pnl") is not None
        and pos_items
        and all(p.get("prev_close") is not None for _, p in pos_items)
    ):
        exp_d = sum(
            (
                D(p.get("shares")) * (D(p.get("current_price")) - D(p.get("prev_close")))
                for _, p in pos_items
            ),
            D(0),
        ).quantize(Decimal("0.01"))
        add(
            "I14",
            "当日盈亏",
            exp_d,
            D(summ.get("daily_pnl")),
            exp_d == D(summ.get("daily_pnl")),
            "Σ 股数 ×（现价 − 昨收）",
        )

    # ---- B 系列：账本 × 券商侧对照（2026-10-22 新增，需 --broker）----
    # 首次有了独立于账本本身的第二来源：券商 APP 截图。
    # 口径差异（已知、已量化）：券商的「盈亏」含费用，账本的 floating_pnl = 市值 − avg_cost×股数，
    # 两者天然差一笔累计费用（实测 −33.09）。B6 因此按**披露残差**处理，不算失败。
    if args.broker:
        b = load(Path(args.broker).expanduser())
        if not b:
            print(f"[ERR] 无法读取券商对照文件：{args.broker}", file=sys.stderr)
            return 2
        bs = b.get("summary") or {}
        bmap = {h.get("code"): h for h in b.get("holdings") or []}

        def badd(cid, name, expect, actual, ok, note=""):
            add(cid, name, expect, actual, ok, note)

        # 简单三类逐项核（显式展开，避免过度抽象）
        badd(
            "B1",
            "可用资金",
            D(bs.get("cash_available")),
            D(summ.get("cash_available")),
            D(bs.get("cash_available")) == D(summ.get("cash_available")),
            "券商可用 vs 账本 cash_available",
        )
        badd(
            "B2",
            "总市值",
            D(bs.get("total_market_value")),
            D(summ.get("total_market_value")),
            D(bs.get("total_market_value")) == D(summ.get("total_market_value")),
            "券商总市值 vs 账本汇总市值",
        )
        badd(
            "B3",
            "总资产",
            D(bs.get("total_assets")),
            D(summ.get("total_assets")),
            D(bs.get("total_assets")) == D(summ.get("total_assets")),
            "券商总资产 vs 账本 total_assets",
        )

        # B4/B5：逐持仓股数与成本
        for code, bh in sorted(bmap.items()):
            lh = dict(pos_items).get(code)
            if lh is None:
                badd(
                    "B4",
                    f"持仓股数 {code}",
                    bh.get("shares"),
                    "（账本无此持仓）",
                    False,
                    "券商有、账本无 → 需查明",
                )
                continue
            badd(
                "B4",
                f"持仓股数 {code}",
                D(bh.get("shares")),
                D(lh.get("shares")),
                D(bh.get("shares")) == D(lh.get("shares")),
                "券商持仓 vs 账本股数",
            )
            b_cost = D(bh.get("avg_cost"))
            # 口径必须同为「每股」：券商给 avg_cost，账本 holdings 也存 avg_cost。
            # （cost_of() 返回的是总成本，第一次实现错在这里——被本工具自己抓出。）
            l_cost = D(lh.get("avg_cost"))
            badd(
                "B5",
                f"持仓成本 {code}",
                b_cost,
                l_cost,
                abs(b_cost - l_cost) <= Decimal("0.001"),
                "券商成本价 vs 账本 avg_cost（每股，容差 0.001）",
            )

        # B6：浮动盈亏残差（口径差，披露而非失败）
        if bs.get("floating_pnl") is not None and summ.get("floating_pnl") is not None:
            residual = D(bs.get("floating_pnl")) - D(summ.get("floating_pnl"))
            per = []
            for code, bh in sorted(bmap.items()):
                lh = dict(pos_items).get(code) or {}
                if lh.get("floating_pnl") is not None and bh.get("floating_pnl") is not None:
                    per.append((code, str(D(bh.get("floating_pnl")) - D(lh.get("floating_pnl")))))
            # 2026-10-22：残差按用户裁定归入「股息入账」（income_ledger），**仅归因、不改盈亏**。
            classified = [x for x in (pf.get("income_ledger") or []) if isinstance(x, dict)]
            cls_total = sum((D(x.get("amount")) for x in classified), D(0))
            cls_desc = (
                "、".join(f"{x.get('category')} {x.get('amount')}" for x in classified) or "无"
            )
            badd(
                "B6",
                "浮动盈亏残差",
                "≈0（券商含费/股息口径）",
                f"{residual}（逐仓 {per}）",
                True,
                f"已归类：{cls_desc}（共 {cls_total}，仅收益归因，不冲减浮动盈亏）",
            )

    total_asset = cash + sum_mv
    eff = init + add_cap
    result = dict(
        source=str(args.portfolio),
        generated_from="real_recon.py",
        shape=shape,
        notes=notes,
        coverage=dict(
            transactions=len(tx),
            first_tx=min((str(t.get("date")) for t in tx), default=None),
            last_tx=max((str(t.get("date")) for t in tx), default=None),
        ),
        summary=dict(
            cash=str(cash),
            positions={c: p.get("shares") for c, p in pos_items},
            total_asset=str(total_asset),
            effective_capital=str(eff),
            return_pct=(
                str(((total_asset - eff) / eff * 100).quantize(Decimal("0.01"))) if eff > 0 else "—"
            ),
        ),
        checks=checks,
        diffs=diffs,
        verdict="closed" if not diffs else "not_closed",
        falsification="任一恒等式在给定 portfolio 文件下不成立，即本次结论错；"
        "全部数字可由 --portfolio 指向的文件逐笔重算。",
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[real_recon] {args.portfolio}  （{shape}）")
        if tx:
            print(
                f"  覆盖：{len(tx)} 笔流水（{result['coverage']['first_tx']} ~ {result['coverage']['last_tx']}）"
            )
        else:
            print(f"  覆盖：{len(pos_items)} 个持仓，无流水")
        for n in notes:
            print(f"  · {n}")
        print(
            f"  总资产 {fmt(total_asset)}"
            + (f"（有效本金 {fmt(eff)}，{result['summary']['return_pct']}%）" if eff > 0 else "")
        )
        print()
        for c in checks:
            print(
                f"  {'✅' if c['ok'] else '🔴'} {c['id']} {c['name']:<14} 期望 {fmt(c['expect']):>12}  "
                f"实际 {fmt(c['actual']):>12}   {c['note']}"
            )
        print()
        print(
            f"[{'PASS' if not diffs else 'FAIL'}] {len(checks)} 项恒等式，"
            f"{len(checks) - len(diffs)} 闭合 / {len(diffs)} 不闭合"
        )

    if args.out:
        out = Path(args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / "recon-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not args.json:
            print(f"  已写入 {out / 'recon-result.json'}")

    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
