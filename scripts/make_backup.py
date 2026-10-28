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
| `snapshots-archive-*.tar.gz` | `_trash/snapshots-archive/` 全部文件 | `tar tzf` **文件数**（`isfile()`）== 源文件数；抽 1 个文件比对 sha256。条目数另报（含目录），因为 `tar tzf | wc -l` 会数进目录 |

**不含**：`outputs/`、`audit/` 之外的本机临时件、以及任何未被 git 跟踪且不在
`_trash/snapshots-archive/` 下的内容（如 `.workbuddy/`）。

## 刻意不做的事

- **不自动删除旧备份**。备份工具删东西是自毁倾向；`--list` / `--retention` 只报告，
  删不删由人决定。
- **不加密**（需要口令管理，属于人的决定）：产物在内盘上就是明文，只防盘坏不防窃取。

## 保留策略（2026-10-28，D-0078）

**为什么需要**：截至 2026-10-28 已累积 16 份 / 510MB，而工具自述"不删"，于是
「无策略」被默认成了「无限增长」。一夜之间新增 2 份 —— 增长速率由人的记忆决定，
不是由策略决定，这是必然失控的那种设计。

| 产物 | 策略 | 依据 |
|---|---|---|
| `*.bundle` | **全留**（不设上限） | 每份约 3MB；它是代码历史唯一"可还原"副本，删它省不出空间，却直接削掉回滚深度 |
| `snapshots-archive-*.tar.gz` | 保留**最近 3 份** + **每月最后一份**（月度锚点） | 每份 36→74MB 且逐份近乎全量重复（同一目录的完整快照）；月度锚点保证"跨月还能回到某个已知状态" |

**执行方式**：`--retention` 只做**分类报告**（保留 / 可清理 + 可回收字节数），
**绝不删除、绝不移动**。理由：目标目录在 `~/Backups/`（个人目录），删备份不可逆，
且"哪一份还能扔"依赖恢复场景判断，不是脚本该替人拍的事。要清理由人执行，
命令由本工具给出（含复核命令）。

## 用法

    python3 scripts/make_backup.py                 # = make backup
    python3 scripts/make_backup.py --dest /path    # 换目标目录
    python3 scripts/make_backup.py --no-archive    # 只备 git 历史（跳过 71MB 归档）
    python3 scripts/make_backup.py --list          # 只看现有备份，不做新的
    python3 scripts/make_backup.py --retention     # 按保留策略出「可清理」报告（不删）
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
    """clone 到临时目录，比对三项。返回 (ok, 说明)。

    ★ 临时目录用 `TemporaryDirectory` 上下文管理器回收，**不写 shutil.rmtree** ——
      本仓回归第 20 项对 scripts/ 做破坏性调用的静态扫描（rmtree/unlink/os.remove），
      2026-10-23 写这个函数时当场被它抓到一次。规则本身是对的：备份工具不该带删能力。
    """
    with tempfile.TemporaryDirectory(prefix="bundle-verify-") as td:
        dst = Path(td) / "c"
        rc, _ = _run(["git", "clone", "-q", str(path), str(dst)])
        if rc != 0:
            return False, "clone 失败"
        got = git_facts(dst)
        if not got:
            return False, "clone 产物不可读"
        if got != expect:
            return False, f"不一致：源 {expect} ≠ 还原 {got}"
        return True, f"HEAD {got[0]} / 提交 {got[1]} / 文件 {got[2]} 三项一致"


def tar_counts(path):
    """返回 (文件数, 条目数, 目录数)。

    ★ 文件数只数 `isfile()`——目录条目不算文件。这条语义必须可单独验证：
      2348 那份 9,197 文件 / 10,024 条目（827 目录），两个口径差 8.6%，
      曾让人误判"备份不完整"（D-0055）。
    """
    with tarfile.open(path, "r:gz") as tf:
        members = tf.getmembers()
    return (sum(1 for m in members if m.isfile()),
            len(members),
            sum(1 for m in members if m.isdir()))


def tar_names(path):
    """tar 内的文件名列表（只含文件，不含目录条目）。"""
    with tarfile.open(path, "r:gz") as tf:
        return [m.name for m in tf.getmembers() if m.isfile()]


def verify_tar(path, src_file_count):
    """文件数 == 源文件数，并抽 1 个文件比 sha256。

    同时报出条目数（含目录），让 `tar tzf | wc -l` 的数与工具的数能对上。
    """
    try:
        n_files, n_entries, n_dirs = tar_counts(path)
        names = tar_names(path)
    except (OSError, tarfile.TarError) as e:
        return False, f"tar 不可读：{e}"
    if n_files != src_file_count:
        return False, (f"文件数 {n_files} ≠ 源文件数 {src_file_count}"
                       f"（条目 {n_entries} = 文件 {n_files} + 目录 {n_dirs}）")
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
    return True, (f"{n_files} 个文件 / {n_entries} 个条目（含 {n_dirs} 个目录）；"
                  f"抽样 {Path(sample).name} sha256 一致")


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

        # gz 不能追加，故一次写好：1 个目录 + 1 个文件
        fake_tar = Path(td) / "fake.tar.gz"
        with tarfile.open(fake_tar, "w:gz") as tf:
            d = tarfile.TarInfo("snapshots-archive/x")
            d.type = tarfile.DIRTYPE
            tf.addfile(d)
            payload = b"hello"
            info = tarfile.TarInfo("snapshots-archive/x/readme.md")
            info.size = len(payload)
            import io
            tf.addfile(info, io.BytesIO(payload))
        ok, _ = verify_tar(fake_tar, 999)
        cases.append(("tar 文件数不符应被拒", not ok))

        # ★ 2026-10-24 补：目录条目**不算**文件缺失。
        #   2348 那份 9,197 文件 / 10,024 条目（827 目录）；若过滤条件被改窄，
        #   会把"条目≠文件"误报成备份不完整——正是 D-0055 记的那个坑。
        cases.append(("tar 计数口径：2 条目 = 1 文件 + 1 目录（目录不算文件）",
                      tar_counts(fake_tar) == (1, 2, 1)))

        # ---- 保留策略（D-0078）：必须真的分类，不能是「一律保留」 ----
        #   判据三态都验：bundle 全留 / 同月只留最近 3 / 跨月留月度锚点。
        #   中间那条若被写成「全保留」，cand 会是 0 → 红；写成「只留 3」→ 跨月锚点丢 → 红。
        with tempfile.TemporaryDirectory() as td2:
            d2 = Path(td2)
            same_month = ["ai-autonomy-team-20261001-0001.bundle",
                          "ai-autonomy-team-20261002-0002.bundle",
                          "snapshots-archive-20261024-0044.tar.gz",
                          "snapshots-archive-20261027-2124.tar.gz",
                          "snapshots-archive-20261028-0036.tar.gz",
                          "snapshots-archive-20261028-0041.tar.gz",
                          "snapshots-archive-20261028-0048.tar.gz"]
            for n in same_month:
                (d2 / n).write_bytes(b"x" * 10)
            rows = classify([d2 / n for n in same_month])
            keep = [p.name for p, dec, _, _ in rows if dec == "keep"]
            cand = [p.name for p, dec, _, _ in rows if dec == "candidate"]
            cases.append(("保留策略：bundle 全留（2/2，且不进候选）",
                          len([n for n in keep if n.endswith(".bundle")]) == 2
                          and not [n for n in cand if n.endswith(".bundle")]))
            cases.append(("保留策略：同月 5 份 tar → 留最近 3 / 候选 2（「全保留」会判红）",
                          len([n for n in keep if n.endswith(".tar.gz")]) == 3
                          and len(cand) == 2))

        with tempfile.TemporaryDirectory() as td3:
            d3 = Path(td3)
            cross = ["snapshots-archive-20261031-0001.tar.gz",
                     "snapshots-archive-20261105-0001.tar.gz",
                     "snapshots-archive-20261110-0001.tar.gz",
                     "snapshots-archive-20261120-0001.tar.gz",
                     "snapshots-archive-20261125-0001.tar.gz"]
            for n in cross:
                (d3 / n).write_bytes(b"x" * 10)
            rows = classify([d3 / n for n in cross])
            keep = [p.name for p, dec, _, _ in rows if dec == "keep"]
            cand = [p.name for p, dec, _, _ in rows if dec == "candidate"]
            # 最近 3 = 1125/1120/1110；10-31 虽是全批最旧，但它是 10 月最新 → 月度锚点必留
            cases.append(("保留策略：跨月留锚点（10-31 被留，11-05 才是候选；「只留 3」会判红）",
                          "snapshots-archive-20261031-0001.tar.gz" in keep
                          and cand == ["snapshots-archive-20261105-0001.tar.gz"]))

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
    # 只认本工具会产出的两种文件名。原来用 `*.*` 通配：macOS 一进目录就写
    # `.DS_Store`，于是「最新一份」被它抢走（2026-10-23 实测）——
    # 列目录是给人做删除决策的，把非备份文件混进来等于给错依据。
    items = sorted([p for pat in ("ai-autonomy-team-*.bundle", "snapshots-archive-*.tar.gz")
                    for p in dest.glob(pat)])
    if not items:
        print(f"[ERR] 目录里没有备份：{dest}", file=sys.stderr)
        return BAD
    print(f"备份目录：{dest}")
    total = 0
    for p in items:
        size = p.stat().st_size
        total += size
        age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).days
        print(f"  {p.name:44s} {human(size):>9s}  {age} 天前")
    newest = max(items, key=lambda p: p.stat().st_mtime)
    print(f"\n合计 {human(total)}（{len(items)} 个文件）")
    print(f"最新一份：{newest.name}（{(datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).days} 天前）")
    return OK


def classify(items):
    """按 D-0078 的保留策略把备份分成 keep / purge_candidate。

    返回 [(path, decision, reason, size)]，decision ∈ {"keep", "candidate"}。

    ★ 纯函数（不碰磁盘内容、不删不移动），故可被 `--self-test` 用合成文件名直接验：
      策略错了会当场红，不必等到"磁盘上正好有那么一份"。
    """
    bundles = sorted([p for p in items if p.name.endswith(".bundle")],
                     key=lambda p: p.name)
    tars = sorted([p for p in items if p.name.endswith(".tar.gz")],
                  key=lambda p: p.name, reverse=True)   # 新 → 旧（文件名时间戳可排序）

    out = [(p, "keep", "bundle 全留（代码历史唯一可还原副本，约 3MB/份）", p.stat().st_size)
           for p in bundles]

    keep_recent = set(t.name for t in tars[:3])
    # 月度锚点：每个自然月里**最新**的那一份（tar 已按新→旧排序，首次出现即该月最新）
    month_seen = set()
    month_anchor = set()
    for t in tars:
        m = t.name.split("-")[2][:6] if len(t.name.split("-")) > 2 else "?"
        if m not in month_seen:
            month_seen.add(m)
            month_anchor.add(t.name)

    for t in tars:
        size = t.stat().st_size
        if t.name in keep_recent:
            out.append((t, "keep", "最近 3 份之一", size))
        elif t.name in month_anchor:
            out.append((t, "keep", "月度锚点（该月最新一份）", size))
        else:
            out.append((t, "candidate", "超出最近 3 份且非月度锚点", size))
    return out


def do_retention(dest):
    """只报告：按策略分类 + 可回收字节数 + 人工清理命令。**不删除、不移动任何文件。**

    退出码恒 0（报告型）。这是刻意的：让"该删了"变成一条会说话的输出，
    而不是让脚本自己去删 —— 删备份的风险由人承担，决策也应由人做。
    """
    if not dest.is_dir():
        print(f"[ERR] 备份目录不存在：{dest}", file=sys.stderr)
        return ERR
    items = sorted([p for pat in ("ai-autonomy-team-*.bundle", "snapshots-archive-*.tar.gz")
                    for p in dest.glob(pat)])
    if not items:
        print(f"[ERR] 目录里没有备份：{dest}", file=sys.stderr)
        return BAD

    rows = classify(items)
    keep = [r for r in rows if r[1] == "keep"]
    cand = [r for r in rows if r[1] == "candidate"]
    total = sum(r[3] for r in rows)
    reclaim = sum(r[3] for r in cand)

    print(f"备份目录：{dest}")
    print(f"合计 {human(total)}（{len(rows)} 个文件）｜策略：bundle 全留 / tar 最近 3 + 每月最后一份\n")
    for p, dec, why, size in rows:
        mark = "保留  " if dec == "keep" else "可清理"
        print(f"  [{mark}] {p.name:44s} {human(size):>9s}  {why}")
    print(f"\n保留 {len(keep)} 份（{human(total - reclaim)}）｜可清理 {len(cand)} 份（{human(reclaim)}）")
    if cand:
        print("\n★ 本工具不会删除任何文件（删备份不可逆，且该删哪份取决于恢复场景，属人的决定）。")
        print("  确认要清理由人执行；执行前先复核每份都能解释「为什么它不需要」：")
        print(f"    ls -l {' '.join(str(p) for p, _, _, _ in cand)}")
        print("  确认后（macOS 走废纸篓，可恢复）：")
        print(f"    trash {' '.join(str(p) for p, _, _, _ in cand)}")
    else:
        print("\n无需清理：没有任何文件同时满足「超出最近 3 份」且「非月度锚点」。")
    return OK


def main():
    ap = argparse.ArgumentParser(description="本地备份（可验证）：git bundle + 快照归档 tar")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help=f"目标目录（默认 {DEFAULT_DEST}）")
    ap.add_argument("--no-archive", action="store_true", help="跳过 _trash/snapshots-archive")
    ap.add_argument("--list", action="store_true", help="只列现有备份，不做新的")
    ap.add_argument("--retention", action="store_true",
                    help="按保留策略出「保留/可清理」报告（只读，不删任何文件）")
    ap.add_argument("--self-test", action="store_true", help="只验证「验证逻辑」本身")
    args = ap.parse_args()

    # 浅克隆绝不能当备份源：Git 允许在 shallow 仓库里 `bundle create --all`，
    # 但产出的 bundle **不可还原**（缺历史边界对象），而"能还原"正是本工具的全部价值。
    # 2026-10-23 CI 实测：actions/checkout 默认 --depth 1，本工具自检 4 项挂第 1 项。
    # 与其产一个看起来成功、实际还原不了的包，不如前置拒绝并说清怎么修。
    if not (args.list or args.retention):
        rc, out = _run(["git", "rev-parse", "--is-shallow-repository"])
        if rc == 0 and out.strip() == "true":
            print("[ERR] 当前是浅克隆（shallow）—— bundle 无法还原，拒绝生成。\n"
                  "      修：git fetch --unshallow（或改用完整 clone）后重跑", file=sys.stderr)
            return ERR

    if args.self_test:
        return self_test()

    dest = Path(args.dest).expanduser()
    if args.list:
        return do_list(dest)
    if args.retention:
        return do_retention(dest)

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
    print("       提醒：本工具不会删旧备份。要清理先跑 --retention（按策略分类 + 给出复核/执行命令）。")
    return OK


if __name__ == "__main__":
    sys.exit(main())
