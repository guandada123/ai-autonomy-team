#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
new_round.py —— 开轮器（约束层 + 记账层）

职责：
  1. 建轮次目录结构（含 round-manifest.json）
  2. 建第 8 查写入域基线（复用 gen_manifest.py --write-domain）
  3. 三控：匿名化 / 确定性乱序 / 长度预算声明
  4. 生成匿名输入包与各席位提示词（--emit-prompts）

★ 架构事实（不可绕过）：
    子代理**只能由主会话通过 Agent 工具发起**。Python 脚本无法调用 Agent 工具。
    因此本脚本产出的是**提示词文件**，不是「已发起的会话」。
    任何声称「全自动多轮编排」的设计在本环境都是假的。
    详见 docs/protocol/07-轮次与指标.md。

依赖：仅 Python 标准库。跨平台。

用法：
    python3 new_round.py --round 2 --role revision_round --from round1
    python3 new_round.py --round 2 --emit-prompts
    python3 new_round.py --check 2          # 校验目录 schema
    python3 new_round.py --self-test

退出码：0=成功  1=schema 不符  2=用法/输入错误
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ★ 读取域（F5）的装载与占位符展开复用 seat_check，避免 X3 式重复实现。
#   两者永远同处一个目录（源：scripts/new_round.py + scripts/judges/；
#   包：migration-package/new_round.py + migration-package/judges/）。
sys.path.insert(0, str(Path(__file__).resolve().parent / "judges"))
try:
    from seat_check import load_read_domains  # noqa: E402
except ImportError:                             # 缺 judges/ 时降级，不阻断开轮
    load_read_domains = None

TZ_CN = timezone(timedelta(hours=8))

EXIT_OK = 0
EXIT_SCHEMA = 1
EXIT_ERROR = 2
EXIT_PENDING = 3      # 「未裁决」—— 判据侧 pending_adjudication（protocol/04 §退出码）
# ★ P0-2（2026-10-22 修）：闸门拒绝**不**复用 3。
#   3 在 protocol/05:50 与 protocol/04:81 已定义为「未裁决」，
#   若闸门也返回 3，上游告警分不清「判据说没裁决」和「前置检查拦住」，会归错因。
EXIT_BLOCKED = 4      # 「开轮前闸门拒绝」—— 本轮未开始，不是判据 pending

DEBATING_SEATS = ("EX-01", "EX-02", "EX-03")
OTHER_SEATS = ("AD-01", "PM-01", "XJ-01")
ANON_LABELS = ("ANON-A", "ANON-B", "ANON-C", "ANON-D")

REQUIRED_DIRS = ("inputs", "presentation", "prompts")
REQUIRED_FILES = ("round-manifest.json", "wd-baseline.json")

# ★ 任务参数化（2026-10-20）
#   001 是历史布局：夹具直接放在 fixtures/ 根；002 及以后放在 fixtures/<task>/ 下。
#   ★★ 为什么必须有这个开关：allow 是【全局】的（read_domains.json 一份），
#      而提示词里的「你的读取域」段是直接把 allow 拼出来的（见第 469 行附近）。
#      不按任务改写 allow，002 的轮次会拿到 001 的输入清单，反之会同时列出两个任务。
DEFAULT_TASK = "001"


def task_fixtures_rel(task=None):
    """该任务的夹具目录（相对包根）。001 → fixtures/；其余 → fixtures/<task>/。"""
    t = str(task or DEFAULT_TASK).strip() or DEFAULT_TASK
    return "fixtures" if t == DEFAULT_TASK else f"fixtures/{t}"


def round_task(round_dir):
    """从 round-manifest.json 读本轮任务号（历史轮次缺省为 001）。"""
    try:
        m = load_json(Path(round_dir) / "round-manifest.json") or {}
    except Exception:
        return DEFAULT_TASK
    return str(m.get("task") or DEFAULT_TASK)

# 匿名化：从同伴产出中剔除的身份字段
IDENTITY_KEYS = ("seat", "round", "model", "model_id", "vendor", "provider",
                 "agent_id", "session", "author")

DEFAULT_PARAMS = "config/params.json"


def now_iso():
    return datetime.now(TZ_CN).replace(microsecond=0).isoformat()


def sha256_of(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for c in iter(lambda: f.read(65536), b""):
                h.update(c)
        return h.hexdigest()
    except OSError:
        return None


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def write_text(path, text):
    """写盘统一 utf-8 + LF。内容未变则不重写（幂等）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text if text.endswith("\n") else text + "\n"
    if p.is_file():
        try:
            if p.read_text(encoding="utf-8") == data:
                return "unchanged"
        except OSError:
            pass
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    return "written"


def write_json(path, obj):
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def strip_identity(obj):
    """递归剔除身份字段。这是「匿名化」的可操作部分。

    ★ 诚实边界：只能删**身份标签**，删不掉**文风指纹**。
      三个席位长期各自写 evidence 的措辞习惯是可辨识的。
      故匿名化降低的是「显式身份驱动」谄媚，不是「文风驱动」谄媚。
    """
    if isinstance(obj, dict):
        return {k: strip_identity(v) for k, v in obj.items() if k not in IDENTITY_KEYS}
    if isinstance(obj, list):
        return [strip_identity(x) for x in obj]
    return obj


def scrub_text(obj, seat_to_label):
    """★ 正文级净化：把字符串里出现的真实席位名替换为匿名编号。

    为什么需要这一步（实测发现）：仅剥离**结构化身份字段**是不够的。
    席位会在**正文里点名同伴** —— 实测 EX-03 的产出中出现过
    「EX-01 与 EX-02 的归类结论相同」「AD-01 的假设清单」这类句子。
    脚本分不清结构字段与正文内容，故必须在文本层再扫一遍。
    """
    if isinstance(obj, str):
        s = obj
        for seat, label in seat_to_label.items():
            s = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(seat)}(?![A-Za-z0-9_-])", label, s)
        return s
    if isinstance(obj, dict):
        return {k: scrub_text(v, seat_to_label) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_text(x, seat_to_label) for x in obj]
    return obj


def make_seed(round_name, prev_name, params_sha):
    """确定性种子 —— 可复现、可审计。"""
    raw = f"{round_name}|{prev_name}|{params_sha}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def read_domain_targets(prev_dir, round_name=None, rd_config=None):
    """本次开轮要净化的禁读路径字符串集合。

    取 deny_absolute 全体 + **全部历史轮次**的目录前缀。
    ★ 不含 deny_default（"runs/"）—— 它太泛，替换它会毁掉正文里的正当提及；
      而具体轮次目录名是精确的，能定向擦掉「runs/round1/…」这类泄漏。

    ★★ F25（2026-10-19 round3 实测暴露）：原实现**只加了 prev_dir 一轮**。
      于是 round3 的匿名包里残留了 `runs/round1/adjudicated.json` ——
      而那个文件含 verdict=adjudicated 与全部期望值。也就是说：
      禁止席位「读」答案的清单，自己把答案的**路径字符串**递进了辩论包。
      这跟 F5 是同一件事的第二种形态：F5 防的是席位主动去读，
      这里漏的是净化层只擦上一轮、没擦更早的轮次。
      现改为枚举 runs/ 下全部 round* 目录（当前轮除外）。
    """
    if load_read_domains is None:
        return []
    rd = load_read_domains(Path(rd_config) if rd_config else
                           (Path.cwd() / "config/read_domains.json"), round_name)
    if not rd:
        return []
    t = [p for p in (rd.get("deny_absolute") or []) if p]
    if prev_dir is not None:
        try:
            for d in sorted(prev_dir.parent.glob("round*")):
                if d.is_dir() and d.name != round_name:
                    t.append(f"runs/{d.name}/")
        except OSError:
            t.append(f"runs/{prev_dir.name}/")
    # ★ F29（P10 实测暴露）：只擦【完整路径】是不够的。pack-ANON-C 正文里
    #   6 次写的都是裸文件名「truth.json」，而 targets 里只有
    #   「fixtures/truth.json」→ 一处都没擦到。所以补上裸 basename。
    #   不会误伤：targets 按长度降序替换，长路径先被吃掉，裸名随后自然消失；
    #   且只对带扩展名的文件项补，目录项（judges/、config/）不补。
    bare = []
    for p in t:
        base = p.rstrip("/").rsplit("/", 1)[-1]
        if base and base != p and base.endswith((".json", ".py", ".csv", ".md")):
            bare.append(base)
    t = list(t) + bare
    # ★ 排序必须带确定性 tie-break：只按 len 降序时，等长元素（如 judges/ 与
    #   config/ 都是 7 字符）的顺序取决于 set 的迭代顺序，而它随 PYTHONHASHSEED
    #   变化 → 同一轮重跑写出不同的 redaction.json → 破坏开轮幂等。
    return sorted(set(t), key=lambda s: (-len(s), s))


def scrub_paths(payload, targets):
    """把禁读路径字符串替换为稳定占位符，返回 (新 payload, 替换计数)。

    ★ 这是【去掉提示】，不是【阻止读取】—— 协议已如实写清（06 文档第三节）。
      目的：历史产物里已经出现过 `fixtures/truth.json` 这个字符串，不擦掉
      就等于把「禁读清单」直接递给席位。降低的是「猜到该读什么」的概率，
      不是「读到」的可能性。
    """
    counts = {}

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str):
            s = o
            for t in targets:
                if t and t in s:
                    counts[t] = counts.get(t, 0) + s.count(t)
                    s = s.replace(t, "[deny-domain path redacted]")
            return s
        return o

    return walk(payload), counts


# ---------------------------------------------------------------- 答案引用净化（第 3 查）
#
# ★ 为什么不擦「全部答案数值」：500.02 / 180.00 就是 ADJ-001 的立场本身。
#   把它们从所有文本里抹掉，下一轮席位就只能看到 stance[].choice 三个字母，
#   辩论会被掏空。实测 A/B 两包也带这些数字（各 2–8 次），它们是席位的
#   独立结论 + 证据指针，擦掉是误伤。
# ★ 真正的毒不是数值，是「这个数值的权威来源是 truth/expected」。AD-01 第 3 查
#   的原文就是「expected=500.02/180.0/7」—— 它攻击的是【引用】，不是【结论】。
#   实测：A/B 里 expected 出现 0 次，只有 C 出现 4 次，且把 truth 指名为权威。
#   所以只擦「引用子句」，不擦数值本身。
# ★ 为什么是子句级而不是整句：pack-C 的 dissent[0].reason 是
#   「…三包数字同为 500.02/180/7，且 truth.json 已固化 exclude」。
#   整句替换会把「三包数字相同」这个实质论点一起毁掉。

ANSWER_SOURCE_TOKENS = (
    "truth", "truth.json", "expected", "rules", "adjudicated.json",
    "verdict-layered", "pending-adjudication", "answer-baseline", "verdict-baseline",
)

# ★ 实测教训：这些词必须【单独触发】擦除，不能要求与答案数值同现。
#   反例（真实文本）：「rules 明定口径差不计入，expected=500.02/180.0/7」
#   前半句用「明定」把 ADJ-001 的答案说成既定规则，而数值在后半句 ——
#   要求同现就会漏掉前半句，而那半句恰恰是更毒的（它把判断伪装成规则）。
#   代价：可能擦掉「按协议规定」这类正当引述，损失一句话，不损失论点。
ANSWER_AUTHORITY_TOKENS = (
    "真值", "规则明定", "明定", "预先固化", "机器可读", "偏离即", "判定为 fail",
)

# ★ 按【字段名】排除立场，不按「数值是否为整数」排除：
#   diff_count 的 7/6/5/4 是立场，必须可见；
#   而 excluded_amount_total 的 180.0 虽是整数，却是货真价实的金额答案。
STANCE_PATHS = ("summary.diff_count",)

_CLAUSE_SEP = "。；;，,\n"


def answer_value_tokens(adj_spec):
    """spec 选项里「金额类答案」的字符串形式集合（用于审计与 values_sha256）。

    ★ 只取 adjudication 层，不取 arithmetic / truth：
      - arithmetic 的 27 / 37490.50 等不含判断，任何席位都能从 CSV 复算，
        擦它只降低可用性、不降低泄漏；
      - truth.json 的 expected 与 adjudication 的 implies 同源，读它不增加覆盖面，
        却让净化器依赖答案文件本身 —— 一旦 truth 被改，净化集合会悄悄变。
    """
    vals = set()
    for it in (adj_spec or {}).get("items") or []:
        for o in it.get("options") or []:
            for k, v in (o.get("implies") or {}).items():
                if k in STANCE_PATHS or isinstance(v, bool) or isinstance(v, list):
                    continue
                if not isinstance(v, (int, float)):
                    continue
                f = float(v)
                if abs(f) < 1:      # 0.0 是 T106 的合法差额，不是答案
                    continue
                vals.add(f"{f:.2f}")
                vals.add(repr(f))
                if abs(f - round(f)) < 1e-9:
                    vals.add(str(int(round(f))))
    return sorted(vals)


def _split_clauses(s):
    """切子句并保留分隔符。

    数字两侧的逗号不切 —— 否则 `1,200.00` 会被切成 `1` 与 `200.00`，
    既毁掉可读性，又可能让 `200.00` 撞上答案值。
    """
    out, buf = [], ""
    for i, ch in enumerate(s):
        if ch in _CLAUSE_SEP:
            if ch in "，," and buf and buf[-1].isdigit():
                nxt = s[i + 1] if i + 1 < len(s) else ""
                if nxt.isdigit():
                    buf += ch
                    continue
            out.append((buf, ch))
            buf = ""
        else:
            buf += ch
    if buf:
        out.append((buf, ""))
    return out


def _has_value(clause, values):
    for v in values:
        if re.search(rf"(?<![\d.]){re.escape(v)}(?![\d.])", clause):
            return True
    return False


def _is_citation(clause, src_tokens):
    low = clause.lower()
    if any(t.lower() in low for t in src_tokens):
        return True
    return any(a in clause for a in ANSWER_AUTHORITY_TOKENS)


def scrub_answer_citations(payload, adj_spec, targets=()):
    """把「对答案权威来源的引用」整子句替换掉，保留立场与数值本身。

    ★ 判据只用 SOURCE / AUTHORITY 两类词，不用「答案数值」——
      数值本身就是立场（500.02 = exclude），拿它当判据会把立场句一起擦掉。
      answer_value_tokens() 的结果只进 redaction 的 values_sha256，供审计核对，
      不参与判断。
    """
    values = answer_value_tokens(adj_spec)
    src_tokens = tuple(ANSWER_SOURCE_TOKENS) + tuple(t for t in targets if t)
    stats = {"clauses_total": 0, "clauses_replaced": 0, "values": values,
             "clauses_with_value_kept": 0}

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str):
            out = []
            for body, sep in _split_clauses(o):
                stats["clauses_total"] += 1
                if body.strip() and _is_citation(body, src_tokens):
                    stats["clauses_replaced"] += 1
                    out.append("[答案来源引用已净化]" + sep)
                else:
                    # ★ 这是【刻意保留】的计数，不是漏网：数值即立场。
                    #   把它记进 redaction，是为了让审计能核对「保留了多少」，
                    #   而不是靠人相信「应该没漏」。
                    if body.strip() and _has_value(body, values):
                        stats["clauses_with_value_kept"] += 1
                    out.append(body + sep)
            return "".join(out)
        return o

    return walk(payload), stats


def build_inputs(root, round_dir, prev_dir, params_path, task=None):
    """匿名化 + 乱序 + deny 路径净化，产出交给席位的输入包。"""
    # ★ F22 修复：原来用 root / params_path —— root 是工作区根，而 config/params.json
    #   在 migration-package/ 内，于是**永远读不到**，params_sha 恒为 "nohash"。
    #   seed 公式里那个「params 指纹」于是成了假输入：改了 params，乱序不变。
    #   现改为按 cwd 解析，与 main() 里读 params 用同一基准。
    params_sha = sha256_of(Path.cwd() / params_path) or "nohash"
    seed = make_seed(round_dir.name, prev_dir.name if prev_dir else "none", params_sha)

    packs, anon_map = {}, {}
    order = list(ANON_LABELS[:len(DEBATING_SEATS)])
    random.Random(int(seed, 16)).shuffle(order)

    idx = 0
    for seat in DEBATING_SEATS:
        fp = prev_dir / f"{seat}.json" if prev_dir else None
        data = load_json(fp) if fp else None
        if data is None:
            continue
        label = order[idx]
        idx += 1
        anon_map[label] = seat
        packs[label] = strip_identity(data)

    # ★ 正文级净化：用完整映射把正文里的真实席位名替换掉
    #   注意也要覆盖**非辩论席位** —— 实测正文里出现过「AD-01 的假设清单」这类引用，
    #   只映射 3 个辩论席位会留下 AD-01 / PM-01 / XJ-01 三处残留
    seat_to_label = {seat: label for label, seat in anon_map.items()}
    seat_to_label.update({"AD-01": "ANON-AUDIT", "PM-01": "ANON-PM", "XJ-01": "ANON-JUDGE"})
    for label in list(packs):
        packs[label] = scrub_text(packs[label], seat_to_label)

    # ★ 第三层净化：禁读路径字符串（F5）。与上一步（身份）性质相同，都是
    #   「显式标识驱动」通道；文风与推理痕迹仍不可擦（见 06 文档诚实边界）。
    targets = read_domain_targets(prev_dir, round_dir.name)
    redaction = {}
    for label in list(packs):
        packs[label], c = scrub_paths(packs[label], targets)
        for k, v in c.items():
            redaction[k] = redaction.get(k, 0) + v

    # ★ 第 3 查（共识是否被结构性制造）：擦掉「对答案权威来源的引用」。
    #   位置在 scrub_paths 之后 —— 路径已先被替换，此层再按子句判断。
    # ★ 任务参数化：判断项 spec 按本轮任务定位（001 在 fixtures/spec，002 在 fixtures/002/spec）
    _spec_p = round_dir.parent.parent / (task_fixtures_rel(task) + "/spec/adjudication.json")
    _adj = load_json(_spec_p) if _spec_p.is_file() else None
    purge = {"clauses_total": 0, "clauses_replaced": 0, "values": [],
             "clauses_with_value_kept": 0}
    for label in list(packs):
        packs[label], st = scrub_answer_citations(packs[label], _adj, targets)
        purge["clauses_total"] += st["clauses_total"]
        purge["clauses_replaced"] += st["clauses_replaced"]
        purge["clauses_with_value_kept"] += st["clauses_with_value_kept"]
        purge["values"] = st["values"]

    res = {}
    for label, payload in packs.items():
        res[f"inputs/pack-{label}.json"] = write_json(round_dir / f"inputs/pack-{label}.json", payload)
    # anon-map 不交给席位
    res["presentation/anon-map.json"] = write_json(
        round_dir / "presentation/anon-map.json",
        {"_warning": "本文件【不得】交给任何席位 —— 它是匿名化的密钥", "map": anon_map})
    res["presentation/order.json"] = write_json(
        round_dir / "presentation/order.json",
        {"seed": seed, "seed_source": "sha256(round|prev|params_sha)[:8]",
         "order": [l for l in order if l in packs],
         "note": "确定性乱序 —— 同轮同参数可复现"})
    res["presentation/redaction.json"] = write_json(
        round_dir / "presentation/redaction.json",
        {"targets": targets, "replaced_counts": redaction,
         "total": sum(redaction.values()),
         "answer_purge": {
             "policy": ("子句含 SOURCE（truth / expected / 禁读路径）→ 整子句替换；"
                        "或含 VALUE（spec 选项里的非整数答案值）且含 AUTHORITY"
                        "（真值 / 规则明定 / 偏离即 / 判定为 fail…）→ 整子句替换"),
             "values_count": len(purge["values"]),
             "values_sha256": hashlib.sha256(
                 "|".join(purge["values"]).encode("utf-8")).hexdigest(),
             "clauses_total": purge["clauses_total"],
             "clauses_replaced": purge["clauses_replaced"],
             "clauses_with_value_kept": purge["clauses_with_value_kept"],
             "boundary": ("★ 只擦【对答案权威来源的引用】，不擦立场数值本身 —— "
                          "500.02/180.00 就是 ADJ-001 的立场，擦掉等于让下一轮"
                          "看不到同伴立场，辩论会被掏空。values_sha256 不写明文值。"),
         },
         "boundary": "只擦【路径字符串】，不是阻止读取；未申报的读取对脚本不可见。"})
    return seed, anon_map, res


def read_domain_block(seat, round_name=None, task=None):
    """把读取域声明注入提示词 —— F5 的直接修复。

    ★ 原实现【从不声明席位可以读什么】：六个模板只约束「不可写什么」。
      于是 EX-03 读了 fixtures/truth.json（含硬裁判真值与 expected），
      再据此给出「独立结论」。那不是违规，是没人告诉它边界在哪。
      声明阻止不了读取，但把「不该读」从【不知者不怪】变成【明知故犯】，
      并给第 9 查一个可核对的基准。
    """
    unavailable = ("## 你的读取域\n\n（读取域配置不可用 —— 本段降级，"
                   "但不构成可读任意文件的许可）\n")
    if load_read_domains is None:
        return unavailable
    rd = load_read_domains(Path.cwd() / "config/read_domains.json", round_name)
    if not rd:
        return unavailable
    can = list(rd.get("allow") or []) + list(rd.get("allow_exceptions") or []) \
        + list((rd.get("allow_extra_by_seat") or {}).get(seat) or [])
    # ★ 任务参数化：allow 是全局的，但提示词只应列出【本轮任务】的输入。
    #   仅改写 fixtures/ 前缀项（runs/ 与 protocol/ 与任务无关，保持原样）。
    if task and str(task) != DEFAULT_TASK:
        fx = task_fixtures_rel(task)
        can = [f"{fx}/{p.split('fixtures/', 1)[1]}" if p.startswith("fixtures/") else p
               for p in can]
    cannot = list(rd.get("deny_absolute") or []) + list(rd.get("deny_default") or [])
    lines = ["## 你的读取域", "", "**可读**："]
    lines += [f"- `{p}`" for p in can] or ["- （无）"]
    lines += ["", "**禁读**（命中即 `E_READ_DOMAIN_VIOLATION`，产出不得进入聚合）："]
    lines += [f"- `{p}`" for p in cannot] or ["- （无）"]
    lines += [
        "",
        "★ `runs/round*/presentation/` 一律不可读（匿名化密钥所在；目录列举 ls 也不行）。",
        "",
        "**你必须在产出里申报 `reads[]`** —— 列出你**实际读过**的文件全集",
        "（读了但没写成 `evidence` 的也要写）。二者语义不同：",
        "`reads[]` = 「我读过什么」，`evidence[]` = 「我用什么支撑结论」。",
        "",
        "★ 声明不是隔离墙：脚本能检出你**申报的**读取与**用作证据的**路径是否越界，",
        "  但无法发现你未申报的读取。请把这一条也当作对自己的约束。",
        "",
    ]
    return "\n".join(lines)


# ★ F16 修复：原实现把「本轮不引入新证据 / idle 不适用」写死在 build_prompts 里，
#   与 --role 无关。于是开一个 evidence_round 时，提示词会同时说
#   「轮次类型：evidence_round」和「本轮不引入新证据、idle 不适用」——自己打自己。
#   现在按 role 取段落，未知 role 显式告警而**不静默降级**（静默降级正是原 bug 的形态）。
ROLE_BLOCKS = {
    "evidence_round": {
        "lead": ["> 本轮**要求引入新证据**。你的 `evidence[]` 至少有一条指针指向此前各轮"
                 "**未出现过**的 `(文件, 选择子)`；重复引用旧证据会被 `idle_detection` 计为空转。"],
        "task": ["> 你的任务是：基于原始输入独立取证，给出可回读的证据链。",
                 "> 同伴产出**仅供参考，不得作为你的证据来源**。"],
        "peer_head": "## 同伴产出（仅作对照，不作为证据来源）",
        "idle": ["> `idle_detection` 与「零边际增益」终止条件**适用**"
                 "（阈值见 `config/params.json`）。连续多轮无新证据会终止该分支。"],
    },
    "revision_round": {
        "lead": ["> 本轮**不引入新证据** —— 你已掌握全部数据。"],
        "task": ["> 你的任务是：阅读匿名同伴的产出，修正或坚持你的立场，"
                 "并对至少一条同伴结论提出反对。"],
        "peer_head": "## 你收到的匿名同伴产出",
        "idle": ["> 本轮为 revision_round，`idle_detection` 与「零边际增益」终止条件**不适用**",
                 "> （该轮按定义不引入新证据，套用会自证终止）。"],
    },
    "devils_advocate_round": {
        "lead": ["> 本轮是**干预轮**，不是辩论轮：**不引入新证据**，"
                 "且**不计入** `debate.max_rounds`。"],
        "task": ["> 你的任务是：把**少数立场**物化为完整、合规、可裁决的产出"
                 "（必须含 `falsification`）。"],
        "peer_head": "## 被保留的少数立场（匿名）",
        "idle": ["> `idle_detection` 与「零边际增益」终止条件**不适用**。"],
    },
}


# ★ F21：非辩论席位的职责一句话（只给职责，不给任何结论信息）
NON_DEBATE_ROLE = {
    "XJ-01": "跨家族评委。你只提供线索，决策权重为 0；你不持立场，也不判定对错。",
    "AD-01": "结构审计。你只驳结构、不驳结论；你的价值在独立复核，不在复述别人的结论。",
    "PM-01": "唯一写入者。你聚合各席产出、保留分歧，不得改写任何原始结论（含少数派）。",
}


def build_prompts(root, round_dir, params, task=None):
    """提示词 = 模板 + 读取域声明 + 匿名同伴产出清单 + 本轮修正指令。"""
    tmpl_dir = root / "templates"
    maxchars = (params.get("presentation") or {}).get("max_chars_per_field") or {}
    budget = "、".join(f"{k} ≤ {v} 字" for k, v in maxchars.items())
    packs = sorted(round_dir.glob("inputs/pack-ANON-*.json"))
    listing = "\n".join(f"- `{p.relative_to(round_dir).as_posix()}`" for p in packs)
    role = (load_json(round_dir / "round-manifest.json") or {}).get("role", "unknown")

    blk = ROLE_BLOCKS.get(role)
    if blk is None:
        # ★ 不静默降级：明确告诉席位「这个轮次声明有问题」。
        sys.stderr.write(f"[WARN] 未知轮次类型 role={role!r} —— 提示词按【显式告警】生成，"
                         f"不降级为 revision_round。请先修 round-manifest.json。\n")
        blk = {
            "lead": [f"> ★ **轮次类型无法识别**：`role={role}`。这不是合法取值"
                     f"（合法：{' / '.join(ROLE_BLOCKS)}）。",
                     "> 请先修 `round-manifest.json` 再开轮，否则本轮的证据与终止条件均不可判定。"],
            "task": ["> 在轮次类型修正之前，请**不要**开始取证或表态。"],
            "peer_head": "## 同伴产出（本轮声明有误，仅供参考）",
            "idle": ["> 因 role 非法，`idle_detection` 的适用范围**未定义**。"],
        }

    usage_note = []
    if (params.get("cost_budget") or {}).get("usage", {}).get("required_for_new_rounds"):
        usage_note = ["> 本轮要求申报 `usage: {tokens, minutes, source}`（见模板）；",
                      "> 缺失将被判 `E_MISSING_USAGE`，挡在聚合之外。申报值为自述，脚本无法验证。"]

    res = {}
    for seat in DEBATING_SEATS:
        t = tmpl_dir / f"{seat.lower()}.md"
        body = t.read_text(encoding="utf-8") if t.is_file() else f"# 席位 {seat}（模板缺失）"
        head = "\n".join([
            f"# 第 {round_dir.name} 轮 · 席位 {seat}",
            "",
            f"> 轮次类型：**{role}**",
            *blk["lead"],
            *blk["task"],
            "",
            blk["peer_head"],
            "",
            listing or "- （无）",
            "",
            "> 同伴身份已匿名化。**你的 `dissent[].against` / `strongest_counter_arguments[].target`",
            "> 必须写匿名编号（如 ANON-A），写真实席位名会被契约校验判 E_PEER_IDENTIFIED。**",
            "> 正文里也**不得**出现任何真实席位名（含 AD-01/PM-01/XJ-01）。",
            "",
            read_domain_block(seat, round_dir.name, task),
            "## 终止条件对本轮的适用性",
            "",
            *blk["idle"],
            "",
            *usage_note,
            "",
            f"## 字数预算：{budget or '（未配置）'}",
            "",
            "---",
            "",
        ])
        res[f"prompts/{seat}.md"] = write_text(round_dir / f"prompts/{seat}.md", head + body)

    # ---- F21：非辩论席位也要拿到本轮提示词 ----
    # ★ 原实现只遍历 DEBATING_SEATS，XJ-01 / AD-01 / PM-01 拿不到提示词，因而也拿不到
    #   「你的读取域」注入段。契约 v2.0 起 reads[] 是硬字段 → 它们会因 E_MISSING_READS 被否决。
    #
    # ★ 但【绝对不能】给它们拼 EX 那套 head：EX 的 head 含 pack-ANON 清单与轮次任务描述，
    #   而 AD-01 必须以**干净上下文**做结构审计。把「谁说了什么」递给审计席位，
    #   等于让审计在别人的结论上跑，而这一点**没有任何校验能发现**。
    #   所以这里只注入：职责一句话 + 读取域 + 必须申报的两项 + 独立性声明。
    for seat in OTHER_SEATS:
        t = tmpl_dir / f"{seat.lower()}.md"
        body = t.read_text(encoding="utf-8") if t.is_file() else f"# 席位 {seat}（模板缺失）"
        head = "\n".join([
            f"# 第 {round_dir.name} 轮 · 席位 {seat}",
            "",
            f"> 轮次类型：**{role}**",
            f"> 你的职责：{NON_DEBATE_ROLE.get(seat, '（未定义）')}",
            "",
            read_domain_block(seat, round_dir.name, task),
            "## 你必须申报的两项（契约 v2.0 起的轮次为硬字段）",
            "",
            "- `reads[]`：**≥1 项**，每项含 `path` 与非空 `purpose` —— 你实际读过的文件全集。",
            "- `usage{}`：`tokens`(int ≥ 0) / `minutes`(number ≥ 0) / `source`"
            "(`self_report` 或 `metered`)。",
            "  缺任一项 → 你的产出被判不合格 → 本轮不验收。两个值都是申报值，脚本无法验证。",
            "",
            "## ★ 独立性声明（不可删，也不要试图绕过）",
            "",
            "本轮提示词**不包含任何席位的结论摘要**，也不告诉你去看哪几份匿名包。",
            "这是刻意的：审计/评审/写作席位的独立性，依赖于你自己去读 "
            f"`{round_dir.name}/` 下的**原始产出**，而不是读别人替你总结的东西。",
            "若你发现自己引用的是某份「摘要」而不是原始文件，请停下来，回去读原文。",
            "",
            "---",
            "",
        ])
        res[f"prompts/{seat}.md"] = write_text(round_dir / f"prompts/{seat}.md", head + body)

    return res


def make_manifest(round_dir, role, prev_dir, seed, contract_version="1.0",
                  usage_required=False):
    roles = ("evidence_round", "revision_round", "devils_advocate_round")
    return {
        "round": round_dir.name,
        "role": role,
        "role_valid_values": list(roles),
        "introduces_new_evidence": role == "evidence_round",
        "prev_round": prev_dir.name if prev_dir else None,
        # ★ F7：契约版本随【轮次】记录，不随工具。seat_check 据此决定 reads[]
        #   与读取域越界是硬字段还是软字段。若用全局版本，重跑历史轮次
        #   会把它追溯作废 —— 而 round2 必须保持 4/4 合规，否则
        #   「round2 可作第 3 轮基线」这个 F7 结论会被自己的修复摧毁。
        "contract_version": str(contract_version),
        # ★ F18：usage 申报的硬性要求同样按轮次门控，理由同上。
        "usage_required": bool(usage_required),
        "expected_seats": list(DEBATING_SEATS),
        "optional_seats": list(OTHER_SEATS),
        "seed": seed,
        "created_at": now_iso(),
        "write_domain": {
            "allow": [f"{round_dir.name}/"],
            "deny_from": "../config/write_domains.json",
        },
        "read_domain": {
            "from": "../config/read_domains.json",
            "seats_must_declare_reads": True,
        },
        # ★ D-0014（AD-01 round6/7 第 3 查实测缺口）：轮次必须显式声明终止条件。
        #   evidence_round 的预期终态是判断层 pending（等人签字），不算「未完成」。
        "termination": {
            "condition": "pending_adjudication" if role == "evidence_round" else "rounds_exhausted",
            "declared_by": "orchestrator",
            "note": ("evidence_round 以 adjudication 层 pending 为预期终态，人类签字为最终终止；"
                     "protocol/02 §4.2 四选一。"),
        },
        "note": "★ 子代理只能由主会话通过 Agent 工具发起，脚本无法代劳。本目录的 prompts/ 是提示词文件，不是已发起的会话。",
    }


def do_check(root, round_dir):
    missing, empty = [], []
    for d in REQUIRED_DIRS:
        p = round_dir / d
        if not p.is_dir():
            missing.append(str(p.name) + "/")
        elif not any(p.iterdir()):
            empty.append(str(p.name) + "/")
    for f in REQUIRED_FILES:
        if not (round_dir / f).is_file():
            missing.append(f)
    m = load_json(round_dir / "round-manifest.json") or {}
    for k in ("round", "role", "expected_seats", "seed"):
        if k not in m:
            missing.append(f"round-manifest.json:{k}")

    print(f"轮次目录 schema 校验: {round_dir}")
    if missing:
        print(f"  ✗ 缺失：{missing}")
    if empty:
        print(f"  ✗ 空目录：{empty}")
    if not missing and not empty:
        print(f"  ✓ 结构完整（role={m.get('role')} seed={m.get('seed')}）")
        return EXIT_OK
    return EXIT_SCHEMA


def _commit_exception(round_dir, exc):
    """把「放行」记录落盘到 `runs/<round>/preflight-exception.json`。

    ★ P0-1（2026-10-22 修）：原实现只 `print("将写入 manifest")`，代码从未写盘 ——
      承诺与动作不一致，属「报告成功但没做」。现在改为真写文件，
      manifest 之后**从该文件回读**，不二次拼装（单一真源）。

    ★ 为什么只在放行时写：拒绝时若也写，会留下一个空的 `runs/roundN/` 目录，
      让「闸门拦住」看起来像「这一轮开过」。痕迹只能记录真实发生的事。

    ★ 目录不存在由 write_json→write_text 自动 mkdir(parents=True)，
      故无需在此之前手工建目录（原计划的「修复陷阱」已由既有写盘层化解）。
    """
    exc = dict(exc)
    exc["when"] = now_iso()
    write_json(Path(round_dir) / "preflight-exception.json", exc)
    return exc


def preflight_gate(root, params_path, card_path, allow_unverified,
                   round_dir=None, who="", reason=""):
    """开轮前闸门（D-0034/D-0035）：家族 + 前提卡。

    返回 `(ok, lines, exception)`：
      ok        闸门是否通过
      lines     给终端的分行说明（调用方原样打印）
      exception 放行记录 dict（未放行则 None）；落盘在 ok=True 时才发生

    ★ 与 manifest 的关系：manifest 的 `preflight.exceptions[]` 从本文件**回读**，
      不在内存里另存一份 —— 避免「同一事实两个来源」后不一致。

    ★ 设计取舍：判定与落盘分离（函数只做一件事）。放行条件可能有多条
      （家族未实测 + 前提卡可疑），若边判定边写盘，前提卡随后否决时
      已经写出的痕迹就成了假记录。故先收集、最后统一提交。
    """
    lines, ok = [], True
    pending = None            # 待提交的放行记录
    try:
        params = json.loads(Path(params_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, [f"[preflight] 读不到 params：{e}"], None
    fam = ((params.get("seat_contract") or {}).get("families") or {})
    if not fam.get("verified"):
        # D-0035：环境本身只有一个模型时，「未实测」是事实不是缺陷 → 不拦，但必须标注
        if int(fam.get("min_families", 3)) <= 1:
            lines.append("[preflight] 单模型环境（min_families=1）：不拦，但六席视为同一模型，"
                         "独立性记 0；结论须标注")
            lines.append("[preflight] 降级路径：多采样多数票（self-consistency）")
        else:
            msg = ("[preflight] 家族未实测（families.verified=false）——"
                   "开轮前必须跑 inspect_agent_routing.py 并写回 params")
            if allow_unverified:
                pending = {
                    "who": who or "unspecified",
                    "reason": reason or "（未提供理由：--reason 可补，缺理由不可审计）",
                    "gate": "families_unverified",
                }
                lines.append(msg + "  ← 本次放行，将写入 preflight-exception.json")
            else:
                lines.append(msg + "；确需放行加 --allow-unverified-families")
                ok = False
    else:
        lines.append(f"[preflight] 家族已实测：{fam.get('verified_at') or '未记时间'}")

    if not card_path:
        lines.append("[preflight] 未提供前提卡（--premise-card）——本轮不做前提审查")
    else:
        try:
            card = json.loads(Path(card_path).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return False, lines + [f"[preflight] 前提卡读取失败：{e}"], None
        import importlib.util as _u
        helper = root / "scripts" / "premise_probe.py"
        if not helper.is_file():
            lines.append("[preflight] 找不到 premise_probe.py，跳过前提审查")
        else:
            spec = _u.spec_from_file_location("_premise", helper)
            mod = _u.module_from_spec(spec); spec.loader.exec_module(mod)
            hits = mod.run_card(card)
            if hits:
                ok = False
                lines.append(f"[preflight] 前提审查命中 {len(hits)} 条：")
                for h in hits:
                    lines.append(f"    🔴 {h['id']} {h['action']} — {h['reason']}")
            else:
                lines.append("[preflight] 前提卡通过三条探针")

    if ok and pending and round_dir is not None:
        pending = _commit_exception(round_dir, pending)
        lines.append(f"[preflight] 放行记录已落盘：{Path(round_dir).name}/preflight-exception.json")
    elif ok and pending:
        # 没有 round_dir 就无处落盘 —— 宁可显式报出来，也不假装写过
        lines.append("[preflight] ⚠ 放行但未给 round_dir，无法留痕（调用方缺陷）")
    return ok, lines, pending


def main():
    ap = argparse.ArgumentParser(description="开轮器（约束层）")
    ap.add_argument("--root", default="..", help="工作区根（默认 ..，即从 migration-package/ 上跳）")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--round", required=False, help="轮次号（如 2）")
    ap.add_argument("--role", default="revision_round",
                    choices=["evidence_round", "revision_round", "devils_advocate_round"])
    ap.add_argument("--from", dest="prev", default=None, help="上一轮目录名（如 round1）")
    ap.add_argument("--params", default=DEFAULT_PARAMS)
    ap.add_argument("--task", default=DEFAULT_TASK,
                    help=f"任务号（默认 {DEFAULT_TASK}）。001 = fixtures/ 根布局；"
                         f"其余 = fixtures/<task>/ 下的夹具与判据")
    ap.add_argument("--emit-prompts", action="store_true")
    ap.add_argument("--check", default=None, help="校验指定轮次号的目录 schema")
    ap.add_argument("--self-test", action="store_true")
    # ★ D-0034/D-0035：开轮前闸门。auto = 仅当目标目录是本包 runs/ 时启用
    ap.add_argument("--preflight", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--premise-card", default="")
    ap.add_argument("--allow-unverified-families", action="store_true")
    # ★ P0-1：放行必须可审计。who 默认取环境用户，reason 允许留空但会在记录里显式标注缺失
    ap.add_argument("--who", default=os.environ.get("USER") or os.environ.get("USERNAME") or "",
                    help="放行责任人（写入 preflight-exception.json，默认取环境用户）")
    ap.add_argument("--reason", default="",
                    help="放行理由（写入 preflight-exception.json；缺理由的放行不可审计）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    runs = Path(args.runs_dir)
    if not runs.is_absolute():
        runs = Path.cwd() / runs

    if args.check:
        c = args.check
        return do_check(root, runs / (f"round{c}" if str(c).isdigit() else c))

    if not args.round:
        ap.print_help()
        return EXIT_ERROR

    # ★ 轮次目录名解析：纯数字加 round 前缀，其余按原样 —— 必须与 --check 用同一规则，
    #   否则会出现「创建了 roundnr2 却去查 nr2」这类两侧不一致
    # ★ P0-1（2026-10-22 修）：本段**前移到闸门之前**。
    #   原顺序是「先闸门、后算目录名」，导致放行时压根不知道该往哪写痕迹 ——
    #   这既是「只 print 不写盘」的结构性原因，也让任何补写盘的修法都拿不到目标路径。
    round_name = f"round{args.round}" if str(args.round).isdigit() else str(args.round)
    round_dir = runs / round_name

    _pf = args.preflight
    if _pf == "auto":
        try:
            _pf = "on" if runs.resolve() == (root / "runs").resolve() else "off"
        except OSError:
            _pf = "off"
    if _pf == "on":
        params_path = Path(args.params)
        if not params_path.is_absolute():
            params_path = Path.cwd() / params_path
        pf_ok, pf_lines, pf_exc = preflight_gate(
            root, params_path, args.premise_card, args.allow_unverified_families,
            round_dir=round_dir, who=args.who, reason=args.reason)
        for ln in pf_lines:
            print(ln)
        if not pf_ok:
            print(f"[preflight] 闸门未通过，拒绝开轮（退出码 {EXIT_BLOCKED} = 前置拦住，"
                  f"区别于 {EXIT_PENDING} = 未裁决）")
            return EXIT_BLOCKED

    prev_dir = (runs / args.prev) if args.prev else None
    # ★ C4 修复：params 必须相对【当前工作目录】解析，不是相对 --root。
    #   原实现用 root/args.params，而 --root 指工作区根、params 在包内
    #   → 永远读不到 → 提示词的「字数预算」长期显示「（未配置）」却不报错。
    #   同一处错误也会让 F5 的读取域段静默降级，故一并根治。
    params = load_json(Path.cwd() / args.params) or {}

    round_dir.mkdir(parents=True, exist_ok=True)
    for d in REQUIRED_DIRS:
        (round_dir / d).mkdir(parents=True, exist_ok=True)

    seed, anon_map, res_inputs = build_inputs(root, round_dir, prev_dir, args.params, args.task)
    # 保持 created_at 稳定 —— 否则重跑会改写 manifest，破坏「幂等」这一可检验判据
    _existing = load_json(round_dir / "round-manifest.json") or {}
    _cver = str((params.get("seat_contract") or {}).get("contract_version") or "1.0")
    _usage_req = bool(((params.get("cost_budget") or {}).get("usage") or {})
                      .get("required_for_new_rounds"))
    _man = make_manifest(round_dir, args.role, prev_dir, seed, _cver, _usage_req)
    if _existing.get("created_at"):
        _man["created_at"] = _existing["created_at"]
    # ★ 任务号入 manifest：下游（seat_check / verify）从这里读，不必再猜夹具目录
    _man["task"] = args.task
    # ★ 2026-10-20 增：规范化的任务号（恒为 T-XX 或原样），供跨轮统计直接使用。
    #   背景：历史 33 轮的 task 字段有四种形态（`?` / `SYNTH-RECON-001` / `002` /
    #   `tasks/T-XX` / `T-XX` 无前缀），任何 startswith("T-") 分类都会漏掉 29 轮。
    #   本字段只在新轮次写入；历史轮次保持原样，统计侧应优先读本字段、缺失时回退 task。
    _m = re.search(r"T-(\d{2})", str(args.task or ""))
    _man["task_id_normalized"] = f"T-{_m.group(1)}" if _m else str(args.task or "")
    # ★ P0-1：闸门放行记录从**文件回读**，不在内存里另存一份 —— 单一真源。
    #   exceptions 为空列表 = 本轮无放行；旧轮次无此字段 = 当时没有闸门，不要追溯补写。
    _exc_fp = round_dir / "preflight-exception.json"
    _exc = load_json(_exc_fp) if _exc_fp.is_file() else None
    _man["preflight"] = {"exceptions": [_exc] if _exc else []}
    res_inputs["round-manifest.json"] = write_json(round_dir / "round-manifest.json", _man)

    # 第 8 查基线（复用 gen_manifest.py）
    # ★ 只建一次：基线必须在轮次【开始前】固定，重跑时静默重置它会直接废掉第 8 查
    gen = Path.cwd() / "gen_manifest.py"
    wd = Path.cwd() / "config/write_domains.json"
    baseline_fp = round_dir / "wd-baseline.json"
    if baseline_fp.is_file():
        res_inputs["wd-baseline.json"] = "unchanged(已存在，不重置)"
    elif gen.is_file() and wd.is_file():
        r = subprocess.run(
            [sys.executable, str(gen), "--root", ".", "--write-domain",
             "config/write_domains.json", "--out",
             baseline_fp.relative_to(Path.cwd()).as_posix()],
            cwd=str(Path.cwd()), capture_output=True, text=True, encoding="utf-8")
        res_inputs["wd-baseline.json"] = "written" if r.returncode == 0 else f"FAILED rc={r.returncode}"
        if r.returncode != 0:
            sys.stderr.write(r.stdout + r.stderr)
    else:
        res_inputs["wd-baseline.json"] = "skipped(gen_manifest/config 缺失)"

    res_prompts = build_prompts(root, round_dir, params, args.task) if args.emit_prompts else {}

    print(f"[OK] 开轮 {round_dir.name}  role={args.role}  seed={seed}")
    print(f"     匿名映射 {len(anon_map)} 条 → presentation/anon-map.json（不交给席位）")
    written = sum(1 for v in list(res_inputs.values()) + list(res_prompts.values()) if v == "written")
    unchanged = sum(1 for v in list(res_inputs.values()) + list(res_prompts.values()) if v == "unchanged")
    for k, v in sorted(list(res_inputs.items()) + list(res_prompts.items())):
        print(f"     [{v:9s}] {k}")
    print(f"     写入 {written} 个，未变 {unchanged} 个（幂等）")
    if not args.emit_prompts:
        print("     提示：加 --emit-prompts 生成各席位提示词")
    print("     ★ 下一步须由人发起子代理（脚本无法代劳）")
    return EXIT_OK


# ---------------------------------------------------------------- 自检

def self_test():
    import tempfile
    n_ok, fails = 0, []

    def expect(label, cond, detail=""):
        nonlocal n_ok
        if cond:
            n_ok += 1
            print(f"  [PASS] {label}" + (f"  {detail}" if detail else ""))
        else:
            fails.append(label)
            print(f"  [FAIL] {label}  {detail}")

    print("=" * 68)
    print("new_round.py 自检")
    print("=" * 68)

    print("\n【匿名化：只删标签，不删内容】")
    src = {"seat": "EX-01", "round": 1, "model": "x", "conclusion": "c",
           "nested": {"seat": "EX-99", "keep": 1}, "list": [{"agent_id": "a", "keep": 2}]}
    out = strip_identity(src)
    expect("顶级 seat 被剔除", "seat" not in out)
    expect("model 被剔除", "model" not in out)
    expect("嵌套 seat 被剔除", "seat" not in out["nested"])
    expect("列表内 agent_id 被剔除", "agent_id" not in out["list"][0])
    expect("非身份字段保留", out["conclusion"] == "c" and out["nested"]["keep"] == 1)

    print("\n【确定性种子】")
    s1 = make_seed("round2", "round1", "abc")
    s2 = make_seed("round2", "round1", "abc")
    s3 = make_seed("round2", "round1", "xyz")
    expect("同输入同种子（可复现）", s1 == s2, s1)
    expect("参数变化 → 种子变化", s1 != s3)
    expect("种子为 8 位", len(s1) == 8)

    print("\n【幂等写盘】")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.txt"
        expect("首次写 = written", write_text(p, "a") == "written")
        expect("同内容再写 = unchanged", write_text(p, "a") == "unchanged")
        expect("改内容 = written", write_text(p, "b") == "written")
        expect("落盘为 LF", p.read_bytes() == b"b\n", repr(p.read_bytes()))
        jp = Path(td) / "j.json"
        write_json(jp, {"中文": 1})
        expect("JSON 落盘可回读", load_json(jp) == {"中文": 1})
        expect("JSON 亦为 LF", b"\r" not in jp.read_bytes())

    print("\n【轮次目录 schema 校验】")
    with tempfile.TemporaryDirectory() as td:
        rd = Path(td) / "round9"
        rc = do_check(Path(td), rd)
        expect("空/缺目录 → exit 1", rc == EXIT_SCHEMA)
        for d in REQUIRED_DIRS:
            (rd / d).mkdir(parents=True, exist_ok=True)
            (rd / d / "placeholder").write_text("x", encoding="utf-8")
        write_json(rd / "round-manifest.json",
                   {"round": "round9", "role": "revision_round",
                    "expected_seats": list(DEBATING_SEATS), "seed": "abcd1234"})
        write_json(rd / "wd-baseline.json", {"files": []})
        rc = do_check(Path(td), rd)
        expect("完整结构 → exit 0", rc == EXIT_OK)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            do_check(Path(td), rd)

    # ★ P0-1/P0-2（2026-10-22 补）：闸门此前**零测试覆盖** —— 16 项断言里没有一条碰过它，
    #   这正是「只 print 不写盘」能存活至今的直接原因。以下用例锁三件事：
    #   ① 放行必须真落盘且三字段可审计；② 拒绝/非例外一律不产文件；③ 拒绝码不再复用 3。
    print("\n【开轮闸门：放行必须留痕（P0-1 / P0-2）】")
    import inspect
    expect("闸门拒绝码 4 ≠ 未裁决码 3",
           EXIT_BLOCKED == 4 and EXIT_PENDING == 3,
           f"EXIT_BLOCKED={EXIT_BLOCKED} EXIT_PENDING={EXIT_PENDING}")
    expect("死参数 runs 已从 preflight_gate 移除",
           "runs" not in inspect.signature(preflight_gate).parameters)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        p_unv = tdp / "p_unverified.json"
        write_json(p_unv, {"seat_contract": {"families": {"verified": False,
                                                          "min_families": 3}}})
        p_one = tdp / "p_single.json"
        write_json(p_one, {"seat_contract": {"families": {"verified": False,
                                                          "min_families": 1}}})
        p_vfy = tdp / "p_verified.json"
        write_json(p_vfy, {"seat_contract": {"families": {"verified": True,
                                                          "verified_at": "2026-10-22T10:00"}}})

        rd = tdp / "runs" / "round90"
        ok, lines, exc = preflight_gate(tdp, p_unv, "", False, round_dir=rd, who="tester")
        expect("未实测且不放行 → 拒绝", ok is False)
        expect("拒绝时不产 exception（不留下「开过」的假象）",
               not (rd / "preflight-exception.json").exists() and exc is None)

        ok, lines, exc = preflight_gate(tdp, p_unv, "", True, round_dir=rd,
                                        who="tester", reason="单模型环境临时放行")
        fp = rd / "preflight-exception.json"
        expect("放行 → 通过", ok is True)
        expect("放行 → 文件真的落盘（原实现只 print）", fp.is_file())
        _d = load_json(fp) or {}
        expect("放行 → who/when/reason 三字段非空",
               all(_d.get(k) for k in ("who", "when", "reason")), str(_d))
        expect("放行 → gate 标明是哪道闸门", _d.get("gate") == "families_unverified")
        expect("放行 → 返回值即落盘内容（不另存一份）", exc == _d)

        rd2 = tdp / "runs" / "round91"
        ok, lines, exc = preflight_gate(tdp, p_one, "", False, round_dir=rd2, who="tester")
        expect("单模型环境（min_families=1）不拦", ok is True)
        expect("单模型不算例外，不产 exception",
               not (rd2 / "preflight-exception.json").exists())

        rd3 = tdp / "runs" / "round92"
        ok, lines, exc = preflight_gate(tdp, p_vfy, "", False, round_dir=rd3, who="tester")
        expect("家族已实测 → 通过且无 exception",
               ok is True and not (rd3 / "preflight-exception.json").exists())

        rd4 = tdp / "runs" / "deep" / "round93"
        ok, lines, exc = preflight_gate(tdp, p_unv, "", True, round_dir=rd4, who="")
        expect("目标目录多级不存在也不崩（自动建目录）",
               ok is True and (rd4 / "preflight-exception.json").is_file())
        expect("who 缺省时写 unspecified 而非空串",
               (load_json(rd4 / "preflight-exception.json") or {}).get("who") == "unspecified")

        ok, lines, exc = preflight_gate(tdp, p_unv, "", True, round_dir=None, who="t")
        expect("放行但无 round_dir → 显式告警而非假装写过",
               ok is True and any("无法留痕" in x for x in lines))

    print("\n" + "-" * 68)
    total = n_ok + len(fails)
    if fails:
        print(f"[FAIL] 自检未通过：{len(fails)}/{total} 项失败")
        for f in fails:
            print("   -", f)
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{total} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
