#!/usr/bin/env python3
"""巡检产出元核对：08:05「看板巡检」是否真的跑了。

## 为什么需要这个脚本

巡检自动化的 prompt 规定「四项均无异常 → 只回一行、不落文件」。
于是 `outputs/inspection-{D}.md` 缺失有两种解释：

  A. 今日真的无异常（正常）
  B. 巡检未跑 / 跑失败（故障——且是静默的，没人会注意到一个没出现的文件）

单看「文件在不在」无法区分 A/B。本脚本的判据：**复检昨日巡检报告已列的
异常条件是否仍在**。条件仍在而报告缺失 → 判 B（疑似未跑，告警级）；
条件全部消失 → 判 A（正常）。这正是铁律「单指标失真禁升级为无数据」
的应用：缺文件是单指标，要复检条件才定性。

## 预注册判据（改判据必须先改本文档并重跑基线，不得事后解释）

- E0 根目录不可用（Makefile 不在）→ exit 3，大声报错（外置卷未挂载不静默）。
- E1 `outputs/inspection-{D}.md` 存在且正文含日期串 {D} → PASS(0)。
     （验内容日期，防旧文件改名冒名 / 读到缓存旧报告。）
- E2 文件缺失 → 复检三条件（均复现自 2026-10-24 巡检报告已列异常）：
  - C1 round8/9/10 均无 `answer-final.json`（出现任一 → 该条件消失）。
  - C2 round13 违规仍在 = `seat-check.json` 的 aggregation_blocked 含 EX-03
        **且** `answer-final.json` 的 aggregation_inputs 含 EX-03
        （blocked 是 dict 列表，取 `seat` 字段比对）。
        blocked 含 + inputs 含 → STILL（违规仍在）；blocked 含 + inputs 不含
        → GONE（已修）；blocked 不含 + inputs 含 → CONTRADICTION（seat-check
        没拦却进了聚合，交人裁）。注意 answer-final 内 EX-03 自带的
        seat_check_status='valid' 是错标（2026-10-24/25 两份巡检报告均指出），
        本判据只信 seat-check.blocked 名单 + inputs 名单，不信该自标字段。
  - C3 任一轮 `metrics.json` 的 flip_rate.value 非 None 且 > 0.4（严格大于，
        与协议阈值说明一致；value=None 是 D=0 未定义，绝不当 0）。
  任一条件仍在 → FAIL(2) 疑似未跑/失败，告警级；
  全部消失     → PASS(0) 判 A（今日无异常，合理）。
- 日志：`outputs/inspection-watch.log` 追加一行（时间戳 / 日期 / 结论 / 判据明细）。
- 告警：设了环境变量 FEISHU_WEBHOOK 才发消息；没设就只写日志 + 非零退出码。
  仓库不存 webhook（凭据不入库，与 recompute_watch.py 同一模式）。

## 用法

    python3 scripts/inspection_watch.py              # 核对今天
    python3 scripts/inspection_watch.py --date 2026-10-24   # 核对指定日
    python3 scripts/inspection_watch.py --dry-list    # 只打印判据复检明细，不写日志

## 已知限制与误报防护（2026-10-25 实测踩到）

巡检是 LLM 任务，触发到落盘耗时不稳（2026-10-25 实测：08:05 触发、
08:22 才出报告、08:26 落盘）。若核对跑太早，会把"还在写"误判为"没跑"。
防护：本核对每日 **09:10** 跑（给巡检 65 分钟宽限，实测 21 分钟 + 3 倍余量）。
仍可能误报的场景：LLM 极端慢（>65 分钟）。此时人工看到告警先去
`outputs/` 核一眼 mtime，再判巡检是否真失败——告警是"该看一眼"的信号，
不是"已定罪"的结论。

## 可证伪性

- 阳性样本：`--date 2099-01-01`（报告必然缺失 + C1/C2 条件仍在）→ 必须 exit 2。
- 阴性样本：`--date 2026-10-24`（报告在）→ 必须 exit 0。
  两条路径已实测；判据若坏（如把缺失当正常），阳性样本会翻绿。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "migration-package" / "runs"
LOG = ROOT / "outputs" / "inspection-watch.log"
FLIP_THRESHOLD = 0.4          # 协议 docs/protocol/07：严格大于才告警
PENDING_ROUNDS = [8, 9, 10]   # 2026-10-24 报告①：未终态三轮（round1 为历史残留，报告自标非在飞，不计）


def log(msg: str) -> None:
    print(msg, flush=True)


def alert(msg: str) -> None:
    """有 webhook 就发飞书，没有就只落日志。不把凭据写进仓库。"""
    hook = os.environ.get("FEISHU_WEBHOOK")
    if not hook:
        log("（未设 FEISHU_WEBHOOK，仅本地日志）")
        return
    body = json.dumps({"msg_type": "text",
                       "content": {"text": f"[ai-autonomy] {msg}"}}).encode()
    req = urllib.request.Request(hook, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        log("已发飞书告警")
    except Exception as e:                      # 网络失败不能吞掉
        log(f"⚠️ 告警发送失败：{e}")


def check_c1() -> tuple[bool, str]:
    """round8/9/10 是否仍无 answer-final。返回 (条件仍在?, 明细)。"""
    still = []
    for r in PENDING_ROUNDS:
        f = RUNS / f"round{r}" / "answer-final.json"
        if not f.is_file():
            still.append(f"round{r}")
    return bool(still), (f"仍无 answer-final：{still}" if still else "round8/9/10 均已终态")


def check_c2() -> tuple[str, str]:
    """round13 S8-1 违规状态。返回 (状态, 明细)。

    状态三值：STILL（双边一致，违规仍在）/ GONE（已修）/ CONTRADICTION（两文件互撕）。
    """
    sc = RUNS / "round13" / "seat-check.json"
    af = RUNS / "round13" / "answer-final.json"
    if not sc.is_file() or not af.is_file():
        return "GONE", "round13 缺 seat-check 或 answer-final，无法复核（视为已清）"
    try:
        blocked_raw = json.loads(sc.read_text(encoding="utf-8")).get("aggregation_blocked") or []
        inputs = json.loads(af.read_text(encoding="utf-8")).get("aggregation_inputs") or []
    except Exception as e:
        return "CONTRADICTION", f"读取失败：{e}"
    # blocked 是 dict 列表（{'seat': 'EX-03', 'errors': [...]}），不能直接 `"EX-03" in`
    blocked = [b.get("seat") if isinstance(b, dict) else b for b in blocked_raw]
    in_blocked = "EX-03" in blocked
    in_inputs = any((i.get("seat") if isinstance(i, dict) else i) == "EX-03" for i in inputs)
    if in_blocked and in_inputs:
        return "STILL", f"blocked={blocked} 且 inputs 含 EX-03（S8-1 违规仍在）"
    if in_blocked != in_inputs:
        return "CONTRADICTION", (f"seat-check 未拦但 answer-final 含 EX-03"
                                 if in_inputs else
                                 f"seat-check.blocked={blocked} 但 inputs 已不含 EX-03") + "——交人裁"
    return "GONE", f"blocked={blocked}，inputs 已不含 EX-03（已修或从未违规）"


def check_c3() -> tuple[bool, str]:
    """翻边率超阈值轮次。value=None（D=0 未定义）绝不当 0。"""
    hits = []
    for mf in sorted(RUNS.glob("round*/metrics.json")):
        try:
            fr = json.loads(mf.read_text(encoding="utf-8")).get("flip_rate") or {}
        except Exception:
            continue
        v = fr.get("value")
        if v is not None and v > FLIP_THRESHOLD:
            hits.append(f"{mf.parent.name}={v}")
    return bool(hits), (f"超阈值(>{FLIP_THRESHOLD})：{hits}" if hits else "无轮次超阈值")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="核对日期（默认今天；YYYY-MM-DD）")
    ap.add_argument("--dry-list", action="store_true",
                    help="只打印判据复检明细，不写日志、不发告警")
    args = ap.parse_args()
    d = args.date

    if not (ROOT / "Makefile").is_file():
        msg = f"仓库根目录不可用：{ROOT}（外置卷未挂载？）"
        log(f"❌ {msg}")
        if not args.dry_list:
            alert(msg)
        return 3

    report = ROOT / "outputs" / f"inspection-{d}.md"

    # E1：报告在且正文含当天日期（防旧文件冒名）
    if report.is_file():
        try:
            body_ok = d in report.read_text(encoding="utf-8")[:2000]
        except Exception as e:
            body_ok = False
            log(f"⚠️ 报告读取失败：{e}")
        if body_ok:
            line = f"{datetime.now():%F %T}  {d}  PASS  报告在且含日期 {d}"
            log(f"✅ {line}")
            if not args.dry_list:
                LOG.parent.mkdir(parents=True, exist_ok=True)
                with LOG.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            return 0
        log(f"⚠️ 报告在但不含日期 {d}（疑似旧文件冒名），转入缺失分支复检")

    # E2：缺失（或冒名）→ 复检三条件
    c1, c1d = check_c1()
    c2, c2d = check_c2()
    c3, c3d = check_c3()
    log(f"   C1 未终态轮次仍在？{c1}  {c1d}")
    log(f"   C2 round13 违规状态：{c2}  {c2d}")
    log(f"   C3 翻边率超阈值仍在？{c3}  {c3d}")

    if c2 == "CONTRADICTION":
        line = (f"{datetime.now():%F %T}  {d}  CONTRADICTION  seat-check 未拦 EX-03 "
                f"但 answer-final 含之，交人裁；报告缺失。{c2d}")
        log(f"⚠️ {line}")
        if not args.dry_list:
            alert(f"巡检核对 {d}：seat-check 未拦 EX-03 但 answer-final 聚合输入含之，且当日无巡检报告，需人裁")
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return 2

    if c1 or c2 == "STILL" or c3:
        detail = f"C1={c1} C2={c2} C3={c3}"
        line = f"{datetime.now():%F %T}  {d}  FAIL  异常条件仍在而报告缺失 → 疑似巡检未跑/失败（{detail}）"
        log(f"❌ {line}")
        log(f"   {c1d}")
        log(f"   {c2d}")
        log(f"   {c3d}")
        if not args.dry_list:
            alert(f"巡检产出核对 {d}：异常条件仍在（{detail}）但无巡检报告，疑似 08:05 巡检未跑或失败")
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return 2

    line = f"{datetime.now():%F %T}  {d}  PASS  报告缺失但异常条件全部消失 → 判「今日无异常」"
    log(f"✅ {line}")
    if not args.dry_list:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
