#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""caliber_lib.py —— 口径库校验 + 审查报告「口径证据充分性」审计。

## 为什么需要

口径库是审查员举证的**唯一证据源**（D-0085 转岗后）：一旦库里 id 重复、
来源文件消失、例外没写 overrides，审查员就会引到不存在的规则，或把例外
当成普适原则泛化。上一轮（二审 0 改动/75）的教训正是"审查员没有可引用的口径"，
所以这条链必须自己也有闸门——**没有校验的证据源，等于没有证据源**。

## 两个模式

1. `--check`：校验口径库自身
   - id 唯一且格式 `C-\\d{3}`/`E-\\d{3}`/`M-\\d{3}`
   - type ∈ {principle, exception, meta}，且 id 前缀与 type 一致
   - 必填字段齐全（statement / rationale / applies_to / source / date）
   - `source` 里的文件路径存在（`#ADJ-00x` 锚点忽略）
   - `overrides` 必须指向存在且 type=principle 的条目
   - `precedence_rule` 与「exception 优先于 principle」一致
   - 反泄露：不得出现「用户裁A/D」这类答案字母复述

2. `--audit-review R1.json [R2.json ...]`：审计审查报告
   - 每条 finding 的 `caliber_refs` 非空、id 均存在
   - 引用 exception 的条目在 `exception_hit` 或 detail 里写明范围（M-002）
   - 缺引用 → 判「口径证据不足」，exit 1

退出码：0 通过 ｜ 1 有不足项/库不合格 ｜ 2 输入不可读
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "audit/baseline/v2/caliber-library.json"
ID_RE = {"principle": r"C-\d{3}", "exception": r"E-\d{3}", "meta": r"M-\d{3}"}
REQUIRED = ("id", "type", "statement", "rationale", "applies_to", "source", "date")
LEAK_RE = re.compile(r"用户裁\s*[A-D]|预注册\s*[A-D]\s*→\s*用户裁")


PATH_RE = re.compile(r"[\w./-]+\.(?:md|json|py)")


def _src_exists(src: str) -> tuple[bool, str]:
    """source 可能含「§6」「+」「（说明）」等多个路径，逐个核对；一个都提不出即不合格。"""
    paths = PATH_RE.findall(src)
    if not paths:
        return False, f"source 未含可追溯的文件路径：{src[:40]}"
    missing = [
        p
        for p in paths
        if not (ROOT / p).exists() and not (ROOT / "migration-package" / p).exists()
    ]
    if missing:
        return False, f"source 指向的文件不存在：{missing}"
    return True, ""


def check_library(path: Path = LIB) -> tuple[list[str], list[str]]:
    errs: list[str] = []
    warns: list[str] = []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # 读库失败必须报错，不能静默当空库
        return [f"口径库不可读：{type(e).__name__}: {e}"], []
    entries = d.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["口径库 entries 缺失或为空（空库不可用于举证）"], []

    ids: dict[str, dict] = {}
    for i, e in enumerate(entries):
        tag = e.get("id") or f"entries[{i}]"
        miss = [k for k in REQUIRED if not e.get(k)]
        if miss:
            errs.append(f"{tag}: 缺必填字段 {miss}")
            continue
        t = e["type"]
        if t not in ID_RE:
            errs.append(f"{tag}: type 非法（{t}）")
            continue
        if not re.fullmatch(ID_RE[t], e["id"]):
            errs.append(f"{tag}: id 与 type 不匹配（{t} 应为 {ID_RE[t]}）")
        if e["id"] in ids:
            errs.append(f"{tag}: id 重复")
        ids[e["id"]] = e
        ok, why = _src_exists(str(e["source"]))
        if not ok:
            errs.append(f"{tag}: {why}")
        if not isinstance(e.get("applies_to"), list) or not e["applies_to"]:
            errs.append(f"{tag}: applies_to 必须是非空数组")

    for tag, e in ids.items():
        for ov in e.get("overrides", []) or []:
            if ov not in ids:
                errs.append(f"{tag}: overrides 指向不存在的条目 {ov}")
            elif ids[ov]["type"] != "principle":
                errs.append(f"{tag}: overrides 只能指向 principle（{ov} 是 {ids[ov]['type']}）")
        if e["type"] == "exception" and not e.get("overrides"):
            warns.append(f"{tag}: 例外未声明 overrides（若确为新增规则请标记 type 而非 exception）")

    pr = d.get("precedence_rule")
    if pr != ["exception", "principle"]:
        errs.append(f"precedence_rule 应为 ['exception','principle']（M-001），实为 {pr}")

    raw = path.read_text(encoding="utf-8")
    if LEAK_RE.search(raw):
        errs.append("反泄露：库内出现「用户裁A/D」类答案字母复述（会把答案带进审查）")

    # 手册↔JSON 一致性：人读手册漏写条目 = 审查员看不到规则（2026-10-31 实测 M-003 曾漏）
    md = path.with_name("CALIBER-LIBRARY.md")
    if md.exists():
        md_txt = md.read_text(encoding="utf-8")
        for eid in ids:
            if eid not in md_txt:
                errs.append(
                    f"{eid}: 人读手册 CALIBER-LIBRARY.md 未收录该条目（手册与 JSON 不一致）"
                )
    else:
        warns.append("未找到人读手册 CALIBER-LIBRARY.md，无法校验手册一致性")

    n_exc = sum(1 for e in ids.values() if e["type"] == "exception")
    if n_exc == 0:
        warns.append("库内无例外条目：原则冲突时无优先级可用（M-001 空转）")
    return errs, warns


# ---------------------------------------------------------------- 引用判据（单一真源）
# L-0040：判据不能留两份真源。--audit-review（闸门）与 cite_rate.py（指标）曾各写一套
# 例外识别/范围判定，结果是同一份报告两个工具给出相反结论（2026-11-01 实测）。
# 此后两边都必须调用 check_item_refs，不得各自重新实现。
SCOPE_RE = re.compile(r"命中|适用|范围|场景")
EXC_HIT_ID_RE = re.compile(r"\b([CEM]-\d{3})\b")


def load_lib_ids(lib_path: Path = LIB) -> dict:
    """{id: entry}；库不可读时抛错（不静默降级，否则会算出假的 100%）。"""
    d = json.loads(lib_path.read_text(encoding="utf-8"))
    return {e["id"]: e for e in d.get("entries", [])}


def check_item_refs(item: dict, lib_ids: dict, alias: bool = False) -> dict:
    """判定单条结论的引用是否合格。闸门与指标共用此函数。

    alias=True 时接受历史报告的同义字段 refs（协议字段为 caliber_refs）。
    """
    refs = item.get("caliber_refs")
    if refs is None and alias:
        refs = item.get("refs")
    refs = list(refs or [])
    good = [r for r in refs if r in lib_ids]
    bad = [r for r in refs if r not in lib_ids]
    valid = bool(refs) and len(good) == len(refs)  # 含任一编造编号 → 整条无效
    exc = [r for r in refs if lib_ids.get(r, {}).get("type") == "exception"]
    scope = str(item.get("exception_hit") or "") + " " + str(item.get("detail") or "")
    scope_ok = bool(SCOPE_RE.search(scope)) if exc else None
    # 声明了 exception_hit 却没把该例外写进 caliber_refs → 引用集合不完整（下游会漏判）
    declared = EXC_HIT_ID_RE.findall(str(item.get("exception_hit") or ""))
    declared_missing = sorted(set(declared) - set(refs))
    return {
        "refs": refs,
        "good": good,
        "bad": bad,
        "valid": valid,
        "exception_ids": exc,
        "scope_ok": scope_ok,
        "declared_exception_ids": declared,
        "declared_not_cited": declared_missing,
    }


def audit_review(paths: list[Path], lib_path: Path = LIB) -> tuple[list[str], list[str], int]:
    """检查审查报告是否给了可引用的口径证据。返回 (问题, 提示, 条目数)。"""
    try:
        lib = json.loads(lib_path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"口径库不可读：{e}"], [], 0
    lib_ids = {e["id"]: e for e in lib.get("entries", [])}
    problems: list[str] = []
    notes: list[str] = []
    total = 0
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{p.name}: 不可读（{type(e).__name__}）")
            continue
        items = d.get("items") or d.get("claims") or d.get("issues") or []
        for it in items:
            qid = it.get("qid", "?")
            total += 1
            c = check_item_refs(it, lib_ids)
            if not c["refs"]:
                problems.append(
                    f"{qid}: 口径证据不足——无 caliber_refs（M-001：只写『存在歧义』不算证据）"
                )
                continue
            if c["bad"]:
                problems.append(f"{qid}: 引用了不存在的条目 {c['bad']}")
            if c["exception_ids"] and not c["scope_ok"]:
                problems.append(f"{qid}: 引用例外 {c['exception_ids']} 但未说明命中范围（M-002）")
            # 2026-11-01 新增：声明了例外命中却未写进引用列表，下游按引用集合判断会漏判
            if c["declared_not_cited"]:
                problems.append(
                    f"{qid}: 声明命中 {c['declared_not_cited']} 但未写入 caliber_refs"
                    f"（引用集合不完整，下游按引用判断会漏判）"
                )
    if problems:
        notes.append("以上为「证据不足」项：这类结论不能作为口径证据使用")
    return problems, notes, total


def main() -> int:
    ap = argparse.ArgumentParser(description="口径库校验 / 审查报告口径证据审计")
    ap.add_argument("--check", action="store_true", help="校验口径库自身")
    ap.add_argument(
        "--audit-review", nargs="+", metavar="REVIEW_JSON", help="审计审查报告的口径证据充分性"
    )
    ap.add_argument("--lib", default=str(LIB), help="口径库路径")
    ap.add_argument("--json", dest="json_out", action="store_true")
    args = ap.parse_args()
    lib = Path(args.lib)

    if args.check:
        errs, warns = check_library(lib)
        out = {"mode": "check", "exit": 1 if errs else 0, "errors": errs, "warns": warns}
    elif args.audit_review:
        problems, notes, total = audit_review([Path(p) for p in args.audit_review], lib)
        out = {
            "mode": "audit-review",
            "exit": 1 if problems else 0,
            "n_items": total,
            "problems": problems,
            "notes": notes,
        }
    else:
        print("需要 --check 或 --audit-review（见 --help）", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if out["mode"] == "check":
            for e in out["errors"]:
                print(f"  🔴 {e}")
            for w in out["warns"]:
                print(f"  🟡 {w}")
            print(
                (
                    "✅ 口径库校验通过"
                    if out["exit"] == 0
                    else f"🔴 FAIL {len(out['errors'])} 项不合格（库本身不可信，审查结论不可用）"
                )
            )
        else:
            for p in out["problems"]:
                print(f"  🔴 {p}")
            for n in out["notes"]:
                print(f"  ℹ️  {n}")
            print(
                (
                    "✅ 口径证据充分"
                    if out["exit"] == 0
                    else f"🔴 FAIL {len(out['problems'])} 条证据不足 / 共 {out['n_items']} 条"
                )
            )
    return out["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
