# -*- coding: utf-8 -*-
import csv, json, collections

BASE = "C:/Users/Administrator/WorkBuddy/2026-10-16-14-46-19/migration-package/fixtures"
LED = BASE + "/ledger.csv"
BILL = BASE + "/bill.csv"
OUT = 'C:/Users/Administrator/WorkBuddy/2026-10-16-14-46-19/_scratch_ex03/_regen.json'

def load(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

led = load(LED)
bill = load(BILL)

ledger_rows = len(led)
bill_rows = len(bill)

led_by = collections.defaultdict(list)
bill_by = collections.defaultdict(list)
for r in led:
    led_by[r["txn_id"]].append(r)
for r in bill:
    bill_by[r["txn_id"]].append(r)

ids = sorted(set(led_by) | set(bill_by))

# ---- dedup: bill ids appearing more than once ----
dup_ids = sorted([k for k, v in bill_by.items() if len(v) > 1])
dedup_removed_txn_count = len(dup_ids)
dedup_amount = 0.0
for k in dup_ids:
    rows = bill_by[k]
    amts = [float(r["amount"]) for r in rows]
    # amount removed = surplus rows beyond the first occurrence
    surplus = amts[1:]
    dedup_amount += sum(surplus)

diffs = []
item_amount = {}  # id -> abs amount gap used for totals

for k in ids:
    lrows = led_by.get(k, [])
    brows = bill_by.get(k, [])
    if k in dup_ids:
        # surplus rows removed
        amts = [float(r["amount"]) for r in brows]
        item_amount[k] = round(sum(amts[1:]), 2)
        diffs.append({"id": k, "category": "重复导入"})
        continue
    if lrows and brows:
        l = lrows[0]; b = brows[0]
        la = float(l["amount"]); ba = float(b["amount"])
        if abs(la - ba) > 1e-9:
            if l["tax_included"] != b["tax_included"]:
                cat = "税务口径差异"
            elif abs(la - ba) <= 0.01 + 1e-9:
                cat = "舍入差"
            else:
                cat = "金额不符"
            item_amount[k] = round(abs(la - ba), 2)
            diffs.append({"id": k, "category": cat})
        elif l["date"] != b["date"]:
            item_amount[k] = 0.0
            diffs.append({"id": k, "category": "跨月归属"})
    elif lrows and not brows:
        item_amount[k] = round(float(lrows[0]["amount"]), 2)
        diffs.append({"id": k, "category": "真实缺失"})
    elif brows and not lrows:
        item_amount[k] = round(float(brows[0]["amount"]), 2)
        diffs.append({"id": k, "category": "账单多余"})

COUNTED = {"舍入差", "真实缺失", "金额不符", "账单多余"}
EXCLUDED = {"税务口径差异", "跨月归属", "重复导入"}

amount_diff_total = round(sum(item_amount[d["id"]] for d in diffs if d["category"] in COUNTED), 2)
excluded_amount_total = round(sum(item_amount[d["id"]] for d in diffs if d["category"] in EXCLUDED), 2)

categories = collections.OrderedDict()
for d in sorted(diffs, key=lambda x: x["id"]):
    categories.setdefault(d["category"], []).append(d["id"])

excluded_categories = [c for c in categories if c in EXCLUDED]

# diagnostics
led_total = round(sum(float(r["amount"]) for r in led), 2)
bill_total = round(sum(float(r["amount"]) for r in bill), 2)

answer = {
    "summary": {
        "ledger_rows": ledger_rows,
        "bill_rows": bill_rows,
        "dedup_removed_txn_count": dedup_removed_txn_count,
        "dedup_amount": round(dedup_amount, 2),
        "diff_count": len(diffs),
        "amount_diff_total": amount_diff_total,
        "excluded_amount_total": excluded_amount_total,
        "excluded_categories": excluded_categories,
    },
    "diffs": [{"id": d["id"], "category": d["category"]} for d in sorted(diffs, key=lambda x: x["id"])],
    "categories": {k: v for k, v in categories.items()},
    "falsification": (
        "本结论在以下任一条件成立时即为错误：(1) 若台账与账单的 amount 并非同一计量口径"
        "（例如台账为不含税额而 tax_included 字段不可信），则不能把 T104/T105 视为'同一经济价值、"
        "仅含税/不含税表述不同'而排除——需先确认 2120/2000 与 1060/1000 是否恰为同一笔业务在 6% 税率下的含税/不含税换算；"
        "若不是同一笔业务，则这 180 元应计入金额差异。(2) 若账单中 T103 的第二行是独立的真实交易而非重复导入，"
        "则 dedup_removed_txn_count 应为 0、dedup_amount 应为 0，且该 1200 元应计入 amount_diff_total。"
        "(3) 若不设金额容忍度（即把舍入差也视为须追缴的真实差异），则 amount_diff_total 会因 T101/T102 增加 0.02 元；"
        "反之若把舍入差视为可容忍项，则 amount_diff_total 应减少 0.02 元。(4) 若 T106 的 4800 元在 10 月账单中属于"
        "跨期真实入账（即应按权责发生制重分类而非仅标注日期差异），则它也需进入金额差异处理。"
    ),
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(answer, f, ensure_ascii=False, indent=2)

print("ledger_rows", ledger_rows, "bill_rows", bill_rows)
print("ledger_total", led_total, "bill_total", bill_total, "gap", round(bill_total - led_total, 2))
print("dup_ids", dup_ids, "dedup_amount", dedup_amount)
print("item_amount", item_amount)
print("amount_diff_total", amount_diff_total)
print("excluded_amount_total", excluded_amount_total)
print("excluded_categories", excluded_categories)
print("categories", json.dumps(categories, ensure_ascii=False))
print("diffs", json.dumps(answer["diffs"], ensure_ascii=False))
