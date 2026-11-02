#!/usr/bin/env python3
"""校验题目的「真值字母」有没有指错选项。

为什么需要：2026-11-01 两小时内犯了两次同一个错——改选项文案/打散答案后，
`expected` 字母没跟着走，结果「真值」指向了干扰项。肉眼核对靠不住，必须机器卡。

判据（两条，缺一即红）：
  1. 每题必须声明 `expected_stance`（一句能唯一定位正确立场的关键词）；
  2. 该关键词必须出现在 `options[expected]` 里，且**不得**出现在其他选项里
     （出现在别处 = 两个选项表达同一立场，题面不互斥，属欠定）。

用法：
    python3 audit/kit/check_stance.py audit/baseline/v2/runs/real-incident-4.json
退出码：0=全部一致；1=有指错或重复立场；2=文件/结构错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def check_batch(data: dict) -> tuple[list[str], list[str]]:
    """返回 (错误, 提示)。纯函数便于单测。"""
    errs: list[str] = []
    notes: list[str] = []
    items = data.get("items") or []
    if not items:
        return ["批次无 items"], notes

    for it in items:
        qid = it.get("qid", "?")
        opts = it.get("options") or {}
        exp = it.get("expected")
        stance = (it.get("expected_stance") or "").strip()

        if not stance:
            errs.append(f"{qid}: 缺 expected_stance（无法证明真值字母指向正确立场）")
            continue
        if exp not in opts:
            errs.append(f"{qid}: expected='{exp}' 不在选项 {sorted(opts)} 中")
            continue
        if stance not in opts[exp]:
            errs.append(f"{qid}: 立场词「{stance}」不在 expected({exp}) 的文本里——字母指错了选项")
            continue
        dup = [k for k, v in opts.items() if k != exp and stance in v]
        if dup:
            errs.append(
                f"{qid}: 立场词「{stance}」同时出现在 {dup}——两选项同立场，题面不互斥（欠定）"
            )

        # 附加提示：选项长度（只提示，不判死——长度阈值由 item_review 的 C3 负责）
        lens = {k: len(v) for k, v in opts.items()}
        if lens:
            mx, mn = max(lens.values()), min(lens.values())
            if mn and mx / mn > 1.5:
                notes.append(f"{qid}: 选项长度比 {mx / mn:.2f} 偏大，交 item_review C3 复核")
    return errs, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="校验真值字母是否指向正确立场")
    ap.add_argument("batch", help="批次 JSON 路径")
    args = ap.parse_args()

    p = Path(args.batch)
    if not p.exists():
        print(f"🔴 文件不存在：{p}", file=sys.stderr)
        return 2
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"🔴 不可读：{type(e).__name__}", file=sys.stderr)
        return 2

    errs, notes = check_batch(data)
    for n in notes:
        print(f"  ⚠️  {n}")
    if errs:
        for e in errs:
            print(f"  🔴 {e}")
        print(f"🔴 {len(errs)} 项不合规")
        return 1
    print(f"✅ {len(data.get('items', []))} 题：真值字母均指向声明立场，且立场唯一")
    return 0


if __name__ == "__main__":
    sys.exit(main())
