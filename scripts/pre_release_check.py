#!/usr/bin/env python3
"""pre_release_check.py —— 发布前一键检查（只读，不修改任何文件）。

## 为什么存在

2026-10-20 会话审计提出 4 条建议，本脚本把其中 3 条落成可执行机制：

  建议 1「交付前跑 check_docs_consistency --strict」→ 本脚本第 A 项
  建议 3「终局结论须以穷尽检索为前提」        → 本脚本第 F、G 项（编号覆盖 + 待办扫描）
  建议 4「交付前跑 check_secrets()」           → 本脚本第 E 项（复用 make_transfer 的模式）

建议 2（改真源先备份）由 `snapshot_sources.py` 承担。

## 检查项

  A. 文档一致性    check_docs_consistency（MANIFEST 数字 / 索引行数 / 契约版本 / 计数声明）
  B. 全量回归      regress.py 必须 56/56、EXIT=0
  C. 判据自检      verify.py --self-test 必须 27/27
  D. 包完整性      MANIFEST 自校验（file_count / total_bytes 与实际一致）
  E. 密钥扫描      源码与文档不得含密钥模式
  F. 决策编号覆盖  被引用的 D-XXXX 必须有 decisions/ 下对应文件
  G. 待办扫描      文档不得残留「未修/待处理/TODO」等未完成表述
  H. 真源快照      存在最近一次 snapshot_sources 的快照（建议 2 的落地检查）

附加信息（**非门禁、不影响退出码**）：
  I. 自洽扫描      check_consistency --sweep 摘要（26 份 / 通过 / 不自洽），
                   对应交接 §6-7「每新增一轮跑一次」——放这里让它被动可见

## 用法

    python3 scripts/pre_release_check.py            # 全量检查，打印明细
    python3 scripts/pre_release_check.py -q         # 只打印结论
    python3 scripts/pre_release_check.py --self-test # 门禁自身的自检（正负样本，不动真仓库）
    退出码：0 全通过 / 1 有失败项 / 2 环境错误
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DOCS = ROOT / "docs"
PKG = ROOT / "migration-package"
PY = sys.executable

# 密钥模式：从单一真源 secret_patterns 导入（此前两处各有一份副本，改一处另一处不知道）
sys.path.insert(0, str(SCRIPTS))
from secret_patterns import scan_file  # noqa: E402

# 待办扫描：命中即视为"文档自认未完成"
TODO_PAT = re.compile(r"(?<!已)(未修|待处理|尚未完成|TODO|FIXME)")
# 这些文件按设计会记录历史问题，扫描时豁免
TODO_EXEMPT = {"会话审计-20261020.md", "审计报告-评审手册3.0rc.md", "评审手册-3.0rc.md"}


def sh(cmd, cwd=None, timeout=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def chk_docs(ctx=None):
    """文档一致性。ctx 由 main 传入前几项的实测数，用于校验文档里的 `N/N` 声明。

    ★ 为什么它现在最后跑：E 项要拿「回归 / 门禁 / 判据自检 / 接手自检」的**实跑项数**去比，
      而这些数只有跑完 B/C 才知道。挪到最后不改字母、不改 8 项的计数，
      只是执行顺序变了 —— 打印顺序仍按 A→H（main 里排序后输出）。
    """
    cmd = [PY, str(SCRIPTS / "check_docs_consistency.py"), "--strict"]
    for k in ("regress", "criteria"):
        if ctx and k in ctx:
            cmd += ["--expect", f"{k}={ctx[k]}"]
    if ctx and "gates" in ctx:
        cmd += ["--expect", f"gates={ctx['gates']}"]
    # 接手自检的项数：问脚本自己（--count 只读 CHECKS 长度），不重跑回归
    rc_h, out_h = sh([PY, str(SCRIPTS / "verify_handover.py"), "--count"])
    if rc_h == 0 and out_h.strip().isdigit():
        cmd += ["--expect", f"handover={out_h.strip()}"]
    rc, out = sh(cmd)
    ok = rc == 0
    # 失败时优先报「具体哪一行不一致」，而不是脚本末尾那句通用提示：
    # 只给提示等于告诉人"有事"却不说"在哪"，还得自己去翻输出（2026-10-23 实测）。
    problems = [ln.strip() for ln in out.splitlines() if re.match(r"^\s*\[[A-F]\]\s", ln)]
    if ok:
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1] if out.strip() else ""
        return True, tail
    return False, problems[0] if problems else (out.strip()[-160:] or "无输出")


def chk_regress():
    rc, out = sh([PY, str(SCRIPTS / "regress.py")], cwd=PKG)
    m = re.search(r"结果：(\d+)/(\d+) 通过", out)
    got = f"{m.group(1)}/{m.group(2)}" if m else "解析失败"
    # 2026-10-29：原写法 `rc == 0 and m and ...` 在解析失败时短路返回 None（不是 False）。
    # 吃 `if not ok` 的上层看不出来，但门禁自检按 `ok is False` 断言 —— 返回值一律规整为 bool。
    return bool(rc == 0 and m and m.group(1) == m.group(2)), f"{got}, EXIT={rc}"


def chk_selftest():
    # v3 Q05（用户裁 C）：audit/ 下的判定脚本此前不在任何检查里，而 make_label_swap.py
    # 会覆盖写 materials 下的 brief —— 一个语法错就能毁材料。这里只做**语法检查**，
    # 不做 lint。并入 C 项而不是新增第 9 项：8/8 是本文件自己声明的基线契约
    # （见 main() 的说明），加第 9 项等于改契约，超出「只纳入语法检查」的原意。
    bad = []
    for sp in sorted(ROOT.glob("audit/**/*.py")):
        try:
            compile(sp.read_text(encoding="utf-8"), str(sp), "exec")
        except SyntaxError as e:
            bad.append(f"{sp.relative_to(ROOT)}:{e.lineno}")
    if bad:
        return False, f"audit 脚本语法错误 {len(bad)} 处：{', '.join(bad[:3])}"

    rc, out = sh([PY, str(PKG / "judges" / "verify.py"), "--self-test"])
    m = re.search(r"(\d+)/(\d+) 项", out)
    if not m:
        # 注入测试时发现：self-test 失败会改变输出格式，初版只报「解析失败」不足以定位
        tail = next(
            (ln.strip() for ln in reversed(out.strip().splitlines()) if ln.strip()), "无输出"
        )
        return False, f"输出无法解析（EXIT={rc}，末行：{tail[:90]}）"
    return (rc == 0 and m.group(1) == m.group(2)), f"{m.group(1)}/{m.group(2)}"


def chk_manifest():
    mp = PKG / "MANIFEST.json"
    if not mp.is_file():
        return False, "MANIFEST.json 缺失"
    m = json.loads(mp.read_text(encoding="utf-8"))
    st = m.get("stats") or {}
    files = [e for e in (m.get("files") or []) if isinstance(e, dict)]
    declared_n, declared_b = st.get("file_count"), st.get("total_bytes")
    # MANIFEST 的字段名是 size（不是 bytes）——初版写错导致实际字节恒为 0
    real_b = sum(int(e.get("size") or 0) for e in files)
    ok = declared_n == len(files) and declared_b == real_b
    return ok, f"声明 {declared_n} 条/{declared_b}B，实际 {len(files)} 条/{real_b}B"


def chk_secrets():
    hits = []
    # 2026-10-29（F7）：audit/、templates/、outputs/ 原不在扫描范围。
    # 密钥防线是**按「哪些目录被扫到」定义的** —— 没被扫到的目录等于没有防线，
    # 而这三个目录恰恰装着评审输出与外发模板（最容易夹带的东西）。
    for base in (
        SCRIPTS,
        DOCS,
        ROOT / "audit",
        ROOT / "templates",
        ROOT / "outputs",
        PKG / "judges",
        PKG / "config",
    ):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {
                ".md",
                ".py",
                ".json",
                ".sh",
                ".cmd",
                ".txt",
                ".env",
            }:
                continue
            for label, frag in scan_file(p):
                hits.append(f"{p.relative_to(ROOT)}::{label}")
    return not hits, ("无命中" if not hits else f"{len(hits)} 处: " + "; ".join(hits[:3]))


# 「编号故意留空」的写法：D-XXXX 未建 / 缺失 / 保留编号 …（示例一律占位，不用真编号）
INTENTIONAL_ABSENT = re.compile(r"D-\d{4}\s*[（(]?\s*(?:未建|未创建|缺失|不存在|保留编号|未使用)")


def chk_decision_ids():
    dec = DOCS / "decisions"
    have = {m.group(0) for p in dec.glob("*.md") for m in [re.search(r"D-\d{4}", p.name)] if m}
    used, intentional = set(), set()

    # ★ 2026-10-23 修（D-0053）：根级四份文档原先**不在扫描范围**——README/ARCHITECTURE
    #   里写着「D-XXXX 未建」却从不被检查，等于这四份文档的引用是盲区。
    targets = [
        ROOT / n
        for n in ("README.md", "ARCHITECTURE.md", "DEPENDENCIES.md", "M4-接续指南.md")
        if (ROOT / n).is_file()
    ]
    for base in (DOCS, SCRIPTS, PKG / "judges", PKG / "protocol", PKG / "config"):
        if base.is_dir():
            targets += [
                p
                for p in base.rglob("*")
                if p.is_file() and p.suffix.lower() in {".md", ".py", ".json"}
            ]

    for p in targets:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        used |= set(re.findall(r"D-\d{4}", text))
        # 标注了「未建」的编号是故意留空，不算缺文件——但只有标注过的才算
        intentional |= {m.group(0)[:6] for m in INTENTIONAL_ABSENT.finditer(text)}

    missing = sorted(used - have - intentional)
    note = f"（{','.join(sorted(intentional))} 已标注未建）" if intentional else ""
    return not missing, (("全覆盖" if not missing else f"缺文件: {', '.join(missing)}") + note)


def chk_todo():
    # 2026-10-29（F7）：原先只扫 `docs/*.md`（顶层、非递归）——
    # docs 子目录里的待办、以及根级四份文档里的待办全是盲区。
    # F 项（决策编号）2026-10-23 已因同款问题修过（D-0053），G 项这次补齐。
    targets = [
        ROOT / n
        for n in ("README.md", "ARCHITECTURE.md", "DEPENDENCIES.md", "M4-接续指南.md")
        if (ROOT / n).is_file()
    ]
    targets += [p for p in DOCS.rglob("*.md") if p.is_file()]
    hits = []
    for p in targets:
        if p.name in TODO_EXEMPT:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            if TODO_PAT.search(ln):
                rel = p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name
                hits.append(f"{rel}:{i}")
    return not hits, ("无残留" if not hits else f"{len(hits)} 处: " + ", ".join(hits[:4]))


def chk_snapshot():
    """真源快照：既要存在，也要与当前真源一致（否则等于「改了没备份」）。

    2026-10-23：路径从 `_trash/snapshots` 迁到 `snapshots`（与 snapshot_sources.py 同步）。
    原路径在 _trash 下、不入版本控制，导致新 clone 的仓库 H 项必红 ——
    「门禁无法在干净环境全过」本身就是缺陷。
    """
    snap = ROOT / "snapshots"
    if not snap.is_dir():
        return False, "无快照目录（先跑 snapshot_sources.py，或 make snapshot）"
    dirs = sorted([d for d in snap.iterdir() if d.is_dir()])
    if not dirs:
        return False, "无快照"
    rc, out = sh([PY, str(SCRIPTS / "snapshot_sources.py"), "--verify"])
    if rc == 2:
        # 2026-10-28（F3）：环境错误（无可用快照）与「发现差异」必须分开说，
        # 否则「没有基准」会被误读成「有基准但不一致」，两种处置完全不同。
        return False, "无可用快照（--verify 返回 2：环境错误，非差异）"
    if rc != 0:
        # 2026-10-28（F3）：--verify 改为三向比对，输出行改以 A/B/C/D 分类开头
        # （A 真源改动 / B 副本缺失 / C 副本被改 / D 真源与副本互异）。
        # 仍按旧文案找「修改/新增」会抽到空串，失败信息就没有可操作内容。
        kinds = [
            ln.strip() for ln in out.splitlines() if ln.strip().startswith(("A ", "B ", "C ", "D "))
        ]
        tail = "；".join(kinds[:3]) or (out.strip().splitlines() or ["无详情"])[-1]
        return False, f"与最近快照 {dirs[-1].name} 不一致（三向比对命中）。{tail}"
    return True, f"最近快照: {dirs[-1].name}，与真源一致"


CHECKS = [
    ("A 文档一致性", chk_docs),
    ("B 全量回归", chk_regress),
    ("C 判据自检", chk_selftest),
    ("D 包完整性", chk_manifest),
    ("E 密钥扫描", chk_secrets),
    ("F 决策编号", chk_decision_ids),
    ("G 待办扫描", chk_todo),
    ("H 真源快照", chk_snapshot),
]


def chk_consistency_info():
    """附加信息（**非门禁**）：全库自洽扫描摘要。

    对应交接文档 §6-7「每新增一轮跑一次自洽检查」。放在这里是为了**被动可见**——
    否则这条只存在于"记得去跑"的人脑里，历史上 4 份分桶矛盾就是这么攒出来的。

    刻意不进门禁：
      - 它反映的是**历史已签字产出**的状态，而项目原则是「不回溯改判」；
      - 新轮次尚未发生时，数字不会变，卡它会变成假告警；
      - 保持「8/8」这一基线契约不被打破。
    因此只打印，永不影响退出码。
    """
    runs = ROOT / "migration-package" / "runs"
    if not runs.is_dir():
        return "（无 runs 目录，跳过）"
    try:
        rc, out = sh(
            [
                PY,
                str(SCRIPTS / "check_consistency.py"),
                "--sweep",
                str(runs),
                "--scope",
                "final",
                "-q",
                "--warn-only",
            ],
            timeout=120,
        )
    except Exception as e:  # 信息项异常不拖垮整体
        return f"（扫描异常：{type(e).__name__}，不影响门禁）"
    m = re.search(r"共 (\d+) 份\s+通过 (\d+) / 不自洽 (\d+)", out)
    if not m:
        return "（未能解析扫描输出，不影响门禁）"
    total, ok, bad = m.group(1), m.group(2), m.group(3)
    return f"{total} 份：{ok} 通过 / {bad} 不自洽（历史产出不回溯改判；见复核报告 §六）"


def chk_upgrade_info():
    """附加信息（**非门禁**）：★ 升级候选的台账缺口。

    2026-10-26 发现：`🔄 跨项目经验升级(周日 23:57)` 被当作"会自动把 ★ 经验捞进
    跨项目规则层"的既定事实，但用户级 MEMORY.md 里 `L-` 编号出现 **0 次**，
    即升级链路**从未留下 provenance**；同批周日自动化全部留痕，说明不是平台故障。

    刻意不进门禁：
      - 「有 ★ 未升级」是常态（人工核对天然滞后），卡它 = 每天一条假告警；
      - 保持「8/8」基线契约不被打破。
    只打印，永不影响退出码；要卡用 `upgrade_status.py --strict`。
    """
    try:
        rc, out = sh([PY, str(SCRIPTS / "upgrade_status.py"), "--json"], timeout=60)
        d = json.loads(out)
    except Exception as e:  # 信息项异常不拖垮整体
        return f"（核对异常：{type(e).__name__}，不影响门禁）"
    if not d.get("rule_layer_exists"):
        # 2026-11-01 修正：规则层（用户级 MEMORY.md）不存在有两种含义，不能混为一谈——
        #   · 在 CI / 换机环境里，~/.workbuddy 本来就没有，升级链路也不在该环境运行；
        #     此时报 🔴「升级链路必然空转」是假故障，只会训练人忽略红字。
        #   · 在本地就必须存在，缺了才是真问题。
        # 旧实现一律报 🔴，导致每次 CI 日志都挂一条不会消失的红字。
        if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
            return (
                f"⚪ 本环境无用户级规则层（{d.get('rule_layer')}）→ "
                "升级链路无法核查，非缺陷（CI/换机环境预期如此）"
            )
        return (
            f"🔴 规则层文件不存在（{d.get('rule_layer')}）——"
            "本地应存在，升级链路必然空转；检查 HOME 是否正确或被覆盖"
        )
    return (
        f"{d['star']} 条 ★ / 已升级 {d['upgraded']} / 本地强制 {d.get('covered_locally', 0)}"
        f" / 待升(预算阻塞) {d.get('deferred', 0)} / 待处置 {len(d['pending'])}"
        f"（超期 {len(d['stale'])} 条；台账 docs/learnings-upgraded.json）"
    )


# ────────────────────── 门禁自身的守卫（--self-test） ──────────────────────
#
# 2026-10-29（F7 遗留）：门禁自身此前没有任何自动测试。F7 补的三处扫描盲区是靠
# 一次性 /tmp 实验证明的 —— CI 上永远不跑，等于「守门员没有守卫」（审计根因 R2）。
#
# 两条设计约束：
#   1. 自校验套件不得写它正在校验的资产 → 全程 TemporaryDirectory 造假仓库；
#   2. 每条都配正负样本，负样本必须能把它推成 FAIL ——
#      只验「干净时绿」等于没验，因为 `return True, "ok"` 也能满足它。


def self_test():
    """门禁自检：先证明每项「会红」，再让它绿。"""
    import contextlib
    import io
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

    saved = (ROOT, SCRIPTS, DOCS, PKG)

    @contextlib.contextmanager
    def at(td):
        """把模块级四个路径指向假仓库。"""
        global ROOT, SCRIPTS, DOCS, PKG
        ROOT, SCRIPTS, DOCS, PKG = td, td / "scripts", td / "docs", td / "migration-package"
        try:
            yield td
        finally:
            ROOT, SCRIPTS, DOCS, PKG = saved

    @contextlib.contextmanager
    def with_sh(stub):
        """替换 shell 边界：B/C/H/A 四类外部依赖逐源 mock，不真跑子进程。"""
        global sh
        old = sh
        sh = stub
        try:
            yield
        finally:
            sh = old

    @contextlib.contextmanager
    def with_checks(stubs):
        global CHECKS
        old = CHECKS
        CHECKS = stubs
        try:
            yield
        finally:
            CHECKS = old

    def make(t, name):
        d = t / name
        (d / "scripts").mkdir(parents=True)
        (d / "scripts" / "ok.py").write_text("# ok\n", encoding="utf-8")
        (d / "docs" / "decisions").mkdir(parents=True)
        (d / "migration-package").mkdir()
        return d

    def canned(rc, out):
        return lambda cmd, cwd=None, timeout=None: (rc, out)

    def run_main(argv):
        old = sys.argv
        sys.argv = ["pre_release_check.py", *argv]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = main()
        finally:
            sys.argv = old
        return rc, out.getvalue(), err.getvalue()

    with tempfile.TemporaryDirectory(prefix="prc-selftest-") as tmp:
        t = Path(tmp)
        # 拼接构造：写成字面量会让本文件自身被 E 项判为泄露（同 regress 第 20 项的自引用陷阱）
        KEY = "sk-" + "A" * 26

        # ── E 密钥扫描 ─────────────────────────────────────────────
        r = make(t, "e-clean")
        with at(r):
            ok, d = chk_secrets()
        expect("E 正样本：干净仓库 → 通过", ok is True, d)

        for sub in ("scripts", "audit", "templates", "outputs"):
            r = make(t, f"e-{sub}")
            (r / sub).mkdir(exist_ok=True)
            (r / sub / "leak.md").write_text(f'token = "{KEY}"\n', encoding="utf-8")
            with at(r):
                ok, d = chk_secrets()
            label = (
                f"E 负样本：{sub}/ 藏密钥 → 拦截（F7 补的盲区）"
                if sub != "scripts"
                else "E 负样本：scripts/ 藏密钥 → 拦截"
            )
            expect(label, ok is False and "leak.md" in d, d)

        # ── G 待办扫描 ─────────────────────────────────────────────
        r = make(t, "g")
        with at(r):
            expect("G 正样本：干净仓库 → 通过", chk_todo()[0] is True)

        for label, path, text in (
            ("docs 顶层含待办", r / "docs" / "top.md", "待处理：补图\n"),
            ("docs 子目录含 TODO（F7 补的盲区）", r / "docs" / "sub" / "deep.md", "TODO: 待补\n"),
            ("根级 README 含未修（F7 补的盲区）", r / "README.md", "未修：某问题\n"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# 标题\n" + text, encoding="utf-8")
            with at(r):
                ok, d = chk_todo()
            expect(
                f"G 负样本：{label} → 拦截", ok is False and path.relative_to(r).as_posix() in d, d
            )
            path.write_text("# 标题\n", encoding="utf-8")

        (r / "docs" / "会话审计-20261020.md").write_text("# TODO 历史记录\n", encoding="utf-8")
        with at(r):
            ok, d = chk_todo()
        expect("G 正样本：豁免文件仍豁免（TODO_EXEMPT 生效）", ok is True, d)

        (r / "docs" / "会话审计-20261021.md").write_text("# TODO 历史记录\n", encoding="utf-8")
        with at(r):
            ok, d = chk_todo()
        expect("G 负样本：豁免按全名精确匹配 → 形近文件不放行", ok is False and "20261021" in d, d)
        (r / "docs" / "会话审计-20261021.md").write_text("# 历史记录\n", encoding="utf-8")

        # ── F 决策编号 ─────────────────────────────────────────────
        # ★ 编号必须动态拼：F 项会扫 scripts/*.py，源码里写死一个不存在的 D-NNNN
        #   等于让测试夹具参与真门禁扫描 —— 实测会直接把真仓库判成「引用了缺失决策」，
        #   而写成「…（未建）」还会被自己的 INTENTIONAL_ABSENT 豁免掉，更难发现。
        ID_OK = "D-" + "0001"
        ID_ABSENT = "D-" + "9999"
        ID_MISS = "D-" + "8888"

        r = make(t, "f")
        (r / "docs" / "decisions" / f"{ID_OK}-样例.md").write_text(f"# {ID_OK}\n", encoding="utf-8")
        (r / "docs" / "index.md").write_text(f"见 {ID_OK}。\n", encoding="utf-8")
        with at(r):
            ok, d = chk_decision_ids()
        expect("F 正样本：引用有对应文件 → 通过", ok is True, d)

        (r / "docs" / "index.md").write_text(f"见 {ID_ABSENT}。\n", encoding="utf-8")
        with at(r):
            ok, d = chk_decision_ids()
        expect("F 负样本：引用不存在的编号 → 拦截", ok is False and ID_ABSENT in d, d)

        (r / "docs" / "index.md").write_text(f"{ID_ABSENT}（未建，保留编号）。\n", encoding="utf-8")
        with at(r):
            ok, d = chk_decision_ids()
        expect("F 正样本：标注「未建」→ 不算缺文件", ok is True, d)

        (r / "README.md").write_text(f"见 {ID_MISS}。\n", encoding="utf-8")
        with at(r):
            ok, d = chk_decision_ids()
        expect("F 负样本：根级文档的引用也在范围（D-0053）", ok is False and ID_MISS in d, d)

        # ── D 包完整性 ─────────────────────────────────────────────
        r = make(t, "d")
        mp = r / "migration-package" / "MANIFEST.json"
        with at(r):
            expect("D 负样本：MANIFEST 缺失 → 拦截", chk_manifest()[0] is False)

        mp.write_text(
            json.dumps(
                {"files": [{"path": "a", "size": 3}], "stats": {"file_count": 1, "total_bytes": 3}}
            ),
            encoding="utf-8",
        )
        with at(r):
            expect("D 正样本：声明与实际一致 → 通过", chk_manifest()[0] is True)

        mp.write_text(
            json.dumps(
                {"files": [{"path": "a", "size": 3}], "stats": {"file_count": 2, "total_bytes": 3}}
            ),
            encoding="utf-8",
        )
        with at(r):
            expect("D 负样本：file_count 与条目数不符 → 拦截", chk_manifest()[0] is False)

        # 历史 bug 回归：字段名是 size，写成 bytes 会让实际字节恒为 0
        mp.write_text(
            json.dumps(
                {"files": [{"path": "a", "bytes": 3}], "stats": {"file_count": 1, "total_bytes": 3}}
            ),
            encoding="utf-8",
        )
        with at(r):
            ok, d = chk_manifest()
        expect("D 负样本：条目用 bytes 键（历史 bug）→ 拦截", ok is False, d)

        # ── B 全量回归（外部依赖：子进程输出 + 退出码）────────────
        r = make(t, "b")
        with at(r):
            with with_sh(canned(0, "结果：56/56 通过\n")):
                expect("B 正样本：56/56 且 EXIT=0 → 通过", chk_regress()[0] is True)
            with with_sh(canned(0, "结果：55/56 通过\n")):
                expect("B 负样本：55/56 → 拦截", chk_regress()[0] is False)
            with with_sh(canned(1, "结果：56/56 通过\n")):
                expect(
                    "B 负样本：项数够但 EXIT=1 → 拦截（退出码也是判据）", chk_regress()[0] is False
                )
            with with_sh(canned(0, "Traceback (most recent call last):\n")):
                ok, d = chk_regress()
            expect(
                "B 负样本：输出不可解析 → 拦截（不把解析失败当过）",
                ok is False and "解析失败" in d,
                d,
            )

        # ── C 判据自检（外部依赖：verify.py + 本地语法扫描）────────
        r = make(t, "c")
        with at(r):
            with with_sh(canned(0, "……41/41 项\n")):
                expect("C 正样本：41/41 → 通过", chk_selftest()[0] is True)
            with with_sh(canned(0, "……40/41 项\n")):
                expect("C 负样本：40/41 → 拦截", chk_selftest()[0] is False)
            with with_sh(canned(0, "boom\n")):
                ok, d = chk_selftest()
            expect("C 负样本：输出不可解析 → 拦截", ok is False and "无法解析" in d, d)

            (r / "audit").mkdir()
            (r / "audit" / "bad.py").write_text("def f(\n", encoding="utf-8")
            with with_sh(canned(0, "……41/41 项\n")):
                ok, d = chk_selftest()
            expect(
                "C 负样本：audit/ 下语法错 → 拦截（语法支路是活的）",
                ok is False and "bad.py" in d,
                d,
            )

        # ── H 真源快照（外部依赖：三向比对退出码 0/1/2）───────────
        r = make(t, "h-none")
        with at(r):
            ok, d = chk_snapshot()
        expect(
            "H 负样本：无 snapshots 目录 → 拦截且给出可执行指引",
            ok is False and "无快照目录" in d,
            d,
        )

        r = make(t, "h-empty")
        (r / "snapshots").mkdir()
        with at(r):
            ok, d = chk_snapshot()
        expect("H 负样本：目录存在但无快照 → 拦截", ok is False and "无快照" in d, d)

        r = make(t, "h")
        (r / "snapshots" / "20260101-000000-x").mkdir(parents=True)
        with at(r):
            with with_sh(canned(2, "[ERR] 无可用快照\n")):
                ok, d = chk_snapshot()
            expect(
                "H 负样本：rc=2 → 报「环境错误」而非「有差异」", ok is False and "环境错误" in d, d
            )
            with with_sh(canned(1, "A scripts/ok.py 真源被改\nC x 副本被改\n")):
                ok, d = chk_snapshot()
            expect(
                "H 负样本：三向比对命中 → 拦截且详情带 A/B/C 分类",
                ok is False and "A scripts/ok.py" in d,
                d,
            )
            with with_sh(canned(1, "无法归类的输出\n")):
                ok, d = chk_snapshot()
            expect("H 负样本：无 A/B/C 行时不崩、详情有兜底", ok is False and bool(d), d)
            with with_sh(canned(0, "[PASS] 一致\n")):
                ok, d = chk_snapshot()
            expect("H 正样本：rc=0 → 通过且报出快照名", ok is True and "20260101-000000-x" in d, d)

        # ── A 文档一致性（ctx 是否真的传进子命令）──────────────────
        r = make(t, "a")
        seen = {}

        def sh_docs(cmd, cwd=None, timeout=None):
            if "--count" in cmd:
                return 0, "6\n"
            seen["cmd"] = list(cmd)
            return 0, "  [OK] 一致\n"

        with at(r):
            with with_sh(sh_docs):
                ok, d = chk_docs({"regress": 56, "criteria": 41, "gates": 8})
        got = " ".join(seen.get("cmd") or [])
        expect(
            "A 正样本：ctx 实测项数真的传给了文档校验（已配置≠被读取）",
            ok is True
            and all(
                f"{k}={v}" in got
                for k, v in (("regress", 56), ("criteria", 41), ("gates", 8), ("handover", 6))
            ),
            got[-90:],
        )

        with at(r):
            with with_sh(canned(1, "  [A] 回归声明 50/56 与实测不符\n")):
                ok, d = chk_docs({"regress": 56, "criteria": 41, "gates": 8})
        expect("A 负样本：不一致 → 拦截并透出具体行（不是通用尾句）", ok is False and "[A]" in d, d)

        # ── 对外契约：8 项、字母 A–H、无重名 ───────────────────────
        names = [n for n, _ in CHECKS]
        letters = "".join(n.split()[0] for n in names)
        expect(
            "契约：门禁固定 8 项（8/8 是本文件的对外基线）",
            len(CHECKS) == 8,
            f"实为 {len(CHECKS)} 项",
        )
        expect("契约：字母 A–H 连续且与名字首字母一致", letters == "ABCDEFGH", letters)
        expect("契约：检查项名不重复", len(set(names)) == len(names))

        # ── 聚合层：失败/异常/静默/环境错误 四条通道 ───────────────
        def ok_stub():
            return True, "1/1"

        def bad_stub():
            return False, "1/2 不一致"

        def boom_stub():
            raise RuntimeError("注入异常")

        # 聚合用例与真实 info 脚本无关：给 sh 一个确定性桩，否则自检输出随环境漂移
        INFO = json.dumps(
            {"rule_layer_exists": True, "star": 0, "upgraded": 0, "pending": [], "stale": []}
        )

        r = make(t, "m")
        with at(r), with_sh(canned(0, INFO)):
            with with_checks([("A 甲", ok_stub), ("B 乙", ok_stub)]):
                rc, out, err = run_main([])
            expect("聚合：全通过 → exit 0", rc == 0 and "[PASS] 2/2" in out, out.strip()[-60:])

            with with_checks([("A 甲", ok_stub), ("B 乙", bad_stub)]):
                rc, out, err = run_main([])
            expect(
                "聚合：任一项失败 → exit 1 且点名该项", rc == 1 and "B 乙" in err, err.strip()[-60:]
            )

            with with_checks([("A 甲", boom_stub), ("B 乙", ok_stub)]):
                rc, out, err = run_main([])
            expect(
                "聚合：单项抛异常 → 记该项失败而非整体崩（exit 1）",
                rc == 1 and "A 甲" in err,
                err.strip()[-60:],
            )

            with with_checks([("A 甲", ok_stub)]):
                rc, out, err = run_main(["-q"])
            expect("聚合：-q 只打结论，不刷明细", rc == 0 and "✅" not in out and "[PASS]" in out)

        nopkg = t / "nopkg"
        nopkg.mkdir()
        with at(nopkg):
            with with_checks([("A 甲", ok_stub)]):
                rc, _, _ = run_main([])
        expect("聚合：包目录缺失 → exit 2（环境错误，与「有失败项」区分）", rc == 2, f"rc={rc}")

    print()
    if fails:
        print(f"[FAIL] 自检未通过：{len(fails)}/{n_ok + len(fails)} 项失败")
        for f in fails:
            print(f"  · {f}")
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{n_ok} 项")
    return 0


def _fingerprint() -> str:
    """git 版本 + 关键文件内容哈希：结论可回溯到具体版本，避免"报告与代码错位"。"""
    import hashlib

    rev, dirty = "unknown", "?"
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            rev = r.stdout.strip()
        st = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if st.returncode == 0:
            dirty = "yes" if st.stdout.strip() else "no"
    except Exception:
        pass
    parts = [f"git={rev}", f"dirty={dirty}"]
    # 模型标识（2026-10-31）：门禁结论也要能回答"这是哪个模型跑出来的"。
    # 只读身份变量，不碰任何 *_KEY/*_TOKEN/*_PROXY。
    model_id = os.environ.get("CODEBUDDY_CURRENT_MODEL_ID", "")
    parts.append(f"model={model_id or 'UNKNOWN'}")
    for rel in (
        "audit/kit/item_review.py",
        "audit/kit/caliber_lib.py",
        "audit/baseline/v2/caliber-library.json",
        "docs/protocol/08-出题质检协议-v1.md",
    ):
        f = ROOT / rel
        if f.exists():
            h = hashlib.sha256(f.read_bytes()).hexdigest()[:8]
            parts.append(f"{f.name}={h}")
        else:
            parts.append(f"{f.name}=MISSING")
    return " | ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="发布前一键检查（只读）")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="门禁自身自检（正负样本，临时目录内构造，不动真仓库）",
    )
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not PKG.is_dir():
        print(f"[ERR] 包目录缺失: {PKG}", file=sys.stderr)
        return 2

    # ── 结论指纹（2026-10-31）：任何一次门禁结果都必须能回溯到"哪一版代码/哪一版口径库"。
    # 本项目被"报告绑错版本"坑过三次（R1 误读已修订的题面、QA 用旧快照判未实现、门禁结论与代码不同步）。
    if not args.quiet:
        fp = _fingerprint()
        print(f"[指纹] {fp}")
        print("")

    # 执行顺序：A（文档一致性）放最后 —— 它要用 B/C 的实测项数校验文档里的 N/N 声明。
    # 打印顺序仍按 CHECKS 原序（A→H），对外看起来没变。
    order = [c for c in CHECKS if c[1] is not chk_docs] + [c for c in CHECKS if c[1] is chk_docs]
    results = {}
    ctx = {"gates": len(CHECKS)}
    for name, fn in order:
        try:
            ok, detail = fn(ctx) if fn is chk_docs else fn()
        except Exception as e:  # 单项异常不拖垮整体
            ok, detail = False, f"检查异常: {type(e).__name__}: {e}"
        results[name] = (ok, detail)
        # 把 B/C 的实测项数喂给 A
        m = re.search(r"^(\d+)/(\d+)", detail.strip())
        if m and m.group(1) == m.group(2):
            key = {"B 全量回归": "regress", "C 判据自检": "criteria"}.get(name)
            if key:
                ctx[key] = int(m.group(1))

    rows, failed = [], []
    for name, _ in CHECKS:
        ok, detail = results[name]
        rows.append((name, ok, detail))
        if not ok:
            failed.append(name)

    if not args.quiet:
        print("[pre_release_check] 发布前检查")
        for name, ok, detail in rows:
            print(f"  {'✅' if ok else '🔴'} {name:<14} {detail}")
        print()
        # 附加信息不参与判定，仅让「每轮跑一次自洽检查」被动可见
        print(f"  ℹ️  {'I 自洽扫描（非门禁）':<12} {chk_consistency_info()}")
        print(f"  ℹ️  {'J ★升级台账（非门禁）':<12} {chk_upgrade_info()}")
        print()

    if failed:
        print(f"[FAIL] {len(failed)}/{len(rows)} 项未通过：{', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"[PASS] {len(rows)}/{len(rows)} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
