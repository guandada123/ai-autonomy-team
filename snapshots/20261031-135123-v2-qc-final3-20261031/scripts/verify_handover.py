#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_handover.py —— 接手验收：一条命令回答「这套东西在你这台机器上能用吗」

## 为什么需要它

项目的原始目标（`docs/protocol/05` + D-0002 的 G1–G4）里，**G4 = 自包含迁移包**，
也就是「凭它能在另一台机器重建并继续开发」。但接手者面对的是 4 份入口文档
（README / M4-接续指南 / 评审手册 / 交接文档），不知道该信哪份、跑到什么算通过。

本脚本把人读文档才能判断的事压成**可执行的验收**：6 项，逐项 PASS/FAIL，一条命令出结论。

## 它检查什么（对应 G1–G4）

| # | 项 | 对应 | 判据 |
|---|---|---|---|
| 1 | 运行时 | G2 | python3 ≥ 3.9 且**零第三方依赖**（本包承诺只用标准库） |
| 2 | 包自检 | G4 | `precheck.py` 通过（人工项 ≤1，须为跨家族） |
| 3 | 判据自检 | G2 | `judges/verify.py --self-test` = 27/27 |
| 4 | 全量回归 | G4 | `scripts/regress.py` = 56/56（**须在整棵工作区**，单包跑不了） |
| 5 | 包完整性 | G4 | MANIFEST 声明条数/字节 = 实际 |
| 6 | 推送钩子 | D-0045 | `.git/hooks/pre-push` 已装且指向 `scripts/pre_push_scan.py` |

**为什么第 6 项要在这里**：钩子是本地文件、**不入库**，而它正是「推前扫密钥 + 核对远端
是否 private」的唯一拦截层。换机 clone 后没有钩子时，`make pre-push` 只能靠人记得跑，
等于这条防线不存在。把它做成接手验收的失败项，新机器的第一条命令就会红并给出修复命令。
⚠️ 因此全新 clone 上首次跑本脚本会**红在第 6 项**，这不是缺陷：跑一次 `make hooks` 即转绿。

**刻意不做的**：不检查跨家族路由（G1）——它依赖本机会话缓存与账号，换机必失效，
只能人工跑 `inspect_agent_routing.py`（脚本会把它列为「需人工」而不是假绿）。

用法：
    python3 scripts/verify_handover.py           # 全量
    python3 scripts/verify_handover.py -q        # 只打印结论
    退出码：0 全通过 / 1 有失败 / 2 环境错误（不是工作区根）
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "migration-package"
PY = sys.executable

# 本项目声称"零第三方依赖"，用它来自证：这些包若被 import，说明有外部依赖混进来
# ★ 2026-10-23 修（代码审查 C-4）：原来是一份 6 个包名的黑名单，
#   注入 `import httpx` 实测仍然返回「第三方依赖命中 0」。改为**标准库白名单**判定。
def _is_stdlib(name):
    """3.10+ 用 sys.stdlib_module_names；3.9 回退查 find_spec 的 origin 是否在 stdlib 下。"""
    if hasattr(sys, "stdlib_module_names"):
        return name in sys.stdlib_module_names
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    if spec.origin in (None, "built-in", "frozen"):
        return True
    try:
        origin = Path(spec.origin).resolve()
        return origin.is_relative_to(Path(sysconfig.get_paths()["stdlib"]).resolve()) \
            and "site-packages" not in spec.origin
    except (OSError, ValueError):
        return False


def sh(cmd, cwd=None, timeout=300):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def chk_runtime():
    v = sys.version_info
    ok_ver = (v.major, v.minor) >= (3, 9)
    # 扫一遍包内 import，确认没有第三方依赖混进来
    hits = set()
    local = {p.stem for p in PKG.rglob("*.py")}      # 包内互 import 不算外部依赖
    pat = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w]*)", re.M)
    for p in PKG.rglob("*.py"):
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pat.finditer(src):
            top = m.group(1).split(".")[0]
            if top in local:
                continue
            if not _is_stdlib(top):
                hits.add(top)
    return (ok_ver and not hits,
            f"python {v.major}.{v.minor}.{v.micro}；非标准库 import 命中 {len(hits)}"
            + (f"：{sorted(hits)}" if hits else "（符合标准库承诺）"))


def chk_precheck():
    rc, out = sh([PY, "precheck.py"], cwd=PKG)
    m = re.search(r"(\d+)\s*项需人工", out)
    manual = int(m.group(1)) if m else 0
    ok = rc == 0
    return ok, f"rc={rc}；需人工 {manual} 项" + ("（跨家族路由，须本机实跑）" if manual else "")


def chk_selftest():
    rc, out = sh([PY, "judges/verify.py", "--self-test"], cwd=PKG)
    m = re.search(r"自检通过：(\d+)/(\d+)", out)
    got = f"{m.group(1)}/{m.group(2)}" if m else "未解析"
    return (rc == 0 and bool(m)), f"rc={rc}；{got}"


def chk_regress():
    if not (ROOT / "scripts" / "regress.py").is_file():
        return False, "找不到 scripts/regress.py —— 说明你拿到的是**单包**，不是整棵工作区"
    rc, out = sh([PY, str(ROOT / "scripts" / "regress.py")], cwd=PKG, timeout=600)
    m = re.search(r"结果：(\d+)/(\d+) 通过", out)
    got = f"{m.group(1)}/{m.group(2)}" if m else "未解析"
    return (rc == 0 and bool(m)), f"rc={rc}；{got}"


def chk_manifest():
    try:
        man = json.loads((PKG / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, f"读不到 MANIFEST：{e}"
    stats = man.get("stats") or {}
    files = stats.get("file_count")
    size = stats.get("total_bytes")
    # 只数会被 MANIFEST 收录的文件：缓存/临时产物不算，但要单独提示（2026-10-22 实测
    # 包内残留 judges/__pycache__/*.pyc —— 跑一次判据就会生成，属交付卫生问题）
    junk, counted = [], 0
    for f in PKG.rglob("*"):
        if not f.is_file() or f.name == "MANIFEST.json":
            continue
        if "__pycache__" in f.parts or f.suffix in (".pyc", ".pyo"):
            junk.append(str(f.relative_to(PKG)))
            continue
        counted += 1
    ok = isinstance(files, int) and counted == files
    detail = f"声明 {files} 条 / {size} 字节；实际 {counted} 个文件"
    if junk:
        detail += f"；🔶 包内残留 {len(junk)} 个缓存文件（建议清掉再交付）"
    return ok, detail


def chk_hook():
    """6 推送钩子：D-0045 的推前闸门是**本地文件、不入库**，换机后必须重装。

    三条全满足才算装好：
      1. 在 git 认定的位置找到 `pre-push`（走 `git rev-parse --git-path`，尊重 core.hooksPath，
         不硬编码 `.git/hooks`——硬编码会在设了 hooksPath 的机器上假红/假绿）；
      2. 可执行；
      3. 内容调的是本仓 `scripts/pre_push_scan.py`——装了别的钩子不算装好。
    """
    try:
        rc, out = sh(["git", "rev-parse", "--git-path", "hooks/pre-push"], cwd=ROOT, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git 不可用（{type(e).__name__}），无法确认钩子"
    if rc != 0:
        return False, "不是 git 仓库——钩子装不上（make hooks 也会失败）"
    rel = (out.strip().splitlines() or [""])[-1].strip()
    hook = ROOT / rel if rel else ROOT / ".git" / "hooks" / "pre-push"
    shown = hook.relative_to(ROOT) if hook.is_relative_to(ROOT) else hook
    if not hook.is_file():
        return False, f"未安装（{shown}）→ 跑 make hooks"
    if not os.access(hook, os.X_OK):
        return False, f"不可执行（{shown}）→ 跑 make hooks"
    if "pre_push_scan.py" not in hook.read_text(encoding="utf-8", errors="replace"):
        return False, f"钩子未调用 pre_push_scan.py（{shown}）→ 跑 make hooks 覆盖"
    if not (ROOT / "scripts" / "pre_push_scan.py").is_file():
        return False, "钩子指向的 scripts/pre_push_scan.py 不存在"
    return True, f"已装且指向 pre_push_scan.py（{shown}）"


CHECKS = [
    ("1 运行时与依赖", chk_runtime),
    ("2 包自检", chk_precheck),
    ("3 判据自检", chk_selftest),
    ("4 全量回归", chk_regress),
    ("5 包完整性", chk_manifest),
    ("6 推送钩子", chk_hook),
]

READING = """\
读什么（按顺序，别全读）：
  ① docs/评审手册-3.0rc.md   §一 5 分钟判断「这东西现在能不能用」+ §四 边界
  ② docs/交接文档-20261021.md §0 现状 / §9 复现命令（新对话或新同事的第一入口）
  ③ docs/protocol/01-架构.md  机制为什么这样设计（读并行/写单线程/双裁判/保分歧）
  ④ migration-package/README.md  重建步骤（换机器时看这份）

已知边界（别当缺陷重新发现）：
  · 单包 migration-package 能跑机制、**不能跑回归**（regress/build_package 刻意不入包）
  · 跨家族路由（G1）依赖本机会话缓存，换机必须人工重跑 inspect_agent_routing.py
  · 33 轮历史产出的契约版本是 2.0/2.1，不随工具升级；引用历史 verdict 必须标注 spec 版本
"""


def main():
    ap = argparse.ArgumentParser(description="接手验收：一条命令判断这套东西能不能用")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--count", action="store_true",
                    help="只打印检查项数（供门禁核对文档里的 N/N 声明，不跑任何检查）")
    args = ap.parse_args()

    # 门禁要核对「接手自检 6/6」这类声明，但它不能为此再跑一遍回归（时长翻倍）。
    # 项数是脚本自己的事实，让它自报即可 —— 单一真源，文档只管对齐。
    if args.count:
        print(len(CHECKS))
        return 0

    if not PKG.is_dir():
        print(f"[ERR] 找不到 {PKG}——请在 ai-autonomy-team 工作区内运行", file=sys.stderr)
        return 2

    rows, failed = [], []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"检查异常：{type(e).__name__}: {e}"
        rows.append((name, ok, detail))
        if not ok:
            failed.append(name)

    if not args.quiet:
        print("[verify_handover] 接手验收")
        for name, ok, detail in rows:
            print(f"  {'✅' if ok else '🔴'} {name:<14} {detail}")
        print()

    if failed:
        print(f"[FAIL] {len(failed)}/{len(rows)} 项未通过：{', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"[PASS] {len(rows)}/{len(rows)} 项通过" + ("" if args.quiet else "——这台机器可以直接接手"))
    if not args.quiet:
        print()
        print(READING, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
