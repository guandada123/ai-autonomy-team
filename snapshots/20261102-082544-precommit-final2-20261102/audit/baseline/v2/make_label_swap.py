#!/usr/bin/env python3
"""生成 v2 第四轮的「标签对调」题面（brief-swap.md）与对应裁决表。

## 为什么这样做

第三轮（26 题）暴露的问题不是题量，是**分布**：22 do : 4 dont。
「全答 do」可得 22/26 = 0.846，两臂的 0.962 只比盲答高 11.6pp。
根因是选材偏斜——我挑的都是「已执行的决策」，执行过自然落在 do 上。

## 做法

不改题面、不改事实、不改候选动作，只把每题末尾 `do` / `dont` 两个标签的
**括号说明对调**。于是「历史上真正发生的那个动作」从 do 挪到 dont：

    V2-05 原：`do`（进门禁）/ `dont`（保持现状，只写在文档里）
    V2-05 换：`do`（保持现状，只写在文档里）/ `dont`（进门禁）

参考裁决随之翻成 dont，分布变成 13 do : 13 dont（对调 9 道）。

## 副产品

同一份材料、两轮标签相反 → 可以直接测「席位是跟着标签答，还是跟着证据答」。
这就是 label_sensitivity.py 要算的东西。

## 约束

- 只对 `verdict == "do"` 的题做对调，且必须显式列在 SWAP 里（不自动挑）。
- 除选项块外**一个字节都不许动**；脚本自己校验这一点并拒绝写坏文件。
- 输出 brief-swap.md，原 brief.md 保留（第三轮结果仍要能复算）。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
MAT = HERE / "materials"

# 挑 9 道：覆盖 CI / 验收 / 门禁 / 静默失败 / 备份 / 流程，且 do-dont 是可对仗的一对
SWAP = ["V2-05", "V2-06", "V2-08", "V2-11", "V2-12", "V2-13", "V2-19", "V2-24", "V2-25"]

RE_DO = re.compile(r"`do`（([^）]*)）")
RE_DONT = re.compile(r"`dont`（([^）]*)）")


def swap_options(text: str) -> str:
    """只换选项块里两个括号的内容，其余原样返回。"""
    head, sep, tail = text.partition("## 请你裁")
    if not sep:
        raise SystemExit("找不到「## 请你裁」段落")
    m_do, m_dont = RE_DO.search(tail), RE_DONT.search(tail)
    if not (m_do and m_dont):
        raise SystemExit("选项块格式不符（缺 do 或 dont 的括号说明）")
    a, b = m_do.group(1), m_dont.group(1)
    new_tail = RE_DO.sub(lambda _: f"`do`（{b}）", tail, count=1)
    new_tail = RE_DONT.sub(lambda _: f"`dont`（{a}）", new_tail, count=1)
    return head + sep + new_tail


def check_only_options_changed(old: str, new: str) -> None:
    """逐行比对：不许出现选项块之外的改动。"""
    o, n = old.splitlines(), new.splitlines()
    if len(o) != len(n):
        raise SystemExit("行数变了，拒绝写盘")
    diff = [(x, y) for x, y in zip(o, n) if x != y]
    if len(diff) > 3:
        raise SystemExit(f"改动行数 {len(diff)} > 3，拒绝写盘")
    for x, y in diff:
        if "`do`" not in x and "`dont`" not in x:
            raise SystemExit(f"改到了选项块之外的行：{x[:40]}")


def main() -> int:
    doc_p = HERE / "adjudication.json"
    doc = json.loads(doc_p.read_text(encoding="utf-8"))
    items = doc["items"]

    missing = [q for q in SWAP if q not in items]
    if missing:
        raise SystemExit(f"裁决表里没有这些题：{missing}")
    bad = [q for q in SWAP if items[q]["verdict"] != "do"]
    if bad:
        raise SystemExit(f"这些题原本不是 do，对调前提不成立：{bad}")

    r4 = {
        "_note": (
            "v2 第四轮裁决表。由 make_label_swap.py 从 adjudication.json 生成："
            "9 道题的 do/dont 括号说明对调，参考裁决随之翻面，用于消除 22:4 的标签偏斜。"
        ),
        "items": {},
    }
    for q, v in items.items():
        item = dict(v)
        item.setdefault("material", f"materials/{q}/brief.md")
        if q in SWAP:
            src = MAT / q / "brief.md"
            old = src.read_text(encoding="utf-8")
            new = swap_options(old)
            check_only_options_changed(old, new)
            (MAT / q / "brief-swap.md").write_text(new, encoding="utf-8")
            item["material"] = f"materials/{q}/brief-swap.md"
            item["label_swapped"] = True
            item["verdict_original"] = v["verdict"]
            item["verdict"] = "dont" if v["verdict"] == "do" else "do"
        r4["items"][q] = item

    out = HERE / "adjudication-r4.json"
    out.write_text(json.dumps(r4, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    act = [v for v in r4["items"].values() if not v.get("withdrawn")]
    from collections import Counter

    c = Counter(v["verdict"] for v in act)
    print(f"对调 {len(SWAP)} 题 → 分布 {dict(c)}（合计 {len(act)}）")
    print(f"写盘：{out.relative_to(HERE)} 与 {len(SWAP)} 份 brief-swap.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
