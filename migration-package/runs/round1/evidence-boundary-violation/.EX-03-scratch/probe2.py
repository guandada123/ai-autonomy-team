# -*- coding: utf-8 -*-
"""EX-03 实测探针 2：真实执行 baseline + 跨平台失败模式复现。"""
import subprocess, sys, os, io, csv, platform, json, locale

PY = sys.executable
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "migration-package", "fixtures")
BASE = os.path.abspath(BASE)

print("== A. 默认编码 / UTF-8 模式 ==")
print("locale.getpreferredencoding(False) =", locale.getpreferredencoding(False))
print("sys.flags.utf8_mode =", sys.flags.utf8_mode)
print("open() 默认 encoding =", io.TextIOWrapper(io.BytesIO()).encoding)

print("\n== B. 直接执行 fixtures/recon.py（原样） ==")
r = subprocess.run([PY, "recon.py"], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
print("rc =", r.returncode)
print("stdout 前 6 行:")
for line in r.stdout.splitlines()[:6]:
    print("   ", line)
if r.stderr.strip():
    print("stderr:", r.stderr.strip()[:400])

print("\n== C. 模拟 macOS：把 recon.py 换成相对路径后执行 ==")
src = open(os.path.join(BASE, "recon.py"), encoding="utf-8").read()
patched = src.replace('BASE = "C:/Users/Administrator/WorkBuddy/2026-10-16-14-46-19/migration-package/fixtures"',
                      'BASE = os.path.dirname(os.path.abspath(__file__))').replace(
                      "import csv, json, collections", "import csv, json, collections, os")
tmp = os.path.join(BASE, "_recon_relpath.py")
open(tmp, "w", encoding="utf-8", newline="\n").write(patched)
r2 = subprocess.run([PY, tmp], cwd="/tmp" if os.name != "nt" else "C:/Windows/Temp",
                    capture_output=True, text=True, encoding="utf-8")
print("rc =", r2.returncode, "(cwd 故意换成与脚本无关的目录，模拟另一台机器)")
print("stdout 前 4 行:")
for line in r2.stdout.splitlines()[:4]:
    print("   ", line)
print("stderr:", (r2.stderr or "").strip()[:400])
os.remove(tmp)

print("\n== D. 原样 recon.py 在非 Windows 根路径下（模拟 mac 无 C:/） ==")
bad = os.path.join(BASE, "_recon_badpath.py")
open(bad, "w", encoding="utf-8", newline="\n").write(src)
# 用 sed 思路无法跨平台；直接直接跑，若 C:/ 不存在则报错。此处演示异常捕获
try:
    with open("C:/Users/Administrator/WorkBuddy/2026-10-16-14-46-19/migration-package/fixtures/ledger.csv", "rb") as f:
        pass
    print("本机 C:/... 路径存在（Windows 验证机），故原样可跑；mac 上该路径必然不存在")
except FileNotFoundError as e:
    print("FileNotFoundError:", e)
os.remove(bad)

print("\n== E. 换行风险：CRLF 文件 + 未指定 newline='' ==")
crlf = os.path.join(BASE, "_crlf_probe.csv")
data = "txn_id,amount\nT001,10.00\n"
open(crlf, "wb").write(data.replace("\n", "\r\n").encode("utf-8"))
with open(crlf, "r", encoding="utf-8") as f:              # 未指定 newline
    rows_bad = list(csv.DictReader(f))
with open(crlf, "r", encoding="utf-8", newline="") as f:  # 指定 newline
    rows_ok = list(csv.DictReader(f))
print("未指定 newline -> 末列 repr:", repr(rows_bad[0]["amount"]), "| 能否 float()?",
      end=" ")
try:
    float(rows_bad[0]["amount"]); print("能")
except ValueError as e:
    print("不能 ->", type(e).__name__, e)
print("指定 newline='' -> 末列 repr:", repr(rows_ok[0]["amount"]))
os.remove(crlf)

print("\n== F. 浮点判定：无 tol 直接比 == 会怎样 ==")
a = 2500.01 - 2500.00; b = 3210.51 - 3210.50; c = 500.00
total = a + b + c
print("未 round 的 amount_diff_total =", repr(total))
print("直接 == 500.02       ->", total == 500.02)
print("round(total,2) == 500.02 ->", round(total, 2) == 500.02)
print("abs(total-500.02) <= 0.01 (judge 的 tol 逻辑) ->", abs(total - 500.02) <= 0.01)

print("\n== G. 解释器可用性（本机） ==")
for cmd in ["python3", "python"]:
    rr = subprocess.run([cmd, "-V"], capture_output=True, text=True, shell=True)
    print(f"  {cmd:8s} -> rc={rr.returncode} {(rr.stdout or rr.stderr).strip()}")
