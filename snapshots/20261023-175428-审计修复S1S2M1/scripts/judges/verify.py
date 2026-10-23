#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py —— 硬裁判 JD-01

设计原则（对应 docs/protocol/04-双裁判规范.md 的三条铁律）：
  1. 不只检查「能不能跑通」，必须检查「跑通了是否正确」
     —— MAST 案例：生成的国际象棋程序能编译，但不遵守规则。
  2. 判 fail 不可被静默覆盖。本脚本只输出裁决；覆盖动作必须由 PM 留痕完成。
  3. 输出必须含可回读证据 + sha256（外部钩子，结构审计第 2 查的判定依据）。

判据分层（对应 docs/protocol/06-写入域与判据分层.md）：
  * arithmetic 层 —— 纯算术锚点，硬裁判自动裁决，**有否决权**
  * adjudication 层 —— 判断项，**不含 expected**，交人类闸门；未裁决时退出码 3
  三道 fail-closed 闸门：算术层路径白名单 / 金额必须用 decimal_equals / 人类闸门防伪

依赖：仅 Python 标准库。可在 Windows / macOS / Linux 运行。

用法:
    # legacy 单层 spec（向后兼容，会打弃用警告）
    python3 verify.py --spec truth.json --answer answer.json

    # 分层：算术层自动裁决
    python3 verify.py --spec spec/arithmetic.json --spec-layer arithmetic --answer answer.json

    # 分层：判断层未裁决 → verdict=pending_adjudication, exit 3
    python3 verify.py --spec spec/adjudication.json --spec-layer adjudication --answer answer.json

    # 分层：人类裁决后重跑 → verdict=adjudicated
    python3 verify.py --spec spec/adjudication.json --spec-layer adjudication \\
        --record adjudication-record.json --answer answer.json

    # 一次性跑两层
    python3 verify.py --spec-dir spec --answer answer.json

    # 只校验 spec 合规性，不比对答案
    python3 verify.py --spec spec/arithmetic.json --lint-spec

    python3 verify.py --self-test

退出码:
    0 = pass / adjudicated
    1 = fail
    2 = 错误（输入缺失 / 格式错误 / spec 违规）
    3 = pending_adjudication（存在未裁决的判断项）
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation
from pathlib import Path

# ---------------------------------------------------------------- 常量

TZ_CN = timezone(timedelta(hours=8))

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2
EXIT_PENDING = 3

# ★ 算术层路径白名单 —— 只有「仅凭输入数据 + 已声明规则即可唯一算出」的量
#   才允许出现在 arithmetic 层。凡比对型判据的 path 不在此列 → 拒载。
#   这 7 条恰好把 diff_count / diffs / amount_diff_total / excluded_amount_total
#   机械地挡在算术层之外 —— 白名单本身就是设计要求，不靠人自觉。
ARITHMETIC_ANCHOR_PATHS = {
    "summary.ledger_rows",
    "summary.bill_rows",
    "summary.dedup_removed_txn_count",
    "summary.dedup_amount",
    "summary.ledger_total",
    "summary.bill_total",
    "summary.raw_total_gap",
}

# 金额类路径 —— 必须用 decimal_equals 并声明 scale，不得用 float 语义的 numeric_equals
MONEY_PATHS = {
    "summary.dedup_amount",
    "summary.ledger_total",
    "summary.bill_total",
    "summary.raw_total_gap",
    "summary.amount_diff_total",
    "summary.excluded_amount_total",
    # 契约 3.0：费用差独立承载（此前被塞进 excluded_amount_total，违反 D-0016）
    "summary.fee_diff_total",
}

# 比对型检查类型（这些类型会在算术层触发白名单校验）
COMPARISON_TYPES = {
    "numeric_equals", "decimal_equals", "bool_equals", "string_equals",
    "count_equals", "set_equals", "list_equals_unordered", "sum_equals",
}

CENT = Decimal("0.01")


class SpecViolation(Exception):
    """spec 违规 —— 一律 fail-closed（退出码 2）。"""


# ---------------------------------------------------------------- 工具

def now_iso():
    """ISO8601（带 +08:00 偏移），跨平台一致。"""
    return datetime.now(TZ_CN).replace(microsecond=0).isoformat()


def sha256_of(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def load_json(path):
    """显式 utf-8 + parse_float=Decimal —— 金额从 JSON 字面量直接构造 Decimal，绕过 float。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f, parse_float=Decimal)
    except OSError as e:
        raise SystemExit(f"[ERR] 无法读取 {path}: {e}")
    except (json.JSONDecodeError, ValueError) as e:
        raise SystemExit(f"[ERR] {path} 不是合法 JSON: {e}")


_MISSING = object()


def to_dec(v):
    """任意数值 → Decimal（经字符串，规避 float 二进制误差）。"""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, int):
        return Decimal(v)
    return Decimal(str(v))


def resolve(obj, path):
    """按点号路径取值，支持 list[index]。取不到返回 _MISSING。"""
    cur = obj
    if not path:
        return cur
    for part in path.split("."):
        m = re.match(r"^([^\[]*)\[(-?\d+)\]$", part)
        try:
            if m:
                key, idx = m.group(1), int(m.group(2))
                if key:
                    if not isinstance(cur, dict) or key not in cur:
                        return _MISSING
                    cur = cur[key]
                if not isinstance(cur, (list, tuple)):
                    return _MISSING
                if idx >= len(cur) or idx < -len(cur):
                    return _MISSING
                cur = cur[idx]
            else:
                if not isinstance(cur, dict) or part not in cur:
                    return _MISSING
                cur = cur[part]
        except (TypeError, KeyError, IndexError):
            return _MISSING
    return cur


def pluck(seq, key):
    out = []
    for item in seq or []:
        if isinstance(item, dict) and key in item:
            out.append(item[key])
        elif key is None:
            out.append(item)
    return out


def norm_scalar(v):
    """归一化用于比较。Decimal 保持原样（Decimal('1.0') == Decimal('1.00')）。"""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        return v.strip()
    return v


def _jsonable(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"无法序列化: {type(o).__name__}")


def brief(v, limit=120):
    if v is _MISSING:
        return "<字段不存在>"
    if isinstance(v, str):
        s = v
    else:
        s = json.dumps(v, ensure_ascii=False, default=_jsonable)
    return s if len(s) <= limit else s[:limit] + "…"


# ---------------------------------------------------------------- 检查类型

def chk_numeric_equals(path, expected, tol, answer, key=None):
    cur = resolve(answer, path)
    if key:
        cur = pluck(cur if isinstance(cur, list) else [], key)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if isinstance(cur, list):
        if len(cur) != 1:
            return False, f"期望单值，实际为列表({len(cur)} 项)"
        cur = cur[0]
    try:
        got = to_dec(cur)
        exp = to_dec(expected)
    except (TypeError, ValueError, InvalidOperation):
        return False, f"非数值: {brief(cur)}"
    if tol:
        return abs(got - exp) <= to_dec(tol), f"实际 {got} vs 期望 {exp} (tol={tol})"
    return got == exp, f"实际 {got} vs 期望 {exp}"


def chk_decimal_equals(path, expected, scale, tol, answer, key=None):
    """金额专用：Decimal + 显式刻度。scale 缺省 2。"""
    scale = 2 if scale is None else int(scale)
    q = Decimal(1).scaleb(-scale)
    cur = resolve(answer, path)
    if key:
        cur = pluck(cur if isinstance(cur, list) else [], key)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if isinstance(cur, list):
        if len(cur) != 1:
            return False, f"期望单值，实际为列表({len(cur)} 项)"
        cur = cur[0]
    try:
        got = to_dec(cur).quantize(q, rounding=ROUND_HALF_EVEN)
        exp = to_dec(expected).quantize(q, rounding=ROUND_HALF_EVEN)
    except (TypeError, ValueError, InvalidOperation):
        return False, f"非金额值: {brief(cur)}"
    if tol is None:
        return got == exp, f"实际 {got} vs 期望 {exp}（Decimal 精确相等，scale={scale}）"
    return abs(got - exp) <= to_dec(tol), f"实际 {got} vs 期望 {exp} (tol={tol}, scale={scale})"


def chk_bool_equals(path, expected, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if not isinstance(cur, bool):
        return False, f"非布尔: {brief(cur)}"
    return cur is bool(expected), f"实际 {cur} vs 期望 {expected}"


def chk_string_equals(path, expected, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    got = norm_scalar(cur)
    exp = norm_scalar(expected)
    return got == exp, f"实际 {brief(got)} vs 期望 {brief(exp)}"


def chk_count_equals(path, expected, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if not isinstance(cur, (list, dict, str)):
        return False, f"不可计数类型: {type(cur).__name__}"
    n = len(cur)
    return n == int(expected), f"实际 {n} 项 vs 期望 {expected} 项"


def _as_set(cur, key):
    vals = pluck(cur, key) if key else (cur if isinstance(cur, list) else [cur])
    return [norm_scalar(v) for v in vals]


def chk_set_equals(path, expected, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if not isinstance(cur, list):
        return False, f"期望列表，实际 {type(cur).__name__}"
    got = set(_as_set(cur, key))
    exp = set(norm_scalar(v) for v in expected)
    if got == exp:
        return True, f"集合一致（{len(exp)} 项）"
    missing = sorted(exp - got, key=str)
    extra = sorted(got - exp, key=str)
    parts = []
    if missing:
        parts.append(f"缺失 {missing}")
    if extra:
        parts.append(f"多出 {extra}")
    return False, "；".join(parts)


def chk_list_equals_unordered(path, expected, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if not isinstance(cur, list):
        return False, f"期望列表，实际 {type(cur).__name__}"
    got = sorted((repr(norm_scalar(v)) for v in _as_set(cur, key)))
    exp = sorted((repr(norm_scalar(v)) for v in expected))
    return got == exp, f"实际 {len(got)} 项 / 期望 {len(exp)} 项"


def chk_sum_equals(path, expected, tol, answer, key):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段缺失: {path}"
    if not isinstance(cur, list):
        return False, f"期望列表，实际 {type(cur).__name__}"
    vals = pluck(cur, key) if key else cur
    try:
        total = sum((to_dec(v) for v in vals), Decimal(0))
    except (TypeError, ValueError, InvalidOperation):
        return False, f"存在非数值项: {brief(vals)}"
    if tol is None:
        return total == to_dec(expected), f"实际合计 {total} vs 期望 {expected}（Decimal 精确）"
    return abs(total - to_dec(expected)) <= to_dec(tol), f"实际合计 {total} vs 期望 {expected}"


def chk_present(path, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return False, f"字段不存在: {path}"
    if cur is None or (isinstance(cur, (list, dict, str)) and len(cur) == 0):
        return False, f"字段为空: {path}"
    return True, f"存在且非空: {brief(cur)}"


def chk_absent(path, answer, key=None):
    cur = resolve(answer, path)
    if cur is _MISSING:
        return True, "字段不存在（符合预期）"
    if cur is None or (isinstance(cur, (list, dict, str)) and len(cur) == 0):
        return True, "字段为空（符合预期）"
    return False, f"本应为空，实际: {brief(cur)}"


def chk_present_or_note(c, answer):
    """双路径断言：主字段非空即通过；为空/缺失则必须由 note 字段给出理由。

    契约依据（D-0024 收口）：TASK.md §C 允许 `excluded_categories: []`，但原
    `present` 断言会把空数组判 fail，逼人编造类别。本判据使「空」与「漏填」可区分：
      - 列表非空            → pass
      - 列表为空/缺失 + note 非空 → pass（记明为何无类别）
      - 列表为空/缺失 + note 缺失/空 → fail

    note 字段名由 spec 的 `note_path` 指定，不硬编码 —— 便于复用到其他可空列表字段。
    """
    path = c.get("path")
    note_path = c.get("note_path")
    cur = resolve(answer, path)

    # 路径 1：主字段有实质内容
    if cur is not _MISSING and cur not in (None, "", [], {}):
        n = len(cur) if isinstance(cur, (list, dict)) else 1
        return True, f"已声明 {n} 项"

    # 路径 2：主字段为空，要求 note 说明
    label = brief(cur) if cur is not _MISSING else "字段缺失"
    if not note_path:
        return False, f"{path} 为空（{label}）且 spec 未指定 note_path，无法判定理由"
    note = resolve(answer, note_path)
    if note is not _MISSING and isinstance(note, str) and note.strip():
        return True, f"{path} 为空（{label}）但有说明：{brief(note)}"
    return False, (f"{path} 为空（{label}）且 {note_path} 未给出说明 —— "
                   f"「空」与「漏填」不可区分。请补充说明，或列出实际排除的类别。")


CHECK_TYPES = {
    "numeric_equals": lambda c, a: chk_numeric_equals(
        c["path"], c["expected"], c.get("tol", 0), a, c.get("key")),
    "decimal_equals": lambda c, a: chk_decimal_equals(
        c["path"], c["expected"], c.get("scale", 2), c.get("tol"), a, c.get("key")),
    "bool_equals": lambda c, a: chk_bool_equals(c["path"], c["expected"], a, c.get("key")),
    "string_equals": lambda c, a: chk_string_equals(c["path"], c["expected"], a, c.get("key")),
    "count_equals": lambda c, a: chk_count_equals(c["path"], c["expected"], a, c.get("key")),
    "set_equals": lambda c, a: chk_set_equals(c["path"], c["expected"], a, c.get("key")),
    "list_equals_unordered": lambda c, a: chk_list_equals_unordered(
        c["path"], c["expected"], a, c.get("key")),
    "sum_equals": lambda c, a: chk_sum_equals(
        c["path"], c["expected"], c.get("tol"), a, c.get("key")),
    "present": lambda c, a: chk_present(c["path"], a, c.get("key")),
    "present_or_note": lambda c, a: chk_present_or_note(c, a),
    "absent": lambda c, a: chk_absent(c["path"], a, c.get("key")),
}


# ---------------------------------------------------------------- answer 侧类型 lint

NON_NEGATIVE_MONEY = {
    # 契约 3.0 §符号语义：这三项是「绝对值合计」，负值即语义错误。
    # 实测 33 轮 × 4 席位 = 132 次读数，负值出现 0 次，故本断言零回溯影响。
    # 刻意不含 raw_total_gap —— 它是 bill_total − ledger_total，可为负（少报场景）。
    "summary.excluded_amount_total",
    "summary.fee_diff_total",
    "summary.dedup_amount",
}


def lint_answer_money_types(answer):
    """金额字段类型 lint：MONEY_PATHS 下的被验值必须是 JSON number。

    为什么需要：`chk_decimal_equals` 用 `to_dec()` 容忍字符串（"380.00"），
    数值断言照常通过，但类型契约已破。实测 5 轮（r6/EX-01、r7/EX-03、r10/EX-03、
    r11/EX-01、r13/EX-02）产出字符串金额而 13/13 全绿。

    后果：① 跨轮 diff 产生伪差异（T-04/EX-01 的 "26640.00"→26640.0 曾被误判为翻边）；
          ② 下游若 sum()/float() 直接消费会崩，而回归测不出。

    设计取舍：本函数**不改变 verdict**，只产出 warnings —— 历史 5 轮是已签字的
    证据链产物，不应因类型形态被回溯改判。`--strict-money-type` 可升级为 fail，
    用于新产出的准入校验（保证检测本身可证伪）。

    例外：`E_` 前缀（目前仅符号越界）属实质错误，调用方**无条件**计入失败判据，
    不受 strict 开关控制 —— 负值不是"形态问题"，是算错了。
    """
    warnings = []
    if not isinstance(answer, dict):
        return warnings
    for path in sorted(MONEY_PATHS):
        cur = resolve(answer, path)
        if cur is _MISSING:
            continue
        if isinstance(cur, str):
            warnings.append({
                "code": "W_MONEY_STRING",
                "path": path,
                "value": brief(cur),
                "detail": (f"金额字段为字符串 {cur!r}，应为 JSON number。"
                           "数值断言仍通过（to_dec 容忍），但类型契约已破。"),
            })
        elif isinstance(cur, bool) or not isinstance(cur, (int, float, Decimal)):
            warnings.append({
                "code": "W_MONEY_TYPE",
                "path": path,
                "value": brief(cur),
                "detail": f"金额字段类型为 {type(cur).__name__}，非 number。",
            })

        # 符号越界（E_ 前缀 → 无条件 fail）
        if path in NON_NEGATIVE_MONEY:
            try:
                if to_dec(cur) < 0:
                    warnings.append({
                        "code": "E_MONEY_NEGATIVE",
                        "path": path,
                        "value": brief(cur),
                        "detail": (f"该字段语义为绝对值合计，不允许为负（实际 {cur}）。"
                                   "契约 3.0 §符号语义：excluded/fee/dedup 三量均取绝对值。"),
                    })
            except (TypeError, ValueError, InvalidOperation):
                pass
    return warnings


def money_type_failures(warnings, strict=False):
    """把金额 lint 结果转成失败判据条目。

    E_ 前缀（符号越界）**无条件**生效 —— 算错了就是算错了，不是形态偏好。
    W_ 前缀（类型漂移）仅在 --strict-money-type 下生效，避免回溯改判历史证据链。

    只处理 `E_MONEY*` / `W_MONEY*`；类别枚举等其他 lint 不在此升级
    （`--strict-money-type` 语义上不应波及非金额项）。
    """
    out = []
    for w in warnings:
        code = w["code"]
        if not (code.startswith("E_MONEY") or code.startswith("W_MONEY")):
            continue
        is_err = code.startswith("E_")
        if is_err or strict:
            out.append({
                "name": f"{code} {w['path']}",
                "type": "money_type",
                "ok": False,
                "expected": "非负 JSON number" if is_err else "JSON number",
                "actual": w["detail"],
                "weight": 1,
            })
    return out


# 契约 3.0 §类别枚举：差异类别的封闭集合。`other:<说明>` 为受控兜底。
# 依据：实测 26 份产出共 44 种写法，"股数/送转未更新"一个语义就有 19 种变体
#（股数差异-送转未更新 / 数量差-送转未更新 / SHARE_QTY_DIFF / 股数不符(送转) …），
# 导致任何按类别聚合的统计都不可靠 —— 与 fee 字段问题同源：同一语义无单一真源。
CATEGORY_ENUM = {
    "股数不符",   # shares 不一致（送转/权益变动未更新）
    "日期口径差",  # date 不一致（自记账按交割日、券商按成交日）
    "费用差",     # fee_total 不一致（漏记/费率错）
    "金额不符",   # amount 不一致
    "单侧缺失",   # 仅一侧存在（非交易性现金流/股息/误入账）
    "重复单号",   # 同 txn_id 多行（分批成交合并记账）
}
CATEGORY_OTHER_PREFIX = "other:"


def lint_answer_categories(answer):
    """`summary.excluded_categories` 的枚举 + 子集双重 lint（契约 3.0 §类别枚举）。

    **语义澄清（2026-10-20 三次核实后的定论）**：
    该字段是「**被排除出金额差异的类别清单**」，语义与字段名一致，**不是**「差异类别全集」。
    它天然是 `diffs[].category` 的**真子集**：
      - diffs 列全部差异（含被排除的类别）；
      - excluded_categories 只列其中「不计入金额差异」的那几类。
    因此 `excluded_categories` 非空而 `excluded_amount_total == 0` **不矛盾** ——
    日期差、股数差、费用差这些类别的 amount 差本就是 0。

    两项校验：
      1. 枚举：值须在 CATEGORY_ENUM 或 `other:<说明>` → `W_CATEGORY_NOT_ENUM`
      2. 子集：值须出现在 diffs[].category 中 → `W_CATEGORY_NOT_IN_DIFFS`
         （防止凭空编造类别名，实测 2/26 份存在此问题）

    均不改变 verdict：历史产出是已签字的，不因命名不规范回溯改判。
    """
    warnings = []
    if not isinstance(answer, dict):
        return warnings
    cats = resolve(answer, "summary.excluded_categories")
    if cats is _MISSING or not isinstance(cats, list) or not cats:
        return warnings

    # ---- 校验 1：封闭枚举 ----
    bad = []
    for cat in cats:
        if not isinstance(cat, str):
            bad.append(repr(cat))
            continue
        if cat.startswith(CATEGORY_OTHER_PREFIX) and len(cat) > len(CATEGORY_OTHER_PREFIX):
            continue          # other:<说明> 合法
        if cat not in CATEGORY_ENUM:
            bad.append(cat)
    if bad:
        head = "、".join(bad[:3])
        more = f" 等 {len(bad)} 项" if len(bad) > 3 else ""
        warnings.append({
            "code": "W_CATEGORY_NOT_ENUM",
            "path": "summary.excluded_categories",
            "value": head,
            "detail": (f"非枚举类别：{head}{more}。契约 3.0 允许的值："
                       f"{sorted(CATEGORY_ENUM)}，兜底写 other:<说明>。"
                       "聚合统计依赖枚举一致性。"),
        })

    # ---- 校验 2：必须是 diffs[].category 的子集 ----
    diffs = resolve(answer, "diffs")
    if isinstance(diffs, list) and diffs:
        in_diffs = {str(x.get("category")) for x in diffs
                    if isinstance(x, dict) and x.get("category")}
        orphan = [c for c in cats if isinstance(c, str)
                  and not c.startswith(CATEGORY_OTHER_PREFIX) and c not in in_diffs]
        if orphan:
            head = "、".join(orphan[:3])
            more = f" 等 {len(orphan)} 项" if len(orphan) > 3 else ""
            warnings.append({
                "code": "W_CATEGORY_NOT_IN_DIFFS",
                "path": "summary.excluded_categories",
                "value": head,
                "detail": (f"这些类别未出现在 diffs[].category 中：{head}{more}。"
                           "excluded_categories 应是被排除类别的**子集**，"
                           "不能凭空新增未在 diffs 中出现的类别名。"),
            })
    return warnings


# ---------------------------------------------------------------- spec 校验（三道闸门）

def lint_arithmetic(spec):
    """闸门 1 + 2：算术层路径白名单 / 金额必须用 decimal_equals。"""
    checks = spec.get("checks") or []
    if not checks:
        raise SpecViolation("arithmetic spec 中没有任何 checks —— 硬裁判不允许空判据")
    for c in checks:
        path = c.get("path")
        ctype = c.get("type")
        if ctype not in CHECK_TYPES:
            raise SpecViolation(f"未知检查类型: {ctype}（name={c.get('name')}）")
        if ctype in COMPARISON_TYPES:
            if path not in ARITHMETIC_ANCHOR_PATHS:
                raise SpecViolation(
                    f"疑似判断项混入算术层（path={path}, name={c.get('name')}）。"
                    f"算术层只允许这些路径：{sorted(ARITHMETIC_ANCHOR_PATHS)}。"
                    "请移至 adjudication.json。"
                )
            if path in MONEY_PATHS and ctype != "decimal_equals":
                raise SpecViolation(
                    f"金额路径必须使用 decimal_equals 并声明 scale（path={path}, 实际 type={ctype}）。"
                    "依据：float 累加会偏离两位小数，容差能掩盖但掩盖不等于正确。"
                )


def lint_adjudication(spec):
    """闸门 3a：判断项不得含 expected。"""
    items = spec.get("items") or []
    if not items:
        raise SpecViolation("adjudication spec 中没有任何 items")
    for it in items:
        if "expected" in it:
            raise SpecViolation(
                f"判断项 {it.get('id')} 含 expected 键 —— 人类闸门设计禁止此项。"
                "判断项的选项应放在 options[].implies，机器侧不得存在可被误当真值的量。"
            )
        if not it.get("options"):
            raise SpecViolation(f"判断项 {it.get('id')} 没有任何 options")
        for o in it["options"]:
            if "implies" not in o:
                raise SpecViolation(f"判断项 {it.get('id')} 的选项 {o.get('id')} 缺少 implies")


def lint_record(record, adj_spec=None):
    """闸门 3b：人类裁决记录防伪（只能校验声明字段，无法证明由人撰写）。

    ★ 2026-10-19 增补：若传入 adj_spec，则 decisions 的 id 必须**恰好**覆盖
      spec 的 items —— 多出也算违规。理由：`ADJ-003` 已因 F15 退役，
      旧 schema 的记录必须【响】（exit 2），而不是被静默忽略后
      让「多签了一项」看起来像签成了。
    """
    if record.get("source") != "human_adjudication":
        raise SpecViolation(
            "adjudication record 的 source 必须是 'human_adjudication'。"
            "禁止把模型自我裁决伪装成人类裁决。"
        )
    if not str(record.get("recorded_by") or "").strip():
        raise SpecViolation("adjudication record 的 recorded_by 不得为空（须为人类署名）")
    decisions = record.get("decisions") or []
    if not decisions:
        raise SpecViolation("adjudication record 没有任何 decisions")

    if adj_spec is not None:
        want = [it.get("id") for it in adj_spec.get("items") or []]
        got = [d.get("id") for d in decisions]
        unknown = [i for i in got if i not in want]
        if unknown:
            raise SpecViolation(
                f"adjudication record 含 spec 之外的判断项：{unknown}；"
                f"本 spec 只有 {want}（已退役项不得再签）"
            )
        dup = sorted({i for i in got if got.count(i) > 1})
        if dup:
            raise SpecViolation(f"adjudication record 的判断项重复：{dup}")


def lint_spec(spec, path=None):
    """按 spec 自身声明的 layer 校验。legacy 单层 spec **不做**白名单/金额闸门校验。"""
    layer = spec.get("layer")
    if layer == "arithmetic":
        lint_arithmetic(spec)
    elif layer == "adjudication":
        lint_adjudication(spec)
    elif layer is None:
        # legacy：只校验检查类型合法性
        for c in spec.get("checks") or []:
            if c.get("type") not in CHECK_TYPES:
                raise SpecViolation(f"未知检查类型: {c.get('type')}（name={c.get('name')}）")
    else:
        raise SpecViolation(f"未知 layer: {layer}（应为 arithmetic / adjudication，或省略表示 legacy）")
    return layer


# ---------------------------------------------------------------- 判断项物化

def infer_check(path, value):
    """由 implies 的 (path, value) 推断应使用的检查类型。"""
    if path in MONEY_PATHS:
        return {"type": "decimal_equals", "scale": 2}
    if isinstance(value, list):
        return {"type": "set_equals", "key": "id"}
    if isinstance(value, bool):
        return {"type": "bool_equals"}
    if isinstance(value, (int, Decimal)):
        return {"type": "numeric_equals"}
    return {"type": "string_equals"}


def materialize_adjudication(adj_spec, record):
    """把人类选择的选项物化为可执行的 checks。"""
    chosen = {d.get("id"): d.get("chosen") for d in record.get("decisions", [])}
    checks = []
    provenance = []
    for it in adj_spec.get("items", []):
        iid = it.get("id")
        if iid not in chosen:
            raise SpecViolation(f"adjudication record 未对 {iid} 作出裁决")
        pick = chosen[iid]
        opt = next((o for o in it["options"] if o.get("id") == pick), None)
        if opt is None:
            raise SpecViolation(
                f"{iid} 的裁决 '{pick}' 不在可选范围内 "
                f"{[o.get('id') for o in it['options']]}"
            )
        for path, val in (opt.get("implies") or {}).items():
            c = {"name": f"{iid} → {path}", "path": path, "expected": val}
            c.update(infer_check(path, val))
            checks.append(c)
            provenance.append({
                "id": iid, "chosen": pick, "path": path,
                "source": "human_adjudication",
                "decided_by": record.get("recorded_by"),
                "rationale": next((d.get("rationale") for d in record["decisions"] if d.get("id") == iid), ""),
            })
    return checks, provenance


# ---------------------------------------------------------------- 执行

def run_checks(spec_or_checks, answer):
    checks = spec_or_checks.get("checks") if isinstance(spec_or_checks, dict) else spec_or_checks
    checks = checks or []
    if not checks:
        raise SystemExit("[ERR] 没有任何 checks —— 硬裁判不允许空判据")
    results = []
    for c in checks:
        name = c.get("name") or c.get("path") or "unnamed"
        ctype = c.get("type")
        fn = CHECK_TYPES.get(ctype)
        if fn is None:
            results.append({
                "name": name, "type": ctype, "ok": False,
                "expected": brief(c.get("expected")) if "expected" in c else "",
                "actual": f"未知检查类型: {ctype}", "weight": c.get("weight", 1),
            })
            continue
        try:
            ok, detail = fn(c, answer)
        except (KeyError, TypeError, ValueError, InvalidOperation) as e:
            ok, detail = False, f"检查执行异常: {type(e).__name__}: {e}"
        results.append({
            "name": name, "type": ctype, "ok": bool(ok),
            "expected": brief(c.get("expected")) if "expected" in c else "",
            "actual": detail,
            "weight": c.get("weight", 1),
        })
    return results


def build_result(task_id, spec_layer, results, pending, adjudication_source, evidence,
                 carries_undeclared_judgment=False, warnings=None):
    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    if failed:
        verdict = "fail"
    elif pending:
        verdict = "pending_adjudication"
    elif adjudication_source == "human":
        verdict = "adjudicated"
    else:
        verdict = "pass"

    return {
        "task_id": task_id,
        "verdict": verdict,
        "judge": "JD-01",
        # 2026-10-20 修正笔误：原为 scripts/judge/verify.py（缺 s）。
        # 仅新产出生效；历史 verdict 的该字段保留原样，不回溯改写。
        "judge_impl": "scripts/judges/verify.py (non-LLM, stdlib-only)",
        "spec_layer": spec_layer,
        "summary": {
            "total": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "pending": len(pending),
        },
        "checks": results,
        "pending": pending,
        "warnings": list(warnings or []),
        "adjudication_source": adjudication_source,
        "carries_undeclared_judgment": carries_undeclared_judgment,
        "evidence": evidence,
        "ts": now_iso(),
        "override_policy": (
            "本裁决不可被静默覆盖。PM 若要推进 fail 的动作，必须留痕："
            "「覆盖 JD-01 的 fail 判定。风险评级：__。不可逆：__。理由：__。承担人：PM-01。」"
        ),
    }


def verdict_to_exit(verdict):
    return {"pass": EXIT_PASS, "adjudicated": EXIT_PASS,
            "fail": EXIT_FAIL, "pending_adjudication": EXIT_PENDING}.get(verdict, EXIT_ERROR)


# ---------------------------------------------------------------- 待裁状态文件（AD-01 第 7 查）

_STATE_DEF = (
    "fail := 任一物化判据 not ok；"
    "pending := 无 --record 且 spec 含 ≥1 判断项；"
    "adjudicated := 有 --record 且 lint_record 通过且物化判据全部 ok；"
    "not_applicable := spec 不含判断项（verdict=pass）"
)


def _verdict_digest(path):
    """把一个 verdict 文件压成可对照的字段集合。

    ★ 记 sha256 而不记字段是不行的（见 build_answer_status 的说明）；
      只记字段不记 sha 又无法证明「读的是同一份文件」—— 但 ts 已经起了这个作用：
      它由 now_iso() 在文件写完之后才生成，抄对了就说明至少读过一次。
    """
    d = load_json(path) if path else None
    if not isinstance(d, dict):
        return None
    return {
        "path": Path(path).as_posix(),
        "verdict": d.get("verdict"),
        "spec_layer": d.get("spec_layer"),
        "pending_count": (d.get("summary") or {}).get("pending"),
        "ts": d.get("ts"),
        "anchored_by": "field（不用 sha256：verdict 含 ts，重算即变，会把非语义噪声编进指纹）",
    }


def build_answer_status(result, answer_path, spec_path, record_path=None,
                        source_verdict=None, post_hoc=False):
    """由 JD-01 的结果派生【机器可读的待裁状态】。

    ★ 为什么不改 answer-final.json 加字段：PM-01 是唯一写入者，工具改写它会破坏
      「单写多读」；且它的 sha256 已被 verdict-layered.json / pending-adjudication.json
      锚定，改了等于让那两份证据指向另一份文件。
    ★ 为什么用 answer 的 sha256 做【唯一强锚】：answer 由 PM 一次写成、之后只读，
      是唯一不会因重算而漂的量。
    """
    state = {"fail": "fail", "pending_adjudication": "pending",
             "adjudicated": "adjudicated", "pass": "not_applicable"}.get(
                 result.get("verdict"), "fail")
    pending = result.get("pending") or []
    answered = []
    if record_path:
        rec = load_json(record_path) or {}
        answered = [d.get("id") for d in (rec.get("decisions") or []) if isinstance(d, dict)]
    return {
        "task_id": result.get("task_id"),
        "spec_layer": result.get("spec_layer"),
        "adjudication_state": state,
        "adjudication_state_definition": _STATE_DEF,
        "pending_items": [p.get("id") for p in pending if isinstance(p, dict)],
        "pending_count": len(pending),
        "answered_items": answered,
        "verdict": result.get("verdict"),
        "answer": {"path": Path(answer_path).as_posix(), "sha256": sha256_of(answer_path)},
        "spec": {"path": Path(spec_path).as_posix() if spec_path else None,
                 "sha256": sha256_of(spec_path) if spec_path else None},
        "record": ({"path": Path(record_path).as_posix(), "sha256": sha256_of(record_path)}
                   if record_path else None),
        "source_verdict": source_verdict,
        "generated_by": "judges/verify.py",
        "provenance": "post_hoc_derived" if post_hoc else "native",
        "ts": now_iso(),
    }


def pending_view(adj_spec):
    return [
        {
            "id": it.get("id"),
            "question": it.get("question"),
            "paths_affected": it.get("paths_affected", []),
            # ★ D-0020（2026-10-20）：pending 投影带选项 label（纯数字选项不可签）。
            "options": [{"id": o.get("id"), "label": o.get("label") or ""}
                        for o in it.get("options", [])],
        }
        for it in adj_spec.get("items", [])
    ]


# ---------------------------------------------------------------- 自检

def self_test():
    """内置自检：硬裁判自己也要能被验证。含 exit 2 类闸门用例。"""
    failures = []
    n_ok = 0

    def expect(label, cond, detail=""):
        nonlocal n_ok
        if cond:
            n_ok += 1
            print(f"  [PASS] {label}" + (f"  {detail}" if detail else ""))
        else:
            failures.append(label)
            print(f"  [FAIL] {label}  {detail}")

    def expect_violation(label, fn):
        try:
            fn()
            expect(label, False, "未抛出 SpecViolation")
        except SpecViolation as e:
            expect(label, True, f"已拒载：{str(e)[:52]}…")
        except Exception as e:  # noqa: BLE001
            expect(label, False, f"抛了非 SpecViolation: {type(e).__name__}")

    print("=" * 68)
    print("硬裁判 JD-01 自检")
    print("=" * 68)

    print("\n【A. 判定逻辑】")
    answer = {
        "summary": {
            "diff_count": 5, "dedup_amount": Decimal("1234.56"),
            "ledger_total": Decimal("37490.50"), "caliber_excluded": True,
        },
        "diffs": [{"id": x} for x in ("D1", "D2", "D3", "D4", "D5")],
        "rows": [{"amt": Decimal("10.50")}, {"amt": Decimal("20.25")}],
    }
    spec = {
        "checks": [
            {"name": "count", "type": "numeric_equals", "path": "summary.diff_count", "expected": 5},
            {"name": "tol", "type": "numeric_equals", "path": "summary.dedup_amount",
             "expected": Decimal("1234.57"), "tol": Decimal("0.02")},
            {"name": "bool", "type": "bool_equals", "path": "summary.caliber_excluded", "expected": True},
            {"name": "set", "type": "set_equals", "path": "diffs", "key": "id",
             "expected": ["D1", "D2", "D3", "D4", "D5"]},
            {"name": "sum", "type": "sum_equals", "path": "rows", "key": "amt",
             "expected": Decimal("30.75"), "tol": Decimal("0.001")},
            {"name": "str", "type": "string_equals", "path": "summary.diff_count", "expected": "5"},
            {"name": "cnt", "type": "count_equals", "path": "diffs", "expected": 5},
            {"name": "lu", "type": "list_equals_unordered", "path": "diffs", "key": "id",
             "expected": ["D5", "D4", "D3", "D2", "D1"]},
            {"name": "dec", "type": "decimal_equals", "path": "summary.ledger_total",
             "expected": Decimal("37490.5"), "scale": 2},
            {"name": "miss", "type": "present", "path": "summary.nonexistent"},
            {"name": "abs", "type": "absent", "path": "summary.nonexistent"},
        ],
    }
    expect_map = {"count": True, "tol": True, "bool": True, "set": True, "sum": True,
                  "str": False, "cnt": True, "lu": True, "dec": True, "miss": False, "abs": True}
    res = run_checks(spec, answer)
    for r in res:
        expect(f"判定 {r['name']:5s}", r["ok"] == expect_map[r["name"]], r["actual"][:56])

    print("\n【B. 浮点治理（F3）】")
    bare_float = (2500.01 - 2500.00) + (3210.51 - 3210.50)
    expect("float 裸算确实偏离 0.02", bare_float != 0.02, f"got {bare_float!r}")
    bare_dec = (Decimal("2500.01") - Decimal("2500.00")) + (Decimal("3210.51") - Decimal("3210.50"))
    expect("Decimal 路径精确等于 0.02", bare_dec == Decimal("0.02"), f"got {bare_dec}")
    r = chk_decimal_equals("summary.ledger_total", Decimal("37490.50"), 2, None, answer)
    expect("decimal_equals 精确相等", r[0], r[1])

    print("\n【C. 三道 fail-closed 闸门】")
    expect_violation("闸门1 判断项混入算术层被拒", lambda: lint_arithmetic({
        "layer": "arithmetic",
        "checks": [{"name": "x", "type": "numeric_equals",
                    "path": "summary.diff_count", "expected": 7}],
    }))
    expect_violation("闸门2 金额未用 decimal_equals 被拒", lambda: lint_arithmetic({
        "layer": "arithmetic",
        "checks": [{"name": "x", "type": "numeric_equals",
                    "path": "summary.ledger_total", "expected": Decimal("37490.50")}],
    }))
    expect_violation("闸门3a 判断项含 expected 被拒", lambda: lint_adjudication({
        "layer": "adjudication",
        "items": [{"id": "ADJ-X", "expected": 1, "options": [{"id": "a", "implies": {}}]}],
    }))
    expect_violation("闸门3b record 缺 source 被拒", lambda: lint_record({
        "recorded_by": "someone", "decisions": [{"id": "A", "chosen": "a"}],
    }))
    expect_violation("闸门3b record 缺署名被拒", lambda: lint_record({
        "source": "human_adjudication", "recorded_by": "  ",
        "decisions": [{"id": "A", "chosen": "a"}],
    }))
    # ★ F15 配套：已退役的判断项不得再签（否则「多签一项」看起来像签成了）
    _two_axis = {"items": [{"id": "ADJ-001", "options": [{"id": "x", "implies": {}}]},
                           {"id": "ADJ-002", "options": [{"id": "7", "implies": {}}]}]}
    expect_violation("闸门3b record 含已退役 id 被拒", lambda: lint_record({
        "source": "human_adjudication", "recorded_by": "someone",
        "decisions": [{"id": "ADJ-001", "chosen": "x"},
                      {"id": "ADJ-002", "chosen": "7"},
                      {"id": "ADJ-003", "chosen": "as_diff"}],
    }, _two_axis))
    expect_violation("闸门3b record 重复项被拒", lambda: lint_record({
        "source": "human_adjudication", "recorded_by": "someone",
        "decisions": [{"id": "ADJ-001", "chosen": "x"},
                      {"id": "ADJ-001", "chosen": "x"}],
    }, _two_axis))
    expect("合法算术 spec 通过校验",
           lint_arithmetic({"layer": "arithmetic", "checks": [
               {"name": "x", "type": "numeric_equals", "path": "summary.ledger_rows", "expected": 27},
               {"name": "y", "type": "decimal_equals", "path": "summary.ledger_total",
                "expected": Decimal("1"), "scale": 2},
           ]}) is None)

    print("\n【D. 裁定语义】")
    adj = {"layer": "adjudication", "items": [{
        "id": "ADJ-001", "question": "q", "paths_affected": ["summary.amount_diff_total"],
        "options": [
            {"id": "count", "implies": {"summary.amount_diff_total": Decimal("680.02")}},
            {"id": "exclude", "implies": {"summary.amount_diff_total": Decimal("500.02")}},
        ]}]}
    ans2 = {"summary": {"amount_diff_total": Decimal("500.02")}}
    checks, prov = materialize_adjudication(adj, {
        "source": "human_adjudication", "recorded_by": "管海林",
        "decisions": [{"id": "ADJ-001", "chosen": "exclude", "rationale": "r"}],
    })
    r2 = run_checks(checks, ans2)
    expect("人类裁决物化后可比对且通过", all(x["ok"] for x in r2), r2[0]["actual"])
    expect("provenance 标注为人类裁决", prov[0]["source"] == "human_adjudication"
           and prov[0]["decided_by"] == "管海林")
    b = build_result("T", "adjudication", [], [{"id": "ADJ-001"}], "none", [])
    expect("未裁决 → pending_adjudication", b["verdict"] == "pending_adjudication")
    expect("未裁决 → exit 3（非 0）", verdict_to_exit(b["verdict"]) == EXIT_PENDING)
    b2 = build_result("T", "arithmetic", [{"name": "x", "ok": False}], [], "none", [])
    expect("算术层失败 → exit 1", verdict_to_exit(b2["verdict"]) == EXIT_FAIL)

    print("\n" + "-" * 68)
    total = n_ok + len(failures)
    if failures:
        print(f"[FAIL] 自检未通过：{len(failures)}/{total} 项失败")
        for f in failures:
            print("   -", f)
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{total} 项")
    return 0


# ---------------------------------------------------------------- 主流程

def _emit(result, out, quiet=False):
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=_jsonable)
    if out:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload + "\n")
        if not quiet:
            print(f"[JD-01] verdict={result['verdict']}  "
                  f"passed={result['summary']['passed']}/{result['summary']['total']}  "
                  f"pending={result['summary']['pending']}  -> {out}")
    elif not quiet:
        print(payload)
    if result["verdict"] == "fail":
        print("\n[FAIL 明细]", file=sys.stderr)
        for r in result["checks"]:
            if not r["ok"]:
                print(f"  - {r['name']}: {r['actual']}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="硬裁判 JD-01（非 LLM · 纯标准库）")
    ap.add_argument("--spec", help="spec 文件 (JSON)")
    ap.add_argument("--spec-dir", help="分层 spec 目录（自动读 arithmetic.json / adjudication.json）")
    ap.add_argument("--spec-layer", default="all",
                    choices=["arithmetic", "adjudication", "all"],
                    help="要评估的层。缺省 all（fail-closed：未裁决不出 pass）")
    ap.add_argument("--record", help="人类裁决记录 (adjudication-record.json)")
    ap.add_argument("--answer", help="被测产出 (JSON)")
    ap.add_argument("--out", help="结果输出路径（默认 stdout）")
    ap.add_argument("--lint-spec", action="store_true", help="只校验 spec 合规性")
    ap.add_argument("--status-out", default=None, metavar="PATH",
                    help="额外产出【机器可读的待裁状态】文件（answer-status.json）。"
                         "不改变 stdout 与退出码；不改写 answer-final.json")
    ap.add_argument("--status-source-verdict", default=None, metavar="PATH",
                    help="把该 verdict 文件的 verdict/spec_layer/pending/ts 记为来源；"
                         "与本次重算不一致则拒绝写出状态文件")
    ap.add_argument("--status-provenance", default="native",
                    choices=("native", "post_hoc_derived"),
                    help="状态文件的来源性质：native=随本轮正常产出；"
                         "post_hoc_derived=事后补生成（如实标注，不改既有产物）")
    ap.add_argument("--self-test", action="store_true", help="运行判定逻辑自检")
    ap.add_argument("--strict-money-type", action="store_true",
                    help="金额字段为字符串（W_MONEY_STRING）时判 fail 而非仅告警。"
                         "用于新产出准入；历史轮次保持告警，避免回溯改判已签字证据链。")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    # ---- 收集 spec ----
    specs = {}
    if args.spec_dir:
        for name in ("arithmetic", "adjudication"):
            p = f"{args.spec_dir.rstrip('/')}/{name}.json"
            try:
                specs[name] = load_json(p)
            except SystemExit:
                if args.spec_layer in (name, "all"):
                    print(f"[ERR] 分层模式下缺少 {p}", file=sys.stderr)
                    return EXIT_ERROR
    elif args.spec:
        _sp = load_json(args.spec)
        # ★ 按 spec 自身声明的 layer 归档：缺省的 None 表示 legacy 单层
        specs[_sp.get("layer")] = _sp
    else:
        ap.print_help()
        return EXIT_ERROR

    # ---- 校验 spec（fail-closed）----
    try:
        for layer_key, sp in specs.items():
            lint_spec(sp, layer_key)
    except SpecViolation as e:
        print(f"[SPEC-VIOLATION] {e}", file=sys.stderr)
        return EXIT_ERROR

    if args.lint_spec:
        print("[OK] spec 合规性校验通过")
        return EXIT_PASS

    if not args.answer:
        print("[ERR] 缺少 --answer", file=sys.stderr)
        return EXIT_ERROR
    answer = load_json(args.answer)

    # ---- answer 侧 lint（金额类型/符号 + 类别枚举）；E_ 无条件 fail，W_ 仅 strict 下 fail ----
    answer_warnings = lint_answer_money_types(answer) + lint_answer_categories(answer)
    for w in answer_warnings:
        tag = "[ERR]" if w["code"].startswith("E_") else "[WARN]"
        print(f"{tag} {w['code']} {w['path']}: {w['detail']}", file=sys.stderr)
    money_fails = money_type_failures(answer_warnings, args.strict_money_type)

    # ---- legacy 单层 ----
    if None in specs:
        sp = specs[None]
        task_id = sp.get("task_id", "unknown")
        results = run_checks(sp, answer) + money_fails
        evidence = [
            {"label": "spec", "path": args.spec, "sha256": sha256_of(args.spec)},
            {"label": "answer", "path": args.answer, "sha256": sha256_of(args.answer)},
        ]
        result = build_result(task_id, "legacy", results, [], "none", evidence,
                              carries_undeclared_judgment=True,
                              warnings=answer_warnings)
        _emit(result, args.out)
        return verdict_to_exit(result["verdict"])

    # ---- 分层 ----
    ar_spec, adj_spec = specs.get("arithmetic"), specs.get("adjudication")
    ref = ar_spec or adj_spec
    task_id = ref.get("task_id", "unknown")
    results, pending, prov, source = [], [], [], "none"

    if args.spec_layer in ("arithmetic", "all") and ar_spec:
        results += run_checks(ar_spec, answer)

    if args.spec_layer in ("adjudication", "all") and adj_spec:
        if args.record:
            record = load_json(args.record)
            try:
                lint_record(record, adj_spec)
                checks, prov = materialize_adjudication(adj_spec, record)
            except SpecViolation as e:
                print(f"[SPEC-VIOLATION] {e}", file=sys.stderr)
                return EXIT_ERROR
            results += run_checks(checks, answer)
            source = "human"
        else:
            pending = pending_view(adj_spec)

    evidence = []
    for label, p in (("spec", args.spec), ("answer", args.answer)):
        if p:
            evidence.append({"label": label, "path": p, "sha256": sha256_of(p)})
    if args.spec_dir:
        for name in ("arithmetic", "adjudication"):
            p = f"{args.spec_dir.rstrip('/')}/{name}.json"
            if (specs.get(name) is not None):
                evidence.append({"label": f"spec_{name}", "path": p, "sha256": sha256_of(p)})
    if args.record:
        evidence.append({"label": "adjudication_record", "path": args.record,
                         "sha256": sha256_of(args.record)})

    results += money_fails

    result = build_result(task_id, args.spec_layer, results, pending, source, evidence,
                          warnings=answer_warnings)
    result["adjudication_provenance"] = prov
    _emit(result, args.out)

    # ---- 待裁状态文件（AD-01 第 7 查①）----
    if args.status_out:
        sv = None
        if args.status_source_verdict:
            sv = _verdict_digest(args.status_source_verdict)
            if sv is None:
                print(f"[ERR] --status-source-verdict 不可解析："
                      f"{args.status_source_verdict}", file=sys.stderr)
                return EXIT_ERROR
            _real = (result.get("summary") or {}).get("pending")
            if (sv.get("verdict") != result.get("verdict")
                    or sv.get("spec_layer") != result.get("spec_layer")
                    or sv.get("pending_count") != _real):
                print("[ERR] --status-source-verdict 与本次重算不一致："
                      f"来源 verdict={sv.get('verdict')} spec_layer={sv.get('spec_layer')} "
                      f"pending={sv.get('pending_count')}；本次 verdict={result.get('verdict')} "
                      f"spec_layer={result.get('spec_layer')} pending={_real}",
                      file=sys.stderr)
                return EXIT_ERROR
        _spec_p = args.spec
        if not _spec_p and args.spec_dir:
            _nm = "arithmetic" if args.spec_layer == "arithmetic" else "adjudication"
            _spec_p = f"{args.spec_dir.rstrip('/')}/{_nm}.json"
        status = build_answer_status(
            result, args.answer, _spec_p, args.record, sv,
            post_hoc=(args.status_provenance == "post_hoc_derived"))
        with open(args.status_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(status, ensure_ascii=False, indent=2,
                               default=_jsonable) + "\n")
        print(f"[JD-01] answer-status → {args.status_out} "
              f"(state={status['adjudication_state']}, "
              f"pending={status['pending_count']}, "
              f"provenance={status['provenance']})")

    return verdict_to_exit(result["verdict"])


if __name__ == "__main__":
    sys.exit(main())
