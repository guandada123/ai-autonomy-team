#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge_baseline.py —— 单 agent 基线的硬裁判（纯标准库，只读）

判分口径：数值按相对误差 ≤0.5% 判对；文本/短 SHA 精确匹配；
          题目本身有缺陷（口径未声明）的项标 excluded，不计入通过也不计入失败。
"""
import json, sys
from pathlib import Path

GT = {  # 真值全部由测量得到，不取自作答
    "T01": {"kind": "int", "v": 9197, "note": "make_backup 工具口径『9197 个文件』；"
            "tar -tzf 条目口径为 10024（含 827 个目录）——题面未声明口径，判 excluded"},
    "T02": {"kind": "int", "v": 35},
    "T03": {"kind": "float", "v": 121.2, "tol": 0.005},
    "T04": {"kind": "int", "v": 53},
    "T05": {"kind": "int", "v": 53, "note": "以 selftest 实跑『53/53』为准"},
    "T06": {"kind": "pair", "v": [73, 6049]},
    "T07": {"kind": "pair", "v": [36, 18]},
    "T08": {"kind": "float", "v": 9.75, "tol": 0.005},
    "T09": {"kind": "float", "v": 10.90, "tol": 0.005},
    "T10": {"kind": "list", "v": ["6353", "5bba", "972c"]},
}
EXCLUDED = {"T01"}

def near(a, b, tol=0.005):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) <= max(abs(b) * tol, 1e-9)

def judge(ans):
    rows = []
    for k, g in GT.items():
        a = ans.get(k)
        if k in EXCLUDED:
            ok, why = None, "题目口径未声明 → 排除"
        elif g["kind"] == "int":
            ok, why = (a == g["v"]), f"答 {a} / 真 {g['v']}"
        elif g["kind"] == "float":
            ok, why = near(a, g["v"], g.get("tol", 0.005)), f"答 {a} / 真 {g['v']}"
        elif g["kind"] == "pair":
            # ★ 判分脚本自身缺陷（首跑暴露）：原实现只认 list，作答用 dict 也被判错。
            #   数值正确、格式不同 ≠ 答错；改为两种容器都接受，按有序值比对。
            vals = list(a.values()) if isinstance(a, dict) else a
            ok, why = (isinstance(vals, list) and len(vals) == 2
                       and near(vals[0], g["v"][0]) and near(vals[1], g["v"][1])), \
                      f"答 {a} / 真 {g['v']}"
        elif g["kind"] == "list":
            ok, why = (isinstance(a, list) and [str(x).lower() for x in a] == [str(x).lower() for x in g["v"]]), \
                      f"答 {a} / 真 {g['v']}"
        rows.append({"id": k, "ok": ok, "why": why, "note": g.get("note", "")})
    return rows

if __name__ == "__main__":
    ans = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rows = judge(ans)
    n = sum(1 for r in rows if r["ok"] is not None)
    passed = sum(1 for r in rows if r["ok"] is True)
    for r in rows:
        mark = "排除" if r["ok"] is None else ("✅" if r["ok"] else "❌")
        print(f"{mark} {r['id']}  {r['why']}" + (f"  ← {r['note']}" if r["note"] else ""))
    print(f"\n有效样本 n={n}  通过 {passed}  通过率 {passed/n:.3f}")
    if passed < n:
        print(f"（若把排除项 T01 按错计入：{passed}/{n+1} = {passed/(n+1):.3f}）")
