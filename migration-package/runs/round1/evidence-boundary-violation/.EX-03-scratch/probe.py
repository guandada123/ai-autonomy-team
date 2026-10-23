# -*- coding: utf-8 -*-
"""EX-03 实测探针：复算 + 浮点风险 + 字节级跨平台检查。纯标准库。"""
import csv, collections, sys, io, os, platform
from decimal import Decimal

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "migration-package", "fixtures")

print("== ENV ==")
print("python", sys.version.split()[0], "| platform", platform.platform())
print("sys.getdefaultencoding()", sys.getdefaultencoding())
print("sys.getfilesystemencoding()", sys.getfilesystemencoding())
print("locale preferred encoding", io.TextIOWrapper(io.BytesIO()).encoding)

print("\n== BYTES ==")
for p in ["ledger.csv", "bill.csv", "truth.json", "recon.py"]:
    fp = os.path.join(BASE, p)
    b = open(fp, "rb").read()
    print(f"{p:12s} bytes={len(b):5d} CRLF={b.count(bytes([13,10])):4d} loneLF={b.count(bytes([10]))} BOM={b[:3]==b'\\xef\\xbb\\xbf'}")

def load(p):
    with open(os.path.join(BASE, p), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

led = load("ledger.csv"); bill = load("bill.csv")
print("\n== RAW ==")
print("ledger_rows", len(led), "bill_rows", len(bill))

# ---- float accumulation vs Decimal ----
def fsum(rows):
    t = 0.0
    for r in rows:
        t += float(r["amount"])
    return t

def dsum(rows):
    t = Decimal("0")
    for r in rows:
        t += Decimal(r["amount"])
    return t

lf, bf = fsum(led), fsum(bill)
ld, bd = dsum(led), dsum(bill)
print("ledger_total float   ", repr(lf), "=?", round(lf, 2))
print("ledger_total Decimal ", ld)
print("bill_total   float   ", repr(bf), "=?", round(bf, 2))
print("bill_total   Decimal ", bd)
print("gap float", repr(bf - lf), "| gap Decimal", bd - ld)
print("float==Decimal(2dp)?", round(lf,2) == float(ld), round(bf,2) == float(bd))

# ---- 逐条 abs 差额的 float/Decimal 对照 ----
print("\n== DIFF AMOUNTS (float vs Decimal) ==")
led_by = collections.defaultdict(list); bill_by = collections.defaultdict(list)
for r in led: led_by[r["txn_id"]].append(r)
for r in bill: bill_by[r["txn_id"]].append(r)
ids = sorted(set(led_by) | set(bill_by))
dup = sorted([k for k, v in bill_by.items() if len(v) > 1])
print("dup_ids", dup)
mismatch = []
for k in ids:
    L = led_by.get(k, []); B = bill_by.get(k, [])
    if L and B and k not in dup:
        la, ba = float(L[0]["amount"]), float(B[0]["amount"])
        ladec, badec = Decimal(L[0]["amount"]), Decimal(B[0]["amount"])
        fgap = abs(la - ba); dgap = abs(ladec - badec)
        if la != ba or L[0]["tax_included"] != B[0]["tax_included"] or L[0]["date"] != B[0]["date"]:
            flag = "FLOAT!=DEC" if round(fgap,2) != float(dgap) else "ok"
            print(f"  {k} ledger={L[0]['amount']} bill={B[0]['amount']} taxL={L[0]['tax_included']} taxB={B[0]['tax_included']} dateL={L[0]['date']} dateB={B[0]['date']} abs_gap float={fgap!r} dec={dgap} [{flag}]")
print("  T107 (台账有账单无):", led_by["T107"][0]["amount"], "| 账单无")
print("  T103 dup rows:", [r["amount"] for r in bill_by["T103"]], "-> surplus", [r["amount"] for r in bill_by["T103"]][1:])

# ---- 关键：float 累加 500.02 (2500.01-2500.00)+(3210.51-3210.50)+500 ----
print("\n== FLOAT EDGE ==")
a = 2500.01 - 2500.00
b = 3210.51 - 3210.50
print("2500.01-2500.00 =", repr(a))
print("3210.51-3210.50 =", repr(b))
print("a+b =", repr(a+b), "== 0.02 ?", a+b == 0.02)
print("round(a+b,2) =", round(a+b,2))
print("Decimal:", Decimal('2500.01')-Decimal('2500.00') + Decimal('3210.51')-Decimal('3210.50'))

# 0.1 经典
print("0.1+0.2 =", repr(0.1+0.2), "==0.3?", 0.1+0.2==0.3)
print("sum([0.1]*10) =", repr(sum([0.1]*10)))

# 规模放大：10000 行 0.01 累加
big = sum([0.01]*10000)
print("sum([0.01]*10000) =", repr(big), "expected 100.0")

# ---- 判定：浮点累加是否与两位小数不一致 ----
print("\n== VERDICT ==")
print("ledger 用 float 累加结果 == 两位小数(Decimal 结果)?", f"{lf:.2f}" == str(ld))
print("bill   用 float 累加结果 == 两位小数(Decimal 结果)?", f"{bf:.2f}" == str(bd))
