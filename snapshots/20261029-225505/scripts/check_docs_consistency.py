#!/usr/bin/env python3
"""check_docs_consistency.py —— 文档与磁盘的一致性校验（只读）。

## 为什么存在

2026-10-20 审计发现 14 处错误数字 + 1 处未验证断言，根因是**数字的"来源"与"所在地"分离**：
  · 「132 次读数」是 `33×4` 的乘法，从未真数过；
  · 「44 种写法」是类别对齐**前**的统计，对齐后仍是旧值；
  · 「24/26 严格满足」是对齐前数字，却被用来论证对齐后的状态。

同类问题在修复过程中又复发 4 次（§八 索引的行数值没跟上被引用文件的行数变化）。
故本脚本把两条教训落成机制：

  1. 文档里的关键统计量，必须能用命令复现 → 本脚本复现它们；
  2. 交叉引用（行数/路径/计数）必须在最后一次改动后统一复核 → 本脚本做这件事。

## 用法

    python3 scripts/check_docs_consistency.py            # 人在环，先看输出
    python3 scripts/check_docs_consistency.py --strict  # CI 用，不一致即 exit 1

## 检查项

  A. MANIFEST 字节数：文档里出现的 `total_bytes`（形如 7,883,426）是否等于 `MANIFEST.json#stats`
     ⚠️ `file_count` **不在本项校验范围内**：下面的正则只匹配 7 位带千分位的字节数，
        4 位文件数（如 1099）永远命不中。原文写作「`file_count` / `total_bytes`」，
        但 file_count 从未实现校验 —— 2026-10-23 审计发现，此处如实收窄声明，
        不假装已覆盖。要补需另写一条只匹配文件数的规则。
  B. 索引行数：`docs/*.md` 中被 `| NNN |` 引用的行数是否等于该文件实际行数
  C. 契约版本：文档里的 `vX.Y` 声明是否与 `config/params.json` 一致
  D. 计数声明：**当前状态文档**（README / ARCHITECTURE / DEPENDENCIES）里的
     「N 条经验 / N 份决策 / N 轮运行 / N 份协议」是否等于磁盘实际条数。
     `docs/*.md` **不在**本项范围：那是历史审计记录，写的是当时的事实，
     拿今天的磁盘去判它错，会把"历史正确"报成红，反而掩盖真问题。
  E. 配对计数：同一行里的 `N/N`（如「全量回归 56/56」「门禁 8/8」「判据自检 27/27」
     「接手自检 6/6」）是否等于**本次实跑**的项数。实测值由调用方用 `--expect NAME=N`
     传入（pre_release_check 会传 regress / gates / criteria / handover 四个）——
     不传则本项跳过：宁可少查，也不自己猜一个数来比。
     `handover` 的项数由 `verify_handover.py --count` 自报（只读 CHECKS 长度），
     不重跑回归 —— 2026-10-23 曾把它列为"已知未覆盖"，理由是重跑会让门禁时长翻倍；
     实际不需要重跑：项数是脚本自己的常量，让它说话即可。

退出码：0 全部一致 / 1 存在不一致（仅 --strict 时）/ 2 环境错误
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ai-autonomy-team/
DOCS = ROOT / "docs"
PKG = ROOT / "migration-package"

OK, BAD, ERR = 0, 1, 2

# D 项扫描范围：只扫「当前状态」类文档。docs/*.md 是历史审计，不参与（见文件头）。
STATUS_DOCS = ["README.md", "ARCHITECTURE.md", "DEPENDENCIES.md"]

# D 项规则：正则 → (人类可读标签, 计数函数)。正则刻意写得窄（"条经验"而不是"条"），
# 宽匹配会把「3 条铁律」这类无关说法一起抓进来。
COUNT_RULES = [
    (re.compile(r"(\d+)\s*条经验"), "经验条数",
     lambda: len(list((ROOT / ".learnings").glob("*.md")))),
    (re.compile(r"(\d+)\s*份决策"), "决策份数",
     lambda: len(list((DOCS / "decisions").glob("D-*.md")))),
    (re.compile(r"(\d+)\s*轮运行"), "运行轮数",
     lambda: len([d for d in (PKG / "runs").glob("round*") if d.is_dir()])),
    (re.compile(r"(\d+)\s*份协议"), "协议份数",
     lambda: len(list((DOCS / "protocol").glob("*.md")))),
]

# E 项：行内出现这些词，才认为同行的 `N/N` 是在说「项数」
PAIR_MARKERS = [
    (re.compile(r"回归|regress"), "regress"),
    (re.compile(r"门禁|pre_release_check"), "gates"),
    (re.compile(r"判据自检|octagon"), "criteria"),
    # 放最后：含「接手」的行若同时提到门禁，按更具体的那个算
    (re.compile(r"接手自检|handover"), "handover"),
]

# G 项豁免判据（提为模块常量：自检要能「写坏它」证明豁免真的在起作用）
HIST_RE = re.compile(r"原始|原为|审计时|对齐前|此前|当时|→")
STAMP_RE = re.compile(r"\d{2}-\d{2}[ T]\d{2}:\d{2}")     # 时点标注：10-29 22:36

# G 项规则：交接摘要正文不得出现「裸易变计数」（判据与豁免见 check_forbidden_counts）
FORBIDDEN_PATTERNS = [
    (re.compile(r"kit 自测\s*\|?\s*\**\s*\d+\s*/\s*\d+"), "kit 自测配对计数"),
    (re.compile(r"kit 自测 \d+ 项"), "kit 自测单项计数"),
    (re.compile(r"\d+ bundle \+ \d+ tar"), "备份产物计数"),
    (re.compile(r"\d+\+? 个历史快照"), "_trash 快照计数"),
    (re.compile(r"唯一副本\s*\|\s*\**\s*\d+\+?\s*目录"), "_trash 目录计数"),
    (re.compile(r"约 \d+ 项"), "聚合总数声明"),
]


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def fmt(n):
    """1234567 -> '1,234,567'"""
    return f"{n:,}"


def _near_manifest(line, hit):
    """数字是否在「包体声明」的近旁。

    只看同行会把「MANIFEST … 字节；token 总量 54,730,833」里的 token 数误判成包体，
    故用不对称窗口：前面 10 字符找「字节/包体/MANIFEST」，后面 6 字符找「字节 / B」。
    """
    i = line.find(hit)
    if i < 0:
        return False
    head = line[max(0, i - 6):i]
    tail = line[i + len(hit):i + len(hit) + 6]
    return ("字节" in head or "包体" in head or "MANIFEST" in head
            or bool(re.match(r"\s*(?:字节|B\b)", tail)))


def check_manifest_numbers(problems):
    """A. 文档中的 MANIFEST 数字 vs 实际 stats。"""
    m = load_json(PKG / "MANIFEST.json")
    if not m or "stats" not in m:
        problems.append(("A", "MANIFEST.json 缺失或结构异常", "无法校验"))
        return 0
    st = m["stats"]
    tb = st["total_bytes"]
    checked = 0
    # ★ 2026-10-23 修（代码审查 C-2）：原正则写死 `7,\d{3},\d{3}`，包体一旦过 8 MB
    #   就一个也匹配不到 —— 校验点数掉到 0 却仍然 PASS（实测注入 8,123,456 未被看见）。
    #   改为「任意千分位大数」，再用同行上下文（字节/MANIFEST）限定为包体声明；
    #   否则会把 token 总量（如 54,730,833）误当成 MANIFEST。
    num_re = re.compile(r"\b\d{1,3}(?:,\d{3}){2,}\b")
    for p in sorted(DOCS.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        for hit in set(num_re.findall(text)):
            line = next((ln for ln in text.splitlines() if hit in ln), "")
            if not _near_manifest(line, hit):
                continue
            val = int(hit.replace(",", ""))
            checked += 1
            if val != tb:
                # 允许历史值出现，但要求同行带"原始为/前"等限定词
                # 历史标记：与 sync_doc_numbers.HIST_MARKERS 保持一致。
                # 旧规则含 `为 \d`，会把「| 当前值 | ... 原始为 X |」这类行整体跳过，
                # 而该行前半部分正是需要校验的当前值 —— 属漏检缺陷（2026-10-21 修）。
                if not re.search(r"原始|原为|审计时|对齐前|此前|当时|上次|上轮|前值|→", line):
                    problems.append(
                        ("A", f"{p.name}: 出现 {hit}", f"MANIFEST 实际 {fmt(tb)}（该行无限定词）"))
    return checked


def check_index_line_counts(problems):
    """B. 索引表的行数声明 vs 实际文件行数。

    匹配形如： | [`文件.md`](./文件.md) | 347 | 描述 |
    行数在**链接之后**，不是之前——初版正则写反了，导致校验 0 处（自测时发现）。
    """
    checked = 0
    idx_re = re.compile(r"\[`?([^`\]]+\.md)`?\]\([^)]*\)\s*\|\s*(\d{1,4})\s*\|")
    for p in sorted(DOCS.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        for name, declared in idx_re.findall(text):
            target = DOCS / name.strip()
            if not target.exists():
                problems.append(("B", f"{p.name} 引用了不存在的 {name.strip()}", "文件缺失"))
                continue
            actual = len(target.read_text(encoding="utf-8").splitlines())
            checked += 1
            if int(declared) != actual:
                problems.append(
                    ("B", f"{p.name}: {name.strip()} 声明 {declared} 行",
                     f"实际 {actual} 行"))

    # 代码行数表：状态文档里的 `| `x.py` | NNN |`（没有 markdown 链接，写法不同、性质相同）。
    # 2026-10-23 实测 ARCHITECTURE 的这张表 28 行里 16 行与实际不符，最大差 164 行。
    code_re = re.compile(r"^\|\s*`([\w./]+\.py)`[^|]*\|\s*(\d+)\s*\|")
    for name in STATUS_DOCS:
        doc = ROOT / name
        if not doc.exists():
            continue
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            m = code_re.match(line)
            if not m:
                continue
            target = next((b / m.group(1) for b in (ROOT / "scripts", ROOT, ROOT / "migration-package")
                           if (b / m.group(1)).is_file()), None)
            if target is None:
                problems.append(("B", f"{name}:{lineno} 引用了不存在的 {m.group(1)}", "文件缺失"))
                continue
            actual = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            checked += 1
            if int(m.group(2)) != actual:
                problems.append(
                    ("B", f"{name}:{lineno} {m.group(1)} 声明 {m.group(2)} 行",
                     f"实际 {actual} 行"))
    return checked


def check_contract_version(problems):
    """C. 文档声明的契约版本 vs params.json。"""
    cfg = load_json(PKG / "config" / "params.json") or {}
    ver = str((cfg.get("seat_contract") or {}).get("contract_version") or "?")
    checked = 0
    pat = re.compile(r"契约 v(\d+\.\d+)")
    for p in sorted(DOCS.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        for hit in set(pat.findall(text)):
            checked += 1
            if hit != ver:
                problems.append(("C", f"{p.name}: 声明 v{hit}", f"params.json 为 v{ver}"))
    return checked


def check_count_claims(problems):
    """D. 当前状态文档里的计数声明 vs 磁盘实际。"""
    checked = 0
    for name in STATUS_DOCS:
        p = ROOT / name
        if not p.exists():
            problems.append(("D", f"{name} 缺失", "状态文档不存在"))
            continue
        text = p.read_text(encoding="utf-8")
        for rx, label, counter in COUNT_RULES:
            hits = set(rx.findall(text))
            if not hits:
                continue
            actual = counter()
            for declared in hits:
                checked += 1
                if int(declared) == actual:
                    continue
                # 同行带历史限定词 = 当时事实，跳过（与 A 项同一口径）
                line = next((ln for ln in text.splitlines() if declared in ln), "")
                if re.search(r"原始|原为|审计时|对齐前|此前|当时", line):
                    continue
                problems.append(
                    ("D", f"{name}: {label}声明 {declared}", f"磁盘实际 {actual}"))
    return checked


def check_paired_counts(problems, expects):
    """E. 同一行 `N/N` 的声明 vs 本次实跑的项数。

    只认「形如 56/56、8/8、27/27 的同一个数出现两次」——
    退出码说明里的 `0/1/2`、`0/1/2/3/4` 天然不匹配（分子分母不同），不会被误抓。
    行内还需命中某个工具标记（回归 / 门禁 / 判据自检），否则「1/1」这类
    与项数无关的比值会被误判。
    """
    if not expects:
        return 0
    checked = 0
    pair_re = re.compile(r"(\d+)/\1")
    for name in STATUS_DOCS:
        p = ROOT / name
        if not p.exists():
            continue
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            nums = pair_re.findall(line)
            if not nums:
                continue
            key = next((k for rx, k in PAIR_MARKERS if rx.search(line)), None)
            if key is None or key not in expects:
                continue
            if re.search(r"原始|原为|审计时|对齐前|此前|当时", line):
                continue
            for n in nums:
                checked += 1
                if int(n) != expects[key]:
                    problems.append(
                        ("E", f"{name}:{lineno} 声明 {n}/{n}（{key}）",
                         f"本次实跑 {expects[key]}/{expects[key]}"))
    return checked


def check_generated_blocks(problems):
    """F. 生成块：文档里 <!-- BEGIN:handover-facts -->…END 块必须与生成器输出一致。

    ★ 2026-10-29（审计 P1）：交接摘要手写「kit 64/64」25 分钟就过期（R3 根因复发）——
    易变数字一律由 scripts/handover_facts.py 生成，本检查钉住「块 == 重新生成」，
    过期即红并给出一条命令的修法。只扫 docs/交接摘要-*.md，没有块的文档不强制。
    """
    try:
        import handover_facts as hf
    except ImportError:
        return 0
    n = 0
    for doc in sorted(DOCS.glob("交接摘要-*.md")):
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(re.escape(hf.BEGIN) + r"\n(.*?)" + re.escape(hf.END) + r"\n", text, re.S)
        if not m:
            continue
        n += 1
        try:
            want = hf.render_inner(hf.collect())
        except Exception as e:      # 真源采集失败也必须报，不能当一致放过
            problems.append(("F", f"{doc.name} 生成块", f"真源采集异常：{type(e).__name__}: {e}"))
            continue
        if m.group(1) != want:
            problems.append(("F", f"{doc.name} 生成块过期",
                             "python3 scripts/handover_facts.py --emit docs/交接摘要-20261029.md"))
    return n


def check_forbidden_counts(problems, docs=None):
    """G. 正文易变计数禁区：交接摘要正文不得出现「裸计数」。

    ★ 2026-10-29 审计 P0-1 的根治。背景：交接摘要正文写死「kit 自测 75/75」，
      实际已 83；而本脚本的 D/E 类只扫 STATUS_DOCS（README/ARCHITECTURE/DEPENDENCIES），
      F 类只校验生成块 —— 正文是纯盲区，于是同一根因一天内复发三次。

    判据不是「把正文也纳入比对」（那要维护第二套真源），而是：
      **易变计数要么带时点（`10-29 22:36 实测`），要么指向生成块**，否则就是裸数字 →
      明天必过期 → 报红，逼作者改成"项数以文末生成块为准"。

    允许的两种写法（负样本，不得误报）：
      | kit 自测 | 全通过（项数以文末生成块为准） |
      （21 bundle + 13 tar，约 1003 MB；10-29 22:36 实测，复算 …）
    历史叙述句（含 → / 原始 / 当时 等）一律跳过 —— 与 A/D/E 类同一口径。
    """
    if docs is not None:
        targets = list(docs)
    else:
        # 只扫**当前**交接摘要（文件名日期最大者）：被取代的旧摘要属时点文件，
        # 与审计记录同理，永不回写、也不受本项约束。
        all_docs = sorted(DOCS.glob("交接摘要-*.md"))
        targets = all_docs[-1:] if all_docs else []
    checked = 0
    for p in targets:
        p = Path(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for rx, label in FORBIDDEN_PATTERNS:
                if not rx.search(line):
                    continue
                checked += 1
                if HIST_RE.search(line) or STAMP_RE.search(line):
                    continue                                     # 历史句 / 带时点 → 放行
                problems.append(
                    ("G", f"{p.name}:{lineno} 正文裸{label}",
                     "易变计数必须带时点（如 10-29 22:36 实测）或指向文末 handover-facts 生成块"))
    return checked


def main():
    ap = argparse.ArgumentParser(description="文档与磁盘一致性校验（只读）")
    ap.add_argument("--strict", action="store_true", help="存在不一致时 exit 1（CI 用）")
    ap.add_argument("-q", "--quiet", action="store_true", help="只输出结论")
    ap.add_argument("--expect", action="append", default=[], metavar="NAME=N",
                    help="配对计数的实测值，可重复：--expect regress=50 --expect gates=8")
    args = ap.parse_args()

    expects = {}
    for item in args.expect:
        if "=" not in item:
            print(f"[ERR] --expect 需要 NAME=N 形式：{item}", file=sys.stderr)
            return ERR
        k, v = item.split("=", 1)
        if not v.isdigit():
            print(f"[ERR] --expect 的值必须是整数：{item}", file=sys.stderr)
            return ERR
        expects[k.strip()] = int(v)

    if not DOCS.is_dir() or not PKG.is_dir():
        print(f"[ERR] 目录缺失：{DOCS} 或 {PKG}", file=sys.stderr)
        return ERR

    problems = []
    a = check_manifest_numbers(problems)
    b = check_index_line_counts(problems)
    c = check_contract_version(problems)
    d = check_count_claims(problems)
    e = check_paired_counts(problems, expects)
    f = check_generated_blocks(problems)
    g = check_forbidden_counts(problems)

    if not args.quiet:
        print("[check_docs_consistency] 文档 ↔ 磁盘一致性")
        print(f"  A. MANIFEST 数字   : 校验 {a} 处")
        print(f"  B. 索引行数        : 校验 {b} 处")
        print(f"  C. 契约版本        : 校验 {c} 处")
        print(f"  D. 计数声明        : 校验 {d} 处")
        print(f"  E. 配对计数        : 校验 {e} 处" + ("" if expects else "（未传 --expect，已跳过）"))
        print(f"  F. 生成块          : 校验 {f} 处")
        print(f"  G. 正文裸计数禁区  : 命中 {g} 处（带时点或指向生成块者放行）")
        print()

    if problems:
        print(f"[FAIL] 发现 {len(problems)} 处不一致：", file=sys.stderr)
        for kind, what, why in problems:
            print(f"  [{kind}] {what}  →  {why}", file=sys.stderr)
        # 给出可直接执行的修复命令（A 类可自动修，B/C 类需人工）
        kinds = {k for k, _, _ in problems}
        if "A" in kinds:
            print("\n[A 类可自动修复] python3 scripts/sync_doc_numbers.py", file=sys.stderr)
        if "F" in kinds:
            print("[F 类可自动修复] python3 scripts/handover_facts.py --emit docs/交接摘要-20261029.md",
                  file=sys.stderr)
        if kinds & {"B", "C", "D", "E", "G"}:
            print("[B/C/D/E/G 类需人工] B=索引行数，C=契约版本，D=计数声明，E=配对计数，\n"
                  "G=正文裸计数（改成「以文末生成块为准」或补时点）；\n"
                  "除 B 需重跑索引外，其余一律改文档数字去匹配实跑结果，别反过来凑",
                  file=sys.stderr)
        if args.strict:
            return BAD
        print("\n（非 --strict 模式：仅报告，不返回失败码）", file=sys.stderr)
        return OK

    print(f"[PASS] 全部一致（合计校验 {a + b + c + d + e + f} 处）")
    return OK


if __name__ == "__main__":
    sys.exit(main())
