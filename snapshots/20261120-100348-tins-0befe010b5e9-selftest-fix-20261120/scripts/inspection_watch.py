#!/usr/bin/env python3
"""巡检产出元核对：08:05「看板巡检」是否真的跑了。

## 为什么需要这个脚本

巡检自动化的 prompt 规定「四项均无异常 → 只回一行、不落文件」。
于是 `outputs/inspection-{D}.md` 缺失有两种解释：

  A. 今日真的无异常（正常）
  B. 巡检未跑 / 跑失败（故障——且是静默的，没人会注意到一个没出现的文件）

单看「文件在不在」无法区分 A/B。本脚本的判据：**复检昨日巡检报告已列的
异常条件是否仍在**，并（2026-11-18 起）**取上游当日产出作反证**。这正是铁律
「单指标失真禁升级为无数据」的应用：缺文件是单指标，复检条件 + 上游证据才能定性。

## 预注册判据（改判据必须先改本文档并重跑基线，不得事后解释）

- E0 根目录不可用（Makefile 不在）→ exit 3，大声报错（外置卷未挂载不静默）。
- E1 `outputs/inspection-{D}.md` 存在且正文含日期串 {D} → PASS(0)。
     （验内容日期，防旧文件改名冒名 / 读到缓存旧报告。）
- E2 文件缺失 → 复检条件 + **上游反证**。
  ★ 2026-11-18（T-018）重构：C1 从「永久触发项」改为「有证据的持久项判定」。
    原实现里 C1 恒为真 ⇒ 只要上游不出报告文件就必然 exit 2，与上游到底跑没跑无关；
    平台口径又只看「有没有产出文本」⇒ 每天 success=1。结果 **脚本恒红 / 平台恒绿**，
    告警既不可见也没有分辨力（`docs/2026-11-18-tins-1af2ada35521-meta-monitor-startup-stall.md`
    第四节）。重构后判据如下：

  - **U 上游反证**（新增，唯一能让 C1 不触发的东西）：核对日 d 的「看板巡检」自动化
    （`automation_id` 前缀 `89087d94`）当天是否**成功派发**且**产出声明无异常**：
      * `U=PASS`        —— 当天存在 `result_success=1` 的派发，且产出文本含「今日无异常」；
      * `U=STALL`       —— 当天无派发 / 派发失败 / 产出为空 / 产出未声明「今日无异常」；
      * `U=UNAVAILABLE` —— 上游库缺失或读失败（取不到证据）。
    数据源只读：`~/.workbuddy/workbuddy.db` 的 `automation_runs`（`mode=ro`，不写库）。
    回归/取证可用 `--upstream <json>` 注入样本，格式
    `{"runs":[{"created_at":<ms>,"result_success":1,"thread_title":"今日无异常…"}]}`，
    样本文件见 `fixtures/upstream-2026-11-18-ok.json` / `…-none.json`。
  - **C1** round8/9/10 均无 `answer-final.json`（出现任一 → 该条件消失）。
    C1 是上游已书面认定的「设计内未终态」持久态，它不可能靠时间消失，
    因此**它本身不再具备触发权**：
      * `U=PASS` → C1 记 **note**（不参与 FAIL 判定），日志写明反证依据与连续拍数；
      * `U=STALL` / `U=UNAVAILABLE` → C1 判 **FAIL(2)**：缺上游反证时「今日真无异常」
        与「上游没跑」症状完全相同，这条真警报通道必须保留（宁可红，不可静默绿）。
  - **连续拍数 N**（默认 3）：自 `outputs/inspection-watch.log` 里 `C1=True` 的日志行
    数出连续拍数，允许中间 1 天空档（断拍那天本就没有日志行，如 11-16 那拍被平台
    90 分钟墙吞掉）。N 只决定 note 的**定性**：≥N 记「持久项（连续 N 拍为真）」，
    <N 记「首现待观察」。N 不放宽也不收紧放行条件 —— 放行只由上游反证决定，
    因为「连续多日不变」本身不带信息，取证才是判据（取舍写在产物文档的判据一节）。
  - **C2** round13 违规仍在 = `seat-check.json` 的 aggregation_blocked 含 EX-03
        **且** `answer-final.json` 的 aggregation_inputs 含 EX-03
        （blocked 是 dict 列表，取 `seat` 字段比对）。
        blocked 含 + inputs 含 → STILL（违规仍在）；blocked 含 + inputs 不含
        → GONE（已修）；blocked 不含 + inputs 含 → CONTRADICTION（seat-check
        没拦却进了聚合，交人裁）。注意 answer-final 内 EX-03 自带的
        seat_check_status='valid' 是错标（2026-10-24/25 两份巡检报告均指出），
        本判据只信 seat-check.blocked 名单 + inputs 名单，不信该自标字段。
  - **C3** 任一轮 `metrics.json` 的 flip_rate.value 非 None 且 > 0.4（严格大于，
        与协议阈值说明一致；value=None 是 D=0 未定义，绝不当 0）。
  - C2 / C3 语义不变，**任一命中即 FAIL(2)**（C1 降级只作用于 C1，不豁免其它条件）。
  - **结论表**（预注册；`e2_verdict()` 是唯一实现，`--self-test` 逐条注坏验证）：

    | 情形 | 结论 |
    | --- | --- |
    | C2=CONTRADICTION | exit 2 交人裁 |
    | C2=STILL 或 C3 命中 | exit 2 |
    | C1 仍在 且 U≠PASS | exit 2（真警报：上游当日无成功派发/产出为空） |
    | 其余（含 C1 仍在但 U=PASS） | PASS(0)，C1 降级为 note |

- 日志：`outputs/inspection-watch.log` 追加一行（时间戳 / 日期 / 结论 / 判据明细），
  PASS 行里保留 `C1=True|False` 标记，供下一拍数「连续拍数」。
- 告警：设了环境变量 FEISHU_WEBHOOK 才发消息；没设就只写日志 + 非零退出码。
  仓库不存 webhook（凭据不入库，与 recompute_watch.py 同一模式）。

## 用法

    python3 scripts/inspection_watch.py              # 核对今天
    python3 scripts/inspection_watch.py --date 2026-10-24   # 核对指定日
    python3 scripts/inspection_watch.py --dry-list    # 只打印判据复检明细，不写日志
    python3 scripts/inspection_watch.py --runs /tmp/fixture-runs --date 2099-01-01 --dry-list
        # 阳性样本不再依赖活数据：--runs 指向合成的 round13（inputs 仍含 EX-03）
    python3 scripts/inspection_watch.py --date 2026-11-18 \
        --upstream fixtures/upstream-2026-11-18-ok.json --dry-list
        # 上游反证样本 A：上游当日有产出且声明无异常 → 期望 PASS(0)
    python3 scripts/inspection_watch.py --date 2026-11-18 \
        --upstream fixtures/upstream-2026-11-18-none.json --dry-list
        # 上游反证样本 B：上游当日无产出 → 期望 exit 2（真警报通道未被削弱）
    python3 scripts/inspection_watch.py --self-test   # 结论表负控自测（不连库、不写日志）

## 已知限制与误报防护（2026-10-25 实测踩到）

巡检是 LLM 任务，触发到落盘耗时不稳（2026-10-25 实测：08:05 触发、
08:22 才出报告、08:26 落盘）。若核对跑太早，会把"还在写"误判为"没跑"。
防护：本核对每日 **09:10** 跑（给巡检 65 分钟宽限，实测 21 分钟 + 3 倍余量）。
上游反证同理：09:10 时上游 08:05 那拍已结束（实测 08:08:07 落 `automation_runs`）；
若偶尔晚到，`U=STALL` 会如实报一次真警报，下一拍重跑即恢复 —— 取向是
「宁可红一次，不可静默绿」。

## 可证伪性

- 阳性样本（旧判据 C2）：`--runs <合成 round13（inputs 仍含 EX-03）> --date 2099-01-01 --dry-list`
  → 必须 exit 2 且命中 C2=STILL。
  ★ 2026-10-27 起不再用活数据当阳性样本：round13 已修，活数据只能产出 GONE。
- 阴性样本：`--date 2026-10-24`（报告在）→ 必须 exit 0。
- 修复态样本：活数据 + `--date 2099-01-01` → 明细行里 C2 必须判 GONE
  （2099 无上游派发 ⇒ 按新结论表 exit 2 属预期；该样本只看 C2 明细，不看退出码）。
- **上游反证样本 A**：`--date 2026-11-18 --upstream fixtures/upstream-2026-11-18-ok.json --dry-list`
  → 必须 exit 0，明细写明「上游当日有产出且声明无异常 ⇒ C1 降级为 note」。
  ★ 这条是重构前不可能通过的：旧实现在同样输入下必然 exit 2（恒红）。
- **上游反证样本 B**：`--date 2026-11-18 --upstream fixtures/upstream-2026-11-18-none.json --dry-list`
  → 必须 exit 2，且告警语义是「上游当日无成功派发/产出为空」而不是「异常条件仍在」。
- 结论表负控：`--self-test` 逐条注坏（U 改 STALL / C2 改 STILL / C3 改真 / C1 改消失）
  必须各自翻红或翻绿，防止「C1 降级」被实现成「无条件豁免」。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "migration-package" / "runs"
LOG = ROOT / "outputs" / "inspection-watch.log"
FLIP_THRESHOLD = 0.4  # 协议 docs/protocol/07：严格大于才告警
PENDING_ROUNDS = [
    8,
    9,
    10,
]  # 2026-10-24 报告①：未终态三轮（round1 为历史残留，报告自标非在飞，不计）

# ── 上游反证（2026-11-18 T-018 新增） ────────────────────────────────────────
UPSTREAM_DB = Path.home() / ".workbuddy" / "workbuddy.db"
UPSTREAM_AID_PREFIX = "89087d94"  # 「看板巡检」自动化 id 前缀
UPSTREAM_PHRASE = "今日无异常"
C1_STREAK_N = 3  # C1 连续 ≥N 拍为真 ⇒ note 定性为「持久项」
C1_STREAK_GAP_DAYS = 2  # 允许中间 1 天空档（断拍那天没有日志行）
U_PASS = "PASS"
U_STALL = "STALL"
U_UNAVAILABLE = "UNAVAILABLE"
CST = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(msg, flush=True)


def alert(msg: str) -> None:
    """有 webhook 就发飞书，没有就只落日志。不把凭据写进仓库。"""
    hook = os.environ.get("FEISHU_WEBHOOK")
    if not hook:
        log("（未设 FEISHU_WEBHOOK，仅本地日志）")
        return
    body = json.dumps({"msg_type": "text", "content": {"text": f"[ai-autonomy] {msg}"}}).encode()
    req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        log("已发飞书告警")
    except Exception as e:  # 网络失败不能吞掉
        log(f"⚠️ 告警发送失败：{e}")


def _iso(ms: int | float | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d %H:%M:%S")


def _day_bounds_ms(d: str) -> tuple[int, int]:
    """核对日 d 的 [起, 止) 毫秒边界（+08）。"""
    start = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
    return start, start + 86_400_000


def _judge_upstream_runs(runs: list, d: str) -> tuple[str, str]:
    """把「上游当日派发记录」判成 U=PASS / U=STALL。纯函数，负控自测直接调它。"""
    start, end = _day_bounds_ms(d)
    same_day = [
        r
        for r in runs
        if isinstance(r, dict)
        and isinstance(r.get("created_at"), (int, float))
        and start <= r["created_at"] < end
    ]
    if not same_day:
        return U_STALL, f"{d} 当天无「看板巡检」派发记录 → 上游未派发（真警报）"
    ok = [
        r
        for r in same_day
        if r.get("result_success") == 1 and UPSTREAM_PHRASE in (r.get("thread_title") or "")
    ]
    if ok:
        best = max(ok, key=lambda r: r["created_at"])
        return U_PASS, (
            f"{d} 有 {len(ok)} 拍 success=1 且产出声明「{UPSTREAM_PHRASE}」"
            f"（最近 {_iso(best['created_at'])}：{(best.get('thread_title') or '')[:40]}）"
        )
    last = max(same_day, key=lambda r: r["created_at"])
    return U_STALL, (
        f"{d} 有 {len(same_day)} 拍但无「success=1 且声明{UPSTREAM_PHRASE}」"
        f"（最近 {_iso(last['created_at'])} success={last.get('result_success')} "
        f"产出={(last.get('thread_title') or '')[:40]!r}）→ 产出为空即真警报"
    )


def load_upstream(d: str, override: str | None = None) -> tuple[str, str]:
    """取上游当日反证。override 为注入样本路径（回归用）；否则只读查库。"""
    if override:
        p = Path(override)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return U_UNAVAILABLE, f"上游样本不可读：{p.name}（{e}）→ 无证据不降级"
        runs = data.get("runs") if isinstance(data, dict) else data
        if not isinstance(runs, list):
            return U_UNAVAILABLE, f"上游样本格式非法：{p.name} → 无证据不降级"
        state, detail = _judge_upstream_runs(runs, d)
        return state, f"[样本 {p.name}] {detail}"
    if not UPSTREAM_DB.is_file():
        return U_UNAVAILABLE, f"上游库不存在（{UPSTREAM_DB.name}）→ 无证据不降级"
    try:
        conn = sqlite3.connect(f"file:{UPSTREAM_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            start, end = _day_bounds_ms(d)
            rows = conn.execute(
                "SELECT created_at, result_success, thread_title FROM automation_runs "
                "WHERE automation_id LIKE ? AND created_at >= ? AND created_at < ? "
                "ORDER BY created_at",
                (f"{UPSTREAM_AID_PREFIX}%", start, end),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:  # 库被锁/表结构变了都不能静默
        return U_UNAVAILABLE, f"读上游库失败：{e} → 无证据不降级"
    return _judge_upstream_runs([dict(r) for r in rows], d)


def c1_streak(up_to: str) -> tuple[int, str]:
    """从 inspection-watch.log 数 C1=True 的连续拍数（允许 1 天空档）。"""
    if not LOG.is_file():
        return 0, "无日志"
    dates: list[str] = []
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return 0, f"日志读取失败：{e}"
    for line in lines:
        m = re.match(r"^(\d{4}-\d{2}-\d{2}) ", line)
        if m and "C1=True" in line:
            dates.append(m.group(1))
    dates = sorted({x for x in dates if x <= up_to})
    if not dates:
        return 0, f"{up_to} 之前无 C1=True 拍"
    streak = 1
    cur = dates[-1]
    for prev in reversed(dates[:-1]):
        gap = (datetime.strptime(cur, "%Y-%m-%d") - datetime.strptime(prev, "%Y-%m-%d")).days
        if gap > C1_STREAK_GAP_DAYS:
            break
        streak += 1
        cur = prev
    return streak, f"自 {cur} 起连续 {streak} 拍为真"


# ── 复检条件（语义不变） ────────────────────────────────────────────────────


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
    if in_inputs:
        # blocked 不含 + inputs 含：seat-check 没拦却进了聚合 —— 这才是两文件互撕
        return "CONTRADICTION", (
            f"seat-check 未拦（blocked={blocked}）但 answer-final 聚合输入含 EX-03——交人裁"
        )
    # ★ 2026-10-27（D-0073）修正：blocked 含 + inputs 不含 = 【已修】，不是互撕。
    #   原实现写的是 `if in_blocked != in_inputs: CONTRADICTION`，把修复态判成
    #   CONTRADICTION，与本文档 §E2 预注册的语义相反 —— round13 两席重跑后
    #   （EX-03 移出 aggregation_inputs）每天 09:10 都会误报「交人裁」。
    return "GONE", (
        f"blocked={blocked}，inputs 已不含 EX-03（已修）"
        if in_blocked
        else f"blocked={blocked}，inputs 亦不含 EX-03（已修或从未违规）"
    )


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


# ── E2 结论表（唯一实现，预注册） ────────────────────────────────────────────


def e2_verdict(c1_still: bool, c2: str, c3: bool, upstream: str) -> tuple[str, str]:
    """E2 结论表。返回 (结论, 理由)，结论 ∈ PASS / FAIL / CONTRADICTION。

    顺序即优先级：CONTRADICTION > C2=STILL / C3 > C1 无反证 > 全过。
    C1 只在「上游无反证」时才触发，这就是 2026-11-18 重构的全部语义。
    """
    if c2 == "CONTRADICTION":
        return "CONTRADICTION", "seat-check 未拦 EX-03 但 answer-final 含之 → 交人裁"
    if c2 == "STILL":
        return "FAIL", "C2 round13 违规仍在（独立触发，C1 降级不影响它）"
    if c3:
        return "FAIL", "C3 翻边率超阈值仍在（独立触发，C1 降级不影响它）"
    if not c1_still:
        return "PASS", "C1 已消失（round8/9/10 均终态）+ C2/C3 未命中"
    if upstream == U_PASS:
        return "PASS", "C1 持久项 + 上游当日产出声明无异常 ⇒ C1 降级为 note"
    return "FAIL", f"C1 仍在且缺上游反证（U={upstream}）⇒ 真警报通道生效"


def c1_note(c1_still: bool, streak: int, upstream: str) -> str:
    """给日志/告警用的一句话定性（不影响退出码）。"""
    if not c1_still:
        return "C1 已消失"
    if upstream != U_PASS:
        return f"C1 仍在（U={upstream}）→ 按真警报处理，未降级"
    if streak >= C1_STREAK_N:
        return f"C1 持久项降级为 note（已连续 {streak} 拍为真 + 上游当日产出声明无异常）"
    return f"C1 首现待观察（连续 {streak} 拍 < N={C1_STREAK_N}）+ 上游反证成立 ⇒ 记 note"


# ── 负控自测（注坏，确认结论表会翻红/翻绿） ──────────────────────────────────


def self_test() -> int:
    cases: list[tuple[str, bool]] = []

    def verdict(name: str, want: str, **kw) -> None:
        got = e2_verdict(**kw)[0]
        cases.append((f"{name} → 实得 {got}", got == want))

    ok_kw = {"c1_still": True, "c2": "GONE", "c3": False, "upstream": U_PASS}
    verdict("基线：C1 仍在 + 上游反证 PASS", "PASS", **ok_kw)
    verdict("注坏：上游当日无派发（U=STALL）", "FAIL", **{**ok_kw, "upstream": U_STALL})
    verdict("注坏：取不到上游库（U=UNAVAILABLE）", "FAIL", **{**ok_kw, "upstream": U_UNAVAILABLE})
    verdict("注坏：C2 违规仍在（U=PASS 也不得豁免）", "FAIL", **{**ok_kw, "c2": "STILL"})
    verdict("注坏：C3 翻边率超阈（U=PASS 也不得豁免）", "FAIL", **{**ok_kw, "c3": True})
    verdict("注坏：C2 两文件互撕", "CONTRADICTION", **{**ok_kw, "c2": "CONTRADICTION"})
    verdict(
        "反证：C1 已消失 + U=STALL 仍 PASS",
        "PASS",
        **{**ok_kw, "c1_still": False, "upstream": U_STALL},
    )

    day = "2026-11-18"
    ms = _day_bounds_ms(day)[0] + 8 * 3600 * 1000 + 8 * 60 * 1000  # 当日 08:08
    good = {
        "created_at": ms,
        "result_success": 1,
        "thread_title": f"{UPSTREAM_PHRASE}。四项巡检均无新增",
    }
    judge = [
        ("上游样本：当日有成功派发且声明无异常", [good], U_PASS),
        ("注坏：上游当日 0 派发", [], U_STALL),
        ("注坏：上游派发失败", [{**good, "result_success": 0}], U_STALL),
        ("注坏：上游产出为空", [{**good, "thread_title": ""}], U_STALL),
        ("注坏：上游产出不提无异常", [{**good, "thread_title": "发现异常：X"}], U_STALL),
        ("注坏：派发不在核对日", [{**good, "created_at": ms + 3 * 86_400_000}], U_STALL),
    ]
    for name, runs, want in judge:
        got = _judge_upstream_runs(runs, day)[0]
        cases.append((f"{name} → 实得 {got}", got == want))

    note_cases = [
        ("note：连续 9 拍 + U=PASS 记持久项", c1_note(True, 9, U_PASS), "持久项"),
        ("note：首现 1 拍 + U=PASS 记待观察", c1_note(True, 1, U_PASS), "首现"),
        ("note：U=STALL 不降级", c1_note(True, 9, U_STALL), "未降级"),
    ]
    for name, text, needle in note_cases:
        cases.append((f"{name} → {text}", needle in text))

    fails = 0
    for name, ok in cases:
        print(f"[{'OK' if ok else 'NG'}] {name}")
        fails += 0 if ok else 1
    print(f"-- 自测 {len(cases)} 项，失败 {fails}")
    return 0 if fails == 0 else 1


# ── 主流程 ────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="核对日期（默认今天；YYYY-MM-DD）",
    )
    ap.add_argument(
        "--dry-list", action="store_true", help="只打印判据复检明细，不写日志、不发告警"
    )
    ap.add_argument(
        "--runs",
        default=None,
        metavar="DIR",
        help="覆盖 runs/ 目录（仅供回归构造阳性样本；生产与定时巡检不传，"
        "缺省为 migration-package/runs）",
    )
    ap.add_argument(
        "--upstream",
        default=None,
        metavar="JSON",
        help="注入上游反证样本（回归/取证用；生产不传，缺省只读查 "
        "~/.workbuddy/workbuddy.db 的 automation_runs）",
    )
    ap.add_argument("--self-test", action="store_true", help="结论表负控自测（不连库、不写日志）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    # ★ 2026-10-27（D-0073）：round13 的 S8-1 违规已修（EX-03 移出聚合输入），
    #   活数据不再提供 STILL 阳性样本。判据必须仍能被正样本击破，故允许回归
    #   用合成目录复现「blocked 含 + inputs 含」的违规态；不传即生产路径，行为不变。
    global RUNS
    if args.runs:
        RUNS = Path(args.runs).resolve()
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

    # E2：缺失（或冒名）→ 复检三条件 + 上游反证
    c1, c1d = check_c1()
    c2, c2d = check_c2()
    c3, c3d = check_c3()
    ustate, udetail = load_upstream(d, args.upstream)
    streak, sdetail = c1_streak(d)
    verdict, reason = e2_verdict(c1, c2, c3, ustate)
    log(f"   C1 未终态轮次仍在？{c1}  {c1d}  [{sdetail}]")
    log(f"   C2 round13 违规状态：{c2}  {c2d}")
    log(f"   C3 翻边率超阈值仍在？{c3}  {c3d}")
    log(f"   U  上游反证：{ustate}  {udetail}")
    log(f"   → 结论：{verdict}｜{reason}｜{c1_note(c1, streak, ustate)}")

    if verdict == "CONTRADICTION":
        line = (
            f"{datetime.now():%F %T}  {d}  CONTRADICTION  seat-check 未拦 EX-03 "
            f"但 answer-final 含之，交人裁；报告缺失。{c2d}"
        )
        log(f"⚠️ {line}")
        if not args.dry_list:
            alert(
                f"巡检核对 {d}：seat-check 未拦 EX-03 但 answer-final 聚合输入含之，且当日无巡检报告，需人裁"
            )
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return 2

    if verdict == "FAIL":
        detail = f"C1={c1} C2={c2} C3={c3} U={ustate}"
        if c1 and ustate != U_PASS:
            # 真警报通道：上游当日无成功派发 / 产出为空 / 取不到证据
            head = "上游「看板巡检」当日无反证（未派发/产出为空/取不到库）而报告缺失 → 上游真停摆，须升级"
        else:
            head = "异常条件仍在而报告缺失 → 疑似巡检未跑/失败"
        line = f"{datetime.now():%F %T}  {d}  FAIL  {head}（{detail}）"
        log(f"❌ {line}")
        log(f"   {c1d}  [{sdetail}]")
        log(f"   {c2d}")
        log(f"   {c3d}")
        log(f"   U: {udetail}")
        if not args.dry_list:
            alert(f"巡检产出核对 {d}：{head}（{detail}）。上游反证明细：{udetail}")
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return 2

    note = c1_note(c1, streak, ustate)
    line = (
        f"{datetime.now():%F %T}  {d}  PASS  报告缺失但{note} → 判「今日无异常」"
        f"（C1={c1} C2={c2} C3={c3} U={ustate} C1连续={streak}拍）"
    )
    log(f"✅ {line}")
    log(f"   依据 U: {udetail}")
    if not args.dry_list:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
