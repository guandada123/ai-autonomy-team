#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seat_check.py —— 席位输出契约校验器 + 聚合防伪闸门

职责：
  1. 校验每个席位产出是否符合 seat_contract（缺 conclusion / evidence / falsification 即不合格）
  2. 抽取证据指针（供 idle_detection 跨轮比对）
  3. 反推立场向量（供 metrics.py 计算翻边率与分歧熵）
  4. `--verify-final`：核对 PM 的聚合产物是否引用了被否决席位

为什么独立成文件、不并入 verify.py：
  verify.py 的输出契约被 docs/protocol/04-双裁判规范.md 要求与 build_result() 逐字段一致，
  且其退出码在 regress.py 中被硬编码为判据。加一个「席位契约」模式会污染这个冻结契约。
  此外两者关注点正交：verify.py 校验「answer vs spec」，本脚本校验「LLM 产出 vs 契约」。

内部刻意自带 约 20 行同名 helper（load_json / sha256_of / resolve），**不抽共享模块**：
  verify.py 的 Decimal 与浮点语义深度纠缠，抽出来会让「冻结的硬裁判」依赖一个会变的模块。
  这是代码内重复，与 X3 要解决的「整文件重复」性质不同。

依赖：仅 Python 标准库。跨平台。

用法：
    python3 seat_check.py --round-dir ../../runs/round2
    python3 seat_check.py --lint-params
    python3 seat_check.py --verify-final ../../runs/round2
    python3 seat_check.py --self-test

退出码：
    0 = 全部席位合规
    1 = 存在不合规席位（或 --verify-final 发现引用被否决席位）
    2 = 输入/用法错误（目录不存在、JSON 不可解析、params 缺必需键）
    3 = 该轮目录内找不到任何席位产出（fail-closed：没东西可校验 ≠ 通过）
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

TZ_CN = timezone(timedelta(hours=8))

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2
EXIT_EMPTY = 3

DEFAULT_PARAMS = "config/params.json"
DEFAULT_SPEC = "fixtures/spec/adjudication.json"

# 家族推断关键词（与 scripts/inspect_agent_routing.py 的 FAMILY_RULES 同源，stdlib 内联）
FAMILY_KEYWORDS = (
    ("deepseek", "DeepSeek"), ("glm", "GLM"), ("chatglm", "GLM"),
    ("kimi", "Kimi"), ("moonshot", "Kimi"),
    ("hy3", "混元"), ("hy4", "混元"), ("hunyuan", "混元"),
    ("minimax", "MiniMax"), ("abab", "MiniMax"),
    ("claude", "Claude"), ("gpt", "OpenAI"), ("o1", "OpenAI"), ("o3", "OpenAI"),
    ("gemini", "Google"), ("qwen", "通义"), ("ernie", "文心"),
)


def _model_family(model_id):
    """从模型 id 推断厂商家族。推断，非自报 —— 依据是运行时 requestModelId。"""
    if not model_id:
        return "UNKNOWN"
    low = str(model_id).lower()
    for kw, fam in FAMILY_KEYWORDS:
        if kw in low:
            return fam
    return "UNKNOWN"


def _load_routing(fpath):
    """读 inspect_agent_routing.py --json 的输出 → {agent_name: requestModelId}。"""
    try:
        d = load_json(Path(fpath))
    except Exception:
        return {}
    out = {}
    for s in d.get("subagents") or []:
        raw = s.get("raw") or {}
        name = raw.get("providerData.agent") or ""
        mid = raw.get("providerData.requestModelId") or ""
        if name:
            out[name] = mid
    return out

# 参与辩论的席位（持立场）
DEBATING_SEATS = ("EX-01", "EX-02", "EX-03")
# 参与运行但不持立场的席位
NON_DEBATING_SEATS = ("AD-01", "PM-01", "XJ-01")

_MISSING = object()


# ---------------------------------------------------------------- helpers（刻意重复）

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
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None
    except (json.JSONDecodeError, ValueError):
        raise


def resolve(obj, path):
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


def now_iso():
    return datetime.now(TZ_CN).replace(microsecond=0).isoformat()


def to_num(v):
    """用于立场反推的数值归一（只用于数值比较，不参与金额裁决）。"""
    if isinstance(v, bool):
        return _MISSING
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if isinstance(v, str):
        try:
            return round(float(v.strip()), 2)
        except ValueError:
            return _MISSING
    return _MISSING


# ---------------------------------------------------------------- params

REQUIRED_PARAM_PATHS = [
    "debate.max_rounds",
    "anti_sycophancy.flip_rate_alert_threshold",
    "anti_sycophancy.flip_rate_denominator",
    "anti_sycophancy.dissent_entropy_floor.value",
    "anti_sycophancy.dissent_entropy_floor.action",
    "anti_sycophancy.idle_detection.consecutive_rounds_without_new_evidence",
    "anti_sycophancy.idle_detection.action",
    "seat_contract.required_fields",
    "seat_contract.stance.primary_axes",
    "seat_contract.stance.joint_axes",
    # F5 / F7 / F10 新增：缺了它们，契约版本门控、PM 自有字段门、读取域判定
    # 都会静默退化成「没这条规则」，正是 L-0005「没有定义式的阈值是装饰品」的翻版。
    "seat_contract.contract_version",
    "seat_contract.stages",
    "seat_contract.reads",
    "seat_contract.pm_required_fields",
    "read_domain.config",
    "read_domain.verdicts",
    "presentation.max_chars_per_field",
    "round_roles.revision_round.idle_detection_applies",
    "cost_budget.per_task_token_hard_cap",
    # F18 新增：预算若只有「上限」而没有「动作」与「申报开关」，
    # 就又是一条「有阈值没定义式」的装饰品（L-0005）。
    "cost_budget.per_task_wall_clock_hard_cap",
    "cost_budget.action",
    "cost_budget.usage.required_for_new_rounds",
]


def lint_params(params):
    """必需键缺失 → 硬失败。理由：静默默认会重建「有阈值没定义」的原缺陷。"""
    missing = []
    for p in REQUIRED_PARAM_PATHS:
        if resolve(params, p) is _MISSING:
            missing.append(p)
    return missing


# ---------------------------------------------------------------- 立场反推

def build_axis_registry(adj_spec, params):
    """从 adjudication.json 的 options[].implies 机械构建反推表。

    两坐标设计：
      * primary 轴（ADJ-001）：数值上完全可分 —— 逐 option 匹配其 implies 的全部键值
      * joint 轴（DIFFSET）：ADJ-002 与 ADJ-003 的 as_diff 分支 implies 数值完全重合，
        无法独立反推。故取其联合产生的可观测量 summary.diff_count。
        ★ choice 直接用 diff_count 的数值字符串（"7"/"6"/"5"），**不引入 wide/medium/narrow 别名** ——
          6 与 5 的选项 id 多重集完全相同（都是 as_diff+not_diff），只能靠数值区分；
          引入会随数据漂移的别名等于给自己埋雷。
    """
    sc = params["seat_contract"]["stance"]
    items = {it.get("id"): it for it in (adj_spec.get("items") or [])}
    registry = {"primary": {}, "joint": {}}

    for ax in sc.get("primary_axes") or []:
        it = items.get(ax)
        if not it:
            continue
        opts = []
        for o in it.get("options") or []:
            opts.append({"id": o.get("id"), "implies": o.get("implies") or {}})
        registry["primary"][ax] = opts

    for ax, members in (sc.get("joint_axes") or {}).items():
        observed = set()
        for mid in members:
            it = items.get(mid)
            if not it:
                continue
            for o in it.get("options") or []:
                for k, v in (o.get("implies") or {}).items():
                    if k.endswith("diff_count"):
                        n = to_num(v)
                        if n is not _MISSING:
                            observed.add(int(n))
        registry["joint"][ax] = {
            "key": "summary.diff_count",
            "values": sorted(observed, reverse=True),
            "members": list(members),
        }
    return registry


def derive_stance(answer, registry):
    """从产出数字反推立场。返回 {axis: {"choice": ..., "why": ...}}"""
    out = {}

    for ax, opts in (registry.get("primary") or {}).items():
        hit = None
        for o in opts:
            ok = True
            for path, exp in (o["implies"] or {}).items():
                got = to_num(resolve(answer, path))
                if got is _MISSING or got != to_num(exp):
                    ok = False
                    break
            if ok and o["implies"]:
                hit = o["id"]
                break
        out[ax] = {
            "choice": hit or "abstain",
            "why": "逐 option 匹配 implies 全部键值" if hit else "不匹配任何选项 → 弃权",
        }

    for ax, meta in (registry.get("joint") or {}).items():
        got = to_num(resolve(answer, meta["key"]))
        if got is _MISSING:
            out[ax] = {"choice": "abstain", "why": f"{meta['key']} 缺失"}
        elif int(got) in meta["values"]:
            out[ax] = {"choice": str(int(got)), "why": f"{meta['key']}={int(got)}"}
        else:
            out[ax] = {
                "choice": "out_of_space",
                "why": f"{meta['key']}={int(got)} 不在选项空间 {meta['values']} 内（如「跨月不计 + 口径不计」组合）",
            }

    return out


def declared_stance(answer):
    """读取席位自述的 stance，返回 {axis: choice}"""
    out = {}
    raw = answer.get("stance")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("axis"):
                out[str(item["axis"])] = str(item.get("choice", "abstain"))
    elif isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k)] = str(v)
    return out


def extract_pointers(answer, pkg_root=None, candidates=()):
    """抽取证据指针。

    ★ pointer_id 的定义式（F17 修复）：
        pointer_id = sha256(sorted("<file>|<selector>"))[:16]
      旧实现是 sha256(整条字符串)。那等于把【路径写法】这类格式噪声编进了语义指纹：
      同一份文件写成 `./fixtures/ledger.csv` 与 `fixtures/ledger.csv` 会被判成两条
      不同证据，于是「空转检测」可被无意义地绕过。

    ★ 残余边界（不可删，已写进协议 07 §4.5）：
      本式只消掉路径写法噪声。`#T104` 改成 `:26` 仍是不同 id ——
      换【选择子写法】依然可以伪造「新证据」。脚本无法区分
      「真的取了一个新观测」与「把同一个观测换了个写法」。
    """
    out = []
    ev = answer.get("evidence")
    if not isinstance(ev, list):
        return out
    for e in ev:
        if isinstance(e, dict):
            p = e.get("pointer") or e.get("path") or e.get("file")
            if p:
                out.append({
                    "pointer": str(p).strip(),
                    "what_it_shows": str(e.get("what_it_shows", "")).strip(),
                })
        elif isinstance(e, str) and e.strip():
            out.append({"pointer": e.strip(), "what_it_shows": ""})
    for item in out:
        comps = pointer_components(item["pointer"], pkg_root, candidates)
        item["pointer_parts"] = [{"file": f, "selector": sel} for f, sel in comps]
        item["pointer_id"] = pointer_id_of(item["pointer"], pkg_root, candidates)
    return out


# ---------------------------------------------------------------- 指针归一化（F6）

# 多指针分隔符：实测席位写 pointer 会用这些连接两个文件
_PTR_SPLIT = re.compile(r"\s*(?:\+|与|、|;|；)\s*")
# 路径 token：到首个 # / : / 空白 / 全角逗号 之前
_PTR_TOKEN = re.compile(r"^([^\s#:，,]+)")
# 括号注释：（bill.csv 无此单号）
_PTR_PAREN = re.compile(r"[（(][^（()）]*[）)]")
# 形如 xxx.ext 的路径
_PATH_LIKE = re.compile(r"[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]{1,8}$")


def resolve_pointer_path(token, pkg_root=None, candidates=()):
    """把自由文本里的路径 token 规范化到【包根相对】形式。

    实测 pointer 混三套根：包根（fixtures/ledger.csv）、
    工作区根（migration-package/judges/）、轮次目录（inputs/pack-ANON-A.json）。
    故按 candidates 顺序探测，取首个存在者；全部未命中 → 原样返回（【不猜】，
    后续按前缀字符串匹配）。目录型 token（尾 /）保留尾斜杠，以便匹配 deny 的前缀项。
    """
    t = str(token or "").strip().strip("，,")
    # ★ 词法归一（不依赖文件是否真实存在）：去 "./" 前缀、折叠重复斜杠。
    #   没有这一步，`./fixtures/x` 只有在文件存在时才会被归一；一旦路径是
    #   「将来才会生成」或「夹具里的占位路径」，两种写法就会被判成两个不同证据源。
    t = re.sub(r"^\./+", "", t)
    t = re.sub(r"/{2,}", "/", t)
    is_dir = t.endswith("/")
    t2 = t.rstrip("/")
    if not t2:
        return ""
    for base in candidates:
        if base is None:
            continue
        try:
            p = Path(base) / t2
            if not p.exists():
                continue
            rel = t2
            if pkg_root is not None:
                try:
                    rel = p.resolve().relative_to(Path(pkg_root).resolve()).as_posix()
                except (ValueError, OSError):
                    rel = t2      # 落在包外 → 保留原 token，不做相对化
            return rel + "/" if is_dir else rel
        except (OSError, ValueError):
            continue
    return (t2 + "/") if is_dir else t2


def normalize_pointer(ptr, pkg_root=None, candidates=()):
    """把自由文本证据指针归一化到【文件级】。

    ★ 为什么是文件级而不是记录级 —— 两类错误的代价不对称：
        粗（文件级）→ 更易判「不独立」→ 更易触发魔鬼代言人轮 = 多花一轮 token，是【干预】；
        细（记录级）→ 更易判「独立」→ 漏触发 = 共享盲区被当成已验证的真相放行，是【污染】。
      且实测同一行被写成 #T104,T105 / :26-27 / #L10 三种形式，
      记录级匹配会制造【假精度】（与 wide/medium/narrow 同源的病）。

    解析失败 → 用整串作 key（不猜它指向何处），并标 ok=False 供诊断。
    """
    raw = str(ptr or "")
    segs = [s for s in _PTR_SPLIT.split(raw) if s.strip()]
    files, selectors, kinds = [], [], []
    for seg in segs:
        s = _PTR_PAREN.sub(" ", seg).strip()
        m = _PTR_TOKEN.match(s)
        if not m:
            continue
        token = m.group(1)
        if not (_PATH_LIKE.search(token) or token.endswith("/")):
            continue
        rest = s[m.end():].strip()
        canon = resolve_pointer_path(token, pkg_root, candidates)
        if canon and canon not in files:
            files.append(canon)
        selectors.append({"file": canon, "selector": rest})
        kinds.append("dir" if canon.endswith("/") else "single_file")
    if not files:
        return {"raw": raw, "segments": segs, "files": [], "file_keys": [raw.strip()],
                "selectors": [], "primary_file": None, "kind": "unresolved", "ok": False}
    kind = "multi_file" if len(files) > 1 else (kinds[0] if kinds else "single_file")
    return {"raw": raw, "segments": segs, "files": files, "file_keys": sorted(files),
            "selectors": selectors, "primary_file": files[0], "kind": kind, "ok": True}


# ---------------------------------------------------------------- 指针指纹（F17）

POINTER_ID_DEFINITION = "sha256(sorted('<file>|<selector>'))[:16]"

_SEL_WS = re.compile(r"\s+")


def _canon_selector(sel):
    """选择子的最小规范化：折叠空白、去首尾标点。

    ★ 刻意【不】解析 `#`（记录 id）与 `:`（行号）的语义差别 —— 两者本就不同，
      强行统一会制造假等价，与 wide/medium/narrow 别名是同一类病。
    """
    s = _SEL_WS.sub(" ", str(sel or "").strip())
    return s.strip("，,。;； ")


def pointer_components(ptr, pkg_root=None, candidates=()):
    """返回已排序去重的 [(file, selector)]。解析失败 → [("", "raw:" + 原文)]（不猜）。"""
    n = normalize_pointer(ptr, pkg_root, candidates)
    if not n.get("ok"):
        return [("", "raw:" + str(ptr).strip())]
    comps = set()
    for s in n.get("selectors") or []:
        comps.add((s.get("file") or n.get("primary_file") or "",
                   _canon_selector(s.get("selector"))))
    if not comps:                      # 兜底：只有文件、没有选择子
        for f in n.get("file_keys") or []:
            comps.add((f, ""))
    return sorted(comps)


def pointer_id_of(ptr, pkg_root=None, candidates=()):
    """语义指纹：只对「文件 + 规范化选择子」的有序集合做哈希。

    排序后哈希 → 多个指针的书写顺序不影响结果（顺序不是语义）。
    """
    comps = pointer_components(ptr, pkg_root, candidates)
    raw = "|".join(f"{f}#{sel}" for f, sel in comps)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 读取域（F5）

def expand_domain(prefix, round_name=None, seat=None):
    """展开 {n} / {prev} / {seat} 占位符。

    {n}    = 轮次目录名去掉 "round" 前缀（round3 → 3）
    {prev} = n-1（round3 → 2）；仅在 n 为整数时展开，否则原样保留
    {seat} = 当前席位 id

    ★ 为什么需要 {prev}（F26）：审计席位的第 1 查（谁在验证谁）与第 3 查
      （共识是否被结构性制造）**必须**对比上一轮才能判断；而契约原本只给它本轮。
      于是它要么无法履职，要么越界被否决 —— 二者必居其一。这是契约缺口，不是席位过错。
    """
    s = str(prefix)
    if round_name:
        m = re.match(r"^round(.+)$", str(round_name))
        n = m.group(1) if m else str(round_name)
        s = s.replace("{n}", n)
        if n.isdigit():
            s = s.replace("{prev}", str(int(n) - 1))
    if seat:
        s = s.replace("{seat}", seat)
    return s


def load_read_domains(path, round_name=None):
    """装载读取域配置并展开占位符。返回 None 表示不可用。"""
    cfg = load_json(path)
    if not isinstance(cfg, dict):
        return None
    out = dict(cfg)
    for key in ("deny_absolute", "deny_default", "allow", "allow_exceptions"):
        out[key] = [expand_domain(p, round_name, None) for p in (cfg.get(key) or [])]
    extra = {}
    for seat, lst in (cfg.get("allow_extra_by_seat") or {}).items():
        extra[seat] = [expand_domain(p, round_name, seat) for p in (lst or [])]
    out["allow_extra_by_seat"] = extra
    return out


def classify_path(path, seat, rd):
    """返回 'deny' | 'allow' | 'unclassified'。

    优先级（见 read_domains.json 的 _README）：
      1) deny_absolute 命中 → deny（任何席位、任何情况都不可覆盖）
      2) allow（含 allow_exceptions 与 allow_extra_by_seat[seat]）命中 → allow
      3) deny_default 命中 → deny
      4) 其余 → unclassified
    ★ 第 2 层能覆盖第 3 层，是为了让「AD-01 必须读本轮机器产出」与
      「辩论席位禁读历史轮次」在同一张清单里共存。第 1 层不可覆盖，
      是为了防止有人用 allow_exceptions 把 truth.json 放回来。
    """
    p = str(path or "").replace("\\", "/").lstrip("./")
    if not p:
        return "unclassified"
    # ★ D-0019（2026-10-20，round13/round20 实测）：本轮 round-manifest.json 是轮次
    #   元数据（role/termination/契约版本），不含答案与席位产出，席位核对轮次类型
    #   需要它。按正则精确放行；runs/roundN/ 整目录仍禁读（修订轮去匿名化依赖）。
    if re.fullmatch(r"runs/round\d+/round-manifest\.json", p):
        return "allow"
    for pre in rd.get("deny_absolute") or []:
        if pre and p.startswith(pre):
            return "deny"
    allows = list(rd.get("allow") or []) + list(rd.get("allow_exceptions") or []) \
        + list((rd.get("allow_extra_by_seat") or {}).get(seat) or [])
    for pre in allows:
        if pre and p.startswith(pre):
            return "allow"
    for pre in rd.get("deny_default") or []:
        if pre and p.startswith(pre):
            return "deny"
    return "unclassified"


def extract_reads(answer):
    """抽取席位【申报】的读取清单（reads[]）。

    reads[] 与 evidence[] 语义不同，必须分开：
      evidence = 我用作证据的指针；reads = 我实际读过的文件全集（读了未引用也要写）。
    只看 evidence 会漏掉「读了真值但没把它写成证据」的席位 —— 那正是最需要抓的形态。
    """
    out = []
    raw = answer.get("reads")
    if not isinstance(raw, list):
        return out
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            p = item.get("path") or item.get("file") or item.get("pointer")
            if p and str(p).strip():
                out.append({"index": i, "path": str(p).strip(),
                            "purpose": str(item.get("purpose", "")).strip()})
        elif isinstance(item, str) and item.strip():
            out.append({"index": i, "path": item.strip(), "purpose": ""})
    return out


def _ver_ge(a, b):
    """版本比较（'2.0' >= '1.0'）。取首个数字段。"""
    def parts(v):
        nums = [int(x) for x in re.findall(r"\d+", str(v))][:2]
        while len(nums) < 2:
            nums.append(0)
        return tuple(nums)
    return parts(a) >= parts(b)


# ---------------------------------------------------------------- 契约校验

# ---------------------------------------------------------------- 语义一致性（F15 配套）

def stance_semantic_conflicts(adj_spec, derived):
    """金额轴 × 清单轴的语义一致性检查。**只出 warning，不阻塞**。

    定义式：
      设 A = ADJ-001 的选定选项，D = ADJ-002（轴名 DIFFSET）的选定选项
          c_ids = A 所属 item 的 affected_ids（口径差那两笔：T104/T105）
          money_counts_caliber := A.implies["summary.excluded_amount_total"] == 0
          list_counts_caliber  := |set(c_ids) ∩ set(D.implies["diffs"])| > 0
          冲突 := money_counts_caliber AND NOT list_counts_caliber

    即：「金额里算了口径差，但差异清单里没有口径差」——两个数字各自都能过校验，
    合起来却不自洽。这是 F15 合并轴之后**新暴露**的组合（旧设计里这种组合根本选不出来）。

    ★ 显式假设（不可省）：excluded_amount_total 的非零部分只来自口径差。
      该假设由夹具构造保证（跨月项两边金额都是 4800.00，金额差为 0）。
      若将来出现「跨月且金额不等」的样本，本判据会误报 —— 那时必须修正这里，
      而不是放宽判据。
    ★ 用 excluded_amount_total 而不是写死 choice == "count"：选项 id 改名
      不会让规则静默失效（L-0005 的教训）。
    """
    items = {it.get("id"): it for it in ((adj_spec or {}).get("items") or [])}
    a_item, d_item = items.get("ADJ-001"), items.get("ADJ-002")
    if not (a_item and d_item):
        return []
    a_choice = ((derived or {}).get("ADJ-001") or {}).get("choice")
    d_choice = ((derived or {}).get("DIFFSET") or {}).get("choice")
    for ch in (a_choice, d_choice):
        if not ch or ch in ("abstain", "out_of_space"):
            return []
    a_opt = next((o for o in a_item.get("options") or [] if o.get("id") == a_choice), None)
    d_opt = next((o for o in d_item.get("options") or [] if o.get("id") == d_choice), None)
    if not (a_opt and d_opt):
        return []
    c_ids = set(a_item.get("affected_ids") or [])
    if not c_ids:
        return []
    excl = (a_opt.get("implies") or {}).get("summary.excluded_amount_total")
    try:
        money_counts = Decimal(str(excl)) == Decimal("0")
    except (InvalidOperation, TypeError, ValueError):
        money_counts = False
    diffs = set((d_opt.get("implies") or {}).get("diffs") or [])
    if money_counts and not (c_ids & diffs):
        return [{
            "axis": "DIFFSET", "versus": "ADJ-001",
            "msg": (f"ADJ-001={a_choice}（金额里含口径差）但 DIFFSET={d_choice}"
                    f"（清单不含 {'/'.join(sorted(c_ids))}）—— 两处数字各自能过校验，"
                    f"但「金额差异包含清单里不存在的项」语义矛盾。请确认这是你的本意。"),
        }]
    return []


# ---------------------------------------------------------------- 预算（F18）

USAGE_BOUNDARY = (
    "usage 全部为【申报值】，脚本无法验证（与 reads[] 同类）。"
    "本机制只让预算成为「可追溯的声明 + 可执行的闸门」，不能证明数字为真。"
    "未申报 = 未核验 ≠ 未超支。"
)


def usage_errors(usage):
    """usage 结构校验。返回错误消息列表（空 = 合法）。"""
    if not isinstance(usage, dict):
        return ["usage 必须是对象 {tokens, minutes, source}"]
    errs = []
    t = usage.get("tokens")
    if isinstance(t, bool) or not isinstance(t, int):
        errs.append(f"tokens 必须是整数（实际 {t!r}）")
    elif t < 0:
        errs.append(f"tokens 不得为负（实际 {t}）")
    m = usage.get("minutes")
    if isinstance(m, bool) or not isinstance(m, (int, float)):
        errs.append(f"minutes 必须是数字（实际 {m!r}）")
    elif m < 0:
        errs.append(f"minutes 不得为负（实际 {m}）")
    src = usage.get("source", "self_report")
    if src not in ("self_report", "metered"):
        errs.append(f"source 只能是 self_report / metered（实际 {src!r}）")
    return errs


def evaluate_budget(report, params):
    """汇总申报用量并与 cost_budget 硬上限比对。

    minutes 取【最大值】而不是求和：PM-01 的 minutes 定义为本轮 wall-clock 跨度，
    其余席位是各自用时 —— 二者是包含关系，不是并列关系。

    status 三态：
      exhausted     —— 有申报值且超上限（可执行的动作：terminate_branch）
      unverifiable  —— 有席位未申报，无法判断（**不等于超支**）
      ok            —— 全部申报且未超限
    """
    cfg = (params or {}).get("cost_budget") or {}
    tok_cap = cfg.get("per_task_token_hard_cap")
    min_cap = cfg.get("per_task_wall_clock_hard_cap")
    tokens_total, minutes_span = 0, None
    declaring, missing = 0, []
    for r in report.get("results") or []:
        u = (r.get("derived") or {}).get("usage")
        if not isinstance(u, dict):
            missing.append(r.get("seat"))
            continue
        declaring += 1
        try:
            tokens_total += int(u.get("tokens") or 0)
        except (TypeError, ValueError):
            pass
        m = u.get("minutes")
        if isinstance(m, (int, float)) and not isinstance(m, bool):
            minutes_span = max(minutes_span or 0.0, float(m))
    reasons = []
    if tok_cap is not None and tokens_total > tok_cap:
        reasons.append(f"tokens {tokens_total} > 上限 {tok_cap}")
    if min_cap is not None and minutes_span is not None and minutes_span > min_cap:
        reasons.append(f"wall-clock {minutes_span:g} 分钟 > 上限 {min_cap}")
    status = "exhausted" if reasons else ("unverifiable" if missing else "ok")
    return {
        "status": status,
        "action": cfg.get("action"),
        "tokens_total": tokens_total,
        "tokens_cap": tok_cap,
        "minutes_span": minutes_span,
        "minutes_cap": min_cap,
        "tokens_span_note": "tokens 为求和，minutes 为取最大 —— 两者不是同一算法",
        "tokens_definition": "sum(全部席位 usage.tokens)（本轮六席合计，不是单席上限）",
        "minutes_definition": "max(各席位 usage.minutes)；PM-01 的 minutes 即本轮跨度",
        "seats_declaring": declaring,
        "missing": missing,
        "exhausted_reasons": reasons,
        "boundary": USAGE_BOUNDARY,
    }


def check_seat(seat, answer, params, registry, round_role, ctx=None):
    """校验单席位产出。返回 result dict。

    ctx（可选）承载读取域与契约版本：
      rd               read_domains 配置（None = 不做读取域判定）
      reads_required   该轮契约是否要求 reads[]（v>=2.0）
      pkg_root / roots 路径规范化用
    """
    ctx = ctx or {}
    errors, warnings = [], []
    sc = params["seat_contract"]
    # ★ PM-01 用【自有字段】校验，不套 base 契约：实测 runs/round2/PM-01.json 没有
    #   seat 字段，套 base 契约必然误判（它的产物就是聚合本身，不是「聚合的输入」）。
    if seat == "PM-01":
        required = list(sc.get("pm_required_fields") or sc["required_fields"])
    else:
        required = list(sc["required_fields"])
    reads_required = bool(ctx.get("reads_required"))
    if reads_required and seat != "PM-01" and "reads" not in required:
        required = required + ["reads"]
    exempt = set(sc.get("stance_exempt_seats") or [])
    maxchars = params["presentation"]["max_chars_per_field"]

    if answer is None:
        return {
            "seat": seat, "status": "invalid",
            "errors": [{"code": "E_NOT_JSON", "field": "-", "msg": "文件缺失或不是合法 JSON"}],
            "warnings": [], "derived": {},
        }

    for f in required:
        if f not in answer:
            errors.append({"code": f"E_MISSING_{f.upper()}", "field": f, "msg": f"硬性字段缺失：{f}"})
        else:
            v = answer[f]
            if f == "conclusion" and (not isinstance(v, str) or not v.strip()):
                errors.append({"code": "E_EMPTY_CONCLUSION", "field": f, "msg": "conclusion 不得为空"})
            if f == "evidence":
                if not isinstance(v, list) or not v:
                    errors.append({"code": "E_EMPTY_EVIDENCE", "field": f,
                                   "msg": "evidence 须为 ≥1 项的数组（外部钩子，结构审计第 2 查）"})
                else:
                    for i, item in enumerate(v):
                        if not isinstance(item, dict) or not str(item.get("pointer", "")).strip() \
                                or not str(item.get("what_it_shows", "")).strip():
                            errors.append({"code": "E_BAD_EVIDENCE_ITEM", "field": f"{f}[{i}]",
                                           "msg": "每项须含非空 pointer 与非空 what_it_shows"})
            if f == "falsification" and (not isinstance(v, str) or not v.strip()):
                errors.append({"code": "E_MISSING_FALSIFICATION", "field": f,
                               "msg": "falsification 不得为空（协议：缺失即视为不可裁决）"})
            if f == "reads":
                if not isinstance(v, list) or not v:
                    errors.append({"code": "E_MISSING_READS", "field": f,
                                   "msg": "reads 须为 ≥1 项的数组（你【实际读过】的文件全集；"
                                          "读了但没写成证据的也要写）"})
                else:
                    for i, item in enumerate(v):
                        if isinstance(item, dict):
                            if not str(item.get("path", "")).strip():
                                errors.append({"code": "E_BAD_READS_ITEM", "field": f"reads[{i}]",
                                               "msg": "每项须含非空 path"})
                        elif not (isinstance(item, str) and item.strip()):
                            errors.append({"code": "E_BAD_READS_ITEM", "field": f"reads[{i}]",
                                           "msg": "每项须为 {path, purpose} 或非空字符串"})

    # 字数预算
    for field, limit in (maxchars or {}).items():
        v = answer.get(field)
        if isinstance(v, str) and len(v) > limit:
            warnings.append({"code": "W_OVER_LENGTH", "field": field,
                             "msg": f"{len(v)} 字 > 预算 {limit}"})
        elif isinstance(v, list):
            for i, item in enumerate(v):
                s = json.dumps(item, ensure_ascii=False)
                if len(s) > limit:
                    warnings.append({"code": "W_OVER_LENGTH", "field": f"{field}[{i}]",
                                     "msg": f"{len(s)} 字 > 预算 {limit}"})

    # 软项
    # ★ 豁免席位不应收到「stance 缺失」警告 —— 否则豁免形同虚设，且会淹没真实信号。
    #   同理，非辩论席位用自己的字段名表达「异议」（XJ-01 用 disagreements，
    #   EX-02 用 strongest_counter_arguments），不适用 `dissent` 缺失告警。
    EXEMPT_FIELD_ALIASES = {
        "stance": {"AD-01", "PM-01", "XJ-01"},
        "dissent": {"XJ-01"},
        "needs_more_data": {"XJ-01"},
    }
    for f in sc.get("warning_fields") or []:
        if f in answer:
            continue
        if seat in (EXEMPT_FIELD_ALIASES.get(f) or set()):
            continue
        if f == "dissent" and any(k in answer for k in ("disagreements", "strongest_counter_arguments")):
            continue
        warnings.append({"code": f"W_MISSING_{f.upper()}", "field": f,
                         "msg": f"软性字段缺失：{f}"})
    if "confidence" not in answer and seat not in (EXEMPT_FIELD_ALIASES.get("stance") or set()):
        warnings.append({"code": "W_MISSING_CONFIDENCE", "field": "confidence",
                         "msg": "缺 confidence，将按 medium 处理"})

    # stance：反推 + 与自述比对
    derived, declared, mismatches = {}, {}, []
    derived = {}
    if seat in exempt:
        pass
    else:
        derived = derive_stance(answer, registry)
        declared = declared_stance(answer)
        if not declared:
            warnings.append({"code": "W_STANCE_DERIVED_FALLBACK", "field": "stance",
                             "msg": "未自述 stance，已由产出数字反推补齐"})
        for ax, d in derived.items():
            if d["choice"] == "out_of_space":
                errors.append({"code": "E_STANCE_OUT_OF_SPACE", "field": f"stance.{ax}",
                               "msg": d["why"]})
            if ax in declared and declared[ax] != d["choice"]:
                mismatches.append({"axis": ax, "declared": declared[ax], "derived": d["choice"]})
                warnings.append({"code": "W_STANCE_MISMATCH", "field": f"stance.{ax}",
                                 "msg": f"自述 {declared[ax]} vs 反推 {d['choice']} —— 这是有价值的信号，非错误"})

    # 匿名化：revision 轮里 dissent.target 不得写真实席位名
    if round_role == "revision_round":
        for f, v in (("dissent", answer.get("dissent")),
                     ("strongest_counter_arguments", answer.get("strongest_counter_arguments"))):
            if isinstance(v, list):
                for i, item in enumerate(v):
                    if not isinstance(item, dict):
                        continue
                    tgt = str(item.get("target") or item.get("against") or "")
                    if tgt in DEBATING_SEATS or tgt in NON_DEBATING_SEATS:
                        errors.append({"code": "E_PEER_IDENTIFIED", "field": f"{f}[{i}].target",
                                       "msg": f"揭示了同伴真实身份「{tgt}」，应写匿名编号"})

    ptrs = extract_pointers(answer, ctx.get("pkg_root"), ctx.get("roots") or ())

    # ---- v2.1：AD-01 必须锚定裁判侧产物（第 1 查「谁在验证谁」的时序前提）----
    # ★ 为什么用字段锚定而不是 mtime：mtime 在 cp -r / git clone / 网盘中转后会被
    #   重置，跨机不可用 —— 而转移包正是跨机场景；且 mtime 结论一旦进模板正文，
    #   模板就无法逐字节重现，第 33 项会红。
    # ★ 为什么 ts 能证明时序：ts 由 now_iso() 在文件**写完之后**才存在。AD-01 若在
    #   裁决前就写完结论，不可能抄到这个值；能抄到，就说明它至少读过一次。
    if seat == "AD-01" and ctx.get("audit_pin_required"):
        aud = answer.get("audited_inputs")
        if not isinstance(aud, list) or not aud:
            errors.append({"code": "E_AUDIT_INPUT_MISSING", "field": "audited_inputs",
                           "msg": "契约 v2.1 要求 AD-01 产出 audited_inputs[]，逐项抄录"
                                  "本轮 verdict-layered.json 与 pending-adjudication.json "
                                  "的 verdict / spec_layer / ts —— 否则无法证明审计"
                                  "发生在裁决之后（第 1 查）"})
        else:
            _root = Path(ctx.get("pkg_root") or ".")
            _paths = []
            for i, x in enumerate(aud):
                if not isinstance(x, dict) or not x.get("path"):
                    errors.append({"code": "E_AUDIT_INPUT_BAD",
                                   "field": f"audited_inputs[{i}]",
                                   "msg": "每项必须是含 path 的对象"})
                    continue
                rel = str(x["path"])
                _paths.append(rel)
                fp = _root / rel.lstrip("./")
                try:
                    src = load_json(fp)
                except (json.JSONDecodeError, ValueError):
                    src = None
                if not isinstance(src, dict):
                    errors.append({"code": "E_AUDIT_INPUT_STALE",
                                   "field": f"audited_inputs[{i}].path",
                                   "msg": f"锚定目标不存在或不可解析：{rel}"
                                          f"（解析为 {fp.as_posix()}）"})
                    continue
                for k in ("verdict", "spec_layer", "ts"):
                    if k in x and str(x[k]) != str(src.get(k)):
                        errors.append({"code": "E_AUDIT_INPUT_STALE",
                                       "field": f"audited_inputs[{i}].{k}",
                                       "msg": f"{rel} 的 {k} 实际为 {src.get(k)!r}，"
                                              f"产出写的是 {x[k]!r} —— 锚定的不是这一版"})
                if x.get("pending_count") is not None:
                    real = (src.get("summary") or {}).get("pending")
                    if real is not None and str(x["pending_count"]) != str(real):
                        errors.append({"code": "E_AUDIT_INPUT_STALE",
                                       "field": f"audited_inputs[{i}].pending_count",
                                       "msg": f"{rel} 的 summary.pending 实际为 {real}，"
                                              f"产出写的是 {x['pending_count']}"})
            # 覆盖性：裁决文件必须被锚定；pending 只在它确实存在时要求
            if not any("verdict" in p for p in _paths):
                errors.append({"code": "E_AUDIT_INPUT_INCOMPLETE", "field": "audited_inputs",
                               "msg": "必须锚定本轮的 verdict 文件（verdict-layered.json）"})
            _rd = Path(ctx["round_dir"]) if ctx.get("round_dir") else None
            if _rd is not None and (_rd / "pending-adjudication.json").is_file() \
                    and not any("pending-adjudication" in p for p in _paths):
                errors.append({"code": "E_AUDIT_INPUT_INCOMPLETE", "field": "audited_inputs",
                               "msg": "本轮存在 pending-adjudication.json，必须一并锚定"
                                      "（第 5 查依赖它证明判断层是否已裁）"})

    # ---- F5：读取域判定（【检测】，不是【阻止】—— 见 06 文档第三节的诚实边界）----
    # ★ 严重度由【该轮契约版本】决定，不写死：
    #   v>=2.0 → error（该轮次有 reads[] 概念，越界即挡在聚合外）
    #   v<2.0  → warning（历史轮次无此概念，用 error 会把它们追溯作废 ——
    #            而 round2 必须保持 4/4 合规，否则「round2 可作第 3 轮基线」这个
    #            F7 结论会被自己的修复摧毁）
    #   无论严重度如何，越界事实都会进入 derived.read_domain → 第 9 查照样记 HIT。
    rd = ctx.get("rd")
    reads_seen = extract_reads(answer)
    reads_recs, ev_recs = [], []
    hard = reads_required

    def _rec(store, where, declared, canon):
        cls = classify_path(canon, seat, rd)
        store.append({"source": where, "declared": declared, "canonical": canon, "class": cls})
        if cls == "deny":
            # ★ D-0021（2026-10-20，round13/14 实测四起）：席位对本轮目录本身的
            #   纯列举申报降级 warning——读目录无文件内容泄漏面；修订轮匿名包在
            #   inputs/ 子目录，canon 不会等于本轮目录。
            if re.fullmatch(r"runs/round\d+/", canon) is not None:
                warnings.append({"code": "W_DIR_LISTING_SELF", "field": where,
                                 "msg": f"本轮目录列举申报（降级，无泄漏面）：{declared} → {canon}"})
            else:
                (errors if hard else warnings).append({
                    "code": "E_READ_DOMAIN_VIOLATION" if hard else "W_READ_DOMAIN_VIOLATION",
                    "field": where, "msg": f"读取域越界：{declared} → {canon}"})
        elif cls == "unclassified":
            warnings.append({"code": "W_READS_UNCLASSIFIED", "field": where,
                             "msg": f"路径不在读取域任何清单内：{canon}"})
        return cls

    if rd:
        pkg_root = ctx.get("pkg_root")
        roots = ctx.get("roots") or ()

        def _keys(freetext):
            """自由文本 → 归一化后的文件级 key 列表。

            ★ 必须走 normalize_pointer，不能直接 resolve 整串：
              实测 pointer 形如 'inputs/pack-ANON-A.json 的 diffs[0].category 与
              summary.amount_diff_total'（含自然语言与多指针连接词），
              直接解析整串会得到 unclassified 的假阳性，把真实信号淹掉。
            """
            return normalize_pointer(freetext, pkg_root, roots)["file_keys"]

        for item in reads_seen:
            for k in _keys(item["path"]):
                _rec(reads_recs, f"reads[{item['index']}]", item["path"], k)
        for i, p in enumerate(ptrs):
            for k in _keys(p["pointer"]):
                _rec(ev_recs, f"evidence[{i}]", p["pointer"], k)
        if not reads_seen:
            warnings.append({"code": "W_MISSING_READS", "field": "reads",
                             "msg": "未申报 reads[] → 第 9 查记 NO_DECLARATION（【未能核验】，不判错）"})

    # ---- F15 配套：金额轴 × 清单轴的语义一致性（只告警，不阻塞）----
    #   放在这里是因为：这是唯一同时握着「同一席位两个轴的实际取值」的位置。
    conflicts = stance_semantic_conflicts(ctx.get("adj_spec"), derived)
    for c in conflicts:
        warnings.append({"code": "W_STANCE_SEMANTIC_CONFLICT",
                         "field": f"stance.{c['axis']}", "msg": c["msg"]})

    # ---- F18：usage 申报（声明式预算）----
    #   严重度按轮次门控：只有 manifest 声明 usage_required=true 的新轮次才硬性要求，
    #   历史轮次（round2 及其夹具）没有该字段，不得追溯作废。
    usage_rec = None
    usage = answer.get("usage")
    if usage is not None:
        uerrs = usage_errors(usage)
        if uerrs:
            errors.append({"code": "E_BAD_USAGE", "field": "usage", "msg": "；".join(uerrs)})
        else:
            usage_rec = {"tokens": int(usage.get("tokens") or 0),
                         "minutes": float(usage.get("minutes") or 0),
                         "source": usage.get("source", "self_report")}
    elif ctx.get("usage_required"):
        errors.append({"code": "E_MISSING_USAGE", "field": "usage",
                       "msg": "本轮要求申报 usage{tokens, minutes, source} —— "
                              "缺失即无法核算预算，按契约判不合格"})

    return {
        "seat": seat,
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
        "stance_mismatches": mismatches,
        "derived": {
            "evidence_pointer_count": len(ptrs),
            "evidence_pointers": ptrs,
            "pointer_id_definition": POINTER_ID_DEFINITION,
            "declared_stance": declared,
            "derived_stance": derived,
            "axes": sorted(derived.keys()),
            "semantic_conflicts": conflicts,
            "usage": usage_rec,
            "read_domain": {
                "declared_reads": reads_recs,
                "evidence_derived": ev_recs,
                "applied": bool(rd),
                "severity": "error" if hard else "warning",
            },
        },
    }


def check_round(round_dir, params, adj_spec, strict_warnings=False, stage="all",
                rd=None, pkg_root=None, roots=(), routing=None):
    """校验整轮。

    stage（F10 修复）：契约校验必须发生在【两处】，因为 AD-01/PM-01 的产出
      晚于 EX/XJ（时序缺陷：原实现只跑一次，跑在 AD/PM 产出之前，
      于是这两个席位【从未被契约校验过】）。
        A = EX-01/02/03 + XJ-01（席位产出后）
        B = AD-01 + PM-01（聚合与审计产出后）
        all = 两者并集（缺省，保持既有行为不变）
    """
    round_dir = Path(round_dir)
    manifest = load_json(round_dir / "round-manifest.json") or {}
    round_role = manifest.get("role", "unknown")
    # ★ 版本随【轮次】而非随工具：若用全局版本，重跑历史轮次会把它追溯作废
    round_ver = str(manifest.get("contract_version") or "1.0")
    reads_required = _ver_ge(round_ver, "2.0")

    if stage == "B":
        pool, strict_missing = ["AD-01", "PM-01"], True
    elif stage == "A":
        pool, strict_missing = list(DEBATING_SEATS) + ["XJ-01"], False
    else:
        pool, strict_missing = list(DEBATING_SEATS) + list(NON_DEBATING_SEATS), False
    expected = []
    for s in pool:
        if s in DEBATING_SEATS or (round_dir / f"{s}.json").is_file() or strict_missing:
            expected.append(s)

    registry = build_axis_registry(adj_spec, params)
    ctx = {"rd": rd, "reads_required": reads_required,
           "pkg_root": pkg_root, "roots": roots,
           "round_dir": str(round_dir),
           "adj_spec": adj_spec,
           # ★ v2.1：AD-01 必须锚定裁判侧产物（第 1 查时序）。同样按轮次门控，
           #   否则 round2/round3（2.0）会被追溯判死 —— 它们早于本机制。
           "audit_pin_required": _ver_ge(round_ver, "2.1"),
           # F18：预算申报的硬性要求按【轮次】门控（与 contract_version 同构），
           # 不按工具全局开关 —— 否则 round2 与全部夹具会被追溯判死。
           "usage_required": bool(manifest.get("usage_required", False))}

    results, found = [], []
    for seat in expected:
        fp = round_dir / f"{seat}.json"
        if not fp.is_file():
            if seat in DEBATING_SEATS or strict_missing:
                results.append({
                    "seat": seat, "file": str(fp), "sha256": None, "status": "invalid",
                    "errors": [{"code": "E_MISSING_SEAT", "field": "-", "msg": "轮次内缺少该席位产出"}],
                    "warnings": [], "derived": {},
                })
            continue
        try:
            answer = load_json(fp)
        except (json.JSONDecodeError, ValueError):
            answer = "__BAD_JSON__"
        if answer == "__BAD_JSON__":
            results.append({
                "seat": seat, "file": str(fp), "sha256": sha256_of(fp), "status": "invalid",
                "errors": [{"code": "E_NOT_JSON", "field": "-", "msg": "不是合法 JSON"}],
                "warnings": [], "derived": {},
            })
            found.append(seat)
            continue
        r = check_seat(seat, answer, params, registry, round_role, ctx)
        r["file"] = str(fp).replace("\\", "/")
        r["sha256"] = sha256_of(fp)
        if strict_warnings and r["warnings"]:
            r["errors"].append({"code": "E_STRICT_WARNINGS", "field": "-",
                                "msg": f"--strict-warnings：{len(r['warnings'])} 条 warning 升级为错误"})
            r["status"] = "invalid"
        results.append(r)
        found.append(seat)

    blocked = [r for r in results if r["status"] != "valid"]
    valid = [r for r in results if r["status"] == "valid"]

    if not found:
        verdict = "no_seats_found"
    elif blocked:
        verdict = "has_invalid"
    else:
        verdict = "all_valid"

    # ---- 软裁判家族核验（D-0012 遗留项落地）：读运行时 providerData，不采信自报 ----
    # protocol/04：XJ-01（跨家族评委）不得由辩论席同家族模型担任。round4 实测发现
    # default 路由会令六席同家族 —— 该前提必须由机器核验，而非 XJ-01 自述。
    xj_family_check = None
    if routing:
        def _norm(s):
            return "".join(ch for ch in str(s).lower() if ch.isalnum())
        fam = {n: _model_family(m) for n, m in routing.items()}
        ex_f, xj_f = set(), set()
        for n, f in fam.items():
            nn = _norm(n)
            if any(_norm(s) in nn for s in DEBATING_SEATS):
                ex_f.add(f)
            if any(_norm(s) in nn for s in NON_DEBATING_SEATS if s == "XJ-01"):
                xj_f.add(f)
        cross = (not bool(ex_f & xj_f)) if (ex_f and xj_f) else None
        xj_family_check = {
            "source": "providerData.requestModelId（inspect_agent_routing.py --json 输出，经 --routing-json 注入）",
            "ex_families": sorted(ex_f),
            "xj_families": sorted(xj_f),
            "cross_family": cross,
            "note": ("cross_family=True 才满足 protocol/04 对 XJ-01 的家族要求；"
                     "False → 本轮 XJ-01 线索按同家族降权（其决策权重本就为 0）；"
                     "None → 记录不足以判定。家族由模型 id 关键词推断，非自报。"),
        }

    return {
        "check": "seat_contract",
        "xj_family_check": xj_family_check,
        "contract_version": round_ver,
        "expected_contract_version": str(
            (params.get("seat_contract") or {}).get("contract_version") or "1.0"),
        "reads_required": reads_required,
        "stage": stage,
        "round_dir": str(round_dir).replace("\\", "/"),
        "round_role": round_role,
        "generated_at": now_iso(),
        "params_sha256": None,  # 由调用方补
        "seats_expected": expected,
        "results": results,
        "aggregation_admissible": [r["seat"] for r in valid],
        "aggregation_blocked": [{"seat": r["seat"], "sha256": r.get("sha256"),
                                 "errors": [e["code"] for e in r["errors"]]} for r in blocked],
        "verdict": verdict,
        "summary": {"total": len(results), "valid": len(valid), "invalid": len(blocked)},
    }


def build_read_domain_report(round_dir, report, rd, rd_path=None):
    """第 9 查的机器产出（F5：席位读取域）。

    ★ 为什么独立成文件、不并进 seat-check.json：
      AD-01 必须能【独立目视核验】第 9 查，而不是相信一个结论值。
      故本文件同时保留每席位的原始提取路径（declared_reads / evidence_derived），
      让「脚本判了什么」与「它据以判断的原始材料」一起可见。

    verdict 三态：
      CLEAN          未发现 deny 命中
      HIT            存在 deny 命中  ⇒ 结构审计第 9 查 HOLD
      NO_DECLARATION 无任何席位申报 reads[]（v<2.0 的历史轮次即此形态）
                     ⇒ 记「未能核验」，【不】据此 HOLD
                       （否则会把所有历史轮次一律判 HOLD —— 那是噪声不是信号）
    """
    per_seat, hits, declared_any = [], [], False
    for r in report.get("results", []):
        info = (r.get("derived") or {}).get("read_domain") or {}
        dd = info.get("declared_reads") or []
        ed = info.get("evidence_derived") or []
        if dd:
            declared_any = True
        for rec in dd + ed:
            if rec.get("class") == "deny":
                hits.append({"seat": r["seat"], "source": rec["source"],
                             "declared": rec["declared"], "canonical": rec["canonical"]})
        per_seat.append({"seat": r["seat"], "status": r["status"],
                         "declared_reads": dd, "evidence_derived": ed,
                         "severity": info.get("severity", "n/a")})

    verdict = "HIT" if hits else ("NO_DECLARATION" if not declared_any else "CLEAN")
    return {
        "check": "read_domain",
        "round_dir": report.get("round_dir"),
        "round_role": report.get("round_role"),
        "contract_version": report.get("contract_version"),
        "stage": report.get("stage"),
        "generated_at": now_iso(),
        "read_domains": str(rd_path).replace("\\", "/") if rd_path else None,
        "applied": bool(rd),
        "verdict": verdict,
        "hits": hits,
        "per_seat": per_seat,
        "summary": {"seats": len(per_seat), "hits": len(hits),
                    "seats_declaring_reads": sum(1 for s in per_seat if s["declared_reads"])},
        "boundary": ("本文件是【声明式下界】：CLEAN 只说明「没有可检出的越界」，"
                     "不说明「没有越界」。席位读取未申报的文件，对脚本完全不可见。"),
        "action": "HOLD" if verdict == "HIT" else ("UNVERIFIED" if verdict == "NO_DECLARATION" else "PASS"),
    }


def verify_final(round_dir, params=None):
    """聚合防伪 + 产物验收门：PM 的 answer-final.json 不得引用被否决席位。

    F10：本函数同时承担 PM 的 schema 完整性门。为什么放在这里而不放进
    aggregation_blocked：后者是「聚合【输入】门」，而 PM 的产物就是聚合本身，
    放进去会让 metrics.py 误判 blocked_by_contract 短路（域不对）。
    本函数是「【产物】验收门」——不合格 = 本轮不验收 = PM 重做聚合，
    是返工不是死锁。
    """
    round_dir = Path(round_dir)
    sc_path = round_dir / "seat-check.json"
    sc = load_json(sc_path)
    if sc is None:
        print(f"[ERR] 缺少 {sc_path}，请先运行 --round-dir 生成契约校验结果",
              file=sys.stderr)
        return EXIT_ERROR
    blocked = {b["sha256"] for b in sc.get("aggregation_blocked", []) if b.get("sha256")}
    blocked_seats = {b["seat"] for b in sc.get("aggregation_blocked", [])}

    af_path = round_dir / "answer-final.json"
    af = load_json(af_path)
    if af is None:
        print(f"[ERR] 缺少 {af_path}", file=sys.stderr)
        return EXIT_ERROR

    inputs = af.get("aggregation_inputs")
    if not isinstance(inputs, list) or not inputs:
        print("[FAIL] answer-final.json 缺少 aggregation_inputs[] —— "
              "PM 必须声明它聚合了哪些席位及各自契约状态", file=sys.stderr)
        return EXIT_INVALID

    violations = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        if item.get("seat") in blocked_seats:
            violations.append(f"引用了被否决席位 {item['seat']}")
        if item.get("seat_check_status") != "valid":
            violations.append(f"{item.get('seat')} 的状态为 {item.get('seat_check_status')}，非 valid")
        if item.get("sha256") and item["sha256"] in blocked:
            violations.append(f"引用了被否决产出的 sha256 {str(item['sha256'])[:16]}…")

    if violations:
        print("[FAIL] 聚合防伪校验未通过：", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return EXIT_INVALID

    # ---- F10 阶段 B：PM 产出 schema 完整性（产物验收门）----
    # 严重度由该轮 contract_version 决定，与 reads[] 用同一套机制（不引入第二条
    # 行为不同的规则）。实测 runs/round2/PM-01.json 八字段本就齐备，故此门控是双保险。
    if params is not None:
        ver = str((load_json(round_dir / "round-manifest.json") or {})
                  .get("contract_version") or "1.0")
        pm_req = list((params.get("seat_contract") or {}).get("pm_required_fields") or [])
        if pm_req:
            pm = load_json(round_dir / "PM-01.json")
            miss = [f for f in pm_req if pm is None or f not in pm]
            if miss:
                msg = (f"PM-01.json 缺 {len(miss)}/{len(pm_req)} 个自有字段：{miss}"
                       + ("（文件不存在）" if pm is None else ""))
                if _ver_ge(ver, "2.0"):
                    print(f"[FAIL] 产物验收门（契约 v{ver}）：{msg}", file=sys.stderr)
                    return EXIT_INVALID
                print(f"[WARN] {msg} —— 契约 v{ver} < 2.0，仅告警；新轮次将硬失败",
                      file=sys.stderr)
            else:
                print(f"[OK] PM-01 schema 完整（{len(pm_req)} 个自有字段）")

        # ---- v2.1：answer-status.json（AD-01 第 7 查①的机器可读载体）----
        # ★ 存在即须自洽；缺失只在 v>=2.1 时失败。
        #   不追溯要求 round2/round3 存在（它们早于本机制），但一旦存在就必须对 ——
        #   一个撒谎的状态文件比没有更危险。
        st_path = round_dir / "answer-status.json"
        st = load_json(st_path)
        if st is None:
            msg = ("answer-status.json 缺失 —— 待裁状态只写在 pm_note 散文里，"
                   "机器无法读取，第 7 查只能靠人读文本")
            if _ver_ge(ver, "2.1"):
                print(f"[FAIL] 产物验收门（契约 v{ver}）：{msg}", file=sys.stderr)
                return EXIT_INVALID
            print(f"[WARN] {msg} —— 契约 v{ver} < 2.1，仅告警；新轮次将硬失败",
                  file=sys.stderr)
        else:
            probs = []
            _ans_sha = sha256_of(af_path)
            _got = str((st.get("answer") or {}).get("sha256") or "")
            if _got != _ans_sha:
                probs.append(f"answer.sha256 不匹配：状态文件记 {_got[:16]}…，"
                             f"实际 answer-final.json 为 {str(_ans_sha)[:16]}… "
                             f"（状态描述的是另一版答案）")
            _state = st.get("adjudication_state")
            if _state not in ("pending", "adjudicated", "fail", "not_applicable"):
                probs.append(f"adjudication_state 非法：{_state!r}")
            _pend = st.get("pending_items") or []
            if _state == "pending":
                if not _pend:
                    probs.append("state=pending 但 pending_items 为空")
                else:
                    _pj = load_json(round_dir / "pending-adjudication.json")
                    if isinstance(_pj, dict):
                        _real = sorted(p.get("id") for p in (_pj.get("pending") or [])
                                       if isinstance(p, dict))
                        if sorted(_pend) != _real:
                            probs.append(f"pending_items {sorted(_pend)} 与 "
                                         f"pending-adjudication.json 的 {_real} 不一致")
            if _state == "adjudicated" and not st.get("record"):
                probs.append("state=adjudicated 但 record 字段为空")
            if _state == "fail" and st.get("verdict") != "fail":
                probs.append(f"state=fail 但 verdict={st.get('verdict')!r}"
                             f"（状态与裁决矛盾）")
            # spec 在本轮之后被改动 → 本条状态已不适用
            # ★ 任务参数化（2026-10-20）：001 在 fixtures/spec，002 及以后在 fixtures/<task>/spec
            _task = str((load_json(round_dir / "round-manifest.json") or {}).get("task") or "001")
            _fx = "fixtures" if _task == "001" else f"fixtures/{_task}"
            _sp = round_dir.parent.parent / f"{_fx}/spec/adjudication.json"
            _ss = (st.get("spec") or {}).get("sha256")
            if _ss and _sp.is_file() and _ss != sha256_of(_sp):
                probs.append("spec.sha256 与当前 fixtures/spec/adjudication.json 不一致"
                             "（spec 在本轮之后被改过，状态需重算）")
            if probs:
                print("[FAIL] answer-status.json 自洽性校验未通过：", file=sys.stderr)
                for p in probs:
                    print(f"  - {p}", file=sys.stderr)
                print("      ★ 一个撒谎的状态文件比没有更危险 —— 不因版本低而放行",
                      file=sys.stderr)
                return EXIT_INVALID
            print(f"[OK] answer-status 自洽（state={_state}, "
                  f"pending={len(_pend)}, answer.sha256 匹配）")

    print(f"[PASS] 聚合防伪校验通过 —— {len(inputs)} 个输入席位全部为 valid")
    return EXIT_OK


# ---------------------------------------------------------------- 自检

def self_test():
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
    print("seat_check.py 自检")
    print("=" * 68)

    params = {
        "seat_contract": {
            "required_fields": ["seat", "round", "conclusion", "evidence", "falsification"],
            "warning_fields": ["confidence", "dissent", "needs_more_data", "stance"],
            "stance_exempt_seats": ["AD-01", "PM-01", "XJ-01"],
            "stance": {"primary_axes": ["ADJ-001"], "joint_axes": {"DIFFSET": ["ADJ-002", "ADJ-003"]}},
        },
        "presentation": {"max_chars_per_field": {"conclusion": 200, "evidence": 400, "dissent": 800}},
    }
    spec = {"items": [
        {"id": "ADJ-001", "options": [
            {"id": "count", "implies": {"summary.amount_diff_total": 680.02,
                                        "summary.excluded_amount_total": 0.0}},
            {"id": "exclude", "implies": {"summary.amount_diff_total": 500.02,
                                          "summary.excluded_amount_total": 180.0}}]},
        {"id": "ADJ-002", "options": [
            {"id": "as_diff", "implies": {"summary.diff_count": 7}},
            {"id": "not_diff", "implies": {"summary.diff_count": 6}}]},
        {"id": "ADJ-003", "options": [
            {"id": "as_diff", "implies": {"summary.diff_count": 7}},
            {"id": "not_diff", "implies": {"summary.diff_count": 5}}]},
    ]}
    reg = build_axis_registry(spec, params)
    expect("注册表含 primary ADJ-001", "ADJ-001" in reg["primary"])
    expect("注册表含 joint DIFFSET", "DIFFSET" in reg["joint"])
    expect("DIFFSET 观测值 = [7,6,5]", reg["joint"]["DIFFSET"]["values"] == [7, 6, 5],
           str(reg["joint"]["DIFFSET"]["values"]))

    print("\n【立场反推】")
    a_excl = {"summary": {"amount_diff_total": 500.02, "excluded_amount_total": 180.0,
                          "diff_count": 7}}
    a_count = {"summary": {"amount_diff_total": 680.02, "excluded_amount_total": 0.0,
                           "diff_count": 5}}
    d1 = derive_stance(a_excl, reg)
    expect("500.02/180.00 → ADJ-001=exclude", d1["ADJ-001"]["choice"] == "exclude", d1["ADJ-001"]["choice"])
    expect("diff_count=7 → DIFFSET=7", d1["DIFFSET"]["choice"] == "7", d1["DIFFSET"]["choice"])
    d2 = derive_stance(a_count, reg)
    expect("680.02/0.00 → ADJ-001=count", d2["ADJ-001"]["choice"] == "count", d2["ADJ-001"]["choice"])
    expect("diff_count=5 → DIFFSET=5", d2["DIFFSET"]["choice"] == "5", d2["DIFFSET"]["choice"])
    d3 = derive_stance({"summary": {"diff_count": 4}}, reg)
    expect("diff_count=4 → out_of_space", d3["DIFFSET"]["choice"] == "out_of_space", d3["DIFFSET"]["why"][:30])
    d4 = derive_stance({}, reg)
    expect("空产出 → 全 abstain",
           d4["ADJ-001"]["choice"] == "abstain" and d4["DIFFSET"]["choice"] == "abstain")

    print("\n【契约硬闸门】")
    good = {"seat": "EX-01", "round": 2, "conclusion": "c",
            "evidence": [{"pointer": "fixtures/ledger.csv#T101", "what_it_shows": "s"}],
            "falsification": "f", "confidence": "high", "dissent": [{"against": "A"}]}
    r = check_seat("EX-01", good, params, reg, "revision_round")
    expect("合规产出 → valid", r["status"] == "valid", f"errors={[e['code'] for e in r['errors']]}")

    for missing, code in (("conclusion", "E_MISSING_CONCLUSION"),
                          ("evidence", "E_MISSING_EVIDENCE"),
                          ("falsification", "E_MISSING_FALSIFICATION")):
        bad = dict(good)
        bad.pop(missing)
        r = check_seat("EX-01", bad, params, reg, "revision_round")
        expect(f"缺 {missing} → invalid({code})",
               r["status"] == "invalid" and any(e["code"] == code for e in r["errors"]),
               str([e["code"] for e in r["errors"]]))

    bad = dict(good)
    bad["evidence"] = []
    r = check_seat("EX-01", bad, params, reg, "revision_round")
    expect("空 evidence 数组 → invalid", r["status"] == "invalid",
           str([e["code"] for e in r["errors"]]))

    print("\n【自述与反推背离 —— 应是 warning 不是 error】")
    mm = dict(good)
    mm["summary"] = {"amount_diff_total": 680.02, "excluded_amount_total": 0.0, "diff_count": 7}
    mm["stance"] = [{"axis": "ADJ-001", "choice": "exclude"}]
    r = check_seat("EX-01", mm, params, reg, "revision_round")
    expect("背离 → 仍 valid", r["status"] == "valid", f"errors={[e['code'] for e in r['errors']]}")
    expect("背离 → 有 W_STANCE_MISMATCH",
           any(w["code"] == "W_STANCE_MISMATCH" for w in r["warnings"]))

    print("\n【豁免席位】")
    r = check_seat("AD-01", dict(good, seat="AD-01"), params, reg, "revision_round")
    expect("AD-01 豁免 stance", r["status"] == "valid" and not r["derived"]["derived_stance"])

    print("\n【匿名化：revision 轮不得点名同伴】")
    leak = dict(good)
    leak["dissent"] = [{"against": "EX-02", "reason": "r", "reverse_evidence": "e"}]
    r = check_seat("EX-01", leak, params, reg, "revision_round")
    expect("点名同伴 → E_PEER_IDENTIFIED",
           any(e["code"] == "E_PEER_IDENTIFIED" for e in r["errors"]),
           str([e["code"] for e in r["errors"]]))
    r2 = check_seat("EX-01", leak, params, reg, "evidence_round")
    expect("evidence 轮不检查匿名（非质询轮）", r2["status"] == "valid")

    print("\n【证据指针抽取】")
    ptrs = extract_pointers(good)
    expect("抽取 1 条指针", len(ptrs) == 1 and ptrs[0]["pointer"] == "fixtures/ledger.csv#T101")
    expect("pointer_id 为 16 位", len(ptrs[0]["pointer_id"]) == 16)
    expect("pointer_id 不含内容哈希（同指针跨轮可识别）",
           extract_pointers(good)[0]["pointer_id"] == ptrs[0]["pointer_id"])

    print("\n【params 键完整性】")
    # ★ 原实现手写了一份 full_params 夹具，结果它与 REQUIRED_PARAM_PATHS 脱节：
    #   清单加了键，夹具没跟，自检就报「完整 params 仍缺键」。手写夹具必漂。
    #   改为【从清单合成】：每个必需 path 填一个哨兵值，构造上不可能缺项。
    def _synth_from_required():
        d = {}
        for p in REQUIRED_PARAM_PATHS:
            cur = d
            parts = p.split(".")
            for k in parts[:-1]:
                cur = cur.setdefault(k, {})
            cur[parts[-1]] = 1
        return d

    expect("由必需键清单合成的 params → 无缺键（构造上不可漂）",
           lint_params(_synth_from_required()) == [])
    real = load_json(DEFAULT_PARAMS)
    if real is not None:
        miss = lint_params(real)
        expect("真实 config/params.json → 必需键齐备", miss == [], str(miss[:3]))
    expect("缺键被检出（空 params）", lint_params({}) != [])

    print("\n【F17：pointer_id 语义指纹】")
    pkg = _pkg_root()
    cand = (pkg,)
    pid = lambda s: pointer_id_of(s, pkg, cand)
    expect("路径写法归一：./fixtures/x 与 fixtures/x 同 id",
           pid("./fixtures/ledger.csv#T101") == pid("fixtures/ledger.csv#T101"))
    expect("选择子不同 → 不同 id（#T101 vs #T102）",
           pid("fixtures/ledger.csv#T101") != pid("fixtures/ledger.csv#T102"))
    expect("文件不同 → 不同 id（ledger vs bill）",
           pid("fixtures/ledger.csv#T101") != pid("fixtures/bill.csv#T101"))
    expect("多指针顺序不影响 id",
           pid("fixtures/ledger.csv#T101 + fixtures/bill.csv#T103")
           == pid("fixtures/bill.csv#T103 + fixtures/ledger.csv#T101"))
    expect("定义式随产出一起可读", POINTER_ID_DEFINITION.startswith("sha256(sorted("))

    print("\n【F18：usage 与预算】")
    expect("usage 合法", usage_errors({"tokens": 100, "minutes": 1.5}) == [])
    expect("tokens 为负 → 拒", usage_errors({"tokens": -1, "minutes": 1}) != [])
    expect("minutes 非数字 → 拒", usage_errors({"tokens": 1, "minutes": "x"}) != [])
    expect("source 非法 → 拒",
           usage_errors({"tokens": 1, "minutes": 1, "source": "wish"}) != [])
    _p = {"cost_budget": {"per_task_token_hard_cap": 1000,
                          "per_task_wall_clock_hard_cap": 30,
                          "action": "terminate_branch"}}
    _ok = evaluate_budget({"results": [{"seat": "EX-01", "derived": {
        "usage": {"tokens": 10, "minutes": 2, "source": "self_report"}}}]}, _p)
    expect("预算未超 → ok", _ok["status"] == "ok", _ok["status"])
    _over = evaluate_budget({"results": [{"seat": "EX-01", "derived": {
        "usage": {"tokens": 5000, "minutes": 2, "source": "self_report"}}}]}, _p)
    expect("预算超限 → exhausted", _over["status"] == "exhausted", _over["status"])
    _nover = evaluate_budget({"results": [{"seat": "EX-01", "derived": {}}]}, _p)
    expect("未申报 → unverifiable（不等于超支）", _nover["status"] == "unverifiable",
           _nover["status"])
    expect("预算结论自带诚实边界", "无法验证" in (_nover.get("boundary") or ""))

    print("\n【F15 配套：金额轴 × 清单轴语义一致性】")
    _adj = {"items": [
        {"id": "ADJ-001", "affected_ids": ["T104", "T105"], "options": [
            {"id": "count", "implies": {"summary.excluded_amount_total": 0.0}},
            {"id": "exclude", "implies": {"summary.excluded_amount_total": 180.0}}]},
        {"id": "ADJ-002", "options": [
            {"id": "7", "implies": {"diffs": ["T101", "T102", "T103", "T104",
                                              "T105", "T106", "T107"]}},
            {"id": "5", "implies": {"diffs": ["T101", "T102", "T103", "T106", "T107"]}}]},
    ]}

    def _conf(a, d):
        return stance_semantic_conflicts(
            _adj, {"ADJ-001": {"choice": a}, "DIFFSET": {"choice": d}})

    expect("count × 5 → 冲突（金额含、清单不含）", len(_conf("count", "5")) == 1)
    expect("count × 7 → 无冲突", _conf("count", "7") == [])
    expect("exclude × 5 → 无冲突（两边都不含）", _conf("exclude", "5") == [])
    expect("弃权 → 不判", _conf("abstain", "5") == [])


    print("\n" + "-" * 68)
    total = n_ok + len(fails)
    if fails:
        print(f"[FAIL] 自检未通过：{len(fails)}/{total} 项失败")
        for f in fails:
            print("   -", f)
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{total} 项")
    return 0


def _pkg_root():
    """定位包根（读取域路径一律以包根为坐标系）。"""
    cwd = Path.cwd()
    if (cwd / "config" / "read_domains.json").is_file():
        return cwd
    alt = cwd / "migration-package"
    if (alt / "config" / "read_domains.json").is_file():
        return alt
    return cwd


def lint_read_domains(path):
    """读取域配置自检 —— 防止「有清单但清单是空的」这种装饰性配置。"""
    rd = load_read_domains(path)
    if rd is None:
        print(f"[ERR] 读不到或不可解析 {path}", file=sys.stderr)
        return EXIT_ERROR
    problems = []
    if not rd.get("deny_absolute"):
        problems.append("deny_absolute 为空 —— 真值文件将无保护")
    if not rd.get("allow"):
        problems.append("allow 为空 —— 席位将无任何合法输入（会把一切判成未分类）")
    for must in ("fixtures/truth.json", "fixtures/spec/"):
        if not any(p.startswith(must) or must.startswith(p)
                   for p in rd.get("deny_absolute") or []):
            problems.append(f"deny_absolute 未覆盖必需项：{must}")
    known = set(DEBATING_SEATS) | set(NON_DEBATING_SEATS)
    unknown = [s for s in (rd.get("allow_extra_by_seat") or {}) if s not in known]
    if unknown:
        problems.append(f"allow_extra_by_seat 含未知席位：{sorted(unknown)}")
    if problems:
        print("[FAIL] 读取域配置不合法：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return EXIT_INVALID
    print(f"[OK] 读取域配置合法（deny_absolute {len(rd['deny_absolute'])} 项 / "
          f"allow {len(rd['allow'])} 项 / allow_exceptions "
          f"{len(rd.get('allow_exceptions') or [])} 项 / "
          f"按席位追加 {len(rd.get('allow_extra_by_seat') or {})} 席）")
    return EXIT_OK


# ---------------------------------------------------------------- 裁决模板（F20）

def _fmt_ids(ids):
    return "、".join(ids) if ids else "（空）"


# ---------------------------------------------------------------- 事实表派生（F28）
#
# ★ 为什么必须派生而不是写死：旧实现把 7 行「事实」写成字面量字典。夹具一改，
#   模板不跟着改，而**没有任何回归项能发现**。实测两处已经错：
#     T103 写 2130.00（实际 1200.00；账单侧还是两行各 1200.00，合计 2400.00，
#          旧模板写成「2130.00 ×2」= 单价错 + 用乘法号代替合计，双重错误）
#     T107 写 520.00（实际 500.00；疑为误取 raw_total_gap=520.02）
#   人在这张表上签字，就是在错数上做判断。
#
# ★ 为什么必须把「观测」与「解释」分开：旧表「性质」列写「口径差（含税/不含税）」，
#   而 ADJ-001 问的正是「口径差是否计入金额」。表在命名上已替人做完归类，
#   再宣称「这部分不需要你判断」。现在观测只给原始值，归类降格为候选解释。

REQUIRED_CSV_COLS = ("txn_id", "date", "amount", "tax_included")

FACT_COLS = ("单号", "台账金额", "台账日期", "台账行数", "台账含税",
             "账单金额", "账单日期", "账单行数", "账单含税")


def _resolve_fixtures_dir(round_dir, pkg_root=None, fixtures_dir=None):
    """定位夹具目录。显式指定时**不做 fallback** —— 用户明确指了哪里，就在那里 fail-closed。

    否则「指向空目录」这种误操作会被 cwd 兜住，变成「用了别的数据却不知道」。
    """
    if fixtures_dir:
        return Path(fixtures_dir)
    cands = []
    if round_dir:
        rd = Path(round_dir)
        cands.append(rd / "inputs")
        cands.append(rd / "fixtures")
        try:
            cands.append(rd.resolve().parent.parent / "fixtures")
        except OSError:
            pass
    if pkg_root:
        cands.append(Path(pkg_root) / "fixtures")
    cands.append(Path.cwd() / "fixtures")
    for c in cands:
        if (c / "ledger.csv").is_file() and (c / "bill.csv").is_file():
            return c
    return None


def _read_side(path):
    """读一侧 CSV → ({txn_id: [{date, amount:Decimal, tax}]}, None) 或 (None, reason)。"""
    p = Path(path)
    if not p.is_file():
        return None, f"缺少 {p.as_posix()}"
    try:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            cols = tuple(rd.fieldnames or ())
            miss = [c for c in REQUIRED_CSV_COLS if c not in cols]
            if miss:
                return None, f"{p.as_posix()} 缺列 {miss}（实际列 {list(cols)}）"
            out = {}
            for lineno, row in enumerate(rd, start=2):
                tid = (row.get("txn_id") or "").strip()
                if not tid:
                    return None, f"{p.as_posix()}:{lineno} txn_id 为空"
                raw = (row.get("amount") or "").strip()
                try:
                    amt = Decimal(raw)
                except (InvalidOperation, ValueError):
                    return None, f"{p.as_posix()}:{lineno} amount 不可解析：{raw!r}"
                out.setdefault(tid, []).append({
                    "date": (row.get("date") or "").strip(),
                    "amount": amt,
                    "tax": (row.get("tax_included") or "").strip(),
                })
    except OSError as e:
        return None, f"读取 {p.as_posix()} 失败：{e}"
    return out, None


def _side_sig(rows):
    """一侧某单号的完整签名。无行 → None（与「有行但全空」区分开）。"""
    if not rows:
        return None
    total = sum((r["amount"] for r in rows), Decimal("0"))
    return (len(rows), total,
            tuple(sorted({r["date"] for r in rows})),
            tuple(sorted({r["tax"] for r in rows})))


def _sum_amount(rows):
    if not rows:
        return None
    return sum((r["amount"] for r in rows), Decimal("0")).quantize(Decimal("0.01"))


def _fmt_cell(v):
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        # ★ 不用 format(x, ",.2f")：千分位与 locale 相关，跨平台会漂。
        return str(v)
    if isinstance(v, (list, tuple, set)):
        xs = sorted(v)
        return "、".join(str(x) for x in xs) if xs else "—"
    return str(v)


def _load_fact_schema(fdir):
    """任务级事实表列 schema（可选）。缺失 → None = 001 默认布局（回归第 33 项锚定其逐字节输出）。

    schema 文件：spec/fact-schema.json
      {"task_id": ..., "key": "txn_id", "sides": [
         {"file": "ledger.csv", "name": "自记账", "date_col": "date", "amount_col": "amount",
          "compare": [{"col": "fee_total", "label": "费用", "agg": "sum", "fmt": "money"}, ...]},
         {...}]}
    ★ 为什么必须可参数化：D-0012——derive_fact_rows 曾写死 001 的 tax_included 列，
      对 002 fail-closed 拒绝（正确行为），但 002 的事实表需要多列比对
      （股数/费用/日期），单列替换会漏掉「只差股数」的 T206。
    """
    p = Path(fdir) / "spec" / "fact-schema.json"
    if not p.is_file():
        return None
    d = load_json(p)
    if not isinstance(d, dict) or not isinstance(d.get("sides"), list) or len(d["sides"]) != 2:
        return None
    return d


def _derive_fact_rows_generic(fdir, schema):
    """按任务 schema 派生「观测不一致」行：逐列比对，任一列不同即入表。"""
    key = schema.get("key") or "txn_id"
    parsed = []
    for sd in schema["sides"]:
        p = Path(fdir) / sd["file"]
        if not p.is_file():
            return None, f"缺少 {p.as_posix()}"
        compare = sd.get("compare") or []
        try:
            with p.open("r", encoding="utf-8-sig", newline="") as f:
                rd = csv.DictReader(f)
                cols = tuple(rd.fieldnames or ())
                need = [key, sd.get("date_col") or "date", sd["amount_col"]] + \
                       [c["col"] for c in compare]
                miss = sorted({c for c in need if c not in cols})
                if miss:
                    return None, f"{p.as_posix()} 缺列 {miss}（实际列 {list(cols)}）"
                rows = []
                for row in rd:
                    tid = (row.get(key) or "").strip()
                    if not tid:
                        return None, f"{p.as_posix()} {key} 为空"
                    try:
                        amt = Decimal((row.get(sd["amount_col"]) or "").strip())
                    except (InvalidOperation, ValueError):
                        return None, f"{p.as_posix()} {key}={tid} amount 不可解析"
                    cells = {}
                    for c in compare:
                        cells[c["label"]] = (c, (row.get(c["col"]) or "").strip())
                    rows.append({"key": tid, "date": (row.get(sd.get("date_col")) or "").strip(),
                                 "amount": amt, "cells": cells})
        except OSError as e:
            return None, f"读取 {p.as_posix()} 失败：{e}"
        parsed.append({"name": sd.get("name") or sd["file"], "rows": rows})

    by_key = [{} for _ in parsed]
    for i, sd in enumerate(parsed):
        for r in sd["rows"]:
            by_key[i].setdefault(r["key"], []).append(r)

    def _agg(c, vals):
        """返回 (显示值, 签名值)。签名值必须可比较且确定。"""
        a = c.get("agg") or "set"
        if a == "sum":
            t = sum((Decimal(v) for v in vals), Decimal("0")).quantize(Decimal("0.01"))
            return (str(t) if c.get("fmt") != "int" else str(int(t)), t)
        s = tuple(sorted(vals))
        return ("、".join(s) if s else "—", s)

    all_ids = sorted(set(by_key[0]) | set(by_key[1]))
    rows, fact_cols = [], None
    for tid in all_ids:
        sides = [by_key[0].get(tid), by_key[1].get(tid)]
        sig = tuple(
            (len(s) if s else 0,) + tuple(
                _agg(c, [r["cells"][c["label"]][1] for r in (s or [])])[1]
                for c in (schema["sides"][i].get("compare") or []))
            + (tuple(sorted({r["date"] for r in s})) if s else (),)
            for i, s in enumerate(sides))
        if sig[0] == sig[1]:
            continue
        row = {"id": tid}
        display, header = [], []
        if fact_cols is None:
            header.append("单号")
        for i, s in enumerate(sides):
            nm = schema["sides"][i].get("name") or schema["sides"][i]["file"]
            if fact_cols is None:
                header += [f"{nm}金额", f"{nm}日期", f"{nm}行数"] + \
                          [f"{nm}{c['label']}" for c in (schema["sides"][i].get("compare") or [])
                           if c["label"] != "金额"]
            if s is None:
                display += ["—"] * (3 + len([c for c in (schema["sides"][i].get("compare") or [])
                                            if c["label"] != "金额"]))
                continue
            display.append(_fmt_cell(_sum_amount([
                {"amount": r["amount"]} for r in s])) or "—")
            display.append(_fmt_cell(sorted({r["date"] for r in s})))
            display.append(str(len(s)))
            for c in (schema["sides"][i].get("compare") or []):
                if c["label"] == "金额":
                    continue
                disp, _ = _agg(c, [v for r in s for _, v in [r["cells"][c["label"]]]])
                display.append(disp)
        row["cells"] = display
        if fact_cols is None:
            fact_cols = tuple(header)
        rows.append(row)
    return {
        "rows": rows,
        "ledger_rows": len(parsed[0]["rows"]),
        "bill_rows": len(parsed[1]["rows"]),
        "fact_cols": fact_cols,
        "fixtures_dir": Path(fdir).as_posix(),
    }, None


def derive_fact_rows(fixtures_dir):
    """从两侧 CSV 派生「观测不一致」的行集合。

    返回 (info, None) 或 (None, reason)。
    info = {rows:[{id, l_amount, ...}], ledger_rows, bill_rows, fixtures_dir}

    ★ 行集合用【定义式】而非写死 id：sig(ledger,txn) != sig(bill,txn)。
      写死单号正是 F28 的成因 —— 夹具一改，表就不跟着改。
    ★ 任务参数化（D-0012）：fixtures/<task>/spec/fact-schema.json 存在时走多列通用路径；
      否则保持 001 默认布局逐字节不变（回归第 33 项锚定）。
    """
    fdir = Path(fixtures_dir)
    schema = _load_fact_schema(fdir)
    if schema is not None:
        return _derive_fact_rows_generic(fdir, schema)
    ledger, why = _read_side(fdir / "ledger.csv")
    if why:
        return None, why
    bill, why = _read_side(fdir / "bill.csv")
    if why:
        return None, why

    rows = []
    for tid in sorted(set(ledger) | set(bill)):
        lr, br = ledger.get(tid), bill.get(tid)
        if _side_sig(lr) == _side_sig(br):
            continue
        rows.append({
            "id": tid,
            "l_amount": _fmt_cell(_sum_amount(lr)),
            "l_dates": _fmt_cell(sorted({r["date"] for r in lr}) if lr else None),
            "l_rows": len(lr) if lr else 0,
            "l_tax": _fmt_cell(sorted({r["tax"] for r in lr}) if lr else None),
            "b_amount": _fmt_cell(_sum_amount(br)),
            "b_dates": _fmt_cell(sorted({r["date"] for r in br}) if br else None),
            "b_rows": len(br) if br else 0,
            "b_tax": _fmt_cell(sorted({r["tax"] for r in br}) if br else None),
        })
    return {
        "rows": rows,
        "ledger_rows": sum(len(v) for v in ledger.values()),
        "bill_rows": sum(len(v) for v in bill.values()),
        "fixtures_dir": fdir.as_posix(),
    }, None


def _render_fact_table(rows, fact_cols=None):
    """渲染事实表。生成端与校验端必须共用这一个函数，否则第 39 项会假红。

    fact_cols 为 None → 001 默认表头与行结构（逐字节不变）；
    rows 元素含 "cells" → schema 驱动（D-0012），表头由 fact_cols 提供。
    """
    if rows and isinstance(rows[0], dict) and "cells" in rows[0]:
        fc = fact_cols or tuple(["单号"] + [f"列{i}" for i in range(len(rows[0]["cells"]))])
        L = ["| " + " | ".join(fc) + " |", "|" + "---|" * len(fc)]
        for r in rows:
            L.append("| " + " | ".join([r["id"]] + list(r["cells"])) + " |")
        return L
    L = ["| " + " | ".join(FACT_COLS) + " |",
         "|" + "---|" * len(FACT_COLS)]
    for r in rows:
        L.append("| " + " | ".join([
            r["id"], r["l_amount"], r["l_dates"], str(r["l_rows"]), r["l_tax"],
            r["b_amount"], r["b_dates"], str(r["b_rows"]), r["b_tax"],
        ]) + " |")
    return L


def _spec_diff_ids(adj):
    """spec 的选项覆盖到的单号并集（用于与派生集合做双向闸门）。"""
    got = set()
    for it in adj.get("items") or []:
        for o in it.get("options") or []:
            d = (o.get("implies") or {}).get("diffs")
            if isinstance(d, list):
                got |= set(d)
    return got


def _item_invariant_ids(item):
    """某判断项的「恒在 / 可争议」划分，由 options 机械求交并差。

    ★ 只有当该 item 的**全部** option 都含 implies.diffs 时才可算；否则返回 (None, None)
      —— 不猜。这句话原本被硬写在「不可争议的事实」标题下，位置错了：
      它其实是 spec 结构，不是 CSV 观测。
    """
    opts = item.get("options") or []
    if not opts:
        return None, None
    for o in opts:
        if not isinstance((o.get("implies") or {}).get("diffs"), list):
            return None, None
    sets = [set(o["implies"]["diffs"]) for o in opts]
    inter = set(sets[0])
    for s in sets[1:]:
        inter &= s
    union = set()
    for s in sets:
        union |= s
    return sorted(inter), sorted(union - inter)


def _load_interpretations(fixtures_dir):
    """候选解释清单。它是【解释】不是【事实】，也不是判据 —— 任何脚本都不得用它打分。"""
    p = Path(fixtures_dir) / "spec" / "interpretations.json"
    if not p.is_file():
        return None, (f"缺少 {p.as_posix()} —— 候选解释清单是签字材料的必读部分，"
                      f"缺失时模板不得生成（否则读者只能靠猜）")
    d = load_json(p)
    if not isinstance(d, dict):
        return None, f"{p.as_posix()} 不可解析或为空"
    items = d.get("items")
    if not isinstance(items, list) or not items:
        return None, f"{p.as_posix()} 的 items 为空"
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            return None, f"{p.as_posix()} items[{i}] 不是对象"
        for k in ("key", "name", "basis", "applies_to", "caveat"):
            if k not in it:
                return None, f"{p.as_posix()} items[{i}] 缺字段 {k}"
        if not isinstance(it["applies_to"], list):
            return None, f"{p.as_posix()} items[{i}].applies_to 必须是数组"
    return items, None


def render_record_template(adj, report, round_dir, spec_sha, pkg_root=None, fixtures_dir=None):
    """生成「人类裁决记录」的填写模板。

    ★ 为什么由脚本生成而不是手写：手写模板必然随 spec 漂移 ——
      round1 那份模板至今还写着已退役的 ADJ-003，就是活证据。
    ★ 为什么必须【无时间戳】：模板要能被逐字节比对（回归第 33 项），
      一旦含时间戳，「模板是否漂移」就永远判不出来。
    ★ 为什么只是模板而不是记录：记录必须由人签。生成器产出 `source` 字段的
      占位说明，而不是替人填 `human_adjudication`。
    ★ 返回 (txt, why)：why 非空时必须【拒绝写出文件】。输出一张「看起来权威
      但数字可能错」的事实表，比不输出危险一个量级 —— 签字人不会去核 CSV，
      他签的正是「这部分我不需要判断」。
    """
    round_name = Path(round_dir).name
    # ★ rdir 必须是【包内相对路径】：模板要告诉人「另存到哪」，而人在包里看
    #   的相对路径是 runs/round3/…。若照抄命令行传入的 round_dir，从包外调用时
    #   会写成 migration-package/runs/round3/…，人按它找会找错地方，
    #   且同一轮从不同 cwd 生成会得到不同模板（不可复现）。
    _rp = Path(round_dir)
    if pkg_root:
        try:
            _rp = _rp.resolve().relative_to(Path(pkg_root).resolve())
        except (ValueError, OSError):
            pass
    rdir = _rp.as_posix().lstrip("./") or round_name
    answer = load_json(Path(round_dir) / "answer-final.json") or {}
    actual_cnt = resolve(answer, "summary.diff_count")
    has_cnt = actual_cnt is not _MISSING and actual_cnt is not None

    # ---- F28：事实表必须从 CSV 派生，且与 spec 双向闸门 ----
    fdir = _resolve_fixtures_dir(round_dir, pkg_root, fixtures_dir)
    if fdir is None:
        return None, ("找不到同时含 ledger.csv 与 bill.csv 的夹具目录"
                      "（已尝试 round_dir/inputs、round_dir/fixtures、包根 fixtures、cwd/fixtures）")
    # ★ 任务参数化：模板第 6 节的 verify 命令必须指向【本轮任务】的 spec 目录，
    #   否则签字人会拿 002 的答案去跑 001 的判据（静默错位，校验不会报错）。
    try:
        _specdir = (fdir.resolve().relative_to(Path(pkg_root).resolve()).as_posix()
                    if pkg_root else fdir.as_posix()) + "/spec"
    except Exception:
        _specdir = fdir.as_posix() + "/spec"
    derived, why = derive_fact_rows(fdir)
    if why:
        return None, f"事实表派生失败：{why}"
    interp, why = _load_interpretations(fdir)
    if why:
        return None, why

    fact_rows = derived["rows"]
    got_ids = {r["id"] for r in fact_rows}
    if not fact_rows:
        return None, ("事实表为空：两侧在「行数/金额合计/日期集合/含税标记」上完全一致，"
                      "与「存在待裁判断」自相矛盾")
    spec_ids = _spec_diff_ids(adj)
    if spec_ids and spec_ids != got_ids:
        return None, (f"数据与 spec 不一致：CSV 派生 {sorted(got_ids)}，"
                      f"spec 选项覆盖 {sorted(spec_ids)} —— 二者必须相同，否则"
                      f"人会在「选项与事实不对应」的情况下签字")
    applies = set()
    for it in interp:
        applies |= set(it["applies_to"])
    if applies != got_ids:
        return None, (f"interpretations.json 的 applies_to 并集 {sorted(applies)} "
                      f"与派生的不一致单号 {sorted(got_ids)} 不一致")

    L = [
        f"# 人类裁决记录 · {round_name}",
        "",
        "> 本文件由 `judges/seat_check.py --emit-record-template` 生成，是**模板**，不是裁决。",
        "> 裁决必须由人填写：另存为 `adjudication-record.json`，逐条写下你的选择与理由。",
        "",
        "## 0 · 签字前必须知道的三件事",
        "",
        "1. **签的是什么**：签的是「我承担这个归类判断」，不是「我确认机器算对了」。",
        "   判断项没有机器真值 —— 这正是判据分层的意义。",
        "2. **选哪个都不会被系统评判为「错」**，但会决定本轮产物是否与你的判断一致。",
    ]
    if has_cnt:
        L.append(f"   本轮机器产出是 `diff_count = {actual_cnt}`。若你选的选项不是这个数，"
                 f"验证会判 **fail** ——")
        L.append("   那是**正确结果**（说明机器产出与你的判断不一致，应由 PM 记录并重做聚合），"
                 "不是你的判断错了。")
    else:
        L.append("   若你的选项与本轮机器产出不一致，验证会判 fail —— 那是正确结果，"
                 "说明产出需重做。")
    L += [
        "3. **防伪边界（不可删）**：校验器只检查 `source` / `recorded_by` / `decisions` 的**声明字段**，",
        "   **无法证明**本文件确实由人而非模型写成。真正的保证是程序性的：",
        "   它在你的机器流程之外填好、放进路径，工具链**不自动生成它**。",
        "",
        f"- spec sha256：`{spec_sha[:16]}…`",
        f"- 判断项数量：**{len(adj.get('items') or [])}**",
        "",
        "## 1 · 原始观测（脚本从两份 CSV 派生，这一节不需要你判断真伪）",
        "",
        "> **本表的定义式**：列出 ledger 与 bill 在「行数 / 金额合计 / 日期集合 / 含税标记」"
        "四项上不完全相同的**全部**单号。",
        "> 金额一律为 CSV 原值合计，**未做任何含税换算**；「行数」为数据行数（不含表头）；"
        "`—` 表示该侧没有这个单号。",
        "",
    ]
    L += _render_fact_table(fact_rows, derived.get("fact_cols"))
    L += [
        "",
        f"**来源**：本轮夹具目录下的 `ledger.csv`（{derived['ledger_rows']} 数据行）"
        f"与 `bill.csv`（{derived['bill_rows']} 数据行）；不一致单号 **{len(fact_rows)}** 个。",
        "",
        "★ **若你发现表中任一行与 CSV 原值不符，请立即停止签字并报告** —— "
        "那说明模板生成器有缺陷，你在错数上做的任何判断都不作数。",
        "",
        "## 1.2 · 可能的解释（这是解释，不是事实）",
        "",
        "> 下列各条是**候选口径**，只用来提示你可以从哪些方向解释 §1 的原始落差。",
        "> 它们**不是结论**，**不是**「系统已认定」，也**不是**你必须接受的分类。",
        "> 是否成立、是否计入差异，**由你在第 2、3 节自行判断**；"
        "若你不采纳某条，请在 rationale 里说明理由。",
        "",
        "| 候选解释 | 它依据哪些原始观测 | 可能适用 | 这条解释可能不成立的地方 |",
        "|---|---|---|---|",
    ]
    for it in interp:
        L.append(f"| {it['name']} | {it['basis']} | "
                 f"{_fmt_ids(it['applies_to'])} | {it['caveat']} |")
    L += [
        "",
        "★ 这五条**不是**「五类差异」。把观测归成几类、每类算不算差异，本来就是你要判断的事。",
        "",
    ]

    # ---- §1.3 审计覆盖声明（第 1 查的时序问题）----
    #    ★ 只依据【文件内容】判断，不依据 mtime：mtime 在 cp -r / git clone /
    #      网盘中转后会被重置，跨机不可用，而转移包正是跨机场景。
    _ad = load_json(Path(round_dir) / "AD-01.json") or {}
    _aud = _ad.get("audited_inputs")
    L += ["## 1.3 · 本轮结构审计的覆盖范围", ""]
    if isinstance(_aud, list) and _aud:
        L += ["本轮结构审计已锚定下列裁判侧产物（字段逐一与文件相同，"
              "可据此确认它是在裁决**之后**才写的结论）：", ""]
        for x in _aud:
            if isinstance(x, dict):
                L.append(f"- `{x.get('path', '?')}`：verdict=`{x.get('verdict', '?')}`、"
                         f"spec_layer=`{x.get('spec_layer', '?')}`、"
                         f"ts=`{x.get('ts', '?')}`")
        L.append("")
    else:
        L += [
            "**本轮结构审计未锚定裁判侧产物**（其产出无 `audited_inputs` 字段）"
            "→ 其**第 1 / 5 / 7 查不得作为签字依据**。",
            "",
            "原因：它在裁决侧产物落盘之前就写了结论，无法证明它审计的是「已裁决」的本轮。"
            "这不是它的过错 —— 契约当时尚未要求该字段 —— 但它使那三查的证据力不足。",
            "",
        ]

    for idx, it in enumerate(adj.get("items") or [], 1):
        L += [
            f"## {idx + 1} · {it['id']}",
            "",
            f"**问题**：{it.get('question', '')}",
            "",
            f"**受影响的字段**：{'、'.join(f'`{p}`' for p in it.get('paths_affected') or [])}",
            "",
        ]
        if it.get("why_it_matters"):
            L += [f"**为什么重要**：{it['why_it_matters']}", ""]
        if it.get("risk_asymmetry"):
            L += [f"**风险不对称**：{it['risk_asymmetry']}", ""]
        L += ["| 选项 | 含义 | 对应的值 |", "|---|---|---|"]
        for o in it.get("options") or []:
            imp = o.get("implies") or {}
            vals = []
            for k, v in imp.items():
                vals.append(f"`{k.split('.')[-1]}={v}`" if not isinstance(v, list)
                            else f"`{k.split('.')[-1]}`：{_fmt_ids(v)}")
            L.append(f"| `{o['id']}` | {o.get('label', '')} | {'；'.join(vals)} |")
        L.append("")

        # ADJ-002 的 2×2 矩阵：把「两个判断点」摊开，避免人选的时候想不清楚
        dec = [o for o in (it.get("options") or []) if isinstance(o.get("decides"), dict)]
        if len(dec) == 4:
            L += [
                "**两个判断点 × 四个组合**：",
                "",
                "| 跨月 T106 | 口径差 T104/T105 | 选项 | 清单条数 |",
                "|---|---|---|---|",
            ]
            for o in dec:
                d = o["decides"]
                L.append(f"| {'列入' if d.get('cross_month') else '**不列入**'} "
                         f"| {'列入' if d.get('caliber') else '**不列入**'} "
                         f"| `{o['id']}` | {o['implies'].get('summary.diff_count')} |")
            L += ["", "★ 四个选项是**并列**的，没有哪个是「默认正确答案」。", ""]

        # ★ 「恒在 / 可争议」由 spec 的 options 机械求交并差，不手写。
        #   这句话原本被硬写在「不可争议的事实」标题下 —— 位置错了：它是 spec 结构，
        #   不是 CSV 观测；而且手写会随 spec 漂移（与 F28 同因）。
        inter, disputed = _item_invariant_ids(it)
        if inter is not None:
            n_opt = len(it.get("options") or [])
            L += [
                f"**由本项的 {n_opt} 个选项机械推导**（这是 spec 结构，不是观测，也不是结论）：",
                "",
                f"- {_fmt_ids(inter)} 出现在**全部 {n_opt} 个**选项中；",
                f"- {_fmt_ids(disputed)} 只在**部分**选项中出现 —— "
                f"后者正是本项要你判断的内容，不是既成事实。",
                "",
            ]

    L += ["## 4 · 本轮各席位的实际取值（供参考，不是你的答案）", "",
          "| 席位 | 自述 | 反推 | 合规 |", "|---|---|---|---|"]
    for seat in list(DEBATING_SEATS) + list(NON_DEBATING_SEATS):
        r = next((x for x in report.get("results") or [] if x.get("seat") == seat), None)
        if not r:
            continue
        d = r.get("derived") or {}
        dec_st = d.get("declared_stance") or {}
        der_st = d.get("derived_stance") or {}

        def fmt(st):
            """两种形状都要吃：declared 是 axis→'choice' 字符串，
            derived 是 axis→{'choice': …, 'why': …} 对象。"""
            if not isinstance(st, dict) or not st:
                return "—"
            parts = []
            for k, v in sorted(st.items()):
                parts.append(f"{k}={v.get('choice') if isinstance(v, dict) else v}")
            return "、".join(parts)

        L.append(f"| {seat} | {fmt(dec_st)} | {fmt(der_st)} | {r.get('status')} |")
    L += [
        "",
        "★ 多数席位的一致性**不是**裁决依据。判断项之所以交给人，正是因为"
        "「全体一致」也可能只是共享同一个盲区（见 `L-0002`、`D-0004`）。",
        "",
        "## 5 · 填写格式",
        "",
        f"另存为 `{rdir}/adjudication-record.json`：",
        "",
        "```json",
        "{",
        '  "task_id": "' + str(adj.get("task_id", "")) + '",',
        '  "source": "human_adjudication",',
        '  "recorded_by": "<你的署名>",',
        '  "recorded_at": "<ISO8601，例如 2026-10-20T10:00:00+08:00>",',
        '  "decisions": [',
    ]
    for i, it in enumerate(adj.get("items") or []):
        opts = " / ".join(o["id"] for o in it.get("options") or [])
        comma = "," if i < len(adj.get("items") or []) - 1 else ""
        L.append(f'    {{"id": "{it["id"]}", "chosen": "<{opts}>", '
                 f'"rationale": "<你的理由>"}}{comma}')
    L += [
        "  ]",
        "}",
        "```",
        "",
        "★ `decisions` 必须**恰好**覆盖上表的全部 id。缺项、或出现 spec 之外的 id"
        "（如已退役的 ADJ-003），都会被拒载（退出码 2）。",
        "",
        "## 6 · 签完后跑这三条", "",
        "```bash",
        "# ① 重新生成待裁决清单（未签时应为 pending，退出码 3）",
        f"python3 judges/verify.py --spec-dir {_specdir} \\",
        f"  --answer {rdir}/answer-final.json --out {rdir}/pending-adjudication.json",
        "",
        "# ② 验证你的裁决（期望 verdict=adjudicated，退出码 0）",
        f"python3 judges/verify.py --spec-dir {_specdir} \\",
        f"  --record {rdir}/adjudication-record.json \\",
        f"  --answer {rdir}/answer-final.json --out {rdir}/adjudicated.json",
        "",
        "# ③ 若 spec 改过，重新生成本模板并与已提交版比对（应逐字节相同）",
        "python3 judges/seat_check.py \\",
        f"  --round-dir {rdir} --emit-record-template /tmp/{round_name}-template.md",
        "```",
        "",
        "## 7 · 诚实边界（不可删）",
        "",
        "- 本模板**不会**告诉你哪个选项会通过。它只列出选项、事实与后果。",
        "- **不得**把「生成本模板」写进任何自动流程后再自动填值 —— 那会让闸门失效，"
        "而校验不会报警。",
        "- 模板可脚本生成；**记录不可**。",
        "",
    ]
    return "\n".join(L), None


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="席位输出契约校验器 + 聚合防伪闸门")
    ap.add_argument("--round-dir", "--round", dest="round_dir", help="轮次目录（如 runs/round2）")
    ap.add_argument("--params", default=DEFAULT_PARAMS)
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--routing-json", default=None, metavar="PATH",
                    help="inspect_agent_routing.py --json 的输出文件。提供时对 XJ-01 做"
                         "家族核验（providerData，不采信自报）→ seat-check.json 的 xj_family_check")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 <round-dir>/seat-check.json）")
    ap.add_argument("--read-domains", default="config/read_domains.json",
                    help="读取域配置（F5）")
    ap.add_argument("--rd-out", default=None,
                    help="第 9 查产出路径（默认 <round-dir>/read-domain-check.json）")
    ap.add_argument("--stage", choices=("all", "A", "B"), default="all",
                    help="F10 分阶段：A=EX+XJ / B=AD+PM / all=并集（缺省，保持既有行为）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict-warnings", action="store_true")
    ap.add_argument("--lint-params", action="store_true")
    ap.add_argument("--lint-read-domains", action="store_true")
    ap.add_argument("--verify-final", action="store_true", help="聚合防伪校验 + PM 产物验收门")
    ap.add_argument("--emit-record-template", default=None, metavar="PATH",
                    help="生成人类裁决记录模板（无时间戳，可逐字节比对）；不写记录本身")
    ap.add_argument("--fixtures-dir", default=None, metavar="DIR",
                    help="事实表的数据来源目录（须含 ledger.csv + bill.csv）。"
                         "缺省时按 round_dir 相邻 → 包根 → cwd 依次探测；"
                         "显式指定则【不做 fallback】，指向错误目录会 fail-closed")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    params = load_json(args.params)
    if params is None:
        print(f"[ERR] 读不到 {args.params}", file=sys.stderr)
        return EXIT_ERROR
    missing = lint_params(params)
    if missing:
        print(f"[ERR] params 缺必需键：{missing}", file=sys.stderr)
        print("     静默默认会重建「有阈值没定义」的原缺陷，故硬失败。", file=sys.stderr)
        return EXIT_ERROR

    if args.lint_params:
        print(f"[OK] params 键完整性校验通过（{len(REQUIRED_PARAM_PATHS)} 项必需键齐备）")
        return EXIT_OK

    if args.lint_read_domains:
        return lint_read_domains(args.read_domains)

    if not args.round_dir:
        ap.print_help()
        return EXIT_ERROR

    if args.verify_final:
        return verify_final(args.round_dir, params)

    adj = load_json(args.spec)
    if adj is None:
        print(f"[ERR] 读不到 {args.spec}", file=sys.stderr)
        return EXIT_ERROR

    pkg = _pkg_root()
    rd = load_read_domains(args.read_domains, Path(args.round_dir).name)
    if rd is None:
        print(f"[WARN] 读不到读取域配置 {args.read_domains} → 第 9 查不做判定",
              file=sys.stderr)
    # 多根探测：实测 pointer 混三套坐标系 —— 轮次目录（inputs/…）、
    # 包根（fixtures/…）、工作区根（migration-package/judges/…）。
    # 缺任何一根都会把合法路径误判为 unclassified 的假阳性。
    roots = tuple(dict.fromkeys(
        p for p in (Path(args.round_dir), pkg, pkg.parent, Path.cwd()) if p))

    report = check_round(args.round_dir, params, adj, args.strict_warnings,
                         stage=args.stage, rd=rd, pkg_root=pkg, roots=roots,
                         routing=(_load_routing(args.routing_json)
                                  if args.routing_json else None))
    report["params_sha256"] = sha256_of(args.params)
    report["spec_sha256"] = sha256_of(args.spec)

    if args.emit_record_template:
        txt, why = render_record_template(adj, report, args.round_dir,
                                          sha256_of(args.spec) or "",
                                          pkg_root=pkg, fixtures_dir=args.fixtures_dir)
        if why:
            # ★ fail-closed：不写文件。宁可让流程停下，也不让人在错数上签字。
            print(f"[ERR] 拒绝生成裁决模板：{why}", file=sys.stderr)
            return EXIT_ERROR
        outp = Path(args.emit_record_template)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with open(outp, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        print(f"[OK] 裁决模板已生成：{outp}")
        print(f"     判断项 {len(adj.get('items') or [])} 条；模板不含时间戳，可逐字节比对。")
        print("     ★ 这是模板，不是裁决。记录必须由人填写。")
        return EXIT_OK

    rdr = None
    if rd is not None:
        rdr = build_read_domain_report(args.round_dir, report, rd, args.read_domains)
        report["read_domain_verdict"] = rdr["verdict"]

    out = Path(args.out) if args.out else Path(args.round_dir) / "seat-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    rd_out = None
    if rdr is not None:
        rd_out = Path(args.rd_out) if args.rd_out else Path(args.round_dir) / "read-domain-check.json"
        rd_out.parent.mkdir(parents=True, exist_ok=True)
        with open(rd_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rdr, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[seat_check] {report['round_dir']}  role={report['round_role']}"
              f"  stage={report['stage']}  contract=v{report['contract_version']}"
              f"（reads {'硬' if report['reads_required'] else '软'}）")
        print(f"  verdict={report['verdict']}  合规 {report['summary']['valid']}"
              f" / 不合格 {report['summary']['invalid']}  (共 {report['summary']['total']})")
        for r in report["results"]:
            mark = "OK   " if r["status"] == "valid" else "FAIL "
            print(f"  [{mark}] {r['seat']}  证据指针 {r['derived'].get('evidence_pointer_count', 0)} 条"
                  f"  警告 {len(r['warnings'])} 条")
            for e in r["errors"]:
                print(f"           ✗ {e['code']}: {e['msg']}")
            for w in r["warnings"]:
                print(f"           ! {w['code']}: {w['msg']}")
        print(f"  可聚合: {report['aggregation_admissible']}")
        if report["aggregation_blocked"]:
            print(f"  ★ 被否决: {[b['seat'] for b in report['aggregation_blocked']]}")
        if rdr is not None:
            print(f"  第 9 查（读取域）: verdict={rdr['verdict']}  命中 {rdr['summary']['hits']} 条"
                  f"  申报读写 {rdr['summary']['seats_declaring_reads']} 席  -> {rd_out}")
        print(f"  -> {out}")

    if report["verdict"] == "no_seats_found":
        print("[FAIL] 该轮目录内找不到任何席位产出 —— 没东西可校验 ≠ 通过", file=sys.stderr)
        return EXIT_EMPTY
    return EXIT_OK if report["verdict"] == "all_valid" else EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
