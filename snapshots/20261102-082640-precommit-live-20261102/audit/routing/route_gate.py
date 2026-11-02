#!/usr/bin/env python3
"""route_gate.py —— 任务路由分诊器 v0：manifest 进，三档路由判定 + S1 偏斜预筛出。

## 为什么需要它

v1.1–v1.3 事实题三轮两臂满分、零不一致对（多席零增量），v3 判断题常数基准 0.700
高过所有臂（选项分布偏斜 7/10 同侧）。缺的不是再实验，而是路由层：
按任务类型选编制，且在入库前先把「注定死」的题批拦下。判据见
audit/routing/DESIGN-v0-路由与四角色编排.md §二（三档判据表）与 §2.5（实施要求）。

## 判定顺序（对每个 item）

1. 必填字段：qid / level / route；level=L2 必须有 ref_label。缺字段或字段矛盾 → exit 2。
2. L1 判据：verification ∈ {machine, attested} 才算有可重算锚点/可查证来源；
   判断题误标 L1（无锚点）在此报错（DESIGN §2.2 的可证伪负样本）。
3. L2 判据：有 ref_label；整批 L2 做 S1 偏斜预筛——任一标签占比 > 0.5 → RED，exit 1。
4. L3：一律 exit 2（判据未定，暂不启用，防止判据被滥用）。

退出码沿用 paired_compare 惯例：
    0 = 全部路由成功且预筛通过
    1 = S1 偏斜预筛报红（批次不可入库）
    2 = 输入/判据自身问题（manifest 缺字段、schema 违规、无法解析、item_gate 不可用等）

## 与 item_gate 的关系（同一真源，不复制判据）

S1 的判据是「最挤的那个标签占了多大比例」，与 audit/kit/item_gate.py 常数基准
（constant_wins）是同一个判据。2026-10-30 已把该判据从 item_gate 的 main() 里
抽出为可导入的 `constant_baseline(labels)`，本文件改为**真复用**——import 失败
即 exit 2（依赖被移动/改名时闸门必须哑火，而不是静默放行）。

## 用法

    python3 audit/routing/route_gate.py --manifest audit/routing/manifest-v1.3-L1.json [--json]
    python3 audit/routing/route_gate.py --manifest <path> --emit-decision-table <out.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ── 判据真源：item_gate.constant_baseline（见 docstring「与 item_gate 的关系」）──
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kit"))
try:
    from item_gate import constant_baseline

    _ITEM_GATE_OK, _ITEM_GATE_ERR = True, ""
except Exception as e:  # 闸门依赖缺失必须报 exit 2，不能静默放行
    constant_baseline = None
    _ITEM_GATE_OK, _ITEM_GATE_ERR = False, f"{type(e).__name__}: {e}"

LEVEL_ROUTE = {"L1": "single", "L2": "multi-role"}
L1_VERIFICATIONS = {"machine", "attested"}
S1_THRESHOLD = 0.5  # 主理人裁决 #4：固定 0.5，不按批次放宽


def validate_items(items, errors: list[str]) -> Counter:
    """逐 item 做必填字段 + 档位判据校验；返回各档位计数。

    计数口径：只对**无错**的 item 计数（2026-10-30 修）。此前缺 route 的脏 item
    也会被计进 routes[type]，虽然退出码仍是 2、不影响判定，但 --json 的 routes
    会被下游消费，把脏 item 计进去等于输出一份自相矛盾的结果。
    """
    routes = Counter({"L1": 0, "L2": 0, "L3": 0})
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f"items[{idx}]: 不是对象（{type(it).__name__}）")
            continue
        qid = it.get("qid")
        if not isinstance(qid, str) or not qid.strip():
            errors.append(f"items[{idx}]: 缺 qid 或非字符串")
            qid = f"items[{idx}]"
        mark = len(errors)  # 该 item 校验前的错误数，用于判断它是否脏
        level = it.get("level")
        route = it.get("route")
        if level not in LEVEL_ROUTE and level != "L3":
            errors.append(f"{qid}: level 非法（{level!r}，须为 L1/L2/L3）")
            continue
        if not isinstance(route, str) or not route.strip():
            errors.append(f"{qid}: 缺 route")
        if len(errors) == mark:
            routes[level] += 1
        if level == "L3":
            errors.append(f"{qid}: L3 未启用（判据未定，暂空不接入，见 DESIGN §2.1）")
            continue
        expected = LEVEL_ROUTE[level]
        if isinstance(route, str) and route.strip() and route != expected:
            errors.append(f"{qid}: 字段矛盾——level={level} 但 route={route!r}（应为 {expected!r}）")
        if level == "L1":
            verification = it.get("verification")
            if verification not in L1_VERIFICATIONS:
                errors.append(
                    f"{qid}: L1 判据不满足——verification={verification!r} "
                    f"须为 machine/attested（无重算锚点的判断题应改判 L2）"
                )
        elif level == "L2":
            ref = it.get("ref_label")
            if not isinstance(ref, str) or not ref.strip():
                errors.append(f"{qid}: level=L2 缺 ref_label（S1 偏斜预筛必需）")
    return routes


def skew_precheck(l2_items: list[dict]) -> dict | None:
    """S1：整批 L2 的 ref_label 分布，任一标签占比 > 0.5 → RED（DESIGN §2.1）。

    占比计算复用 kit.item_gate.constant_baseline（与常数基准同一真源），不在本地
    另写一份 Counter/most_common。
    """
    if not l2_items:
        return None
    labels = [it["ref_label"].strip() for it in l2_items]
    base = constant_baseline(labels)
    top_share = round(base["top_share"], 4)
    return {
        "n_l2": base["n"],
        "distribution": base["distribution"],
        "max_label": base["top_label"],
        "max_share": top_share,
        "threshold": S1_THRESHOLD,
        "verdict": "RED" if top_share > S1_THRESHOLD else "PASS",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="任务路由分诊器 v0（三档判据 + S1 偏斜预筛）")
    ap.add_argument("--manifest", required=True, help="路由标注 manifest（JSON）路径")
    ap.add_argument("--json", dest="json_out", action="store_true", help="输出机器可读 JSON")
    ap.add_argument(
        "--emit-decision-table",
        dest="decision_table",
        default="",
        help="路由成功（exit 0）时写出 decision-table，供 downstream 的 "
        "aggregate_seats / paired_compare.grade 消费；非 0 退出码拒绝写出",
    )
    args = ap.parse_args()

    errors: list[str] = []
    items: list = []
    if not _ITEM_GATE_OK:
        errors.append(
            f"item_gate 不可用：{_ITEM_GATE_ERR}（audit/kit/item_gate.py 无法导入，闸门依赖缺失）"
        )

    manifest = None
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"manifest 无法解析：{type(e).__name__}: {e}")
    if isinstance(manifest, dict):
        items = manifest.get("items")
        if not isinstance(items, list):
            errors.append("manifest 缺 items 或 items 不是数组")
            items = []
        elif not items:
            errors.append("manifest items 为空（空批次不可路由，fail-safe 不放行）")
    elif manifest is not None:
        errors.append(f"manifest 顶层须为对象，实为 {type(manifest).__name__}")

    routes = validate_items(items, errors) if not errors else Counter({"L1": 0, "L2": 0, "L3": 0})
    skew = None
    if not errors:
        l2_items = [it for it in items if isinstance(it, dict) and it.get("level") == "L2"]
        skew = skew_precheck(l2_items)

    if errors:
        exit_code = 2
    elif skew is not None and skew["verdict"] == "RED":
        exit_code = 1
    else:
        exit_code = 0

    # ── decision-table：只在路由成功时写出（红批/坏输入不产出，防止下游拿到脏表）──
    dt_path = ""
    if args.decision_table:
        if exit_code != 0:
            print(f"⛔ 未写出 decision-table：exit={exit_code}（只有 exit 0 的批次可下游消费）")
        else:
            dt = {
                "manifest": str(Path(args.manifest).resolve()),
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "aggregate_contract": {
                    "seats_input": "aggregate_seats.py",
                    "grade": "paired_compare.grade",
                },
                "items": {},
            }
            for it in items:
                if not isinstance(it, dict):
                    continue
                qid = str(it.get("qid"))
                dt["items"][qid] = {
                    "level": it.get("level"),
                    "route": it.get("route"),
                    "answer": None,  # 路由阶段尚无作答，由 R4 席位填充
                    "veto": None,  # 由 R3 风险裁判填充
                    "debate": False,  # 由 P2 交叉比对填充
                    "source_manifest": str(Path(args.manifest).resolve()),
                }
            Path(args.decision_table).write_text(
                json.dumps(dt, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            dt_path = str(args.decision_table)

    if args.json_out:
        print(
            json.dumps(
                {
                    "exit": exit_code,
                    "n_items": len(items),
                    "routes": {"L1": routes["L1"], "L2": routes["L2"], "L3": routes["L3"]},
                    "skew": skew,
                    "errors": errors,
                    "decision_table": dt_path or None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return exit_code

    # ── 人读输出：中文，逐档统计 + 每个异常 item 一行 ─────────────────────────
    print(f"路由分诊 v0 ｜ {args.manifest} ｜ {len(items)} 题")
    print(f"  L1 单席核查    {routes['L1']}")
    print(f"  L2 多角色评审  {routes['L2']}")
    print(f"  L3 完整流水线  {routes['L3']}（未启用）")
    for err in errors:
        print(f"  ❌ {err}")
    if skew is not None:
        dist_s = " ".join(f"{k}={v}" for k, v in sorted(skew["distribution"].items()))
        print(
            f"  S1 偏斜预筛：分布 {dist_s} ｜ 最高 {skew['max_label']} 占比 "
            f"{skew['max_share']:.2f}（阈值 {skew['threshold']:.2f}）"
        )
    if exit_code == 0:
        print("✅ PASS 全部路由成功且预筛通过")
    elif exit_code == 1:
        print(
            f"🔴 RED S1 报红：标签 {skew['max_label']} 占比 {skew['max_share']:.2f} > "
            f"{skew['threshold']:.2f} → 该批不可入库（重排选项/补对侧真值后复检）"
        )
    else:
        print(f"❌ exit 2 输入/判据自身问题，共 {len(errors)} 条（见上方逐条）")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
