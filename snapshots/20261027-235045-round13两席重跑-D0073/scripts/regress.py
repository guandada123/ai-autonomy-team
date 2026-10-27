#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regress.py —— 全量回归验收套件

为什么要有它：口头声称「回归全绿」不是一个可复现的判据。
把判据固化成一条命令，任何人（含目标机）都能重跑并得到同样的结论。

★ 幂等性要求（F11）：本套件跑完后，包内任何文件的 sha256 都不得改变。
  所有会写盘的步骤都对着 _trash/ 下的工作副本或 scratch 输出跑。
  第 30 项就是这条要求的守门项。

依赖：仅 Python 标准库。

用法：
    python3 regress.py                 # 跑全部项
    python3 regress.py --json          # 机器可读
退出码：0 = 全绿；1 = 有失败
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # 本脚本在 scripts/ 下，上跳一级才是工作区根
PKG = ROOT / "migration-package"
PY = sys.executable

# ★ 消硬编码：轮次目录名集中在此，避免散落各处（L-0004 教训：加规则时先跑基线，
#   而消硬编码不改变现有 11 项行为，是保持基线全绿的前提）
ROUND1 = "runs/round1"
RECORD_SELFTEST = f"{ROUND1}/adjudication-record.SELFTEST-PLUMBING-ONLY.json"

# ★ F11 修复：夹具必须在【工作副本】上跑，绝不能让工具把机器产出写回包内夹具。
#   原实现漏传 --out，于是 seat_check / metrics 把 seat-check.json / metrics.json
#   写进 fixtures/regress/**，其 generated_at 每次变 → sha256 变 → 下一次跑时
#   第 10 项（MANIFEST 自校验）必然假红。净效果是「只在重建 MANIFEST 后的
#   第一次跑是绿的」——一套非幂等的回归。
#   现在：包内夹具只含【输入】；机器产出全部落在 _trash/ 的工作副本里。
FIXT_SRC = PKG / "fixtures/regress"                      # 只读来源
TMP = ROOT / "_trash/tmp-regress"                        # scratch 根（不入库）
TMP_FIXT = ROOT / "_trash/tmp-regress/fixtures"          # 工作副本（可写）
FIXT = "../_trash/tmp-regress/fixtures"                  # 供 CLI 用的相对路径（cwd=PKG）


def run(args, cwd=None):
    r = subprocess.run([PY] + args, cwd=str(cwd or ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Suite:
    def __init__(self):
        self.items = []

    def check(self, no, name, ok, detail):
        self.items.append({"no": no, "name": name, "ok": bool(ok), "detail": detail})

    def expect(self, no, name, rc, out, want_rc, must_contain=None):
        ok = (rc == want_rc)
        if ok and must_contain:
            ok = must_contain in out
        d = f"退出码 {rc}（期望 {want_rc}）"
        if must_contain:
            d += f" | 含「{must_contain}」= {must_contain in out}"
        self.check(no, name, ok, d)


def move_stale_fixtures():
    """把工作副本里「源已不存在」的文件改名到 stale-<ts>/（不删除）。

    ★ 2026-10-23 修（代码审查 C-9）：`copytree(dirs_exist_ok=True)` 是合并式复制，
      上游删除/改名夹具后，旧副本仍留在工作目录里 —— 测试可能继续读旧文件而"通过"。
      项目禁止删除，故只做改名，现场仍可回查。
    """
    if not TMP_FIXT.is_dir():
        return []
    src = {p.relative_to(FIXT_SRC).as_posix() for p in FIXT_SRC.rglob("*") if p.is_file()}
    stale = [p for p in TMP_FIXT.rglob("*")
             if p.is_file() and p.relative_to(TMP_FIXT).as_posix() not in src]
    if not stale:
        return []
    dest = TMP / f"stale-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    for p in stale:
        tgt = dest / p.relative_to(TMP_FIXT)
        tgt.parent.mkdir(parents=True, exist_ok=True)
        p.rename(tgt)
    return stale


def main():
    ap = argparse.ArgumentParser(description="30 项全量回归验收")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    s = Suite()
    judge = ["judges/verify.py"]
    ans = f"{ROUND1}/answer-final.json"
    scratch = ROOT / "_trash/tmp-regress/out"
    scratch.mkdir(parents=True, exist_ok=True)

    # ★ 夹具工作副本（合并式复制；删除的唯一去向是改名到 _trash/，故此处不出删）
    #   复制前先把「源已没有」的陈旧文件搬走，避免测试读旧夹具（C-9）
    _stale = move_stale_fixtures()
    if _stale:
        print(f"[fixtures] 移出 {len(_stale)} 个陈旧文件 → {TMP}/stale-*")
    shutil.copytree(FIXT_SRC, TMP_FIXT, dirs_exist_ok=True)

    def jrun(extra, tag):
        """统一把结果写到 scratch —— 摘要行只在给 --out 时打印。"""
        return run(judge + extra + ["--out", f"../_trash/tmp-regress/out/{tag}.json"], PKG)

    # 1 自检
    rc, out = run(judge + ["--self-test"], PKG)
    s.expect(1, "硬裁判自检（含闸门用例）", rc, out, 0, "自检通过")

    # 2 legacy 不回归
    rc, out = jrun(["--spec", "fixtures/truth.json", "--answer", ans], "legacy")
    s.expect(2, "legacy 单层不回归", rc, out, 0, "passed=14/14")

    # 3 算术层
    rc, out = jrun(["--spec", "fixtures/spec/arithmetic.json",
                    "--spec-layer", "arithmetic", "--answer", ans], "arith")
    s.expect(3, "算术层自动裁决", rc, out, 0, "passed=10/10")

    # 4 判断层未裁决
    rc, out = jrun(["--spec", "fixtures/spec/adjudication.json",
                    "--spec-layer", "adjudication", "--answer", ans], "pending")
    s.expect(4, "判断层未裁决 → pending/exit 3", rc, out, 3, "pending_adjudication")

    # 5 人类裁决回填
    rec = PKG / RECORD_SELFTEST
    if rec.is_file():
        rc, out = jrun(["--spec", "fixtures/spec/adjudication.json",
                        "--spec-layer", "adjudication", "--record",
                        RECORD_SELFTEST,
                        "--answer", ans], "adjudicated")
        s.expect(5, "人类裁决回填 → adjudicated", rc, out, 0, "adjudicated")
    else:
        s.check(5, "人类裁决回填 → adjudicated", False, "缺少管路自检用记录文件")

    # 6 闸门1
    #   ★ 夹具必须在【随包转移的 fixtures/】里，不能落在 _trash/tmp-regress/ ——
    #     否则解压到 Mac 后这两项直接 FAIL（文件不存在），套件不可迁移。
    rc, out = run(judge + ["--spec", f"{FIXT}/bad-gate1-judgment.json",
                           "--answer", ans], PKG)
    s.expect(6, "闸门1 判断项混入算术层被拒", rc, out, 2, "SPEC-VIOLATION")

    # 7 闸门2
    rc, out = run(judge + ["--spec", f"{FIXT}/bad-gate2-money.json",
                           "--answer", ans], PKG)
    s.expect(7, "闸门2 金额未用 decimal_equals 被拒", rc, out, 2, "SPEC-VIOLATION")

    # 8 三数不变
    frozen = ROOT / "fixtures/frozen/truth.json.frozen"
    live = PKG / "fixtures/truth.json"
    if frozen.is_file():
        ok = sha(frozen) == sha(live)
        s.check(8, "truth.json 与冻结基线逐字节一致", ok,
                f"sha256 {sha(live)[:16]}（基线 {sha(frozen)[:16]}）")
    else:
        s.check(8, "truth.json 与冻结基线逐字节一致", False, "缺少冻结基线文件")

    # 9 单一真实源
    rc, out = run([str(ROOT / "scripts/build_package.py"), "--check"])
    s.expect(9, "单一真实源一致（源↔包）", rc, out, 0, "全部一致")

    # 10 包完整性
    rc, out = run(["gen_manifest.py", "--root", ".", "--check", "MANIFEST.json"], PKG)
    s.expect(10, "包完整性（MANIFEST 自校验）", rc, out, 0, "完全一致")

    # 11 precheck
    rc, out = run(["precheck.py"], PKG)
    s.expect(11, "目标机预检", rc, out, 0, "结果：")

    # ==================== 本轮新增：第 12–21 项 ====================
    TMP.mkdir(parents=True, exist_ok=True)
    SC = ["judges/seat_check.py", "--params", "config/params.json",
          "--spec", "fixtures/spec/adjudication.json"]

    # 12 开轮器幂等
    #    ★ 每次都用工作副本刷新 nr1，去掉「已存在则跳过」的缓存（否则夹具一改，
    #      第 12 项就在拿旧内容测幂等，会给出假绿）
    prev = TMP / "nr1"
    shutil.copytree(TMP_FIXT / "roundA", prev, dirs_exist_ok=True)
    nr_args = ["new_round.py", "--root", "..", "--runs-dir", "../_trash/tmp-regress",
             "--preflight", "off",
               "--round", "nr2", "--role", "revision_round", "--from", "nr1", "--emit-prompts"]
    rc1, out1 = run(nr_args, PKG)
    rc2, out2 = run(nr_args, PKG)
    s.check(12, "新轮次初始化幂等（复跑写入 0 个）",
            rc1 == 0 and rc2 == 0 and "写入 0 个" in out2,
            f"首跑 rc={rc1} / 复跑 rc={rc2} / 复跑「写入 0 个」={('写入 0 个' in out2)}")

    # 13 目录 schema
    rc, out = run(["new_round.py", "--root", "..", "--runs-dir", "../_trash/tmp-regress",
             "--preflight", "off",
                   "--check", "nr2"], PKG)
    s.expect(13, "新轮次目录 schema 完整", rc, out, 0, "结构完整")

    # 为后续指标项准备 seat-check.json
    for r in ("roundA", "roundB", "roundC", "roundD1", "roundD2"):
        run(SC + ["--round-dir", f"{FIXT}/{r}"], PKG)

    # 14 席位契约正向
    rc, out = run(SC + ["--round-dir", f"{FIXT}/roundA"], PKG)
    ok14 = rc == 0 and "verdict=all_valid" in out
    s.check(14, "席位契约正向（roundA 全合规）", ok14,
            f"rc={rc} / verdict=all_valid = {'verdict=all_valid' in out}")

    # 15 席位契约负向
    rc, out = run(SC + ["--round-dir", f"{FIXT}/seat-bad"], PKG)
    ok15 = rc == 1 and "被否决" in out and "E_MISSING_CONCLUSION" in out
    s.check(15, "席位契约负向（缺 conclusion/evidence 即否决）", ok15,
            f"rc={rc} / 含被否决 = {'被否决' in out} / 含 E_MISSING_CONCLUSION = {'E_MISSING_CONCLUSION' in out}")

    # 16 聚合防伪
    shutil.copyfile(TMP_FIXT / "final-bad/answer-final.json",
                    TMP_FIXT / "seat-bad/answer-final.json")
    rc, out = run(SC + ["--round-dir", f"{FIXT}/seat-bad", "--verify-final"], PKG)
    s.check(16, "聚合防伪（引用被否决席位 → 拒）", rc == 1,
            f"rc={rc}（期望 1） / {out.strip().splitlines()[-1] if out.strip() else ''}")

    # 17 params 真参数：翻边率阈值改 0.0 → restart_round
    rc, out = run(["metrics.py", "--round-dir", f"{FIXT}/roundB", "--prev", f"{FIXT}/roundA",
                   "--params", f"{FIXT}/params-threshold0.json",
                   "--spec", "fixtures/spec/adjudication.json"], PKG)
    ok17 = rc == 1 and "restart_round" in out
    s.check(17, "params 真参数（阈值 0.0 → restart_round）", ok17,
            f"rc={rc}（期望 1） / 含 restart_round = {'restart_round' in out}")

    # 18 分歧熵下限 → 魔鬼代言人轮
    rc, out = run(["metrics.py", "--round-dir", f"{FIXT}/roundC",
                   "--spec", "fixtures/spec/adjudication.json"], PKG)
    ok18 = rc == 3 and "force_devils_advocate_round" in out
    s.check(18, "分歧熵下限（全一致 + 无独立证据 → 魔鬼代言人轮）", ok18,
            f"rc={rc}（期望 3） / 含 force_devils_advocate_round = {'force_devils_advocate_round' in out}")

    # 19 空转检测（仅 evidence_round 生效）
    run(["metrics.py", "--round-dir", f"{FIXT}/roundD1", "--prev", f"{FIXT}/roundA",
         "--spec", "fixtures/spec/adjudication.json"], PKG)
    rc, out = run(["metrics.py", "--round-dir", f"{FIXT}/roundD2", "--prev", f"{FIXT}/roundD1",
                   "--spec", "fixtures/spec/adjudication.json"], PKG)
    ok19a = rc == 4 and "terminate_branch" in out
    _, out_rev = run(["metrics.py", "--round-dir", f"{FIXT}/roundB", "--prev", f"{FIXT}/roundA",
                      "--spec", "fixtures/spec/adjudication.json"], PKG)
    ok19b = "terminate_branch" not in out_rev and "不适用" in out_rev
    s.check(19, "空转检测（evidence 轮触发 / revision 轮豁免）", ok19a and ok19b,
            f"D2 rc={rc}（期望 4）/ 触发={ok19a} / revision 豁免={ok19b}")

    # 20 无 unlink 静态扫描
    #    注意：模式串必须拼接构造，否则本文件自身会被自己扫到（自引用假阳性）
    BANNED = ["unlink" + "(", "os.remove" + "(", "shutil.rmtree" + "(", "os.unlink" + "("]
    hits = []
    scanned = 0
    for py in sorted((ROOT / "scripts").rglob("*.py")):
        scanned += 1
        src = py.read_text(encoding="utf-8", errors="replace")
        # 逐行扫，跳过纯注释/文档行，避免把「说明性提及」当违规
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            for pat in BANNED:
                if pat in line:
                    hits.append(f"{py.relative_to(ROOT).as_posix()}:{i}::{pat}")
    s.check(20, "无删除调用（删除的唯一去向是改名到 _trash/）", not hits,
            f"扫描 {scanned} 个脚本，全部合规" if not hits else f"命中：{hits}")

    # 21 params 键完整性
    rc, out = run(SC + ["--lint-params"], PKG)
    s.expect(21, "params 键完整性", rc, out, 0, "必需键齐备")

    # ============ 第 22–29 项：读取域（F5）/ 归一化（F6）/ 四态（F7）/ 阶段B（F10）============
    # 全部断言值均取自第四轮实测输出，不是推想值。凡与实测不符即应回查实现，
    # 而不是回头改断言（改断言等于把失败翻译成通过）。

    # 22 读取域清单有效
    rc, out = run(["judges/seat_check.py", "--lint-read-domains"], PKG)
    s.expect(22, "读取域清单有效（deny_absolute/allow/deny_default/按席位 四层）",
             rc, out, 0, "读取域配置合法")

    # 23 提示词注入读取域段（复用第 12 项生成的 nr2）
    pf = TMP / "nr2/prompts/EX-01.md"
    txt = pf.read_text(encoding="utf-8") if pf.is_file() else ""
    ok23 = all(k in txt for k in ("你的读取域", "fixtures/truth.json", "reads"))
    s.check(23, "提示词注入读取域段（可读 / 禁读 / 必须申报 三件事）", ok23,
            f"{'有' if pf.is_file() else '缺'} prompts/EX-01.md（{len(txt)} 字符）"
            f" / 含「你的读取域」={'你的读取域' in txt}"
            f" / 含禁读样例 fixtures/truth.json={'fixtures/truth.json' in txt}"
            f" / 含 reads={'reads' in txt}")

    # 24 读取域越界 → 席位被否决（不得进入聚合）
    rc, out = run(SC + ["--round-dir", f"{FIXT}/seat-readbad",
                        "--out", f"{TMP}/readbad-sc.json",
                        "--rd-out", f"{TMP}/readbad-rd.json"], PKG)
    ok24 = rc == 1 and "E_READ_DOMAIN_VIOLATION" in out and "被否决" in out
    s.check(24, "读取域越界 → 该席位被否决（挡在聚合外）", ok24,
            f"rc={rc}（期望 1） / E_READ_DOMAIN_VIOLATION={'E_READ_DOMAIN_VIOLATION' in out}"
            f" / 含被否决={'被否决' in out}")

    # 25 第 9 查产出可判定（正例 CLEAN）
    rd_p = TMP / "readA-rd.json"
    rc, out = run(SC + ["--round-dir", f"{FIXT}/round-readA",
                        "--out", f"{TMP}/readA-sc.json",
                        "--rd-out", f"{rd_p}"], PKG)
    rd = json.loads(rd_p.read_text(encoding="utf-8")) if rd_p.is_file() else {}
    ok25 = (rc == 0 and rd.get("verdict") == "CLEAN"
            and (rd.get("summary") or {}).get("seats_declaring_reads") == 3)
    s.check(25, "第 9 查产出可判定（round-readA → CLEAN / 3 席申报）", ok25,
            f"rc={rc} / verdict={rd.get('verdict')}"
            f" / 申报席位={(rd.get('summary') or {}).get('seats_declaring_reads')}")

    # 26 F6 负向：同一文件的三种写法（#T101,T102 / :26-27 / 整路径）
    #    → 文件级归一化后必须判为共享 → 三席都不独立 → 强制魔鬼代言人轮
    run(SC + ["--round-dir", f"{FIXT}/roundE"], PKG)      # 在工作副本内生成 seat-check.json
    rc, out = run(["metrics.py", "--round-dir", f"{FIXT}/roundE",
                   "--spec", "fixtures/spec/adjudication.json",
                   "--out", f"{TMP}/m-roundE.json"], PKG)
    mE = json.loads((TMP / "m-roundE.json").read_text(encoding="utf-8")) \
        if (TMP / "m-roundE.json").is_file() else {}
    deE = mE.get("dissent_entropy") or {}
    ok26 = (rc == 3 and deE.get("shared_file_count") == 1
            and deE.get("independent_evidence_seats") == []
            and deE.get("devils_advocate_required") is True)
    s.check(26, "F6 负向：同文件三种写法 → 判共享、无独立证据、触发魔鬼代言人轮", ok26,
            f"rc={rc}（期望 3） / shared_file_count={deE.get('shared_file_count')}（期望 1）"
            f" / 独立席位={deE.get('independent_evidence_seats')}"
            f" / devils={deE.get('devils_advocate_required')}")

    # 27 F6 正向：三个不同文件 → 各有独有观测 → 有独立证据、不触发
    run(SC + ["--round-dir", f"{FIXT}/roundF"], PKG)
    rc, out = run(["metrics.py", "--round-dir", f"{FIXT}/roundF",
                   "--spec", "fixtures/spec/adjudication.json",
                   "--out", f"{TMP}/m-roundF.json"], PKG)
    mF = json.loads((TMP / "m-roundF.json").read_text(encoding="utf-8")) \
        if (TMP / "m-roundF.json").is_file() else {}
    deF = mF.get("dissent_entropy") or {}
    ok27 = (rc == 0 and deF.get("shared_file_count") == 0
            and len(deF.get("independent_evidence_seats") or []) == 3
            and deF.get("devils_advocate_required") is False)
    s.check(27, "F6 正向：三不同文件 → 判独立、不触发魔鬼代言人轮", ok27,
            f"rc={rc}（期望 0） / shared_file_count={deF.get('shared_file_count')}（期望 0）"
            f" / 独立席位={deF.get('independent_evidence_seats')}"
            f" / devils={deF.get('devils_advocate_required')}")

    # 28 F7 基线四态（真值来自实际 round1 / round2 产物）
    #   ok 的 D=5 而非 6：EX-03 在 ADJ-001 上弃权，可比对 pair 只剩 2+3=5。
    #   这个数字是实测的；若哪天变成 6，说明立场反推或弃权处理被改动了，须回查。
    st = {}
    for tag, extra in (("ok", ["--prev", "runs/round2"]),
                       ("no_seat", ["--prev", "runs/round1"]),
                       ("none", [])):
        p = TMP / f"f7-{tag}.json"
        run(["metrics.py", "--round-dir", "runs/round2",
             "--spec", "fixtures/spec/adjudication.json", "--out", f"{p}"] + extra, PKG)
        st[tag] = (json.loads(p.read_text(encoding="utf-8")).get("flip_rate") or {}) \
            if p.is_file() else {}
    ok28 = (st["ok"].get("status") == "ok"
            and st["ok"].get("denominator") == 5
            and st["ok"].get("value") == 0.0
            and st["no_seat"].get("status") == "baseline_no_compliant_seat"
            and st["none"].get("status") == "not_provided")
    s.check(28, "F7 基线四态（ok/D=5、baseline_no_compliant_seat、not_provided）", ok28,
            f"ok: status={st['ok'].get('status')} D={st['ok'].get('denominator')}"
            f" value={st['ok'].get('value')}"
            f" | round1: {st['no_seat'].get('status')}"
            f" | 无 prev: {st['none'].get('status')}")

    # 29 F10 阶段 B：AD-01 + PM-01 首次纳入契约校验
    #   ★ 负向夹具必须「只因缺 conclusion 而红」——若它也因读取域越界而红，
    #     那这条就证明不了 conclusion 闸门在工作（旧版夹具正是假红）。
    rc8, out8 = run(SC + ["--stage", "B", "--round-dir", f"{FIXT}/round8",
                          "--out", f"{TMP}/stage-b-ok.json",
                          "--rd-out", f"{TMP}/stage-b-ok-rd.json"], PKG)
    rc9, out9 = run(SC + ["--stage", "B", "--round-dir", f"{FIXT}/round9",
                          "--out", f"{TMP}/stage-b-bad.json",
                          "--rd-out", f"{TMP}/stage-b-bad-rd.json"], PKG)
    ok29 = (rc8 == 0 and "verdict=all_valid" in out8
            and rc9 == 1 and "E_MISSING_CONCLUSION" in out9
            and "E_READ_DOMAIN_VIOLATION" not in out9)
    s.check(29, "F10 阶段 B（AD-01+PM-01）：合规放行 / 缺 conclusion 否决", ok29,
            f"round8 rc={rc8} all_valid={'verdict=all_valid' in out8}"
            f" | round9 rc={rc9} 缺conclusion={'E_MISSING_CONCLUSION' in out9}"
            f" 无读取域假红={'E_READ_DOMAIN_VIOLATION' not in out9}")

    # ============ 第 30–36 项：判断轴自由度（F15）/ 记录覆盖（F20）/ 指针指纹（F17）/ 预算（F18）============
    sys.path.insert(0, str(PKG / "judges"))
    import importlib
    scm = importlib.import_module("seat_check")
    vfm = importlib.import_module("verify")

    # 30 判断项数量与退役项（F15 的结构性断言）
    #    ★ 这一项填的是「没有空号」这个坑：合并轴之后 spec 必须**恰好**两项，
    #      且 ADJ-003 不得以任何形式复活（复活会让 4 组合互相冲突的老毛病回来）。
    _raw = (PKG / "fixtures/spec/adjudication.json").read_text(encoding="utf-8")
    _ids = [it.get("id") for it in (scm.load_json(PKG / "fixtures/spec/adjudication.json") or {}).get("items", [])]
    #    ★ 断言用 JSON 键形式而不是裸串：spec 正文里有一句「如已退役的 ADJ-003」
    #      （record_format.must_cover 的说明），裸串匹配会把这句说明误判成复活。
    _revived = '"id": "ADJ-003"' in _raw
    ok30 = _ids == ["ADJ-001", "ADJ-002"] and not _revived
    s.check(30, "判断轴恰好 2 项且 ADJ-003 未复活", ok30,
            f"items={_ids} / 出现 ADJ-003 的 item 定义 = {_revived}")

    # 31 单轴 4 选项自洽 + 8 组合全部可判定（纯内存，零写盘）
    #    ★ 这是 F15 的**本体断言**：旧设计 4 种组合只有 1 种能过，
    #      意味着「人只能签出机器已算出的答案」。现在 8 种（2×4）必须全部可判定。
    adj = scm.load_json(PKG / "fixtures/spec/adjudication.json")
    d_item = next((it for it in adj["items"] if it["id"] == "ADJ-002"), None) or {}
    opts = d_item.get("options") or []
    ids_ok = bool(opts) and all(len(o["implies"]["diffs"]) == int(o["id"]) for o in opts)
    distinct = len({tuple(o["implies"]["diffs"]) for o in opts}) == 4
    id_set = sorted(o["id"] for o in opts) == ["4", "5", "6", "7"]
    a_item = next((it for it in adj["items"] if it["id"] == "ADJ-001"), {})

    combos, combo_fail = 0, []
    for o1 in (a_item.get("options") or []):
        for o2 in opts:
            rec = {"source": "human_adjudication", "recorded_by": "x",
                   "decisions": [{"id": "ADJ-001", "chosen": o1["id"]},
                                 {"id": "ADJ-002", "chosen": o2["id"]}]}
            checks, _prov = vfm.materialize_adjudication(adj, rec)
            ans = {"summary": {}}
            for c in checks:
                if c["path"] == "diffs":
                    ans["diffs"] = [{"id": t} for t in c["expected"]]
                elif c["path"].startswith("summary."):
                    ans["summary"][c["path"].split(".", 1)[1]] = c["expected"]
                else:
                    ans[c["path"]] = c["expected"]
            res = vfm.run_checks(checks, ans)
            combos += 1
            if not all(r["ok"] for r in res):
                combo_fail.append(f"{o1['id']}×{o2['id']}")
    ok31 = ids_ok and distinct and id_set and combos == 8 and not combo_fail
    s.check(31, "判断轴：4 选项自洽 + 8 组合全部可判定", ok31,
            f"len==id:{ids_ok} / 互不相同:{distinct} / id 集:{id_set} / "
            f"组合 {combos}（期望 8） / 失败组合:{combo_fail or '无'}")

    # 32 人类裁决记录覆盖性（负向）：缺项 / 含已退役 id
    #    ★ L-0008：负向必须断言「因何失败」，不能只看退出码
    ans_ok = f"{ROUND1}/answer-final.json"
    rec_missing = {"source": "human_adjudication", "recorded_by": "x",
                   "decisions": [{"id": "ADJ-001", "chosen": "exclude"}]}
    rec_retired = {"source": "human_adjudication", "recorded_by": "x",
                   "decisions": [{"id": "ADJ-001", "chosen": "exclude"},
                                 {"id": "ADJ-002", "chosen": "7"},
                                 {"id": "ADJ-003", "chosen": "as_diff"}]}
    p_miss = TMP / "rec-missing.json"
    p_ret = TMP / "rec-retired.json"
    for pth, obj in ((p_miss, rec_missing), (p_ret, rec_retired)):
        with open(pth, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    rc_m, out_m = run(judge + ["--spec-dir", "fixtures/spec", "--record",
                               str(p_miss), "--answer", ans_ok], PKG)
    rc_r, out_r = run(judge + ["--spec-dir", "fixtures/spec", "--record",
                               str(p_ret), "--answer", ans_ok], PKG)
    ok32 = (rc_m == 2 and "ADJ-002" in out_m
            and rc_r == 2 and "ADJ-003" in out_r)
    s.check(32, "裁决记录覆盖性（缺项 / 含已退役 id → exit 2 且点名）", ok32,
            f"缺项 rc={rc_m} 点名={('ADJ-002' in out_m)} / "
            f"退役 rc={rc_r} 点名={('ADJ-003' in out_r)}")

    # 33 round2 裁决材料齐备 + 模板不漂移
    pen = PKG / "runs/round2/pending-adjudication.json"
    tpl = PKG / "runs/round2/adjudication-record.TEMPLATE.md"
    pen_ids = [p.get("id") for p in (json.loads(pen.read_text(encoding="utf-8")).get("pending") or [])] \
        if pen.is_file() else []
    tpl_txt = tpl.read_text(encoding="utf-8") if tpl.is_file() else ""
    regen = TMP / "tpl-regen.md"
    run(["judges/seat_check.py", "--round-dir", "runs/round2",
         "--params", "config/params.json", "--spec", "fixtures/spec/adjudication.json",
         "--emit-record-template", str(regen)], PKG)
    same = regen.is_file() and tpl.is_file() and regen.read_bytes() == tpl.read_bytes()
    tpl_ok = all(k in tpl_txt for k in ("ADJ-001", "ADJ-002", "`7`", "`6`", "`5`", "`4`",
                                        "--spec-dir", "source"))
    ok33 = pen_ids == ["ADJ-001", "ADJ-002"] and tpl_ok and same
    s.check(33, "round2 裁决材料齐备 + 模板可逐字节重现", ok33,
            f"pending ids={pen_ids} / 模板要素齐备={tpl_ok} / 重生成逐字节相同={same}")

    # 34 pointer_id 定义式（F17）正负向
    pkg_dir = scm._pkg_root()
    pcand = (pkg_dir,)
    pid = lambda x: scm.pointer_id_of(x, pkg_dir, pcand)
    ok34 = (pid("./fixtures/ledger.csv#T101") == pid("fixtures/ledger.csv#T101")
            and pid("fixtures/ledger.csv#T101") != pid("fixtures/ledger.csv#T102")
            and pid("fixtures/ledger.csv#T101") != pid("fixtures/bill.csv#T101")
            and pid("fixtures/ledger.csv#T101 + fixtures/bill.csv#T103")
            == pid("fixtures/bill.csv#T103 + fixtures/ledger.csv#T101"))
    s.check(34, "pointer_id 只含语义（路径写法归一 / 选择子与文件区分 / 顺序无关）", ok34,
            "路径写法同 id / 选择子异 id / 文件异 id / 多指针顺序无关 = "
            f"{ok34}")

    # 35 空转抗改写（F17 实效）：同一批证据只改路径写法 → 必须仍判空转
    d3 = TMP / "roundD3"
    d3.mkdir(parents=True, exist_ok=True)
    for fn in ("round-manifest.json", "EX-01.json", "EX-02.json", "EX-03.json"):
        srcf = TMP_FIXT / "roundD2" / fn
        if not srcf.is_file():
            continue
        data = json.loads(srcf.read_text(encoding="utf-8"))
        for e in (data.get("evidence") or []):
            p = str(e.get("pointer") or "")
            if p and not p.startswith("./"):
                e["pointer"] = "./" + p
        with open(d3 / fn, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    run(SC + ["--round-dir", str(d3)], PKG)
    rc, out = run(["metrics.py", "--round-dir", str(d3), "--prev", f"{FIXT}/roundD2",
                   "--spec", "fixtures/spec/adjudication.json"], PKG)
    ok35 = rc == 4 and "terminate_branch" in out
    s.check(35, "空转抗改写（同证据改路径写法 → 仍判空转、exit 4）", ok35,
            f"rc={rc}（期望 4） / 含 terminate_branch = {'terminate_branch' in out}")

    # 36 预算声明闸门（F18）：超限 / 未申报 / 非法值
    g_over = TMP / "roundG"
    g_bad = TMP / "roundGbad"
    for dest, usage in ((g_over, {"tokens": 999999, "minutes": 5, "source": "self_report"}),
                        (g_bad, {"tokens": -1, "minutes": 5})):
        dest.mkdir(parents=True, exist_ok=True)
        for fn in ("round-manifest.json", "EX-01.json", "EX-02.json", "EX-03.json"):
            srcf = TMP_FIXT / "roundA" / fn
            if not srcf.is_file():
                continue
            data = json.loads(srcf.read_text(encoding="utf-8"))
            if fn.startswith("EX-"):
                data["usage"] = dict(usage)
            with open(dest / fn, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
    p_b1 = TMP / "budget-over.json"
    # ★ 必须先跑 seat_check 生成 seat-check.json —— metrics 读它，缺则直接报错（退 2）。
    run(SC + ["--round-dir", str(g_over)], PKG)
    rc1, _o1 = run(["metrics.py", "--round-dir", str(g_over),
                    "--spec", "fixtures/spec/adjudication.json", "--out", str(p_b1)], PKG)
    b1 = json.loads(p_b1.read_text(encoding="utf-8")).get("budget") if p_b1.is_file() else {}
    rc_bad, out_bad = run(SC + ["--round-dir", str(g_bad)], PKG)
    p_b2 = TMP / "budget-none.json"
    rc2, _o2 = run(["metrics.py", "--round-dir", f"{FIXT}/roundA",
                    "--spec", "fixtures/spec/adjudication.json", "--out", str(p_b2)], PKG)
    b2 = json.loads(p_b2.read_text(encoding="utf-8")).get("budget") if p_b2.is_file() else {}
    ok36 = ((rc1 == 4 and (b1 or {}).get("status") == "exhausted")
            and (b2 or {}).get("status") == "unverifiable" and rc2 == 0
            and rc_bad == 1 and "E_BAD_USAGE" in out_bad)
    s.check(36, "预算声明闸门（超限→exit4 / 未申报→unverifiable 不判超支 / 负值→E_BAD_USAGE）", ok36,
            f"超限 rc={rc1} status={(b1 or {}).get('status')} / "
            f"未申报 rc={rc2} status={(b2 or {}).get('status')} / "
            f"负值 rc={rc_bad} E_BAD_USAGE={'E_BAD_USAGE' in out_bad}")

    # ==================== 第 37 项：第 3 轮前置体检 ====================
    # ★ 为什么要有这一项：B1/B2 的形态是「代码硬性要求，模板却没写」——
    #   这类缺口必须**在开轮之前**被判红，而不是等 6 个席位跑完才发现 exit 5。
    #   一个只在跑第 3 轮时才暴露的缺口，等于把回归的价值让给了运气。
    _tpl_usage = {}
    for _s in ("xj-01", "ad-01", "pm-01"):
        _p = ROOT / "templates" / f"{_s}.md"
        _tpl_usage[_s] = _p.is_file() and "usage" in _p.read_text(encoding="utf-8")
    _prompts = sorted((TMP / "nr2/prompts").glob("*.md"))
    _stems = sorted(p.stem for p in _prompts)
    _want_stems = ["AD-01", "EX-01", "EX-02", "EX-03", "PM-01", "XJ-01"]
    _ad_p = TMP / "nr2/prompts/AD-01.md"
    _ad = _ad_p.read_text(encoding="utf-8") if _ad_p.is_file() else ""
    _ad_clean = ("pack-ANON" not in _ad) and ('"conclusion"' not in _ad)
    _ad_decl = "不包含任何席位的结论摘要" in _ad
    # ★ F25：净化必须覆盖【全部历史轮次】，不能只擦上一轮。
    #   round3 实测：pack 里残留 runs/round1/adjudicated.json（该文件含 verdict 与期望值），
    #   而 redaction 的 targets 只有 runs/round2/ —— 禁读清单自己把答案路径递进了辩论包。
    sys.path.insert(0, str(PKG))
    nrm = importlib.import_module("new_round")
    _tgt = nrm.read_domain_targets(PKG / "runs/round2", "round3",
                                   rd_config=PKG / "config/read_domains.json")
    _hist = [t for t in _tgt if t.startswith("runs/round")]
    _f25 = ("runs/round1/" in _hist and "runs/round2/" in _hist
            and "runs/round3/" not in _hist)
    ok37 = (all(_tpl_usage.values()) and _stems == _want_stems
            and _ad_clean and _ad_decl and _f25)
    s.check(37, "第 3 轮前置体检（六模板含 usage / 六席提示词齐备 / AD-01 未被污染 / 净化覆盖全历史）", ok37,
            f"模板含 usage={_tpl_usage} / 提示词={_stems} / "
            f"AD-01 无同伴摘要={_ad_clean} / 含独立性声明={_ad_decl} / "
            f"净化目标(历史轮次)={_hist} / F25={_f25}")

    # ============ 第 38–44 项：P10（fail-closed / F28 事实表 / F27 预算 / 状态文件 / 净化 / 时序）============

    # 38 模板生成 fail-closed：夹具缺失 → exit 2 且**不写文件**
    #    ★ 输出一张「看起来权威但数字可能错」的表，比不输出危险一个量级 ——
    #      签字人不会去核 CSV，他签的正是「这部分我不需要判断」。
    #    ★ 断言「文件未创建」：若 fail-closed 被破坏，上次跑留下的文件会让
    #      本项持续红 —— 那是「修好了才能绿」的正确语义，不是假红。
    _bad_fixt = TMP / "p10-empty-fixtures"
    _bad_fixt.mkdir(parents=True, exist_ok=True)
    _bad_tpl = _bad_fixt / "should-not-exist.md"
    rc38, out38 = run(SC + ["--round-dir", "runs/round3",
                            "--fixtures-dir", str(_bad_fixt),
                            "--emit-record-template", str(_bad_tpl)], PKG)
    ok38 = (rc38 == 2 and not _bad_tpl.is_file() and "拒绝生成裁决模板" in out38)
    s.check(38, "模板生成 fail-closed（夹具缺失 → exit 2 且不写文件）", ok38,
            f"rc={rc38}（期望 2） / 文件未创建={not _bad_tpl.is_file()} / "
            f"报拒绝={'拒绝生成裁决模板' in out38}")

    # 39 F28：裁决模板的事实表必须由 CSV 派生，且旧错误数字不得残留
    #    ★ 这一项直接捕获 F28 —— 旧模板写死 T103=2130.00 / T107=520.00，
    #      账单侧还用「×2」代替合计（实际是两行各 1200.00）。而此前**没有任何
    #      回归项**能发现那张表是错的，于是人一直在错数上签字。
    _tpl_p = TMP / "p10-tpl.md"
    rc, out = run(SC + ["--round-dir", "runs/round3",
                        "--emit-record-template", str(_tpl_p)], PKG)
    _tpl = _tpl_p.read_text(encoding="utf-8") if _tpl_p.is_file() else ""
    _finfo, _fwhy = scm.derive_fact_rows(PKG / "fixtures")
    _frows = {r["id"]: r for r in (_finfo or {}).get("rows", [])}
    _f103, _f107 = _frows.get("T103", {}), _frows.get("T107", {})
    _tab_ok = all(
        f"| {r['id']} | {r['l_amount']} | {r['l_dates']} | {r['l_rows']} | {r['l_tax']} "
        f"| {r['b_amount']} | {r['b_dates']} | {r['b_rows']} | {r['b_tax']} |" in _tpl
        for r in _frows.values()) if _frows else False
    ok39 = (rc == 0 and _fwhy is None and len(_frows) == 7 and _tab_ok
            and "2130.00" not in _tpl and "520.00" not in _tpl
            and _f103.get("l_amount") == "1200.00"
            and _f103.get("b_amount") == "2400.00" and _f103.get("b_rows") == 2
            and _f107.get("l_amount") == "500.00" and _f107.get("b_rows") == 0)
    s.check(39, "事实表由 CSV 派生（T103=1200/账单2400×2行、T107=500/0行；旧错数已消失）", ok39,
            f"rc={rc} 派生why={_fwhy} 行数={len(_frows)} 逐格命中={_tab_ok} / "
            f"残留2130.00={'2130.00' in _tpl} 残留520.00={'520.00' in _tpl}")

    # 40 F28：§1 只放可观测值，不含归类名；归类降格到 §1.2 并明标「不是事实」
    #    ★ 旧表把「口径差」「跨月归属」放在标题「不可争议的事实」之下，
    #      而 ADJ-001/002 问的正是这两类算不算差异 —— 表在命名上已替人做完归类。
    _sec1 = ""
    if "## 1 ·" in _tpl:
        _pa = _tpl.index("## 1 ·")
        _pb = _tpl.find("## 1.2 ·", _pa)
        _sec1 = _tpl[_pa:_pb] if _pb > 0 else ""
    _labels = ("舍入差", "重复计", "口径差", "口径差异", "跨月归属", "真实缺失")
    _bad_labels = [x for x in _labels if x in _sec1]
    _interp, _iw = scm._load_interpretations(PKG / "fixtures")
    _ap_set = set()
    for _itx in (_interp or []):
        _ap_set |= set(_itx["applies_to"])
    _inv_derived = "由本项的 4 个选项机械推导" in _tpl
    ok40 = (bool(_sec1) and not _bad_labels
            and "## 1.2 · 可能的解释（这是解释，不是事实）" in _tpl
            and "不是结论" in _tpl and "不是事实" in _tpl
            and "由你在第 2、3 节自行判断" in _tpl
            and _ap_set == set(_frows) and _inv_derived)
    s.check(40, "§1 只含观测（无归类名）/ §1.2 明标「解释非事实」/ 恒在项由 spec 推导", ok40,
            f"§1 长={len(_sec1)} 命中归类词={_bad_labels} / "
            f"applies_to==派生集合={_ap_set == set(_frows)} / 恒在项由spec推导={_inv_derived}")

    # 41 F27：cap 按实测重设，且数字真的进了判据（负向对照）
    #    ★ 只断言 cap==160000 是装饰品；必须证明同一份数据在旧 cap 下判 exhausted。
    _six_rows = [("EX-01", 9800, 9.5), ("EX-02", 21000, 9), ("EX-03", 23500, 14),
                 ("XJ-01", 16800, 11), ("AD-01", 31000, 13.0), ("PM-01", 24000, 21.0)]
    _brep = {"results": [{"seat": s0, "derived": {"usage": {
        "tokens": t0, "minutes": m0, "source": "self_report"}}} for s0, t0, m0 in _six_rows]}
    _prm = json.loads((PKG / "config/params.json").read_text(encoding="utf-8"))
    _bnew = scm.evaluate_budget(_brep, {"cost_budget": _prm["cost_budget"]})
    _bold = scm.evaluate_budget(_brep, {"cost_budget": {
        **_prm["cost_budget"], "per_task_token_hard_cap": 80000}})
    ok41 = (_bnew["tokens_total"] == 126100 and _bnew["status"] == "ok"
            and _bnew["minutes_span"] == 21.0
            and _bold["status"] == "exhausted"
            and _prm["cost_budget"]["per_task_token_hard_cap"] == 160000
            and "_semantic" in _prm["cost_budget"]
            and "126100" in _prm["cost_budget"]["_basis"]
            and "round3" in _prm["cost_budget"]["_basis"]
            and "tokens_definition" in _bnew)
    s.check(41, "预算按实测重设（126100→ok；同数据配旧cap80000→exhausted 负向对照）", ok41,
            f"实测 tokens={_bnew['tokens_total']} status={_bnew['status']} / "
            f"旧cap status={_bold['status']} / cap={_prm['cost_budget']['per_task_token_hard_cap']} / "
            f"_basis 含实测={'126100' in _prm['cost_budget']['_basis']} / "
            f"输出含定义式={'tokens_definition' in _bnew}")

    # 42 待裁状态文件：与 answer 的 sha 绑定 + 四态自洽 + stale 必拒
    #    ★ D-0013：改在【临时工作副本】上测机制属性。旧版写死「round3 必须 pending」，
    #      round3 一旦签署（2026-10-20）该项就红 —— 红的不是机制，是世界状态变了。
    #      回归测性质，不测快照。
    _sdir = TMP / "p42-live"
    _sdir.mkdir(parents=True, exist_ok=True)
    for _fn in ("seat-check.json", "answer-final.json", "PM-01.json",
                "round-manifest.json", "pending-adjudication.json"):
        _srcf = PKG / "runs/round3" / _fn
        if _srcf.is_file():
            (_sdir / _fn).write_text(_srcf.read_text(encoding="utf-8"),
                                     encoding="utf-8", newline="\n")
    # ① 副本上重生成状态文件（无 --record → pending 四态），sha 绑定必须自洽
    run(["judges/verify.py", "--spec-dir", "fixtures/spec",
         "--answer", "../_trash/tmp-regress/p42-live/answer-final.json",
         "--status-out", "../_trash/tmp-regress/p42-live/answer-status.json",
         "--status-provenance", "post_hoc_derived",
         "--out", "../_trash/tmp-regress/p42-live/_v.json"], PKG)
    _stf = _sdir / "answer-status.json"
    _st = json.loads(_stf.read_text(encoding="utf-8")) if _stf.is_file() else {}
    _afsha = sha(_sdir / "answer-final.json")
    _st_ok = (_st.get("answer", {}).get("sha256") == _afsha
              and _st.get("adjudication_state") == "pending"
              and _st.get("pending_items") == ["ADJ-001", "ADJ-002"]
              and _st.get("provenance") == "post_hoc_derived")
    # ② 篡改副本 answer 的绑定 sha → --verify-final 必拒
    _stbad = json.loads(json.dumps(_st)) if _st else {}
    _stbad.setdefault("answer", {})["sha256"] = "0" * 64
    (_sdir / "answer-status.json").write_text(
        json.dumps(_stbad, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    rc_st, out_st = run(SC + ["--round-dir", str(_sdir), "--verify-final"], PKG)
    ok42 = (_st_ok and rc_st == 1 and "answer.sha256 不匹配" in out_st)
    s.check(42, "待裁状态文件与 answer 的 sha 绑定（临时副本）；sha 被改 → --verify-final 必拒", ok42,
            f"副本自洽={_st_ok} / 篡改后 rc={rc_st}（期望 1） / "
            f"报出 sha 不匹配={'answer.sha256 不匹配' in out_st}")

    # 43 答案引用净化：正向擦除 / 负向不误伤立场与原始值 / 幂等
    _adjj = json.loads((PKG / "fixtures/spec/adjudication.json").read_text(encoding="utf-8"))
    _pos1, _ = nrm.scrub_answer_citations(
        "truth.json 的 rules 与 expected 已把两个判断写成机器可读真值，偏离即 JD-01 fail。", _adjj)
    _pos2, _ = nrm.scrub_answer_citations("rules 明定口径差不计入，expected=500.02/180.0/7", _adjj)
    _neg1, _ = nrm.scrub_answer_citations(
        "我取 exclude（500.02/180.00），依据是 tax_included 列翻转", _adjj)
    _neg2, _ = nrm.scrub_answer_citations("T104 台账 2120.00、账单 2000.00，差 120.00", _adjj)
    _neg3, _ = nrm.scrub_answer_citations("DIFFSET 取 7，清单含全部七条", _adjj)
    _again, _agst = nrm.scrub_answer_citations(_pos1, _adjj)
    ok43 = ("truth.json" not in _pos1 and "expected" not in _pos1.lower()
            and "[答案来源引用已净化]" in _pos1
            and "500.02" not in _pos2 and "expected" not in _pos2
            and _neg1 == "我取 exclude（500.02/180.00），依据是 tax_included 列翻转"
            and _neg2 == "T104 台账 2120.00、账单 2000.00，差 120.00"
            and _neg3 == "DIFFSET 取 7，清单含全部七条"
            and _again == _pos1 and _agst["clauses_replaced"] == 0)
    s.check(43, "答案引用净化（引用被擦 / 立场与原始值不误伤 / 二次净化零改动）", ok43,
            f"正向1 含truth={'truth.json' in _pos1} / 正向2 含500.02={'500.02' in _pos2} / "
            f"负向立场保留={_neg1 == '我取 exclude（500.02/180.00），依据是 tax_included 列翻转'} / "
            f"负向原值保留={_neg2 == 'T104 台账 2120.00、账单 2000.00，差 120.00'} / "
            f"幂等={_again == _pos1 and _agst['clauses_replaced'] == 0}")

    # 44 审计时序锚定（第 1/7 查）：缺字段→invalid / ts 不符→invalid / 相符→valid / v2.0 不追溯
    #    ★ 断言必须说明【因何】失败（L-0008），不能只断言 status=invalid ——
    #      否则任何别的校验错误都会让它假绿。
    _regx = scm.build_axis_registry(_adjj, _prm)
    _adbase = json.loads((PKG / "runs/round3/AD-01.json").read_text(encoding="utf-8"))
    _vlj = json.loads((PKG / "runs/round3/verdict-layered.json").read_text(encoding="utf-8"))
    _pndj = json.loads((PKG / "runs/round3/pending-adjudication.json").read_text(encoding="utf-8"))
    _good_ai = [
        {"path": "runs/round3/verdict-layered.json", "verdict": _vlj["verdict"],
         "spec_layer": _vlj["spec_layer"], "ts": _vlj["ts"]},
        {"path": "runs/round3/pending-adjudication.json", "verdict": _pndj["verdict"],
         "spec_layer": _pndj["spec_layer"],
         "pending_count": _pndj["summary"]["pending"], "ts": _pndj["ts"]},
    ]
    _ctx_on = {"audit_pin_required": True, "pkg_root": PKG,
               "round_dir": str(PKG / "runs/round3"), "reads_required": True,
               "rd": None, "usage_required": True, "roots": ()}
    _ctx_off = dict(_ctx_on, audit_pin_required=False)

    def _audit_errs(ai, ctx):
        a = dict(_adbase)
        if ai is None:
            a.pop("audited_inputs", None)
        else:
            a["audited_inputs"] = ai
        r = scm.check_seat("AD-01", a, _prm, _regx, "revision_round", ctx)
        return [e["code"] for e in (r.get("errors") or [])]

    _eA = _audit_errs(None, _ctx_on)
    _eB = _audit_errs([dict(_good_ai[0], ts="1999-01-01T00:00:00+08:00"), _good_ai[1]], _ctx_on)
    _eC = _audit_errs(_good_ai, _ctx_on)
    _eD = _audit_errs(None, _ctx_off)
    ok44 = ("E_AUDIT_INPUT_MISSING" in _eA
            and "E_AUDIT_INPUT_STALE" in _eB
            and not any(c.startswith("E_AUDIT_INPUT") for c in _eC)
            and not any(c.startswith("E_AUDIT_INPUT") for c in _eD))
    s.check(44, "审计时序锚定（缺字段/ts 不符 → 拒；相符 → 通过；v2.0 不追溯）", ok44,
            f"缺字段={_eA} / ts错={_eB} / 正确={_eC} / 关闸={_eD}")

    # ==================== 第 45 项：002 判据可加载且未裁决 → pending ====================
    # ★ D-0010：第二个任务（券商交割单 ↔ 自记账）夹具在 fixtures/002/，spec 与 001 物理分离。
    #   此条保证它保持「可硬裁判验证 + 未裁决时 exit 3」——删掉 002 或判据损坏会立刻红。
    _t002 = json.loads((PKG / "fixtures/002/truth.json").read_text(encoding="utf-8"))["expected"]
    _a002 = {
        "summary": {
            "ledger_rows": _t002["ledger_rows"], "bill_rows": _t002["bill_rows"],
            "ledger_total": _t002["ledger_total"], "bill_total": _t002["bill_total"],
            "raw_total_gap": _t002["raw_total_gap"],
            "dedup_removed_txn_count": _t002["dedup_removed_txn_count"],
            "dedup_amount": _t002["dedup_amount"],
            "diff_count": _t002["diff_count"],
            "amount_diff_total": _t002["amount_diff_total"],
            "excluded_amount_total": _t002["excluded_amount_total"],
            "excluded_categories": ["交易费用差异"],
        },
        "diffs": [{"id": i, "category": "待裁决"} for i in _t002["diff_ids"]],
        "categories": {"待裁决": _t002["diff_ids"]},
        "falsification": "若券商费率口径与自记账一致且不存在未入账费用，则本结论不成立。",
    }
    (TMP / "answer-002.json").write_text(
        json.dumps(_a002, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    rc, out = run(["judges/verify.py", "--spec-dir", "fixtures/002/spec",
                   "--answer", "../_trash/tmp-regress/answer-002.json",
                   "--out", "../_trash/tmp-regress/verdict-002.json"], PKG)
    s.expect(45, "002 判据可加载（算术 10/10 + 判断层 2 项 pending → exit 3）",
             rc, out, 3, "pending_adjudication")

    # ==================== 第 46 项：--task 002 提示词只列 002 的输入 ====================
    # ★ 防「任务串台」：allow 在 read_domains.json 里是全局的，new_round --task 负责
    #   按任务改写提示词的「你的读取域」。若有人改回全局直拼，席位会拿到两个任务的
    #   输入清单而校验不报警 —— 这条就是把那次事故（D-0010 审计发现）固化成回归。
    rc, out = run(["new_round.py", "--root", "..", "--runs-dir", "../_trash/tmp-regress",
             "--preflight", "off",
                   "--round", "t002p", "--role", "evidence_round", "--task", "002",
                   "--emit-prompts"], PKG)
    _p002 = (ROOT / "_trash/tmp-regress/t002p/prompts/EX-01.md").read_text(
        encoding="utf-8") if rc == 0 else ""
    # ★ --round 非纯数字时不加 round 前缀（与 --check 同一规则），目录名就是 t002p
    _m002 = json.loads((ROOT / "_trash/tmp-regress/t002p/round-manifest.json")
                       .read_text(encoding="utf-8")) if rc == 0 else {}
    ok47 = (rc == 0
            and "- `fixtures/002/TASK.md`" in _p002
            and "- `fixtures/002/ledger.csv`" in _p002
            and "- `fixtures/TASK.md`" not in _p002          # 001 输入不得混入
            and "- `fixtures/ledger.csv`" not in _p002
            and _m002.get("task") == "002")
    s.check(46, "--task 002 的提示词只列 002 的输入（防任务串台）", ok47,
            f"rc={rc} / 含002输入={'- `fixtures/002/TASK.md`' in _p002} / "
            f"混入001={'- `fixtures/TASK.md`' in _p002} / manifest.task={_m002.get('task')}")

    # ==================== 第 47 项：--task 缺省 = 001，行为不变 ====================
    # ★ 防回归：任务参数化不得改变历史轮次的行为。缺省时提示词必须仍是根 fixtures/，
    #   且 002 的文件不得出现在 allow 段（deny 段出现属正常 —— 全局禁读清单）。
    rc, out = run(["new_round.py", "--root", "..", "--runs-dir", "../_trash/tmp-regress",
             "--preflight", "off",
                   "--round", "t001p", "--role", "evidence_round", "--emit-prompts"], PKG)
    _p001 = (ROOT / "_trash/tmp-regress/t001p/prompts/EX-01.md").read_text(
        encoding="utf-8") if rc == 0 else ""
    ok48 = (rc == 0
            and "- `fixtures/ledger.csv`" in _p001
            and "fixtures/002/TASK.md" not in _p001
            and "fixtures/002/ledger.csv" not in _p001)
    s.check(47, "--task 缺省 = 001（提示词仍是根 fixtures 输入，002 不混入）", ok48,
            f"rc={rc} / 含001输入={'- `fixtures/ledger.csv`' in _p001} / "
            f"混入002={'fixtures/002/ledger.csv' in _p001}")

    # ==================== 第 48 项：软裁判家族核验（providerData，不采信自报） ====================
    # ★ D-0012 遗留项落地。round4 实测：default 路由令六席全 GLM，XJ-01 跨家族前提
    #   失效而无人知晓 —— 因为此前第 1 查只看自报。本项把「家族核验必须来自运行时
    #   providerData」固化成回归：同家族 → cross_family=False；异家族 → True。
    _sdir48 = TMP / "p48-family"
    _sdir48.mkdir(parents=True, exist_ok=True)
    for _fn in ("seat-check.json", "answer-final.json", "PM-01.json",
                "round-manifest.json", "pending-adjudication.json"):
        _srcf = PKG / "runs/round3" / _fn
        if _srcf.is_file():
            (_sdir48 / _fn).write_text(_srcf.read_text(encoding="utf-8"),
                                       encoding="utf-8", newline="\n")
    def _fam48(xj_mid, ex_mid):
        rt = {"seat-xj01": xj_mid, "seat-ex01": ex_mid, "seat-ex02": ex_mid}
        (TMP / "p48-routing.json").write_text(
            json.dumps({"subagents": [
                {"raw": {"providerData.agent": n, "providerData.requestModelId": m}}
                for n, m in rt.items()]}, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
        run(SC + ["--round-dir", str(_sdir48),
                  "--routing-json", "../_trash/tmp-regress/p48-routing.json"], PKG)
        _rep = json.loads((_sdir48 / "seat-check.json").read_text(encoding="utf-8"))
        return (_rep.get("xj_family_check") or {}).get("cross_family")
    _same = _fam48("glm-5.3-flash", "glm-5.3-flash")       # XJ 与 EX 同家族
    _cross = _fam48("hy4-preview", "deepseek-v4.1-flash")  # XJ 与 EX 异家族
    ok48 = (_same is False and _cross is True)
    s.check(48, "软裁判家族核验（providerData 注入：同家族→False / 异家族→True）", ok48,
            f"同家族 cross_family={_same}（期望 False） / 异家族 cross_family={_cross}（期望 True）")

    # ==================== 第 49 项：20 条留出任务批自检 ====================
    # ★ 扩样（D-0012/扩样草案）：make_tasks_20.py 生成的每组夹具必须能被硬裁判加载
    #   （算术 10/10 + 判断层 pending）。生成器改动或夹具损坏在此拦截。
    #   自检过程会在包内临时写 _selfcheck-* 文件并在结束前删除；若中途崩溃留下
    #   残留，第 54 项（幂等守门）会红 —— 这是特性。
    rc_sc, out_sc = run(["scripts/make_tasks_20.py", "--out", "migration-package/fixtures/tasks",
                         "--self-check"], ROOT)
    s.check(49, "20 条留出任务批自检（生成器 + 每组 算术10/10 + pending）",
            rc_sc == 0 and "20/20" in out_sc,
            f"rc={rc_sc} / 20/20={'20/20' in out_sc}")

    # ==================== 第 50 项：L3 前提探针 ====================
    # ★ 2026-10-23 覆盖率基线发现：premise_probe.py 从未被任何自动测试执行（0%）。
    #   L3 前提审查是方案里唯一的新意，却只靠 --calibrate 的 20 条自标样本"证明"。
    #   这里用一张坏卡与一张好卡把判定与退出码钉住 —— 只写 _trash，不碰包内。
    bad_card = {"decision": "做一件事", "beneficiary": "我们", "metric": "无",
                "do_nothing_cost": "待定"}
    good_card = {"decision": "给发布流程接 CI",
                 "beneficiary": "负责夜间发布的值班同学",
                 "metric": {"name": "周返工次数", "baseline": "3 次/周",
                            "method": "CI 失败记录"},
                 "do_nothing_cost": {"description": "每月约两次手工返工",
                                     "comparable": True}}
    p_bad, p_good = TMP / "premise-bad.json", TMP / "premise-good.json"
    p_bad.write_text(json.dumps(bad_card, ensure_ascii=False), encoding="utf-8")
    p_good.write_text(json.dumps(good_card, ensure_ascii=False), encoding="utf-8")
    rc_b, out_b = run(["scripts/premise_probe.py", "--card", str(p_bad)])
    rc_g, out_g = run(["scripts/premise_probe.py", "--card", str(p_good)])
    s.check(50, "L3 前提探针：坏卡命中 3 条 / 好卡 0 命中",
            rc_b == 1 and "命中 3 条" in out_b and "terminate_proposed" in out_b
            and rc_g == 0 and "命中 0 条" in out_g,
            f"坏卡 rc={rc_b} 命中3={'命中 3 条' in out_b} / "
            f"好卡 rc={rc_g} 命中0={'命中 0 条' in out_g}")

    # ==================== 第 51 项：L3 校准集未退化 ====================
    # ★ 2026-10-23（D-0052）：校准集是三条探针唯一可证伪的依据。防三件事——
    #   ① 样本被删 → n 变小；② 判据回归 → 误拦变多；
    #   ③ 钉住阈值的边界样本被删 → th=6 不再误报，敏感性分析退化成空测。
    #
    # ★★ 2026-10-26 改判据（D-0070 / D-0071）：原来卡的是 `accuracy == 1.0`，
    #   那等于把「校准集全对」当成不变量。但 D-0070 已查明该集标签与卡号前缀同构
    #   （T 组全拦 / N 组全过），F1=1.00 无区分力；且 T15『老张』2 字是具体人名却被标拦，
    #   属**已知误标**。给人名加豁免后，实现与该误标不一致（FN=1）——这是**修对**，不是退化。
    #   故判据改为：**不得误拦（fp==0，即不冤枉任何好卡）**；漏放至多 1 张（即 T15 那张误标）；
    #   th=6 仍须被边界样本推翻（保留对抗性）。
    rc_c, out_c = run(["scripts/premise_probe.py", "--calibrate", "--json"])
    rc_6, out_6 = run(["scripts/premise_probe.py", "--calibrate", "--th", "6", "--json"])
    try:
        cj, cj6 = json.loads(out_c), json.loads(out_6)
    except json.JSONDecodeError as e:
        cj, cj6 = {}, {}
        print(f"[ERR] 校准输出解析失败：{e}", file=sys.stderr)
    s.check(51, "L3 校准集未退化（n≥36、不误拦、漏放≤1 且为已知误标；th=6 仍被推翻）",
            cj.get("n", 0) >= 36 and cj.get("fp", 1) == 0 and cj.get("fn", 99) <= 1
            and cj6.get("fp", 0) >= 1,
            f"n={cj.get('n')} fp={cj.get('fp')} fn={cj.get('fn')}（1=T15 已知误标）"
            f" / th=6 fp={cj6.get('fp')}")

    # ==================== 第 52 项：24h 否决窗口载体 ====================
    # ★ 2026-10-24（D-0054）：方案里「超时视为接受」原先**无载体**——33 轮
    #   verdict-layered.json 里 veto_deadline 0 处、无脚本读取。veto_sweep 的核心承诺是
    #   「否决权不因没人跑扫描而悄悄过期」：必须显式 --settle 才算接受。
    vt = TMP / "veto"; vt.mkdir(parents=True, exist_ok=True)
    v = vt / "verdict-r34.json"
    v.write_text(json.dumps({"round": "r34", "verdict": "pass", "checks": 14}),
                 encoding="utf-8")
    r1, o1 = run(["scripts/veto_sweep.py", "--list", str(vt)])
    r2, o2 = run(["scripts/veto_sweep.py", "--stamp", str(v)])
    r3, o3 = run(["scripts/veto_sweep.py", "--list", str(vt)])
    r4, o4 = run(["scripts/veto_sweep.py", "--reject", str(v), "--who", "tester",
                  "--reason", "回归测试否决"])
    r5, o5 = run(["scripts/veto_sweep.py", "--reject", str(v), "--who", "tester",
                  "--reason", "第二次应被拒"])
    s.check(52, "24h 否决窗口：未盖戳可暴露→盖戳→窗口内可列→否决→不可重复否决",
            r1 == 0 and "未盖" in o1 and r2 == 0 and r3 == 1 and "可否决" in o3
            and r4 == 0 and "human_veto" in o4 and r5 == 2,
            f"rc={r1},{r2},{r3},{r4},{r5}")

    # ==================== 第 53 项：题库 v1 未漂移 ====================
    # ★ 2026-10-24（D-0057）：题库已冻结。改任何一份作答/真值/判分文件 =
    #   换了一张试卷，此前所有分数作废。用 sha256 锚住，而不是靠"我记得没改"。
    man = json.loads((ROOT / "audit/baseline/frozen-v1.json").read_text(encoding="utf-8"))
    drift = []
    for f in man["files"]:
        fp = ROOT / f["path"]
        if not fp.is_file():
            drift.append(f"{f['path']} 缺失")
        elif hashlib.sha256(fp.read_bytes()).hexdigest() != f["sha256"]:
            drift.append(f"{f['path']} 内容已变")
    s.check(53, f"题库 v1 未漂移（{len(man['files'])} 个文件 sha256 逐个比对）",
            not drift, "一致" if not drift else "；".join(drift[:3]))

    # ==================== 第 54 项：巡检产出元核对（阳性/阴性双样本）============
    # ★ 2026-10-25：08:05「看板巡检」prompt 规定「无异常不落文件」，于是
    #   inspection-{D}.md 缺失有两种解释（真无异常 / 未跑）。单看文件在不在
    #   无法区分——先复检昨日报告已列异常条件是否仍在（round8/9/10 终态、
    #   round13 S8-1、翻边率阈值），条件仍在而报告缺失才判「疑似未跑」。
    #   踩过的坑：aggregation_blocked 是 dict 列表，`"EX-03" in blocked` 恒 False，
    #   会把 STILL 误判成 CONTRADICTION——退出码都是 2，只看退出码发现不了，
    #   故阳性必须同时断言分支关键词（可证伪：判据坏了阳性会翻绿或换分支）。
    # ★ 2026-10-27（D-0073）：round13 的 S8-1 已修（EX-03 移出聚合输入），活数据
    #   不再提供 STILL 阳性样本。判据的可证伪性不能靠"活数据里恰好有个历史缺陷"
    #   续命——故阳性样本改为合成 round13（seat-check 原样 + inputs 仍含 EX-03），
    #   并新增「修复态」断言：活数据必须判 GONE，不得判 CONTRADICTION。
    #   （原实现 `if in_blocked != in_inputs: CONTRADICTION` 会把「blocked 含 +
    #   inputs 不含」这一修复态判成互撕，与其自述规格相反 —— 已随本次一并修正。）
    # ★ 样本目录用 TemporaryDirectory 托管：第 20 项门禁禁止源码出现删除调用
    #   （删除的唯一去向是改名到 _trash/），故不自写 rmtree，交给退出即清的临时目录。
    with tempfile.TemporaryDirectory(prefix="regress54-") as _td:
        fx_root = Path(_td)
        fx13 = fx_root / "round13"
        fx13.mkdir(parents=True, exist_ok=True)
        shutil.copy(PKG / "runs" / "round13" / "seat-check.json", fx13 / "seat-check.json")
        (fx13 / "answer-final.json").write_text(json.dumps({
            "aggregation_inputs": [
                {"seat": "EX-03",
                 "sha256": "ed2befbd66a66d221fa57ff284a3d4784c060f4f6b3aed14dab1424ce65dd583",
                 "seat_check_status": "valid"},
                {"seat": "EX-01",
                 "sha256": "2d31dc02f362f3d47b69d34ff24c270d6238536546483e49851ed408401bd90c",
                 "seat_check_status": "valid"},
            ]}), encoding="utf-8")
        r_pos, o_pos = run(["scripts/inspection_watch.py", "--date", "2099-01-01",
                            "--dry-list", "--runs", str(fx_root)])
    r_neg, o_neg = run(["scripts/inspection_watch.py", "--date", "2026-10-24",
                        "--dry-list"])
    r_live, o_live = run(["scripts/inspection_watch.py", "--date", "2099-01-01",
                          "--dry-list"])
    s.check(54, "巡检元核对：阳性(合成样本)exit2命中STILL / 阴性(报告在)exit0 / 活数据判GONE",
            r_pos == 2 and "STILL" in o_pos and "FAIL" in o_pos
            and r_neg == 0 and "PASS" in o_neg
            and "GONE" in o_live and "CONTRADICTION" not in o_live,
            f"阳性 rc={r_pos} 含STILL={'STILL' in o_pos} / 阴性 rc={r_neg} / "
            f"活数据={'GONE' if 'GONE' in o_live else '非GONE'}")

    # ==================== 第 55 项：回归幂等守门 ====================
    # ★ F11 的守门项。全部夹具项都已在上面跑完；若任何一项把机器产出写回了
    #   包内（而非 _trash/ 工作副本），包内 sha256 就会变，此条立刻变红。
    #   它把「回归套件是否自污染」从「靠人记得先重建 MANIFEST」变成可自动判定。
    #   2026-10-25：第 54 项插入后，本守门从 54 顺延为 55（沿用 D-0057 先例）。
    rc, out = run(["gen_manifest.py", "--root", ".", "--check", "MANIFEST.json"], PKG)
    # 第 50/51/54 项只读或只写 _trash，故「包内是否被污染」的判定仍可放在最后
    s.expect(55, "回归幂等（跑完后包内仍与 MANIFEST 一致）", rc, out, 0, "完全一致")

    ok_all = all(i["ok"] for i in s.items)
    npass = sum(1 for i in s.items if i["ok"])

    if args.json:
        print(json.dumps({"ok": ok_all, "passed": npass, "total": len(s.items),
                          "items": s.items}, ensure_ascii=False, indent=2))
        return 0 if ok_all else 1

    print("=" * 72)
    print("全量回归验收")
    print("=" * 72)
    for i in s.items:
        print(f"  [{('PASS' if i['ok'] else 'FAIL'):4s}] {i['no']:2d}. {i['name']}")
        print(f"          {i['detail']}")
    print("-" * 72)
    print(f"结果：{npass}/{len(s.items)} 通过")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
