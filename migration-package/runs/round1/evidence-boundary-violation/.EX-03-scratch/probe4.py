# -*- coding: utf-8 -*-
"""EX-03 实测探针 4：用真裁判复现浮点失败 + 字节级可复现性。"""
import os, sys, json, subprocess, hashlib

PY = sys.executable
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FIX = os.path.join(ROOT, "migration-package", "fixtures")
JUDGE = os.path.join(ROOT, "scripts", "judge", "verify.py")
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)))

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

print("== N. 用真裁判复现「无 tol 的浮点失败」 ==")
# 复算：不 round，保留 float 噪声
import csv, collections
def load(p):
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
led, bill = load(os.path.join(FIX, "ledger.csv")), load(os.path.join(FIX, "bill.csv"))
lf = 0.0
for r in led: lf += float(r["amount"])
bf = 0.0
for r in bill: bf += float(r["amount"])
gap_raw = bf - lf
gap_rounded = round(bf - lf, 2)
print("  bill_total(未round) =", repr(bf))
print("  raw_total_gap(未round) =", repr(gap_raw), "| round(...,2) =", gap_rounded)

# answer A：未 round，浮点原样吐出
ans_raw = {"summary": {"bill_total": bf, "raw_total_gap": gap_raw}}
# answer B：round 到两位小数
ans_rnd = {"summary": {"bill_total": gap_rounded and round(bf, 2), "raw_total_gap": gap_rounded}}
# answer C：字符串两位小数
ans_str = {"summary": {"bill_total": f"{bf:.2f}", "raw_total_gap": f"{gap_raw:.2f}"}}

specs = {
    "无tol":   {"task_id": "T", "checks": [
        {"name": "bill_total", "type": "numeric_equals", "path": "summary.bill_total", "expected": 38010.52},
        {"name": "raw_gap", "type": "numeric_equals", "path": "summary.raw_total_gap", "expected": 520.02}]},
    "有tol=0.01": {"task_id": "T", "checks": [
        {"name": "bill_total", "type": "numeric_equals", "path": "summary.bill_total", "expected": 38010.52, "tol": 0.01},
        {"name": "raw_gap", "type": "numeric_equals", "path": "summary.raw_total_gap", "expected": 520.02, "tol": 0.01}]},
}
for sname, spec in specs.items():
    sp = os.path.join(TMP, "_spec.json"); open(sp, "w", encoding="utf-8").write(json.dumps(spec))
    for aname, ans in [("A 未round(float原样)", ans_raw), ("B round(2)", ans_rnd), ("C 字符串2位", ans_str)]:
        ap = os.path.join(TMP, "_ans.json"); open(ap, "w", encoding="utf-8").write(json.dumps(ans))
        r = subprocess.run([PY, JUDGE, "--spec", sp, "--answer", ap], capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        det = "; ".join(f"{c['name']}:{c['ok']}({c['actual']})" for c in out["checks"])
        print(f"  spec[{sname:11s}] answer[{aname:20s}] verdict={out['verdict']:5s} | {det}")
os.remove(os.path.join(TMP, "_spec.json")); os.remove(os.path.join(TMP, "_ans.json"))

print("\n== O. 字节级可复现性：重跑 recon.py vs 已存 baseline（不改动原文件） ==")
src = open(os.path.join(FIX, "recon.py"), encoding="utf-8").read()
regen_out = os.path.join(TMP, "_regen.json")
patched = src.replace('OUT = BASE + "/answer-baseline.json"',
                      'OUT = %r' % regen_out.replace("\\", "/"))
assert patched != src, "OUT 替换失败"
tmp_py = os.path.join(TMP, "_recon_copy.py")
open(tmp_py, "w", encoding="utf-8", newline="\n").write(patched)
r = subprocess.run([PY, tmp_py], cwd=FIX, capture_output=True, text=True, encoding="utf-8")
print("  recon.py(重定向副本) rc =", r.returncode)
print("  审计：原 answer-baseline.json 未被改动 sha256 =", sha(os.path.join(FIX, "answer-baseline.json")))
b1 = open(regen_out, "rb").read()
b3 = open(os.path.join(FIX, "answer-baseline-v3.json"), "rb").read()
print("  重生成(本机)            sha256 =", sha(regen_out))
print("  answer-baseline-v3.json sha256 =", sha(os.path.join(FIX, "answer-baseline-v3.json")))
print("  answer-baseline.json    sha256 =", sha(os.path.join(FIX, "answer-baseline.json")))
print(f"  v3 CRLF数={b3.count(bytes([13]))} | 重生成 CRLF数={b1.count(bytes([13]))}")
j3 = json.loads(b3.decode("utf-8")); j1 = json.loads(b1.decode("utf-8"))
print("  语义相等(剔除falsification)?", j3["summary"] == j1["summary"] and j3["categories"] == j1["categories"])
j0 = json.loads(open(os.path.join(FIX, "answer-baseline.json"), "rb").read().decode("utf-8"))
print("  重生成 vs 存量 answer-baseline.json summary 语义相等?",
      j1["summary"] == j0["summary"] and j1["categories"] == j0["categories"])
print("  重生成 vs 存量 answer-baseline.json 字节完全一致?",
      open(regen_out, "rb").read() == open(os.path.join(FIX, "answer-baseline.json"), "rb").read())

print("\n== P. 确定性：PYTHONHASHSEED 对集合/字典顺序的影响 ==")
for seed in ["0", "1", "42", "random"]:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    r = subprocess.run([PY, "-c",
        "import collections,json;s=sorted({'T101','T102','T103'});"
        "d=collections.OrderedDict();[d.setdefault(c,[]).append(i) for c,i in [('舍入差','T101'),('口径差异','T104')]];"
        "print(json.dumps(d,ensure_ascii=False),s)"],
        capture_output=True, text=True, encoding="utf-8", env=env)
    print(f"  PYTHONHASHSEED={seed:7s} -> {r.stdout.strip()}")
