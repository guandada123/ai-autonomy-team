#!/usr/bin/env python3
"""标签敏感性：同一份材料、两轮标签相反，看席位跟着证据走还是跟着标签走。

## 为什么需要它

第三轮 26 题的裁决分布是 22 do : 4 dont，「全答 do」可得 0.846。
根因不是题量，是我挑的题都是「已执行的决策」——执行过自然落在 do 上。

第四轮把 9 道题的 do/dont 括号说明对调（材料、事实、候选动作一字不改），
参考裁决随之翻面，分布变成 13:13。于是同一份材料在两轮里标签相反：

    V2-05 第三轮：`do`（进门禁）          正确 = do
    V2-05 第四轮：`dont`（进门禁）        正确 = dont

同一个动作，两个标签。如果席位答的是**动作**，两轮应当一致；
如果答的是**标签**（见 do 就选），两轮就会不一致。

## 指标

- 动作一致率 = 两轮选中的「动作」相同的比例（defer 视为同一动作）
- 标签跟随 = 两轮选了**同一个标签**而动作相反（只可能发生在 do/dont 之间）
- 动作倒退 = 第三轮动作正确、第四轮动作错误

## 口径提醒

两轮是不同会话、不同批次（第四轮分 A/B 两批），所以「一致」本身就是有价值的信息：
它说明答案不依赖于标签书写方式。反过来，「不一致」不能直接判为能力差——
也可能是第四轮读材料更仔细、发现了第三轮漏掉的点。故本脚本只报事实，不下判决。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FLIP = {"do": "dont", "dont": "do", "defer": "defer"}


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d.get("answers", d)


def verdict(ans):
    if not isinstance(ans, dict):
        return ""
    return str(ans.get("verdict", "")).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser(description="标签敏感性：同材料两轮标签相反的一致性")
    ap.add_argument("--r3", nargs="+", required=True, help="第三轮每席作答（按席对齐）")
    ap.add_argument("--r4", nargs="+", required=True, help="第四轮每席作答（顺序与 --r3 对齐）")
    ap.add_argument("--swapped", default="audit/baseline/v2/adjudication-r4.json",
                    help="第四轮裁决表（含 label_swapped 标记）")
    args = ap.parse_args()

    if len(args.r3) != len(args.r4):
        raise SystemExit("--r3 与 --r4 数量必须一致（按席对齐）")
    doc = json.loads(Path(args.swapped).read_text(encoding="utf-8"))["items"]
    qs = [q for q, v in doc.items() if v.get("label_swapped") and not v.get("withdrawn")]
    if not qs:
        raise SystemExit("裁决表里没有 label_swapped 的题")

    print(f"对调题 {len(qs)} 道：{' '.join(qs)}\n")
    hdr = f"{'席':<6}{'一致':>6}{'标签跟随':>10}{'动作倒退':>10}{'两轮都对':>10}"
    print(hdr)
    print("-" * len(hdr) * 2)

    tot = {"same": 0, "follow": 0, "regress": 0, "both_ok": 0, "n": 0}
    detail = {}
    for p3, p4 in zip(args.r3, args.r4):
        a3, a4 = load(p3), load(p4)
        name = (json.loads(Path(p3).read_text(encoding="utf-8")).get("seat")
                or Path(p3).stem)
        same = follow = regress = both_ok = 0
        rows = {}
        for q in qs:
            v3, v4 = verdict(a3.get(q)), verdict(a4.get(q))
            if v3 not in FLIP or v4 not in FLIP:
                rows[q] = {"r3": v3 or "缺", "r4": v4 or "缺", "note": "缺答"}
                continue
            act4 = FLIP[v4]                     # 还原成第三轮口径的动作
            ok3 = v3 == doc[q]["verdict_original"]
            ok4 = v4 == doc[q]["verdict"]
            s = act4 == v3
            same += s
            if not s and v3 == v4:
                follow += 1
            if ok3 and not ok4:
                regress += 1
            if ok3 and ok4:
                both_ok += 1
            rows[q] = {"r3": v3, "r4": v4, "动作一致": s, "第三轮对": ok3, "第四轮对": ok4}
        n = sum(1 for r in rows.values() if "note" not in r)
        print(f"{name:<6}{same:>5}/{n:<2}{follow:>9}{regress:>11}{both_ok:>11}")
        for k in tot:
            tot[k] += {"same": same, "follow": follow, "regress": regress,
                       "both_ok": both_ok, "n": n}[k]
        detail[name] = rows

    print("-" * len(hdr) * 2)
    print(f"{'合计':<6}{tot['same']:>5}/{tot['n']:<2}{tot['follow']:>9}"
          f"{tot['regress']:>11}{tot['both_ok']:>11}")
    rate = tot["same"] / tot["n"] if tot["n"] else 0
    print(f"\n动作一致率 {rate:.1%}（{tot['same']}/{tot['n']}）")
    if tot["follow"]:
        print(f"⚠️ 标签跟随 {tot['follow']} 例：两轮选了同一个标签，但那个标签在两轮里指相反的动作")
    else:
        print("✅ 无标签跟随：没有任何一例在两轮里选同一个标签而动作相反")
    print("\n注：两轮是不同会话/批次，故「不一致」不等于能力差；本表只报事实。")

    out = Path(args.r4[0]).parent / "label-sensitivity.json"
    out.write_text(json.dumps({"rate": round(rate, 3), "totals": tot, "detail": detail},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"明细已写：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
