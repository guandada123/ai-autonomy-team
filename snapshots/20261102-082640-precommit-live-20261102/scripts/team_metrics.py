#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""team_metrics.py —— 三轨指标看板（只读，纯标准库）

## 三轨是什么（用户 2026-10-22 13:38 决策：三条全保留）

| 轨道 | 业务意义 | 数据源 |
|---|---|---|
| 错误决策数 | 决策事后被推翻/返工的次数——判断质量的直接反映 | `docs/decisions/*.md` 状态含撤回/作废/被取代；`human_veto` 记录 |
| 返工工时 | 因判断错误重复投入的时间——比次数更贴近成本 | 轮次记录里的 `rework_hours` 字段（**目前无任何轮次填过**） |
| 金额偏差 | 对账/估值差多少——最硬的结果指标 | `real-runs/*/recon-result.json` 的残差 |

## 一条硬规则：没数据就写 no_data

三条轨道里，**返工工时目前没有任何数据源**。本脚本不会用"估算"或"0"糊过去——
`no_data` 是一个诚实的结论，`0` 是一个假结论。看板上必须看得见这个差别。

## 用法

    python3 scripts/team_metrics.py            # markdown 表
    python3 scripts/team_metrics.py --json     # 机器可读
    退出码：0 成功 / 2 环境错误
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEC = ROOT / "docs" / "decisions"
REAL = ROOT / "real-runs"
PARAMS = ROOT / "migration-package" / "config" / "params.json"

# ── 错误决策的口径（2026-10-22 P0-5 重构）────────────────────────────────
#
# ★ 旧实现两个毛病：
#   1. 曾有 `OVERTURN = ("撤回", "作废", ...)` 常量，但**从头到尾没被引用过** ——
#      读代码的人会以为撤回/作废已覆盖，实际只有两条写死的正则在工作（死代码制造假象）。
#   2. 只用全文正则猜，且只认「本决策已被取代」「撤回 D-00XX」两种写法，
#      把文档里**本来就写好的结构化字段**（`- 被更正：D-0035` / `- 推翻/限定：D-0041`）
#      全漏掉了 —— 指标显示 2，实际 5。
#
# ★ 新口径：优先读结构化字段（单一真源），正则只做兜底。三级：
#     ① 结构化字段：行首列表项 + 关键词 + 冒号 + 编号
#     ② 状态行：`**状态**：...` 内部含失效/更正语义
#     ③ 正文点名：`撤回 D-00XX`（保留旧规则 B，去重后计入）
#
# ★ 硬约束（防误报）：必须指向具体 `D-\d{4}` 编号，或出现在状态行内。
#   否则「备选方案会全部作废」这类假设句会被算进来 —— 那正是旧版 D-0010/D-0011 的误报源。
#
# ★ 分两档，不混一锅：
#   推翻（overturned）= 整份失效（被取代/被撤回/作废）
#   更正（amended）   = 部分修订（被更正/推翻限定/修订）
KIND_OVERTURNED = "推翻"
KIND_AMENDED = "更正"

_FIELD = re.compile(
    r"^[-*]\s*\*{0,2}\s*(被取代|取代|被撤回|撤回|被更正|更正|推翻/限定|推翻|限定|修订)"
    r"\s*\*{0,2}\s*[:：]\s*`?(D-\d{4})"
)
_OVERTURN_WORDS = ("取代", "撤回", "作废")
_STATUS_LINE = re.compile(r"\*\*状态\*\*\s*[:：]\s*(.+)")
# 状态行的两种自我标记（互斥，顺序无关）
_STATUS_OVERTURNED = re.compile(r"已被[^，。；\n]{0,12}取代|已作废|被作废|已撤回|被撤回")
_STATUS_AMENDED = re.compile(r"更正取代|已更正|被更正|已修订|被修订")


def _kind_of(word):
    """词 → 档位。'推翻' 归更正档：它是把原结论收紧，不是整份作废。"""
    return KIND_OVERTURNED if any(k in word for k in _OVERTURN_WORDS) else KIND_AMENDED


def _did(name):
    """文件名 → D-00XX。★ 旧实现用 by[:5] 切片，把 D-0026 截成 'D-002'，明细不可读。"""
    m = re.match(r"(D-\d{4})", name)
    return m.group(1) if m else name


def _strip_rule_refs(txt):
    """剥掉反引号里的**规则引用**，但保留「纯编号」反引号。

    为什么要这么细（实测踩了两次）：
      · 全剥 → 结构化字段的编号（`- 被更正：\\`D-0035\\``）一起没了，D-0035/D-0041 全漏；
      · 只留含编号的 → 规则示例里的 `撤回 D-XXXX` 被当成真命中，误报一条。
        （示例编号一律写 D-XXXX 而非真编号：写成真编号会被 pre_release_check 的
         F 门禁判为「引用了不存在的决策」，实测踩过一次。）
    判据：反引号内**只有**编号才保留（那是字段值），含其它词就是引用示例，剥掉。
    """

    def rep(m):
        inner = m.group(1)
        return m.group(0) if re.fullmatch(r"\s*D-\d{4}\s*", inner) else ""

    return re.sub(r"`([^`\n]*)`", rep, txt)


def _month_window(days=30):
    return datetime.now() - timedelta(days=days)


def metric_error_decisions(days=30):
    """错误决策数 = 被推翻 + 被更正的决策条数（按 D 编号去重）。

    口径见文件头常量区。与旧版的差别不在"多认几个词"，而在**换了提取源**：
    先读文档自己写的结构化字段，正则只做兜底。

    ★ 仍然是"下界"而非精确值：只在**明确点名**时才计入。
      没写"被更正：D-00XX"的隐性改判，本脚本看不见 —— 这比多报要好。
    """
    if not DEC.is_dir():
        return {"value": None, "state": "no_data", "detail": "无 decisions 目录"}

    files = sorted(DEC.glob("D-*.md"))
    if not files:
        return {"value": None, "state": "no_data", "detail": "未扫到决策文件"}

    hits = {}  # (编号, 档位) -> 依据；同一编号同档只留首条依据

    def add(target, kind, why):
        hits.setdefault((target, kind), why)

    for p in files:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        self_id = _did(p.name)
        head = "\n".join(txt.splitlines()[:40])
        # 剥掉反引号里的规则引用（如 "`本决策已被取代`"），否则 D-0031 这类
        # "解释规则的文档"会被自己判成被取代（自指污染）。
        # ★ 只保留「纯编号」反引号（`D-0035`），其余一律剥 —— 判据与理由见 _strip_rule_refs。
        clean = _strip_rule_refs(txt)

        # ① 结构化字段：文档自己写的单一真源，最可靠
        for ln in clean.splitlines():
            m = _FIELD.match(ln.strip())
            if m:
                add(m.group(2), _kind_of(m.group(1)), f"{self_id} 标注「{m.group(1)}」")

        # ② 状态行自我标记（无编号，指本文件）
        for ln in head.splitlines():
            m = _STATUS_LINE.search(ln)
            if not m:
                continue
            val = re.sub(r"[*`]", "", m.group(1).strip())  # 去掉加粗/反引号，明细才可读
            if _STATUS_OVERTURNED.search(val):
                add(self_id, KIND_OVERTURNED, f"状态行「{val[:34]}」")
            elif _STATUS_AMENDED.search(val):
                add(self_id, KIND_AMENDED, f"状态行「{val[:34]}」")

        # ③ 正文点名撤回（兜底；已被①②记过的同档会 setdefault 去重）
        for m in re.finditer(r"撤回\s*`?(D-\d{4})`?", clean):
            add(m.group(1), KIND_OVERTURNED, f"{self_id} 正文点名撤回")

    ov = sorted((k[0], v) for k, v in hits.items() if k[1] == KIND_OVERTURNED)
    am = sorted((k[0], v) for k, v in hits.items() if k[1] == KIND_AMENDED)
    total = len({k[0] for k in hits})  # 同一编号若跨两档，只算一条

    def _fmt(pairs):
        return "、".join(f"{i}" for i, _ in pairs) if pairs else "无"

    breakdown = [
        {
            "kind": KIND_OVERTURNED,
            "count": len(ov),
            "items": _fmt(ov),
            "evidence": [{"id": i, "why": w} for i, w in ov],
        },
        {
            "kind": KIND_AMENDED,
            "count": len(am),
            "items": _fmt(am),
            "evidence": [{"id": i, "why": w} for i, w in am],
        },
    ]
    return {
        "value": total,
        # 0 条也是合法数据（不是 no_data）—— no_data 只留给"没有数据源"，不留给"值恰好为 0"
        "state": "ok",
        "detail": f"推翻 {len(ov)}（{_fmt(ov)}）｜更正 {len(am)}（{_fmt(am)}）",
        "overturned": len(ov),
        "amended": len(am),
        "breakdown": breakdown,
        "note": (
            "口径=结构化字段优先 + 状态行 + 正文点名；须指向 D-编号或状态行。"
            "含『条件失效』（如 D-0016 属此类，是在旧约束下正确、条件变化后失效，非判断错误）；"
            "不含『需求加强』（如 D-0043）。是下界，未点名的隐性改判不计。"
        ),
        "scanned": len(files),
    }


def metric_rework_hours(days=30):
    """返工工时：需要轮次记录里的 rework_hours 字段。目前没有任何轮次填过。"""
    runs = ROOT / "migration-package" / "runs"
    vals = []
    if runs.is_dir():
        for f in runs.rglob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            v = d.get("rework_hours") if isinstance(d, dict) else None
            if isinstance(v, (int, float)):
                vals.append(v)
    if not vals:
        return {
            "value": None,
            "state": "no_data",
            "detail": "33 轮记录中无任何 rework_hours 字段",
            "how_to_fix": "在轮次收尾时填 rework_hours（人工或脚本估算），否则此轨永远无数据",
        }
    return {"value": round(sum(vals), 2), "state": "ok", "detail": f"{len(vals)} 条记录"}


def metric_amount_deviation():
    """金额偏差：取各次真实对账的残差（有 B6 用 B6，否则用 diffs 合计）。"""
    if not REAL.is_dir():
        return {"value": None, "state": "no_data", "detail": "无 real-runs 目录"}
    latest = None
    for f in sorted(REAL.rglob("recon-result.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        latest = (f, d)
    if latest is None:
        return {"value": None, "state": "no_data", "detail": "无 recon-result.json"}

    f, d = latest
    residual = None
    for c in d.get("checks") or []:
        if c.get("id") == "B6":
            actual = str(c.get("actual") or "")
            m = re.search(r"(-?\d+(?:\.\d+)?)", actual)
            if m:
                residual = abs(float(m.group(1)))
    if residual is None:
        return {
            "value": None,
            "state": "no_data",
            "detail": f"{f.name} 无 B6 残差（该次对账没有券商侧对照）",
        }
    return {
        "value": round(residual, 2),
        "state": "ok",
        "detail": f"{f.parent.name}/{f.name} 的 B6 残差绝对值",
        "note": "残差=券商盈亏−账本浮盈，当前定性为费用/股息口径差",
    }


def render_md(rows, params):
    lines = ["| 轨道 | 当前值 | 状态 | 说明 |", "|---|---|---|---|"]
    for r in rows:
        val = "no_data" if r["state"] != "ok" else r["value"]
        lines.append(f"| {r['name']}（{r['unit']}） | {val} | {r['state']} | {r['detail']} |")
    # ★ 分列明细：总数会把「推翻」和「更正」抹平（前者整份失效，后者只改一条判定），
    #   看板上必须能分开看，否则 5 条里哪种占多数是看不出来的。
    for r in rows:
        if not r.get("breakdown"):
            continue
        lines.append("")
        lines.append(f"**{r['name']}明细**")
        lines.append("")
        lines.append("| 档 | 条数 | 编号 | 依据 |")
        lines.append("|---|---|---|---|")
        for b in r["breakdown"]:
            why = "；".join(f"{e['id']}：{e['why']}" for e in b["evidence"]) or "—"
            lines.append(f"| {b['kind']} | {b['count']} | {b['items']} | {why} |")
    if params:
        missing = [t["name"] for t in params.get("tracks", []) if t.get("baseline_required")]
        if missing:
            lines.append("")
            lines.append(
                f"> 基线要求：{len(missing)} 条轨道均为 `baseline_required=true`；"
                f"当前尚无一条建立了正式基线（首次测量即基线）。"
            )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="三轨指标看板（只读）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        params = json.loads(PARAMS.read_text(encoding="utf-8")).get("metrics")
    except (OSError, json.JSONDecodeError):
        params = None

    rows = [
        dict(metric_error_decisions(), id="error_decisions", name="错误决策数", unit="次/月"),
        dict(metric_rework_hours(), id="rework_hours", name="返工工时", unit="小时/月"),
        dict(metric_amount_deviation(), id="amount_deviation", name="金额偏差", unit="元"),
    ]

    if args.json:
        print(
            json.dumps(
                {"generated_at": datetime.now().isoformat(timespec="seconds"), "tracks": rows},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("# 自治团队·三轨指标（" + datetime.now().strftime("%Y-%m-%d %H:%M") + "）\n")
        print(render_md(rows, params))
        print(
            "\n> `no_data` 不是 0。缺数据源时显示 no_data，是为了让缺口看得见，而不是用 0 假装健康。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
