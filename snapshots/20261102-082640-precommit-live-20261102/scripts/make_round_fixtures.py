#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_round_fixtures.py —— 生成多轮/席位契约的回归夹具

放在 migration-package/fixtures/regress/ 下，供 scripts/regress.py 第 12–21 项使用。
全部为**合成数据**，不触碰真实 round1/round2 产物。

夹具设计（每个都在验证一条具体机制）：
  roundA   role=evidence_round，3 席立场全一致且**有**证据指针
           → 熵=0 但不应触发魔鬼代言人轮（缺「无独立证据」这一子句）
  roundB   role=revision_round，EX-02 在 ADJ-001 上翻边
           → 翻边率 1/4=0.25；且 revision_round 不触发空转
  roundC   role=evidence_round，全一致且**无**证据指针
           → 魔鬼代言人轮应触发（exit 3）
  roundD1/roundD2  role=evidence_round，两轮证据指针完全相同
           → 空转连击达标，应 terminate_branch（exit 4）
  seat-bad  三个席位分别缺 conclusion / 缺 evidence / 合规
           → seat_check exit 1，aggregation_blocked 非空
  final-bad answer-final 引用了被否决席位
           → --verify-final exit 1
  params-threshold0  翻边率阈值改 0.0 的临时参数
           → metrics 报 restart_round（exit 1），证明 params 是真参数

依赖：仅 Python 标准库。

用法：python3 make_round_fixtures.py --out ../migration-package/fixtures/regress
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

EXCLUDE = {"summary.amount_diff_total": 500.02, "summary.excluded_amount_total": 180.0}
COUNT = {"summary.amount_diff_total": 680.02, "summary.excluded_amount_total": 0.0}


def seat(seat_id, stance_choice, diff_count, ptr=None, **extra):
    """造一份合规席位产出。

    ★ 注意 summary 必须是**嵌套**结构：立场反推按点号路径 resolve("summary.amount_diff_total")，
      若写成扁平的 {"summary.amount_diff_total": ...} 会一律反推为 abstain。
      （这个坑是本轮实测踩到的 —— 夹具自己写错了，指标全落弃权。）
    """
    if stance_choice == "exclude":
        summary = {"amount_diff_total": 500.02, "excluded_amount_total": 180.0}
    elif stance_choice == "count":
        summary = {"amount_diff_total": 680.02, "excluded_amount_total": 0.0}
    else:  # abstain：不给金额结论，只给 diff_count
        summary = {}
    summary["diff_count"] = diff_count
    summary["ledger_rows"] = 27
    summary["bill_rows"] = 27

    d = {
        "seat": seat_id,
        "round": 2,
        "conclusion": f"{seat_id} 的立场：{stance_choice}",
        "evidence": ([{"pointer": ptr, "what_it_shows": "夹具证据"}] if ptr else []),
        "falsification": "若前提被推翻则本结论不成立",
        "confidence": "medium",
        "stance": [
            {"axis": "ADJ-001", "choice": stance_choice},
            {"axis": "DIFFSET", "choice": str(diff_count)},
        ],
        "summary": summary,
    }
    d.update(extra)
    return d


def manifest(round_name, role, seed="deadbeef", cver="1.0", seats=("EX-01", "EX-02", "EX-03")):
    return {
        "round": round_name,
        "role": role,
        "contract_version": cver,
        "expected_seats": list(seats),
        "seed": seed,
        "prev_round": None,
        "introduces_new_evidence": role == "evidence_round",
    }


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser(description="生成多轮/席位契约回归夹具")
    ap.add_argument("--out", default="../migration-package/fixtures/regress")
    args = ap.parse_args()
    out = Path(args.out)

    # ★ 本机铁律：删除的唯一去向是「改名」，不调用 unlink / rmtree
    #   （沙箱拦截 unlink；且 regress 第 20 项会静态扫描并判失败）
    if out.exists():
        stamp = datetime.now().strftime("%H%M%S")
        out.rename(out.parent / f"{out.name}.old-{stamp}")
        print(f"[note] 旧夹具已改名为 {out.name}.old-{stamp}（未删除）")

    n = 0

    # ---- roundA：全一致 + 三席各有独有指针 → 有独立证据，不应触发魔鬼代言人轮 ----
    A_PTRS = ["fixtures/ledger.csv#T101", "fixtures/bill.csv#T103", "fixtures/TASK.md"]
    a = out / "roundA"
    write(a / "round-manifest.json", manifest("roundA", "evidence_round", "aaaa1111"))
    write(a / "EX-01.json", seat("EX-01", "exclude", 7, ptr=A_PTRS[0]))
    write(a / "EX-02.json", seat("EX-02", "exclude", 7, ptr=A_PTRS[1]))
    write(a / "EX-03.json", seat("EX-03", "abstain", 7, ptr=A_PTRS[2]))
    n += 4

    # ---- roundB：EX-02 翻边；revision_round；三席各有独有指针（须合规）----
    b = out / "roundB"
    write(b / "round-manifest.json", manifest("roundB", "revision_round", "bbbb2222"))
    write(b / "EX-01.json", seat("EX-01", "exclude", 7, ptr="fixtures/ledger.csv#T101"))
    write(b / "EX-02.json", seat("EX-02", "count", 7, ptr="fixtures/bill.csv#T104"))
    write(b / "EX-03.json", seat("EX-03", "abstain", 7, ptr="fixtures/TASK.md#d3"))
    n += 4

    # ---- roundC：全一致 + 三席引用**同一批指针** → 无独立证据 → 魔鬼代言人轮应触发 ----
    c = out / "roundC"
    write(c / "round-manifest.json", manifest("roundC", "evidence_round", "cccc3333"))
    for sid in ("EX-01", "EX-02", "EX-03"):
        write(c / f"{sid}.json", seat(sid, "exclude", 7, ptr="fixtures/ledger.csv#shared"))
    n += 4

    # ---- roundD1/D2：指针与 roundA 完全相同 → 第二轮起新指针数为 0 → 空转连击 ----
    for tag, seed in (("roundD1", "dddd4441"), ("roundD2", "dddd4442")):
        d = out / tag
        write(d / "round-manifest.json", manifest(tag, "evidence_round", seed))
        for i, sid in enumerate(("EX-01", "EX-02", "EX-03")):
            choice = "abstain" if sid == "EX-03" else "exclude"
            write(d / f"{sid}.json", seat(sid, choice, 7, ptr=A_PTRS[i]))
        n += 4

    # ---- seat-bad：缺字段 ----
    sb = out / "seat-bad"
    write(sb / "round-manifest.json", manifest("seat-bad", "revision_round", "eeee5555"))
    s1 = seat("EX-01", "exclude", 7, ptr="fixtures/ledger.csv#T101")
    s1.pop("conclusion")  # 缺硬性字段 1
    s2 = seat("EX-02", "exclude", 7, ptr="fixtures/ledger.csv#T102")
    s2.pop("evidence")  # 缺硬性字段 2
    s3 = seat("EX-03", "abstain", 7, ptr="fixtures/ledger.csv#T103")  # 合规
    write(sb / "EX-01.json", s1)
    write(sb / "EX-02.json", s2)
    write(sb / "EX-03.json", s3)
    n += 4

    # ---- final-bad：引用了被否决席位 ----
    fb = out / "final-bad"
    write(
        fb / "answer-final.json",
        {
            "summary": {"ledger_rows": 27, "bill_rows": 27},
            "aggregation_inputs": [
                {"seat": "EX-01", "sha256": "deadbeef", "seat_check_status": "invalid"},
                {"seat": "EX-03", "sha256": "cafebabe", "seat_check_status": "valid"},
            ],
        },
    )
    n += 1

    # ==================== 第 22–29 项用夹具（第四轮）====================

    # ---- roundE：三席引用【同一文件的不同写法】→ 归一化后仍共享 → 不独立 ----
    # ★ 这是 F6 的核心用例：旧实现用 sha256(整串) 去重，#T101,T102 / :26-27 / 整路径
    #   三种写法各得不同 id → 误判为「三席各自独立」。文件级归一化后必须判为共享。
    e = out / "roundE"
    write(e / "round-manifest.json", manifest("roundE", "evidence_round", "eeee5551"))
    write(e / "EX-01.json", seat("EX-01", "exclude", 7, ptr="fixtures/ledger.csv#T101,T102"))
    write(e / "EX-02.json", seat("EX-02", "exclude", 7, ptr="fixtures/ledger.csv:26-27"))
    write(e / "EX-03.json", seat("EX-03", "exclude", 7, ptr="fixtures/ledger.csv"))
    n += 4

    # ---- roundF：三席引用【三个不同文件】→ 各有独有观测 → 有独立证据 ----
    f = out / "roundF"
    write(f / "round-manifest.json", manifest("roundF", "evidence_round", "ffff5552"))
    write(f / "EX-01.json", seat("EX-01", "exclude", 7, ptr="fixtures/ledger.csv#T101"))
    write(f / "EX-02.json", seat("EX-02", "exclude", 7, ptr="fixtures/bill.csv#T103"))
    write(f / "EX-03.json", seat("EX-03", "exclude", 7, ptr="fixtures/TASK.md"))
    n += 4

    # ---- round-readA：契约 v2.0 + reads 全在 allow 内 → 第 9 查 CLEAN ----
    R_OK = [
        {"path": "fixtures/ledger.csv", "purpose": "复算"},
        {"path": "fixtures/TASK.md", "purpose": "读任务书"},
    ]
    ra = out / "round-readA"
    write(ra / "round-manifest.json", manifest("round-readA", "evidence_round", "aaaa5553", "2.0"))
    for sid, ptr in (
        ("EX-01", "fixtures/ledger.csv#T101"),
        ("EX-02", "fixtures/bill.csv#T103"),
        ("EX-03", "fixtures/TASK.md"),
    ):
        write(ra / f"{sid}.json", seat(sid, "exclude", 7, ptr=ptr, reads=R_OK))
    n += 4

    # ---- seat-readbad：契约 v2.0 + EX-01 申报读了 truth.json → E_READ_DOMAIN_VIOLATION ----
    sb2 = out / "seat-readbad"
    write(
        sb2 / "round-manifest.json", manifest("seat-readbad", "evidence_round", "bbbb5554", "2.0")
    )
    write(
        sb2 / "EX-01.json",
        seat(
            "EX-01",
            "exclude",
            7,
            ptr="fixtures/ledger.csv#T101",
            reads=[{"path": "fixtures/truth.json", "purpose": "查真值"}],
        ),
    )
    write(sb2 / "EX-02.json", seat("EX-02", "exclude", 7, ptr="fixtures/bill.csv#T103", reads=R_OK))
    write(sb2 / "EX-03.json", seat("EX-03", "abstain", 7, ptr="fixtures/TASK.md", reads=R_OK))
    n += 4

    # ---- round8：契约 v2.0，AD-01 + PM-01 齐备 → 阶段 B 应 all_valid ----
    # ★ 目录名必须含数字：读取域的 {n} 靠 round(\d+) 解析。
    #   曾用名 round-stage-b → {n} 展开失败 → AD-01 读自己本轮产出被判越界，
    #   正向夹具自己红了。这条约束是实测踩出来的，不是设计出来的。
    PM_FULL = {
        "round": 2,
        "board": [{"id": "R2-EX-01", "owner": "EX-01", "state": "通过", "judge": "pass"}],
        "decision": "采纳 exclude",
        "alternatives_rejected": [{"option": "count", "why_not": "论据被数据证伪"}],
        "risk": {"rating": "低", "irreversible": "否", "rollback": "轮次目录改名进 _trash/"},
        "diff_preserved": [{"side": "少数", "conclusion": "X", "falsification": "Y"}],
        "self_doubt": "我可能把弃权误当成未表态。",
        "next": ["由人签署裁决记录"],
    }
    sbf = out / "round8"
    write(
        sbf / "round-manifest.json",
        manifest("round8", "revision_round", "cccc5555", "2.0", seats=("AD-01", "PM-01")),
    )
    write(
        sbf / "AD-01.json",
        {
            "seat": "AD-01",
            "round": 8,
            "conclusion": "八查中第 5/8 查判 HOLD",
            "evidence": [{"pointer": "runs/round8/metrics.json", "what_it_shows": "熵为 0"}],
            "falsification": "若 metrics 缺该字段则本审计不成立",
            "reads": [{"path": "runs/round8/metrics.json", "purpose": "读机器产出"}],
            "action": "HOLD",
            "checks": [{"no": 5, "name": "规格假设", "verdict": "HOLD", "reason": "-"}],
            "assumptions_to_adjudicate": [],
            "cost_score": {},
            "barely_passed": "-",
            "my_blind_spots": "-",
        },
    )
    write(sbf / "PM-01.json", PM_FULL)
    n += 3

    # ---- round9：契约 v2.0，AD-01 缺 conclusion → 阶段 B 应 has_invalid ----
    #   ★ 负向夹具必须让「唯一缺陷」就是缺 conclusion —— 否则它红了也证明不了
    #     conclusion 闸门在工作（旧版同名夹具其实是被读取域越界打红的，假红）。
    adb = out / "round9"
    write(
        adb / "round-manifest.json",
        manifest("round9", "revision_round", "dddd5556", "2.0", seats=("AD-01", "PM-01")),
    )
    write(
        adb / "AD-01.json",
        {
            "seat": "AD-01",
            "round": 9,
            "evidence": [{"pointer": "runs/round9/metrics.json", "what_it_shows": "熵为 0"}],
            "falsification": "-",
            "reads": [{"path": "runs/round9/metrics.json", "purpose": "-"}],
            "action": "PASS",
        },
    )
    write(adb / "PM-01.json", PM_FULL)
    n += 3

    # ---- 闸门 1 负向：判断项混入算术层 → 应被拒载（exit 2）----
    #   ★ 这两个文件原先是【手工建在 _trash/tmp-regress/】里的，因此随包迁移后
    #     解压副本上第 6/7 项直接 FAIL（找不到文件）。回归套件依赖未随包转移的
    #     临时文件，等于这套验收不可迁移。故改为由本生成器产出。
    write(
        out / "bad-gate1-judgment.json",
        {
            "_purpose": "回归6用：故意把判断项 summary.diff_count 塞进算术层，期望被拒载（exit 2）",
            "task_id": "GATE-TEST-06",
            "layer": "arithmetic",
            "checks": [
                {
                    "name": "合法锚点",
                    "type": "numeric_equals",
                    "path": "summary.ledger_rows",
                    "expected": 27,
                },
                {
                    "name": "★违规：判断项混入算术层",
                    "type": "numeric_equals",
                    "path": "summary.diff_count",
                    "expected": 7,
                },
            ],
        },
    )
    n += 1

    # ---- 闸门 2 负向：金额路径用 float 语义的 numeric_equals → 应被拒载（exit 2）----
    write(
        out / "bad-gate2-money.json",
        {
            "_purpose": "回归7用：故意对金额路径使用 float 语义的 numeric_equals，期望被拒载（exit 2）",
            "task_id": "GATE-TEST-07",
            "layer": "arithmetic",
            "checks": [
                {
                    "name": "★违规：金额路径未用 decimal_equals",
                    "type": "numeric_equals",
                    "path": "summary.ledger_total",
                    "expected": 37490.50,
                    "tol": 0.01,
                },
            ],
        },
    )
    n += 1

    # ---- params-threshold0：翻边率阈值改 0.0 ----
    base = Path(__file__).resolve().parent.parent / "migration-package/config/params.json"
    pj = json.loads(base.read_text(encoding="utf-8")) if base.is_file() else {}
    pj.setdefault("anti_sycophancy", {})["flip_rate_alert_threshold"] = 0.0
    write(out / "params-threshold0.json", pj)
    n += 1

    print(f"[OK] 生成的回归夹具 → {out.resolve()}")
    for d in sorted(p for p in out.iterdir()):
        tag = "dir " if d.is_dir() else "file"
        cnt = len(list(d.iterdir())) if d.is_dir() else ""
        print(f"     [{tag}] {d.name} {cnt}")
    print(f"     合计 {n} 个文件")
    print()
    print("  注意：写入 fixtures/ 属于「禁止写入域」，但这是**授权的夹具新增**，")
    print("        已在 docs/decisions/D-0004 留痕；下一轮基线在其之后采集，故不受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
