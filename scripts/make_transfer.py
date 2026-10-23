"""make_transfer.py —— 把整棵工作区打成可迁移到 Mac 的 zip。

为什么带整棵工作区而不是只带 migration-package/：
  regress.py / build_package.py / make_round_fixtures.py 当初刻意【不入包】，
  故只带包的话，Mac 上能跑机制但**不能继续开发**，也凑不齐 30 项回归。

设计约束：
  - 纯标准库（zipfile / hashlib），跨平台
  - 不调用 unlink / os.remove / rmtree（本机沙箱拦截；regress 第 20 项静态扫描）
  - 打包前做两道强制检查：① 包内 MANIFEST 自校验 ② 密钥高置信度模式扫描
    任一不过即**拒绝打包**（宁可不出包，也不出一个自校验不过的包）

用法：
    python3 scripts/make_transfer.py                # 默认输出到 outputs/transfer-m4-YYYYMMDD.zip
    python3 scripts/make_transfer.py --check-only   # 只做预检，不打包
    python3 scripts/make_transfer.py --out /tmp/x.zip
退出码：0 成功｜1 预检失败｜2 运行错误
"""

import argparse
import hashlib
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "migration-package"

# 包内顶层目录名（解压后所有东西都在它下面，避免散落）
TOP = "ai-autonomy-team"

INCLUDE_DIRS = [
    "scripts",
    "docs",
    "templates",
    ".learnings",
    "migration-package",
    ".workbuddy/memory",
]

INCLUDE_FILES = [
    "M4-接续指南.md",
    # "_build_xlsx.py" 已于 2026-10-23 移出：它 import openpyxl（违反零第三方依赖），
    # 且 OUT 写死 Windows 路径 C:/Users/Administrator/...，在本机不可运行。
    # 已归档到 _archive/one-off/，需要时从 git 历史取。
]

# ★ 回归第 8 项依赖它：truth.json 与冻结基线逐字节比对。
#   漏掉它 → Mac 上第 8 项直接 FAIL（不是跳过，是 FAIL —— 这是刻意设计）。
#   2026-10-23：从 `_trash/backup-pre-P5/` 迁到 `fixtures/frozen/`。
#   验收依赖不该住在名为 _trash 的目录里 —— 那目录整体不进版本控制，新克隆会缺基线。
INCLUDE_EXACT = [
    "fixtures/frozen/truth.json.frozen",
]

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"}
SKIP_EXT = {".pyc", ".pyo", ".zip"}

# 高置信度密钥模式（低置信度的「apiKey」字样会命中说明文档，故不列入）
# 密钥模式改为从单一真源导入（2026-10-21）：此前本文件与 pre_release_check 各一份副本
from secret_patterns import SECRET_PATTERNS  # noqa: E402,F401


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files():
    """产生 (相对路径 posix, 绝对 Path)。按路径排序保证确定性。"""
    out = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if p.suffix.lower() in SKIP_EXT:
                continue
            out.append((p.relative_to(ROOT).as_posix(), p))
    for f in INCLUDE_FILES + INCLUDE_EXACT:
        p = ROOT / f
        if p.is_file():
            out.append((f, p))
    # 去重（INCLUDE_FILES 可能与 INCLUDE_DIRS 重叠）
    seen, uniq = set(), []
    for rel, p in out:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((rel, p))
    uniq.sort(key=lambda t: t[0])
    return uniq


def check_manifest():
    """包内 MANIFEST 自校验。不可用 gen_manifest --check 即拒绝打包。"""
    gen = PKG / "gen_manifest.py"
    man = PKG / "MANIFEST.json"
    if not gen.is_file() or not man.is_file():
        return False, "缺少 gen_manifest.py 或 MANIFEST.json"
    r = subprocess.run([sys.executable, str(gen), "--root", ".", "--check", "MANIFEST.json"],
                       cwd=str(PKG), capture_output=True, text=True, encoding="utf-8")
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode == 0, tail[-1] if tail else f"rc={r.returncode}"


def check_secrets(files):
    hits = []
    for rel, p in files:
        if p.suffix.lower() not in {".md", ".py", ".json", ".csv", ".txt", ".cmd", ".sh", ".example"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat, label in SECRET_PATTERNS:
            for m in pat.finditer(text):
                hits.append(f"{rel}::{label}::{m.group(0)[:24]}…")
    return hits


def check_frozen():
    p = ROOT / "fixtures/frozen/truth.json.frozen"
    if not p.is_file():
        return False, "缺少冻结基线（回归第 8 项会 FAIL）"
    return True, sha256_file(p)[:16]


def main():
    ap = argparse.ArgumentParser(description="打包整棵工作区供迁移")
    ap.add_argument("--out", default=None, help="输出 zip 路径")
    ap.add_argument("--check-only", action="store_true", help="只做预检，不打包")
    args = ap.parse_args()

    if not PKG.is_dir():
        print("[ERR] 找不到 migration-package/，请在正确的工作区根运行", file=sys.stderr)
        return 2

    print("=" * 72)
    print("预检（任一不过即拒绝打包）")
    print("=" * 72)

    ok_man, man_msg = check_manifest()
    print(f"  [{'OK  ' if ok_man else 'FAIL'}] 包内 MANIFEST 自校验 —— {man_msg}")

    ok_fro, fro_msg = check_frozen()
    print(f"  [{'OK  ' if ok_fro else 'FAIL'}] 冻结基线存在 —— {fro_msg}")

    files = iter_files()
    hits = check_secrets(files)
    print(f"  [{'OK  ' if not hits else 'FAIL'}] 密钥模式扫描 —— "
          f"{'无命中' if not hits else f'{len(hits)} 处待复核'}")
    for h in hits[:10]:
        print(f"          ! {h}")

    if not (ok_man and ok_fro and not hits):
        print("\n[ABORT] 预检未通过，不生成 zip。", file=sys.stderr)
        return 1

    total_bytes = sum(p.stat().st_size for _, p in files)
    print(f"\n  待打包：{len(files)} 个文件 / {total_bytes} 字节（未压缩）")

    if args.check_only:
        print("\n[OK] 仅预检模式，未生成 zip。")
        return 0

    out = Path(args.out) if args.out else (
        ROOT / "outputs" / f"transfer-m4-{datetime.now().strftime('%Y%m%d')}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, p in files:
            z.write(p, arcname=f"{TOP}/{rel}")

    zb = out.stat().st_size
    digest = sha256_file(out)
    side = out.with_suffix(out.suffix + ".sha256")
    side.write_text(f"{digest}  {out.name}\n", encoding="utf-8", newline="\n")

    print("\n" + "=" * 72)
    print("[OK] 转移包已生成")
    print("=" * 72)
    print(f"  路径   ：{out}")
    print(f"  校验值 ：{digest}")
    print(f"  文件数 ：{len(files)}（压缩后 {zb} 字节，压缩前 {total_bytes} 字节）")
    print(f"  校验文件：{side}")
    print()
    print("  下一步（Mac 上）：")
    print(f"    unzip {out.name} && cd {TOP}")
    print("    cd migration-package && python3 precheck.py")
    print("    cd .. && python3 scripts/regress.py   # 预期 45/45，再跑一次仍 45/45")
    print("    cd migration-package && python3 inspect_agent_routing.py --json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
