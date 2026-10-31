#!/usr/bin/env python3
"""出题质检闸：把四角色结论 + 命题结构变成机器可判的准入闸（D-0085 v1）。

## 为什么需要它

v2 的 26 道题四臂全对（D-0063）、v3 常数基准 0.700 高过所有臂（D-0064），两次事故
事后回看都不是「模型不行」，而是**题本身坏了**：正确项比干扰项长一截、正确项里塞
「人工复核/持续跟踪」这类元话语、选项之间立场重叠。而这些坏题的共同特征是**在
出分之前就能看出来**——只要有人看。

四角色（R1/R2/R3/R4）在答题侧四代零增量（v1.1–v1.3、v2 一审、v2 二审），但它们的
结论本来就是「题面自洽 / 选项结构 / 命题否决 / 可解性」四类质检意见。D-0085 把它们
从答题侧下线、前置为出题侧质检层：**人（模型）给结论，机检闸给准入**。

判据在 D-0085 §三 预注册，本文件只做执行，不新增判据；阈值写死，**不随批次放宽**
（放宽一次之后每次都会放宽）。

## 判据（逐题；否决轴 C1→C2→C3 一题只记一个码，C4 是独立记档轴）

| # | 判据 | 处置 |
|---|---|---|
| `C0` | `options_sha256`（选填）与 `options` 重算值不符（档案被手改） | exit 2 |
| `C1` | `r1.verdict == "contradicted"`（题面事实矛盾） | REJECT |
| `C2` | `r3.veto is True`（命题程序性否决） | REJECT |
| `C3` | 正确项为最长选项**且**长度 ≥ 其余均值 × 1.3（可凭字数猜答案） | REJECT |
| `C4` | 正确项含元话语词、且干扰项全不含（弱泄漏，可接受但记档） | WARN |
| `C5` | 并入 C3：C3 命中且 `r2.type == "reverse_inferable"` 时在理由后追加佐证 | —— |

否决轴顺序即理由优先级（C1 命中说明题面本身就错了，再谈选项长度没有意义）。C4 单独
一条轴、与否决并行判定：它说的是「措辞在喊话」而不是「不可用」，两者正交；实测 25 题
里 13 条命中 C4、且这 13 条**全部同时满足 C3 判据**（其中 10 条记码 C3，另 3 条被 C2
抢先记码），若按「命中即停」这些措辞缺陷就永远进不了档，而改题时恰好要一起改。故同一
题**可以**同时出现在 rejects 与 warns 里。

批次级（C6）：盲答正确率 `< 0.3` → 批次 REJECT（不可解：题面推不出答案）；
`== 1.0` → 批次 WARN（饱和：冷读全对，交给 item_gate 复核区分度）。

`blind.verdict` 必须落在 `options` 的键里，否则 exit 2：R4 若写成「选项D」「D.」，
C6 会把它全判 miss → 批次按「不可解：题面推不出答案」报红，**把格式错诊成命题不可解、
指错方向**。这条不是判据，是把误导性报错前移成明确的 schema 错。

## 字符串归一化规则（一条，无特例）

档案里的**值**（`qid` / `correct` / `blind.verdict` / `options_sha256`）一律先 `strip()`
再参与比对与判据；**`options` 的键不 strip**——键是 P0 冻结件，原样比对，改一个空格就是
另一道题。

理由：`" D"` 既不改变题目含义、也不改变选项键，它只是同一个值的不同书写；把它判成
exit 2 会让整批卡在一个尾随换行上（R4 输出带 `\n` 是常事），而它想表达的答案毫无歧义。
反过来，`"D."`「选项D」这类**拼错语义**的值无法无歧义还原（是 D 还是新选项 D.？），
一律 exit 2。区分「书写差异」与「语义差异」是这条规则的全部内容。

（此前 `blind` 归一化而 `correct` 原样比对，同一类输入给出相反结论——没有判据依据，
已统一。qa 的 `" D"` 用例钉的正是这条。）

## C0 为什么是「选填 + 校验即拒」，而不是必填

dossier 是题面的**派生物**：题面改了、档案没重生成，报告就会挂在旧文本上——本轮 R1 指
摘 V2-20「24 个月 vs 12 个月」被判误读，根因就是**报告没绑题面版本**（D-0084），不是
R1 错。故每题可带 `options_sha256`（对 `options` 做规范化 JSON 后取 sha256），闸门重算
比对：**存在但不符 → exit 2**（有人手改了档案）；**缺失 → 不报错**，只在人读输出末尾提
示「未绑定题面版本（n 题）」。必填且不校验只是形式主义；必填且校验会卡住历史批次，
反而逼人手工补哈希、补错更糟。

## 为什么 C3 用整数比较

阈值 1.3 是有理数 13/10，而 `float(1.3)` 不是 13/10；浮点乘法在不同平台/数量级上可能
不精确，边界题（如 13 字 vs 10/10/10，数学上恰好压线）的命中就会由舍入决定。故写成
`c * n_others * 10 >= sum_others * 13`：两边都是整数，可证伪、可复现，压线的定义交给
算术而不是浮点。

（此前这里举过 `10 * 1.3 = 13.000000000000002` 的例子——本平台实测 `10 * 1.3 == 13.0`
精确成立，该举例不成立，已删。整数写法保留的理由不是"已复现的漏判"，而是不让平台与
精度参与判据：CPython 上两者目前一致，换 `float32`/`Decimal`/别的语言就可能翻脸。）

## 退出码

沿用 `item_gate` / `route_gate` 惯例：

    0 = 无 REJECT（可含 WARN）
    1 = 存在任一题 REJECT，或批次 REJECT（C6）
    2 = 输入/判据自身问题（文件不存在、坏 JSON、顶层非对象、items 缺失/非数组/空数组、
        题目缺 qid/options/correct/r1/r2/r3/blind、correct 不在 options 键内、
        blind.verdict 不在 options 键内、枚举值非法、options 少于 2 项、
        C0 哈希与题目内容不符）

空批一律 exit 2：闸门没有输入时**不能**静默返回「全部通过」。

## 用法

    python3 audit/kit/item_review.py --dossier audit/baseline/v2/dossier-v2.json
    python3 audit/kit/item_review.py --dossier <path> --json

## 与 item_gate / route_gate 的边界（职责不重叠，判据不复制）

- `item_gate.py`：答题**结果**侧准入——饱和 / 全错 / 常数胜出 / pass^k。要有作答才跑。
- `route_gate.py`：**路由**分诊 + S1 标签偏斜预筛。要有 manifest 才跑。
- `item_review.py`（本文件）：只看**命题本身**（题干结构 + 四角色结论），不看任何作答
  结果，因此能在出题时点跑、比前两者更早。C6 用的盲答是「冷读作答」，是题目可解性的
  证据，不参与判分（D-0085 §五）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# ── 判据常量：D-0085 §三 预注册。改这里等于改判据，须同步改决策文档与验收套件 ──
R1_VERDICTS = ("corroborated", "contradicted", "unverifiable")
R2_TYPES = ("ambiguity", "non_exhaustive", "reverse_inferable", "none")
REQUIRED_FIELDS = ("qid", "options", "correct", "r1", "r2", "r3", "blind")

# C4 元话语词表：这些话只出现在正确答案里，就是在对阅卷人喊话，不是在答题
META_WORDS = ("人工复核", "闭环", "待验证", "持续跟踪", "持续暴露", "跟进")

# C3 长度比阈值 1.3 = 13/10：整数比较要分子分母，见文件头「为什么 C3 用整数比较」
LEN_RATIO_NUM, LEN_RATIO_DEN = 13, 10
LEN_RATIO = LEN_RATIO_NUM / LEN_RATIO_DEN

# C6 盲答基线阈值（D-0085 §三）
BLIND_MIN_ACC = 0.3
BLIND_SATURATED = 1.0

DETAIL_CLIP = 60  # C1 引用 r1.detail 的截断上限（字）
BATCH_QID = "@batch"  # 批次级判定的 qid 占位：让 rejects/warns 与 exit 码自洽

# 错误文案存成**单行常量**：此前拆成相邻两个 f-string，整句在任何一行都 grep 不到，
# 已造成两位同事按 grep 判断"未实现"（D5 被误判过一次）。文案本身一个字都没改。
BLIND_OUT_OF_OPTIONS = "不在 options 键内（格式错误，不得当作可解性信号）"


def _one_line(text, limit: int = 0) -> str:
    """压成单行（reason 要能一行打印），可选截断到 limit 字以内。"""
    s = " ".join(str(text or "").split())
    if limit and len(s) > limit:
        s = s[:limit]
    return s


def options_sha256(options: dict) -> str:
    """题面绑定哈希（C0）：规范化 JSON 后取 sha256 十六进制。

    规范化 = 键排序 + 紧凑分隔符 + 不转义中文。三者缺一就会与出题管线的算法分叉、
    把好档案全判成「被手改」——所以口径写死在这里，别处不许另写一份。
    """
    payload = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── schema 校验：全部错误一次性收齐，逐条给「qid: 消息」────────────────────────
def validate_items(items: list, errors: list[str]) -> tuple[list[dict], int]:
    """校验并返回 (**干净**题目列表, 未绑定题面版本的题数)。

    口径同 route_gate：出错就 exit 2、不产出判定，避免 --json 里出现「exit 2 但
    rejects 非空」这种自相矛盾的下游输入。

    C0（`options_sha256`）是**选填自校验**：缺失只计数（人读输出末尾提示），存在但不符
    即 exit 2——档案是题面的派生物，哈希对不上说明有人手改过档案，此时任何结论都绑错了
    版本（D-0084 的 V2-20「误读」就是这么来的）。
    """
    clean: list[dict] = []
    unbound = 0
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f"items[{idx}]: 不是对象（{type(it).__name__}）")
            continue
        # mark 必须是本 item 的**第一个**动作：此前放在 qid 校验之后，导致「只缺 qid」的
        # 脏题（带 items[i] 占位 qid）能通过 len(errors) == mark 混进 clean。当时靠
        # 「errors 非空就不判定」兜住，但凡有人复用 validate_items 的返回值就会中招。
        mark = len(errors)  # 本 item 校验前的错误数，用于判断它是否脏
        item_unbound = False  # 本题是否缺 options_sha256（只对干净题计数）
        raw_qid = it.get("qid")
        if isinstance(raw_qid, str) and raw_qid.strip():
            qid = raw_qid.strip()
        else:
            qid = f"items[{idx}]"
            errors.append(f"{qid}: 缺 qid 或不是非空字符串")

        options = it.get("options")
        options_ok = isinstance(options, dict) and all(isinstance(v, str) for v in options.values())
        if not isinstance(options, dict):
            errors.append(f"{qid}: 缺 options 或不是对象（{type(options).__name__}）")
        else:
            if len(options) < 2:
                errors.append(f"{qid}: options 少于 2 项（{len(options)} 项），无干扰项可比，长度判据不成立")
            bad = sorted(k for k, v in options.items() if not isinstance(v, str))
            if bad:
                errors.append(f"{qid}: options 文本须为字符串，异常键 {bad}")

        # C0 题面版本自校验：只在 options 自身可算哈希时才比对（脏 options 已单独报错）
        # null / 空串 / 全空白 视同「未设置」——「没填」不等于「声明了一个错的哈希」，前者
        # 只提示、后者才 exit 2（否则出题管线用空串占位就会被一律判成篡改）。
        want_sha = it.get("options_sha256")
        blank_sha = want_sha is None or (isinstance(want_sha, str) and not want_sha.strip())
        if blank_sha:
            item_unbound = True
        elif options_ok and not (isinstance(want_sha, str)
                                 # 十六进制大小写/首尾空白不影响取值，归一化后再比；非字符串一律不符
                                 and want_sha.strip().lower() == options_sha256(options)):
            errors.append(f"{qid}: options_sha256 与选项内容不符（疑似手工篡改档案）")

        correct = it.get("correct")
        if not isinstance(correct, str) or not correct.strip():
            errors.append(f"{qid}: 缺 correct 或不是非空字符串")
        elif isinstance(options, dict) and correct.strip() not in options:
            # 值先 strip 再比对：见文件头「字符串归一化规则」（曾只对 blind 归一化、correct
            # 原样比对，同一类输入两种结论，没有判据依据）。
            errors.append(f"{qid}: correct={correct!r} 不在 options 的键 {sorted(options)} 中")

        r1 = it.get("r1")
        if not isinstance(r1, dict):
            errors.append(f"{qid}: 缺 r1 或不是对象")
        elif r1.get("verdict") not in R1_VERDICTS:
            errors.append(f"{qid}: r1.verdict 非法（{r1.get('verdict')!r}，须为 "
                          f"{'/'.join(R1_VERDICTS)}）")

        r2 = it.get("r2")
        if not isinstance(r2, dict):
            errors.append(f"{qid}: 缺 r2 或不是对象")
        elif r2.get("type") not in R2_TYPES:
            errors.append(f"{qid}: r2.type 非法（{r2.get('type')!r}，须为 {'/'.join(R2_TYPES)}）")

        r3 = it.get("r3")
        if not isinstance(r3, dict):
            errors.append(f"{qid}: 缺 r3 或不是对象")
        elif not isinstance(r3.get("veto"), bool):
            # 必须是 JSON 的 true/false，"true" 这种字符串会静默绕过 C2
            errors.append(f"{qid}: r3.veto 须为布尔值（实为 {type(r3.get('veto')).__name__} "
                          f"{r3.get('veto')!r}），字符串会静默绕过 C2")
        elif r3.get("basis") is not None and not isinstance(r3.get("basis"), str):
            errors.append(f"{qid}: r3.basis 须为字符串或 null（实为 {type(r3.get('basis')).__name__}）")

        blind = it.get("blind")
        if not isinstance(blind, dict):
            errors.append(f"{qid}: 缺 blind 或不是对象")
        elif not isinstance(blind.get("verdict"), str) or not blind["verdict"].strip():
            errors.append(f"{qid}: blind.verdict 缺或不是非空字符串（C6 盲答基线必需）")
        elif isinstance(options, dict) and blind["verdict"].strip() not in options:
            # 格式错必须在此拦下：R4 若写成「选项D」「D.」，C6 会全判 miss → 批次按
            # 「不可解：题面推不出答案」报红，把**格式错**诊成**命题不可解**、指错方向。
            # 这不是判据，是把误导性报错前移成明确的 schema 错。
            errors.append(f"{qid}: blind.verdict={blind['verdict'].strip()!r} {BLIND_OUT_OF_OPTIONS}")

        if len(errors) == mark:
            clean.append({"qid": qid, "options": options, "correct": correct.strip(),
                          "r1": r1, "r2": r2, "r3": r3,
                          "blind": blind["verdict"].strip()})
            unbound += item_unbound
    if errors:
        # clean 只在「整批无错」时可用：有错则整批不判定（半份结果比没有结果更危险）。
        # 这一句让脏题游进 clean 在结构上不可能，而不是只靠 len(errors) == mark 这个
        # 每项都要维护的脆弱不变式（D2 就是 mark 位置放错漏掉的）。
        return [], 0
    return clean, unbound


# ── 逐题判据 ────────────────────────────────────────────────────────────────
def review_item(it: dict) -> tuple[dict | None, dict | None]:
    """返回 (reject, warn)，各自为 {"qid","code","reason"} 或 None。

    两条轴必须分开（首版把 C4 塞进「命中即停」链里，WARN 恒为 0）：

    - **否决轴 C1→C2→C3**：一题只记一个码（顺序即理由优先级）。同一题的三个码同时成立
      时，报告里出现三条理由是噪音，且 C1/C2 的可追溯性（引 R1 detail / R3 reason）
      最需要被看见——C1 命中说明题面本身就错了，此时再讨论选项长度没有意义。
    - **记档轴 C4**：独立判、与否决并行。C4 说的是「措辞在喊话」而非「不可用」，两者
      正交；实测题库里 13/25 命中 C4、且这 13 题**全部**同时满足 C3 判据——若按命中即停，
      这些措辞缺陷就永远进不了档，而改题时恰好要一起改。
    """
    qid, options, correct = it["qid"], it["options"], it["correct"]
    r1, r2, r3 = it["r1"], it["r2"], it["r3"]
    reject = None

    # C1 题面事实矛盾：R1 已核出题面自相矛盾，题面不改就没有可讨论的余地
    if r1["verdict"] == "contradicted":
        reject = {"qid": qid, "code": "C1",
                  "reason": _one_line(r1.get("detail"), DETAIL_CLIP) or "R1 判 contradicted"}

    # C2 命题程序性否决：R3 的否决理由已写明，直接引用，不再自行改写（否定裁决要可追溯）
    elif r3.get("veto") is True:
        reject = {"qid": qid, "code": "C2",
                  "reason": _one_line(r3.get("reason")) or f"R3 否决（basis={r3.get('basis')}）"}

    # C3（含 C5）长度泄漏：正确项最长且比其余均值长 ≥1.3× → 不读题、数一下字数就能选
    else:
        lens = {k: len(v) for k, v in options.items()}
        c_len = lens[correct]
        others = [n for k, n in lens.items() if k != correct]
        s_others, n_others = sum(others), len(others)
        if c_len > 0 and c_len == max(lens.values()) and \
                c_len * n_others * LEN_RATIO_DEN >= s_others * LEN_RATIO_NUM:
            avg = s_others / n_others
            ratio = "∞" if avg == 0 else f"{c_len / avg:.2f}"
            reason = (f"长度泄漏：正确项 {correct} {c_len} 字居最长，其余均值 {avg:.1f} 字，"
                      f"比值 {ratio}× ≥ {LEN_RATIO}×（不看题面数选项长度即可反推）")
            if r2.get("type") == "reverse_inferable":
                # C5：R2 的独立结论与长度线索同向，一并写进 C3 理由（不单列 code）
                reason += "（R2 亦判 reverse_inferable，佐证）"
            reject = {"qid": qid, "code": "C3", "reason": reason}

    # C4 元话语泄漏：正确项在喊「人工复核/持续跟踪」，干扰项干净 → 措辞本身是线索
    # 只 WARN：这类题还能作答，泄漏强度不足以一票否决（D-0085 预注册即如此）
    hits = [w for w in META_WORDS if w in options[correct]]
    warn = None
    if hits and not any(w in text for k, text in options.items()
                        if k != correct for w in META_WORDS):
        warn = {"qid": qid, "code": "C4",
                "reason": f"元话语泄漏：正确项 {correct} 含「{'/'.join(hits)}」，"
                          f"其余选项全不含（措辞即线索，记档待改）"}
    return reject, warn


# ── 批次判据 C6：盲答基线（可解性）──────────────────────────────────────────
def batch_check(items: list[dict]) -> dict:
    """盲答正确率：<30% 不可解（题坏），=100% 饱和（无区分度），两者都不该入库。"""
    n = len(items)
    hits = sum(1 for it in items if it["blind"] == it["correct"])
    acc = hits / n
    base = {"blind_acc": round(acc, 4), "hits": hits, "n": n}
    if acc < BLIND_MIN_ACC:
        return {**base, "verdict": "REJECT", "level": "reject",
                "reason": f"不可解：盲答正确率 {acc * 100:.1f}%<30%（{hits}/{n} 题，"
                          f"题面推不出答案或真值有问题）"}
    if acc >= BLIND_SATURATED:
        return {**base, "verdict": "WARN", "level": "warn",
                "reason": "饱和：盲答全对，需 item_gate 复核区分度"}
    return {**base, "verdict": "PASS", "level": "",
            "reason": f"盲答正确率 {acc * 100:.1f}%（{hits}/{n} 题）：可解且未饱和"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="出题质检闸 v1：C0 题面哈希自校验 / C1 题面矛盾 / C2 命题否决 / "
                    "C3 长度泄漏 / C4 元话语 / C6 盲答基线")
    ap.add_argument("--dossier", required=True, help="命题档案 dossier.json 路径")
    ap.add_argument("--json", dest="json_out", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    errors: list[str] = []
    doc = None
    try:
        # utf-8-sig：Windows/表格工具导出的 dossier 常带 BOM，用纯 utf-8 解会让首字符变成
        # '\ufeff' 而 json.loads 报错 → 好档案被打成 exit 2。utf-8-sig 对无 BOM 文件行为不变。
        doc = json.loads(Path(args.dossier).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        errors.append(f"{args.dossier}: 文件不存在")
    except Exception as e:  # 坏 JSON / 编码错误 / 权限 —— 一律 fail-safe
        errors.append(f"{args.dossier}: 无法解析：{type(e).__name__}: {e}")

    items: list = []
    if doc is not None:
        if not isinstance(doc, dict):
            errors.append(f"dossier 顶层须为对象，实为 {type(doc).__name__}")
        else:
            raw = doc.get("items")
            if not isinstance(raw, list):
                errors.append("dossier 缺 items 或 items 不是数组")
            elif not raw:
                errors.append("dossier items 为空（空批不可质检，fail-safe 不放行）")
            else:
                items = raw

    batch = str(doc.get("batch") or "") if isinstance(doc, dict) else ""
    clean, unbound = validate_items(items, errors) if not errors else ([], 0)

    # ── 判定 ────────────────────────────────────────────────────────────────
    rejects: list[dict] = []
    warns: list[dict] = []
    bc = None
    if not errors:
        for it in clean:
            reject, warn = review_item(it)
            if reject is not None:
                rejects.append(reject)
            if warn is not None:
                warns.append(warn)
        bc = batch_check(clean)
        # 批次级判定也进 rejects/warns：否则 exit 1 而 rejects 为空，结果自相矛盾
        if bc["verdict"] != "PASS":
            entry = {"qid": BATCH_QID, "code": "C6", "reason": bc["reason"]}
            (rejects if bc["verdict"] == "REJECT" else warns).append(entry)
        exit_code = 1 if rejects else 0
    else:
        # 输入坏时不产出任何判定：半份结果比没有结果更危险
        bc = {"blind_acc": 0.0, "verdict": "REJECT",
              "reason": "输入/schema 错误，未做批次判定（fail-safe）"}
        exit_code = 2

    summary = {"reject": len(rejects), "warn": len(warns)}

    if args.json_out:
        print(json.dumps({"exit": exit_code, "batch": batch, "n_items": len(items),
                          "rejects": rejects, "warns": warns,
                          "batch_checks": {"blind_acc": bc["blind_acc"],
                                           "verdict": bc["verdict"],
                                           "reason": bc["reason"]},
                          "summary": summary, "errors": errors},
                         ensure_ascii=False, indent=2))
        return exit_code

    # ── 人读输出：中文，逐条一行（含 qid 与 code），末行总结 + 批次判定 ────────
    print(f"出题质检 v1 ｜ {args.dossier} ｜ 批次 {batch or '(未命名)'} ｜ {len(items)} 题")
    for err in errors:
        print(f"  ❌ {err}")
    for e in rejects:
        print(f"  🔴 REJECT {e['qid']:<10} {e['code']}  {e['reason']}")
    for e in warns:
        print(f"  🟡 WARN   {e['qid']:<10} {e['code']}  {e['reason']}")
    # 批次行：C6 命中时理由已在上面的 @batch 行里，此处只给数字与结论，避免同一句打两遍
    if errors:
        print("  批次 C6：未判定（输入不可信，fail-safe 不做批次结论）")
    elif bc["verdict"] == "PASS":
        print(f"  批次 C6：{bc['reason']} → PASS")
    else:
        print(f"  批次 C6 盲答基线 {bc['hits']}/{bc['n']} = {bc['blind_acc'] * 100:.1f}% → "
              f"{bc['verdict']}")

    if exit_code == 0:
        verdict_line = f"✅ PASS 无 REJECT（WARN {summary['warn']} 条记档）"
    elif exit_code == 1:
        verdict_line = (f"🔴 FAIL 有 {summary['reject']} 条 REJECT，"
                        f"改造后复检（选项均衡化 / 改题面 / 重判立场互斥）")
    else:
        verdict_line = f"❌ exit 2 输入/schema 错误 {len(errors)} 条，未产出任何判定"
    batch_word = "未判定" if errors else bc["verdict"]
    print(f"总结：REJECT {summary['reject']} ｜ WARN {summary['warn']} ｜ "
          f"批次 {batch_word} ｜ exit {exit_code}")
    print(verdict_line)
    # C0 末行提示：档案没带 options_sha256 时不报错（选填），但结论无法与题面版本对齐，
    # 报告引用时必须写明「本题面版本未绑定」。只在**有判定**时打：exit 2 的屏上已经写了
    # 「未产出任何判定」，再补一句「本报告的结论…」是自相矛盾（qa 报的 UX 缺陷）。
    if unbound and not errors:
        print(f"⚠️ 未绑定题面版本（{unbound} 题）：dossier 无 options_sha256，"
              f"本报告的结论不可与具体题面版本对齐（改题后须重跑，勿复用）")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
