# -*- coding: utf-8 -*-
"""EX-03 实测探针 3：newline 语义、编码环境、3.9 兼容性静态扫描、输出字节一致性。"""
import os, sys, csv, io, subprocess, re, ast

PY = sys.executable
BASE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "migration-package", "fixtures"))
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

print("== H. 环境变量编码开关 ==")
for k in ["PYTHONUTF8", "PYTHONIOENCODING", "PYTHONLEGACYWINDOWSSTDIO", "LANG", "LC_ALL", "PYTHONHASHSEED"]:
    print(f"  {k} = {os.environ.get(k)!r}")

print("\n== I. 引号内嵌换行：newline='' 的必要性 ==")
p = os.path.join(BASE, "_q.csv")
open(p, "wb").write(b'txn_id,note\nT001,"line1\nline2"\nT002,ok\n')
for kw in [{}, {"newline": ""}]:
    with open(p, "r", encoding="utf-8", **kw) as f:
        rows = list(csv.DictReader(f))
    print(f"  newline={kw.get('newline','<未指定>')!r:8s} -> 行数={len(rows)} note[0]={rows[0]['note']!r}")
os.remove(p)

print("\n== J. 输出字节：Windows newline 默认 vs newline='\\n' ==")
for kw, tag in [({}, "默认(Windows->CRLF)"), ({"newline": "\n"}, "newline='\\n'")]:
    fp = os.path.join(BASE, f"_out_{tag[:4]}.txt")
    with open(fp, "w", encoding="utf-8", **kw) as f:
        f.write("a\nb\n")
    b = open(fp, "rb").read()
    import hashlib
    print(f"  {tag:22s} bytes={b!r} sha256={hashlib.sha256(b).hexdigest()[:16]}")
    os.remove(fp)

print("\n== K. 3.9 兼容性静态扫描（3.10+/语法新特性） ==")
targets = [os.path.join(BASE, "recon.py"),
           os.path.join(ROOT, "scripts", "judge", "verify.py")]
NEW = {ast.Match: "match 语句(3.10+)", ast.TryStar: "except*(3.11+)"}
for t in targets:
    if not os.path.exists(t):
        print(f"  {t} 不存在，跳过"); continue
    src = open(t, encoding="utf-8").read()
    tree = ast.parse(src)
    hits = [type(n).__name__ for n in ast.walk(tree) if type(n) in NEW]
    # 二进制 | 类型联合注解
    union_ann = re.findall(r"->\s*\w+\s*\|\s*\w+", src)
    fstr = "f-string" if re.search(r"f['\"]", src) else "无 f-string"
    print(f"  {os.path.basename(t)}: 3.10+节点={hits or '无'} | 注解联合(|)={union_ann or '无'} | {fstr}")
    # 尝试以最低版本语义编译
    try:
        compile(src, t, "exec")
        print(f"     compile() 通过（本机 {sys.version.split()[0]}）")
    except SyntaxError as e:
        print(f"     SyntaxError: {e}")

print("\n== L. 尾随换行 / 空行 -> 行数定义敏感点 ==")
for name in ["ledger.csv", "bill.csv"]:
    raw = open(os.path.join(BASE, name), "rb").read()
    print(f"  {name}: 末尾字节={raw[-3:]!r} 以\\n结尾={raw.endswith(bytes([10]))}")

print("\n== M. 输出文件（baseline）字节与内容 ==")
import hashlib, json
for f in ["answer-baseline.json", "answer-baseline-v2.json", "answer-baseline-v3.json"]:
    fp = os.path.join(BASE, f)
    if os.path.exists(fp):
        b = open(fp, "rb").read()
        try:
            j = json.loads(b.decode("utf-8"))
            s = j.get("summary", {})
            print(f"  {f}: len={len(b)} CRLF={b.count(bytes([13]))} keys={list(j.keys())}")
            print(f"      summary={ {k: s.get(k) for k in ['ledger_rows','bill_rows','dedup_amount','amount_diff_total','excluded_amount_total']} }")
        except Exception as e:
            print(f"  {f}: {e}")
