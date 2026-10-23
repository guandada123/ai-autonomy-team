#!/usr/bin/env python3
"""make_backup.py —— 一条命令做「可验证的」本地备份。

## 为什么需要它

D-0045 定了「整库推私有远端」，但远端**不覆盖** `.gitignore` 的 `_trash/`：
历史快照归档（88 个 / 6319 文件 / 71 MB）只存在本机外置盘上，盘坏即全丢。
2026-10-23 手工做过一次备份，命令散落在对话里 —— 那就等于下次没人会做。

本脚本把那次手工动作固化，并且**每份产物都做还原验证**：
「文件存在」不算备份，「能还原且内容一致」才算。

## 覆盖范围

| 产物 | 内容 | 验证方式 |
|---|---|---|
| `*.bundle`（`git bundle --all`） | 全部 git 历史（不含未跟踪 / 被忽略文件） | `git clone` 到临时目录，比对 HEAD / 提交数 / 受控文件数 |
| `snapshots-archive-*.tar.gz` | `_trash/snapshots-archive/` 全部文件 | `tar tzf` 文件条目数 == 源文件数；抽 1 个文件比对 sha256 |

**不含**：`outputs/`、`audit/` 之外的本机临时件、以及任何未被 git 跟踪且不在
`_trash/snapshots-archive/` 下的内容（如 `.workbuddy/`）。

## 刻意不做的事

- **不自动删除旧备份**。备份工具删东西是自毁倾向；`--list` 只报告，删不删由人决定。
- **不加密**（需要口令管理，属于人的决定）：产物在内盘上就是明文，只防盘坏不防窃取。

## 用法

    python3 scripts/make_backup.py                 # = make backup
    python3 scripts/make_backup.py --dest /path    # 换目标目录
    python3 scripts/make_backup.py --no-archive    # 只备 git 历史（跳过 71MB 归档）
    python3 scripts/make_backup.py --list          # 只看现有备份，不做新的
    python3 scripts/make_backup.py --self-test     # 只验「验证逻辑」本身对不对

退出码：0 成功且验证通过 / 1 验证失败或无可还原产物 / 2 环境错误
"""
import argparse
import hashlib
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "_trash" / "snapshots-archive"
DEFAULT_DEST = Path.home() / "Backups" / "ai-autonomy-team"

OK, BAD, ERR = 0, 1, 2


def _run(args, cwd=None, text=True):
    r = subprocess.run(args, capture_output=True, text=text, cwd=str(cwd or ROOT))
    return r.returncode, (r.stdout if text else r.stdout.decode("utf-8", "ignore"))


def git_facts(repo):
    """返回 (HEAD, 提交数, 受控文件数)。三项都取不到则视为不可用。"""
    rc1, head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    rc2, cnt = _run(["git", "rev-list", "--count", "HEAD"], cwd=repo)
    rc3, files = _run(["git", "ls-files"], cwd=repo)
    if rc1 or rc2 or rc3:
        return None
    return head.strip(), cnt.strip(), len(files.splitlines())


def verify_bundle(path, expect):
    """clone 到临时目录，比对三项。返回 (ok, 说明)。"""
    tmp = Path(tempfile.mkdtemp(prefix="bundle-verify-"))
    try:
        rc, _ = _run(["git", "clone", "-q", str(path), str(tmp / "c")])
        if rc != 0:
            return False, "clone 失败"
        got = git_facts(tmp / "c")
        if not got:
            return False, "clone 产物不可读"
        if got != expect:
            return False, f"不一致：源 {expect} ≠ 还原 {got}"
        return True, f"HEAD {got[0]} / 提交 {got[1]} / 文件 {got[2]} 三项一致"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def verify_tar(path, src_file_count):
    """条目数 == 源文件数，并抽 1 个文件比 sha256。"""
    try:
        with tarfile.open(path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers() if m.isfile()]
    except (OSError, tarfile.TarError) as e:
        return False, f"tar 不可读：{e}"
    if len(names) != src_file_count:
        return False, f"条目数 {len(names)} ≠ 源文件数 {src_file_count}"
    sample = next((n for n in names if n.endswith(".md")), names[0] if names else None)
    if not sample:
        return False, "tar 内没有文件"
    try:
        with tarfile.open(path, "r:gz") as tf:
            data = tf.extractfile(sample).read()
    except (OSError, tarfile.TarError, AttributeError) as e:
        return False, f"抽样读取失败：{e}"
    src = ROOT / "_trash" / sample
    if not src.is_file():
        return False, f"抽样文件在源里找不到：{sample}"
    if hashlib.sha256(data).hexdigest() != hashlib.sha256(src.read_bytes()).hexdigest():
        return False, f"抽样 sha256 不一致：{sample}"
    return True, f"{len(names)} 个文件；抽样 {Path(sample).name} sha256 一致"


def self_test():
    """验证「验证逻辑」自身：好的通过、被篡改的必须拒绝。"""
    cases = []
    expect = git_facts(ROOT)
    if expect is None:
        print("[FAIL] 连源仓库的三项事实都取不到，无法自检", file=sys.stderr)
        return ERR

    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "good.bundle"
        rc, _ = _run(["git", "bundle", "create", str(good), "--all"])
        cases.append(("完好的 bundle 应通过", rc == 0 and verify_bundle(good, expect)[0]))

        bad = Path(td) / "bad.bundle"
        bad.write_bytes(b"\x00" * 64)
        ok, _ = verify_bundle(bad, expect)
        cases.append(("非 bundle 文件应被拒", not ok))

        # 期望值故意改一位：证明比对真的在比，而不是恒真
        wrong = (expect[0], str(int(expect[1]) + 1), expect[2])
        ok, _ = verify_bundle(good, wrong)
        cases.append(("期望值不符应被拒", not ok))

        fake_tar = Path(td) / "fake.tar.gz"
        with tarfile.open(fake_tar, "w:gz") as tf:
            payload = b"hello"
            info = tarfile.TarInfo("snapshots-archive/x/readme.md")
            info.size = len(payload)
            import io
            tf.addfile(info, io.BytesIO(payload))
        ok, _ = verify_tar(fake_tar, 999)
        cases.append(("tar 条目数不符应被拒", not ok))

    bad_n = 0
    for name, ok in cases:
        bad_n += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    print(f"\n[{'PASS' if not bad_n else 'FAIL'}] 备份验证逻辑自检：{len(cases) - bad_n}/{len(cases)} 项")
    return OK if not bad_n else BAD


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def do_list(dest):
    if not dest.is_dir():
        print(f"[ERR] 备份目录不存在：{dest}", file=sys.stderr)
        return ERR
    items = sorted(dest.glob("*.*"))
    if not items:
        print(f"[ERR] 目录里没有备份：{dest}", file=sys.stderr)
        return BAD
    print(f"备份目录：{dest}")
    for p in items:
        age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).days
        print(f"  {p.name:44s} {human(p.stat().st_size):>9s}  {age} 天前")
    newest = max(items, key=lambda p: p.stat().st_mtime)
    print(f"\n最新一份：{newest.name}（{(datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).days} 天前）")
    return OK


def main():
    ap = argparse.ArgumentParser(description="本地备份（可验证）：git bundle + 快照归档 tar")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help=f"目标目录（默认 {DEFAULT_DEST}）")
    ap.add_argument("--no-archive", action="store_true", help="跳过 _trash/snapshots-archive")
    ap.add_argument("--list", action="store_true", help="只列现有备份，不做新的")
    ap.add_argument("--self-test", action="store_true", help="只验证「验证逻辑」本身")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    dest = Path(args.dest).expanduser()
    if args.list:
        return do_list(dest)

    src = git_facts(ROOT)
    if not src:
        print("[ERR] 取不到源仓库事实（HEAD/提交数/文件数），拒绝生成无法验证的备份", file=sys.stderr)
        return ERR
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    failed = 0

    # ① git 历史
    bundle = dest / f"ai-autonomy-team-{stamp}.bundle"
    rc, err = _run(["git", "bundle", "create", str(bundle), "--all"])
    if rc != 0:
        print(f"[ERR] bundle 生成失败：{err[:120]}", file=sys.stderr)
        return ERR
    ok, msg = verify_bundle(bundle, src)
    print(f"  {'✅' if ok else '🔴'} {bundle.name}  {human(bundle.stat().st_size)}  {msg}")
    failed += 0 if ok else 1

    # ② 快照归档（gitignore，远端不覆盖 —— 这才是最脆弱的一份）
    if not args.no_archive:
        if not ARCHIVE.is_dir():
            print(f"  ⚠️ 跳过归档：{ARCHIVE} 不存在")
        else:
            n = sum(1 for p in ARCHIVE.rglob("*") if p.is_file())
            tar = dest / f"snapshots-archive-{stamp}.tar.gz"
            rc, err = _run(["tar", "czf", str(tar), "-C", str(ARCHIVE.parent), ARCHIVE.name])
            if rc != 0:
                print(f"[ERR] tar 生成失败：{err[:120]}", file=sys.stderr)
                return ERR
            ok2, msg2 = verify_tar(tar, n)
            print(f"  {'✅' if ok2 else '🔴'} {tar.name}  {human(tar.stat().st_size)}  {msg2}")
            failed += 0 if ok2 else 1

    print()
    if failed:
        print(f"[FAIL] {failed} 份产物没通过还原验证 —— 别把它当备份", file=sys.stderr)
        return BAD
    print(f"[PASS] {stamp} 备份完成且逐份验证通过 → {dest}")
    print("       提醒：本工具不会删旧备份；要清理用 --list 看清后手动删。")
    return OK


if __name__ == "__main__":
    sys.exit(main())
