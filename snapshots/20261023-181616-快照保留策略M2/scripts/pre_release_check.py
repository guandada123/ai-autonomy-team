#!/usr/bin/env python3
"""pre_release_check.py —— 发布前一键检查（只读，不修改任何文件）。

## 为什么存在

2026-10-20 会话审计提出 4 条建议，本脚本把其中 3 条落成可执行机制：

  建议 1「交付前跑 check_docs_consistency --strict」→ 本脚本第 A 项
  建议 3「终局结论须以穷尽检索为前提」        → 本脚本第 F、G 项（编号覆盖 + 待办扫描）
  建议 4「交付前跑 check_secrets()」           → 本脚本第 E 项（复用 make_transfer 的模式）

建议 2（改真源先备份）由 `snapshot_sources.py` 承担。

## 检查项

  A. 文档一致性    check_docs_consistency（MANIFEST 数字 / 索引行数 / 契约版本）
  B. 全量回归      regress.py 必须 50/50、EXIT=0
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
    退出码：0 全通过 / 1 有失败项 / 2 环境错误
"""
import argparse
import json
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


def chk_docs():
    rc, out = sh([PY, str(SCRIPTS / "check_docs_consistency.py"), "--strict"])
    ok = rc == 0
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1] if out.strip() else ""
    return ok, tail


def chk_regress():
    rc, out = sh([PY, str(SCRIPTS / "regress.py")], cwd=PKG)
    m = re.search(r"结果：(\d+)/(\d+) 通过", out)
    got = f"{m.group(1)}/{m.group(2)}" if m else "解析失败"
    return (rc == 0 and m and m.group(1) == m.group(2)), f"{got}, EXIT={rc}"


def chk_selftest():
    rc, out = sh([PY, str(PKG / "judges" / "verify.py"), "--self-test"])
    m = re.search(r"(\d+)/(\d+) 项", out)
    if not m:
        # 注入测试时发现：self-test 失败会改变输出格式，初版只报「解析失败」不足以定位
        tail = next((ln.strip() for ln in reversed(out.strip().splitlines()) if ln.strip()), "无输出")
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
    for base in (SCRIPTS, DOCS, PKG / "judges", PKG / "config"):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {".md", ".py", ".json", ".sh", ".cmd", ".txt", ".env"}:
                continue
            for label, frag in scan_file(p):
                hits.append(f"{p.relative_to(ROOT)}::{label}")
    return not hits, ("无命中" if not hits else f"{len(hits)} 处: " + "; ".join(hits[:3]))


def chk_decision_ids():
    dec = DOCS / "decisions"
    have = {m.group(0) for p in dec.glob("*.md")
            for m in [re.search(r"D-\d{4}", p.name)] if m}
    used = set()
    for base in (DOCS, SCRIPTS, PKG / "judges", PKG / "protocol", PKG / "config"):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".md", ".py", ".json"}:
                try:
                    used |= set(re.findall(r"D-\d{4}", p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
    missing = sorted(used - have)
    return not missing, ("全覆盖" if not missing else f"缺文件: {', '.join(missing)}")


def chk_todo():
    hits = []
    for p in DOCS.glob("*.md"):
        if p.name in TODO_EXEMPT:
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if TODO_PAT.search(ln):
                hits.append(f"{p.name}:{i}")
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
    if rc != 0:
        # 抽出「修改 N」那行，给出可操作信息
        line = next((ln.strip() for ln in out.splitlines() if "修改" in ln or "新增" in ln), "")
        return False, f"真源与最近快照 {dirs[-1].name} 不一致（改了未打快照）。{line}"
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
        rc, out = sh([PY, str(SCRIPTS / "check_consistency.py"),
                      "--sweep", str(runs), "--scope", "final", "-q", "--warn-only"], timeout=120)
    except Exception as e:                       # 信息项异常不拖垮整体
        return f"（扫描异常：{type(e).__name__}，不影响门禁）"
    m = re.search(r"共 (\d+) 份\s+通过 (\d+) / 不自洽 (\d+)", out)
    if not m:
        return "（未能解析扫描输出，不影响门禁）"
    total, ok, bad = m.group(1), m.group(2), m.group(3)
    return f"{total} 份：{ok} 通过 / {bad} 不自洽（历史产出不回溯改判；见复核报告 §六）"


def main():
    ap = argparse.ArgumentParser(description="发布前一键检查（只读）")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    if not PKG.is_dir():
        print(f"[ERR] 包目录缺失: {PKG}", file=sys.stderr)
        return 2

    rows, failed = [], []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:                       # 单项异常不拖垮整体
            ok, detail = False, f"检查异常: {type(e).__name__}: {e}"
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
        print()

    if failed:
        print(f"[FAIL] {len(failed)}/{len(rows)} 项未通过：{', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"[PASS] {len(rows)}/{len(rows)} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
