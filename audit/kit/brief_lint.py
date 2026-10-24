#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brief_lint.py —— 材料包准入检查（防「题目里已经写着答案」）。

## 为什么需要它

v3 首轮四席在 4 道题上一致地不同于用户裁决。我最初怀疑是「事实」段把成本那面写重了，
但消融实验（删掉整段事实）反而让**对照组**翻得更多（3/6），争议组只翻 1/4 ——
说明问题不在「事实」，而在**「情形」段已经含有结论**。

三处实证：

    Q04 情形：「v2 判断类题库已定『停止扩张』(D-0063)」
         → 把「要不要封存」偷换成「怎么封存」，读者自然会选「移走」
    Q09 情形：「此前 v2 的参考裁决全部来自我，结果是天花板（四臂 26/26）」
         → 这句话本身就是选项 A 的论证
    Q06 情形：列出「L-0021 全员满分」作为示例
         → 提前暗示这批经验有价值，指向「分享」

这三处都不是笔误，是**我在提问时顺手把理由也写进去了**。

## 六条规则

| 编号 | 检查 | 级别 | 为什么 |
|---|---|---|---|
| R1 | 情形段含结论词（说明/意味着/因此/等于/结果是/已定） | FAIL | 题目在替读者下结论 |
| R2 | 情形段引用决策编号（D-）/经验编号（L-） | FAIL | 读者会去查那条记录的结论，等于给答案 |
| R3 | 事实段含评价词（可观/不划算/太重/负担/显然/必须） | FAIL | 事实应中性，褒贬是判断 |
| R4 | 事实段含结论词 | WARN | 轻度的推理混入事实 |
| R5 | 某选项说明比最长选项短 40% 以上 | WARN | 描述不对称会引导选择 |
| R6 | 缺「情形 / 事实 / 选项」三段之一 | FAIL | 结构不全 |

## 用法

    python3 audit/kit/brief_lint.py audit/baseline/v3/materials
    python3 audit/kit/brief_lint.py <dir> --strict     # WARN 也算失败

退出码：0 全过 ｜ 1 有 FAIL（或 --strict 下有 WARN） ｜ 2 路径错
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ★ 词表固定，不含主观判断。改词表要改版本号，否则历史可比性丢失。
CONCL = ["说明", "意味着", "因此", "所以", "等于", "结果是", "已定", "已证明", "无法区分"]
EVAL = ["可观", "不划算", "太重", "负担", "显然", "必须", "隐患", "污染", "代价过高"]
# 决策/经验编号：读者能拿去查结论，等于把答案留在题面上
IDREF = re.compile(r"\b[DWL]-\d{3,4}\b")


def sections(text: str) -> dict[str, str]:
    """按 ## 切成段。缺的段返回空串。"""
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            # 段名常带括号说明（「## 选项（选一个）」）。此前按精确名匹配，
            # 结果 10 份全部误报「缺选项段」——两个口径不一致，正中本项目老毛病。
            # 取括号前的部分作为规范名。
            cur = re.sub(r"[（(].*?[)）]", "", line[3:]).strip()
            out[cur] = ""
        elif cur:
            out[cur] += line + "\n"
    return out


def lint_one(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    sec = sections(text)
    q = path.parent.name
    issues = []

    def add(rule, level, msg):
        issues.append({"q": q, "rule": rule, "level": level, "msg": msg})

    for need in ("情形", "事实", "选项"):
        if not sec.get(need, "").strip():
            add("R6", "FAIL", f"缺「## {need}」段")

    sit = sec.get("情形", "")
    fact = sec.get("事实", "")

    hit = [w for w in CONCL if w in sit]
    if hit:
        add("R1", "FAIL", f"情形段含结论词 {hit} —— 情形只该写情境，不该写推论")

    ids = sorted(set(IDREF.findall(sit)))
    if ids:
        add("R2", "FAIL", f"情形段引用编号 {ids} —— 读者可循编号查到你当时的结论")

    hit = [w for w in EVAL if w in fact]
    if hit:
        add("R3", "FAIL", f"事实段含评价词 {hit} —— 事实应中性")

    hit = [w for w in CONCL if w in fact]
    if hit:
        add("R4", "WARN", f"事实段含结论词 {hit} —— 轻度推理混入事实")

    opts = re.findall(r"^- `([^`]+)`\s*(.+)$", sec.get("选项", ""), re.M)
    if len(opts) < 2:
        add("R6", "FAIL", "选项少于 2 个")
    else:
        lens = {k: len(v.strip()) for k, v in opts}
        lo, hi = min(lens.values()), max(lens.values())
        if hi and lo < hi * 0.6:
            short = [k for k, v in lens.items() if v == lo]
            add("R5", "WARN", f"选项 {short} 描述明显短于其他（{lo} vs {hi} 字）")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description="材料包准入检查：防题面里写着答案")
    ap.add_argument("root", help="含 */brief.md 的目录")
    ap.add_argument("--strict", action="store_true", help="WARN 也算失败")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    files = sorted(root.glob("*/brief.md"))
    if not files:
        print(f"[ERR] {root} 下没有 */brief.md", file=sys.stderr)
        return 2

    all_issues = []
    for f in files:
        all_issues += lint_one(f)

    fails = [i for i in all_issues if i["level"] == "FAIL"]
    warns = [i for i in all_issues if i["level"] == "WARN"]

    if args.json:
        import json as _j
        print(_j.dumps({"files": len(files), "issues": all_issues,
                        "fail": len(fails), "warn": len(warns)},
                       ensure_ascii=False, indent=2))
    else:
        print(f"[brief-lint] 检查 {len(files)} 份 ｜ FAIL {len(fails)} ｜ WARN {len(warns)}")
        for i in fails + warns:
            mark = "🔴" if i["level"] == "FAIL" else "🟡"
            print(f"  {mark} {i['q']} {i['rule']}: {i['msg']}")
        if not fails and not warns:
            print("  ✅ 全部通过")
        print("\n★ 通过 lint ≠ 题目有效。它只查「题面里有没有写着答案」，"
              "查不了「题有没有区分度」——后者归 item_gate。")

    if fails:
        return 1
    return 1 if (args.strict and warns) else 0


if __name__ == "__main__":
    raise SystemExit(main())
