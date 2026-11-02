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
| `memory-*.tar.gz` | 3 份 MEMORY.md（user / claw / home） | **逐份全量** sha256 比对（不是抽样）——只有 2~3 份，全量成本可忽略 |

**为什么纳入 MEMORY.md（2026-10-29）**：三者本体 + 14 份 memory_snapshots + 20+ 个
`.bak` **全压在同一块外置盘** `/Volumes/ZHITAI`（`~/.workbuddy` 与 `~/WorkBuddy` 都是
指向它的软链），而 GitHub 私有远端不含 MEMORY.md（隔离策略：记忆不入库）。外置盘一旦
故障，MEMORY 的所有本地副本一起归零。本产品把 MEMORY 备份到**内置系统盘**
`~/Backups/`（disk3s5），实现真正的跨物理盘副本。

**不含**：`outputs/`、`audit/` 之外的本机临时件、以及任何未被 git 跟踪且不在
`_trash/snapshots-archive/` 下的内容。

## 刻意不做的事

- **不自动删除旧备份**。备份工具删东西是自毁倾向；`--list` / `--retention` 只报告，
  删不删由人决定。
- **不加密**（需要口令管理，属于人的决定）：产物在内盘上就是明文，只防盘坏不防窃取。
- **MEMORY 不上传远端**：含持仓/路径等半敏感信息，遵守本机敏感信息隔离策略——只做本地
  跨盘副本，绝不进任何 Git 仓库、绝不上云。产物权限 **600**，仅属主可读。

## 保留策略（2026-10-28，D-0078）

**为什么需要**：截至 2026-10-28 已累积 16 份 / 510MB，而工具自述"不删"，于是
「无策略」被默认成了「无限增长」。一夜之间新增 2 份 —— 增长速率由人的记忆决定，
不是由策略决定，这是必然失控的那种设计。

| 产物 | 策略 | 依据 |
|---|---|---|
| `*.bundle` | **全留**（不设上限） | 每份约 3MB；它是代码历史唯一"可还原"副本，删它省不出空间，却直接削掉回滚深度 |
| `snapshots-archive-*.tar.gz` | 保留**最近 3 份** + **每月最后一份**（月度锚点） | 每份 36→101MB 且逐份近乎全量重复（同一目录的完整快照）；月度锚点保证"跨月还能回到某个已知状态" |
| `memory-*.tar.gz` | 保留**最近 3 份** + **每月最后一份**（月度锚点） | 每份约 300KB；与快照归档**各自独立**计算保留集（不共用"最近 3"预算——两类体量/用途不同，混算会互相挤占） |

**执行方式**：`--retention` 只做**分类报告**（保留 / 可清理 + 可回收字节数），
**绝不删除、绝不移动**。理由：目标目录在 `~/Backups/`（个人目录），删备份不可逆，
且"哪一份还能扔"依赖恢复场景判断，不是脚本该替人拍的事。要清理由人执行，
命令由本工具给出（含复核命令）。

## 用法

    python3 scripts/make_backup.py                 # = make backup（bundle + 快照归档 + MEMORY）
    python3 scripts/make_backup.py --dest /path    # 换目标目录
    python3 scripts/make_backup.py --no-archive    # 跳过 _trash/snapshots-archive 归档
    python3 scripts/make_backup.py --no-memory     # 跳过 MEMORY.md 备份
    python3 scripts/make_backup.py --list          # 只看现有备份，不做新的
    python3 scripts/make_backup.py --retention     # 按保留策略出「可清理」报告（不删）
    python3 scripts/make_backup.py --self-test     # 只验「验证逻辑」本身对不对

退出码：0 成功且验证通过 / 1 验证失败或无可还原产物 / 2 环境错误
"""

import argparse
import hashlib
import re
import shlex
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
    """返回 (HEAD, 提交数, 受控文件数)。三项都取不到则视为不可用。

    ⚠️ 文件数取 **HEAD 的 tree**（`git ls-tree -r --name-only HEAD`），
    不是索引（`git ls-files`）。2026-11-02 实测踩中：
        本函数原用 ls-files，而 `git bundle create --all` 打的是 **HEAD**。
        只要有 staged 但未提交的改动（例如 `git rm` 一个文件后还没 commit），
        索引就比 HEAD 少一个文件 → 自检里的「完好的 bundle 应通过」立刻 FAIL，
        报错却指向 bundle 验证逻辑 —— **症状与病因完全对不上**，
        会让人去查 verify_bundle，而真实原因是"你有未提交的暂存改动"。
    口径依据：备份产物里装的永远是 HEAD 的内容，所以"这个 bundle 有几个文件"
    必须数 HEAD。数索引等于把"工作树此刻的状态"混进"备份里有什么"。
    """
    rc1, head = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo)
    rc2, cnt = _run(["git", "rev-list", "--count", "HEAD"], cwd=repo)
    rc3, files = _run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo)
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
    return (
        sum(1 for m in members if m.isfile()),
        len(members),
        sum(1 for m in members if m.isdir()),
    )


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
        return False, (
            f"文件数 {n_files} ≠ 源文件数 {src_file_count}"
            f"（条目 {n_entries} = 文件 {n_files} + 目录 {n_dirs}）"
        )
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
    return True, (
        f"{n_files} 个文件 / {n_entries} 个条目（含 {n_dirs} 个目录）；"
        f"抽样 {Path(sample).name} sha256 一致"
    )


# ── MEMORY.md 本地跨盘备份（2026-10-29）────────────────────────────────────
# 为什么：user/claw/home 三份 MEMORY.md 与其 14 份 memory_snapshots、20+ 个 .bak
#   全部位于同一块外置盘（~/.workbuddy 与 ~/WorkBuddy 都是指向 /Volumes/ZHITAI 的软链），
#   而私有远端按隔离策略不含 MEMORY.md。外置盘故障 = MEMORY 全线归零。
#   备份目标 ~/Backups/ 在内置系统盘（disk3s5），这才是真正的跨物理盘副本。
# 为什么全量校验而非抽样：MEMORY 只有 2~3 份，抽样的取样偏差没有收益，反而给"没抽到
#   的那份可能坏了"留口子（D-0055「行数完整≠数值完整」的同族教训）。


def memory_sources(base=None):
    """返回实际存在的 MEMORY 源列表 [(标签, Path)]。

    ★ 用 Path.home() + glob，绝不硬编码 user-<十六进制ID> 目录名（会随账号变）；
      软链解析交给 pathlib（实测 ~/.workbuddy → /Volumes/ZHITAI/workbuddy_data）。
    ★ 源缺失是**正常降级**而非环境错误：外置盘未挂载时 user/claw 可能不在，
      返回空列表由 main 跳过 MEMORY，不拖垮 bundle 主流程。
    ★ base 仅用于测试注入临时根（换机/换账号也能自证"不写死 ID"）；生产不传。
    """
    home = Path(base) if base is not None else Path.home()
    out = []
    for p in sorted((home / ".workbuddy").glob("user-*-personal/MEMORY.md")):
        if p.is_file():
            out.append(("user", p))
    claw = home / "WorkBuddy" / "Claw" / ".workbuddy" / "memory" / "MEMORY.md"
    if claw.is_file():
        out.append(("claw", claw))
    hm = home / ".workbuddy" / "MEMORY.md"
    if hm.is_file():
        out.append(("home", hm))
    return out


def verify_memory_tar(path, expect):
    """逐份全量 sha256 比对 expect={arcname: hexdigest}。

    返回 (ok, 说明)。arcname 用「标签/文件名」避免不同源同名冲突。
    """
    try:
        with tarfile.open(path, "r:gz") as tf:
            members = {m.name: m for m in tf.getmembers() if m.isfile()}
    except (OSError, tarfile.TarError) as e:
        return False, f"tar 不可读：{e}"
    if set(members) != set(expect):
        missing = sorted(set(expect) - set(members))
        extra = sorted(set(members) - set(expect))
        return False, f"文件集合不符：缺 {missing} 多 {extra}"
    try:
        with tarfile.open(path, "r:gz") as tf:
            for arc, want in expect.items():
                data = tf.extractfile(members[arc]).read()
                if hashlib.sha256(data).hexdigest() != want:
                    return False, f"sha256 不一致：{arc}"
    except (OSError, tarfile.TarError, AttributeError) as e:
        return False, f"读取成员失败：{e}"
    return True, f"{len(expect)} 份 MEMORY 逐份全量 sha256 一致"


def build_memory_tar(dest, stamp, sources):
    """打包 MEMORY 到 dest/memory-<stamp>.tar.gz，权限 600，并逐份验证。

    返回 (ok, 说明, path|None)。sources=[(标签, Path)]；空列表 → (True, 跳过, None)。
    ★ 先算每份 sha256 再打包：防止「打包过程中源被并发改写」造成校验假通过
      （memory_snapshot.py docstring：MEMORY 一天被并行会话改 4 次）。
    """
    if not sources:
        return True, "跳过（无 MEMORY 源，可能外置盘未挂载）", None
    import io

    expect = {}
    payloads = []
    for label, src in sources:
        data = src.read_bytes()
        arc = f"{label}/{src.name}"
        expect[arc] = hashlib.sha256(data).hexdigest()
        payloads.append((arc, data))
    tar = dest / f"memory-{stamp}.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        for arc, data in payloads:
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            info.mode = 0o600  # 归档内也标 600，还原后不变成可读
            info.mtime = int(datetime.now().timestamp())
            tf.addfile(info, io.BytesIO(data))
    tar.chmod(0o600)  # 产物本身：仅属主可读
    ok, msg = verify_memory_tar(tar, expect)
    return ok, msg, tar


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
        cases.append(
            (
                "tar 计数口径：2 条目 = 1 文件 + 1 目录（目录不算文件）",
                tar_counts(fake_tar) == (1, 2, 1),
            )
        )

        # ---- 保留策略（D-0078）：必须真的分类，不能是「一律保留」 ----
        #   判据三态都验：bundle 全留 / 同月只留最近 3 / 跨月留月度锚点。
        #   中间那条若被写成「全保留」，cand 会是 0 → 红；写成「只留 3」→ 跨月锚点丢 → 红。
        with tempfile.TemporaryDirectory() as td2:
            d2 = Path(td2)
            same_month = [
                "ai-autonomy-team-20261001-0001.bundle",
                "ai-autonomy-team-20261002-0002.bundle",
                "snapshots-archive-20261024-0044.tar.gz",
                "snapshots-archive-20261027-2124.tar.gz",
                "snapshots-archive-20261028-0036.tar.gz",
                "snapshots-archive-20261028-0041.tar.gz",
                "snapshots-archive-20261028-0048.tar.gz",
            ]
            for n in same_month:
                (d2 / n).write_bytes(b"x" * 10)
            rows = classify([d2 / n for n in same_month])
            keep = [p.name for p, dec, _, _ in rows if dec == "keep"]
            cand = [p.name for p, dec, _, _ in rows if dec == "candidate"]
            cases.append(
                (
                    "保留策略：bundle 全留（2/2，且不进候选）",
                    len([n for n in keep if n.endswith(".bundle")]) == 2
                    and not [n for n in cand if n.endswith(".bundle")],
                )
            )
            cases.append(
                (
                    "保留策略：同月 5 份 tar → 留最近 3 / 候选 2（「全保留」会判红）",
                    len([n for n in keep if n.endswith(".tar.gz")]) == 3 and len(cand) == 2,
                )
            )

        with tempfile.TemporaryDirectory() as td3:
            d3 = Path(td3)
            cross = [
                "snapshots-archive-20261031-0001.tar.gz",
                "snapshots-archive-20261105-0001.tar.gz",
                "snapshots-archive-20261110-0001.tar.gz",
                "snapshots-archive-20261120-0001.tar.gz",
                "snapshots-archive-20261125-0001.tar.gz",
            ]
            for n in cross:
                (d3 / n).write_bytes(b"x" * 10)
            rows = classify([d3 / n for n in cross])
            keep = [p.name for p, dec, _, _ in rows if dec == "keep"]
            cand = [p.name for p, dec, _, _ in rows if dec == "candidate"]
            # 最近 3 = 1125/1120/1110；10-31 虽是全批最旧，但它是 10 月最新 → 月度锚点必留
            cases.append(
                (
                    "保留策略：跨月留锚点（10-31 被留，11-05 才是候选；「只留 3」会判红）",
                    "snapshots-archive-20261031-0001.tar.gz" in keep
                    and cand == ["snapshots-archive-20261105-0001.tar.gz"],
                )
            )

        # ★ 清理命令引用（2026-10-28 审查 P2-1）：含空格路径必须能被 shlex 无损拆回，
        #   否则人复制执行的 trash 命令会拆参 —— 纯字符串用例，不需要文件真实存在。
        import shlex as _shlex

        p_sp = Path("/tmp/x/space dir") / "snapshots-archive-20261024-0044.tar.gz"
        _, trash_cmd = cleanup_commands([(p_sp, "candidate", "x", 1)])
        cases.append(
            (
                "清理命令：空格路径经引用后可无损拆回（未引用=删除命令拼错）",
                _shlex.split(trash_cmd)[1:] == [str(p_sp)] and trash_cmd.startswith("trash "),
            )
        )

        # ★ 月份解析防呆（2026-10-28 审查 P3-5）：非日期形文件名不得凭 split
        #   下标混进「月度锚点」——它连月份都读不出来。名字以 "0" 开头保证
        #   字符串排序时它最旧（否则 keep_recent 会先把它留下，用例失去区分力）。
        with tempfile.TemporaryDirectory() as td4:
            d4 = Path(td4)
            weird_batch = [
                "snapshots-archive-0legacy.tar.gz",
                "snapshots-archive-20261101-0001.tar.gz",
                "snapshots-archive-20261102-0001.tar.gz",
                "snapshots-archive-20261103-0001.tar.gz",
            ]
            for n in weird_batch:
                (d4 / n).write_bytes(b"x" * 10)
            rows = classify([d4 / n for n in weird_batch])
            dec = {p.name: dec for p, dec, _, _ in rows}
            # 区分点：0legacy 靠"月度锚点"留下（旧 split 实现）= keep → 红；
            # 新实现读不出月份 → 只能当 candidate。1101 是"最近 3 份"的正常保留，作对照。
            cases.append(
                (
                    "月份解析：非日期形文件名不得当月度锚点（split 下标实现会判红）",
                    dec.get("snapshots-archive-0legacy.tar.gz") == "candidate"
                    and dec.get("snapshots-archive-20261101-0001.tar.gz") == "keep",
                )
            )

        # ── MEMORY.md 本地跨盘备份（2026-10-29）──
        # 夹具全是合成内容，绝不出现真实持仓/路径/凭据字面量（L-0035/L-0038）。
        with tempfile.TemporaryDirectory() as tdm:
            dm = Path(tdm)
            srcs = []
            for label in ("user", "claw", "home"):
                p = dm / f"src-{label}.md"
                p.write_bytes(("synthetic-memory-content-" + label).encode())
                srcs.append((label, p))
            ok, _, mt = build_memory_tar(dm, "20261029-9999", srcs)
            cases.append(("MEMORY：3 份打包 + 逐份全量 sha256 校验通过", ok and mt is not None))
            cases.append(
                (
                    "MEMORY：产物权限 600（仅属主可读，半敏感信息）",
                    mt is not None and (mt.stat().st_mode & 0o777) == 0o600,
                )
            )
            # 负样本：key 与 tar 内一致但期望 sha 全错 → 必被拒（证明逐份比对在承重，
            #   不是文件集合不符的假拒）。
            wrong_expect = {f"{label}/src-{label}.md": "0" * 64 for label, _ in srcs}
            ok_wrong, _ = verify_memory_tar(mt, wrong_expect) if mt else (True, "")
            cases.append(("MEMORY：期望 sha 全错必被拒（校验承重，非抽样）", not ok_wrong))
            # 负样本：文件集合缺一份 → 必被拒
            fewer = {f"{label}/src-{label}.md": "0" * 64 for label, _ in srcs[:2]}
            ok_fewer, _ = verify_memory_tar(mt, fewer) if mt else (True, "")
            cases.append(("MEMORY：文件集合不符必被拒", not ok_fewer))
            # 正样本：空源降级跳过，不拖垮 bundle 主流程
            ok_empty, _, mt_empty = build_memory_tar(dm, "20261029-9998", [])
            cases.append(
                ("MEMORY：空源（外置盘未挂载）降级跳过且不算失败", ok_empty and mt_empty is None)
            )

        # 两类 tar 各自独立算 retention，不共用「最近 3」预算
        with tempfile.TemporaryDirectory() as tdc:
            dc = Path(tdc)
            names = []
            for i in range(1, 6):
                names.append(f"snapshots-archive-2026100{i}-000{i}.tar.gz")
                names.append(f"memory-2026100{i}-000{i}.tar.gz")
            for n in names:
                (dc / n).write_bytes(b"x" * 10)
            dec = {p.name: d for p, d, _, _ in classify([dc / n for n in names])}
            snap_keep = [n for n in names if n.startswith("snapshots-") and dec.get(n) == "keep"]
            mem_keep = [n for n in names if n.startswith("memory-") and dec.get(n) == "keep"]
            # 混算（错误实现）会让一类独占「最近 3」，另一类 keep 明显少于 3
            cases.append(
                (
                    "MEMORY：两类 tar 各自独立保留（不共用最近 3 预算）",
                    len(snap_keep) == 3 and len(mem_keep) == 3,
                )
            )

        # _backup_files 纳入 memory-*.tar.gz 且仍滤 .DS_Store
        with tempfile.TemporaryDirectory() as tdb:
            db = Path(tdb)
            for n in (
                "ai-autonomy-team-20261001-0001.bundle",
                "snapshots-archive-20261001-0001.tar.gz",
                "memory-20261001-0001.tar.gz",
                ".DS_Store",
            ):
                (db / n).write_bytes(b"x")
            got = [p.name for p in _backup_files(db)]
            cases.append(
                (
                    "MEMORY：_backup_files 纳入 memory-*.tar.gz 且滤 .DS_Store",
                    "memory-20261001-0001.tar.gz" in got and ".DS_Store" not in got,
                )
            )

    bad_n = 0
    for name, ok in cases:
        bad_n += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    print(
        f"\n[{'PASS' if not bad_n else 'FAIL'}] 备份验证逻辑自检：{len(cases) - bad_n}/{len(cases)} 项"
    )
    return OK if not bad_n else BAD


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def _backup_files(dest):
    """本工具会产出的三类文件名（排除 .DS_Store 等异物）。

    ★ 不用 `*.*` 通配：macOS 一进目录就写 `.DS_Store`，「最新一份」会被它抢走
      （2026-10-23 实测）——列目录是给人做删除决策的，混入异物等于给错依据。
    ★ 2026-10-29 加 memory-*.tar.gz（MEMORY 跨盘备份）。
    """
    return sorted(
        [
            p
            for pat in (
                "ai-autonomy-team-*.bundle",
                "snapshots-archive-*.tar.gz",
                "memory-*.tar.gz",
            )
            for p in dest.glob(pat)
        ]
    )


def do_list(dest):
    if not dest.is_dir():
        print(f"[ERR] 备份目录不存在：{dest}", file=sys.stderr)
        return ERR
    items = _backup_files(dest)
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
    print(
        f"最新一份：{newest.name}（{(datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).days} 天前）"
    )
    return OK


def classify(items):
    """按 D-0078 的保留策略把备份分成 keep / purge_candidate。

    返回 [(path, decision, reason, size)]，decision ∈ {"keep", "candidate"}。

    ★ 纯函数（不碰磁盘内容、不删不移动），故可被 `--self-test` 用合成文件名直接验：
      策略错了会当场红，不必等到"磁盘上正好有那么一份"。
    ★ 2026-10-29：snapshots-archive-*.tar.gz 与 memory-*.tar.gz **各自独立**计算
      「最近 3 + 月度锚点」，不共用"最近 3"预算。两类体量（100MB vs 300KB）、
      用途（快照回滚 vs 记忆恢复）、生成频率都不同，混在一起算会让一类把另一类
      挤出保留集（例如一天内打了 5 份快照，MEMORY 当天那份就变 candidate）。
    """
    bundles = sorted([p for p in items if p.name.endswith(".bundle")], key=lambda p: p.name)

    out = [
        (p, "keep", "bundle 全留（代码历史唯一可还原副本，约 3MB/份）", p.stat().st_size)
        for p in bundles
    ]

    # 按前缀分组，每组独立套用同一套保留策略（最近 3 + 月度锚点）
    groups = (
        (
            "snapshots-archive",
            sorted(
                [
                    p
                    for p in items
                    if p.name.startswith("snapshots-archive-") and p.name.endswith(".tar.gz")
                ],
                key=lambda p: p.name,
                reverse=True,
            ),
        ),
        (
            "memory",
            sorted(
                [p for p in items if p.name.startswith("memory-") and p.name.endswith(".tar.gz")],
                key=lambda p: p.name,
                reverse=True,
            ),
        ),
    )
    for prefix, tars in groups:
        label = "快照归档" if prefix == "snapshots-archive" else "MEMORY"
        keep_recent = set(t.name for t in tars[:3])
        # 月度锚点：每个自然月里**最新**的那一份（tar 已按新→旧排序，首次出现即该月最新）
        month_seen = set()
        month_anchor = set()
        for t in tars:
            # ★ 正则而非 split 下标（审查 P3-5）：`split("-")[2][:6]` 把文件名形状
            #   写死在第 3 段，形状一变就静默错月。非日期形文件名不参与锚点
            #   （它不可能是"某月最新一份"——月份从名字里都读不出来）。
            m = re.match(re.escape(prefix) + r"-(\d{6})", t.name)
            if m:
                month = m.group(1)
                if month not in month_seen:
                    month_seen.add(month)
                    month_anchor.add(t.name)

        for t in tars:
            size = t.stat().st_size
            if t.name in keep_recent:
                out.append((t, "keep", f"{label}：最近 3 份之一", size))
            elif t.name in month_anchor:
                out.append((t, "keep", f"{label}：月度锚点（该月最新一份）", size))
            else:
                out.append((t, "candidate", f"{label}：超出最近 3 份且非月度锚点", size))
    return out


def cleanup_commands(cand):
    """把「复核 / 清理」两条命令拼好（纯字符串，可自检）。

    ★ 路径必须经 shlex 引用（2026-10-28 代码审查 P2-1，探针实证）：--dest 可含空格，
      未引用时 `trash …/space dir/…` 会被 shell 拆成两个参数 ——
      这是给人复制执行的**删除命令**，拼错的代价远大于多一行代码。
    """
    args = [str(p) for p, _, _, _ in cand]
    return "ls -l " + shlex.join(args), "trash " + shlex.join(args)


def do_retention(dest):
    """只报告：按策略分类 + 可回收字节数 + 人工清理命令。**不删除、不移动任何文件。**

    退出码恒 0（报告型）。这是刻意的：让"该删了"变成一条会说话的输出，
    而不是让脚本自己去删 —— 删备份的风险由人承担，决策也应由人做。
    """
    if not dest.is_dir():
        print(f"[ERR] 备份目录不存在：{dest}", file=sys.stderr)
        return ERR
    items = _backup_files(dest)
    if not items:
        print(f"[ERR] 目录里没有备份：{dest}", file=sys.stderr)
        return BAD

    rows = classify(items)
    keep = [r for r in rows if r[1] == "keep"]
    cand = [r for r in rows if r[1] == "candidate"]
    total = sum(r[3] for r in rows)
    reclaim = sum(r[3] for r in cand)

    n_mem = len([r for r in rows if r[0].name.startswith("memory-")])
    print(f"备份目录：{dest}")
    print(
        f"合计 {human(total)}（{len(rows)} 个文件，其中 MEMORY {n_mem} 份）"
        f"｜策略：bundle 全留 / 快照归档与 MEMORY 各自「最近 3 + 每月最后一份」\n"
    )
    for p, dec, why, size in rows:
        mark = "保留  " if dec == "keep" else "可清理"
        print(f"  [{mark}] {p.name:44s} {human(size):>9s}  {why}")
    print(
        f"\n保留 {len(keep)} 份（{human(total - reclaim)}）｜可清理 {len(cand)} 份（{human(reclaim)}）"
    )
    if cand:
        ls_cmd, trash_cmd = cleanup_commands(cand)
        print("\n★ 本工具不会删除任何文件（删备份不可逆，且该删哪份取决于恢复场景，属人的决定）。")
        print("  确认要清理由人执行；执行前先复核每份都能解释「为什么它不需要」：")
        print(f"    {ls_cmd}")
        print(
            "  确认后（macOS 走废纸篓，可恢复；`trash` 非 macOS 自带，无则用 Finder 或 osascript）："
        )
        print(f"    {trash_cmd}")
    else:
        print("\n无需清理：没有任何文件同时满足「超出最近 3 份」且「非月度锚点」。")
    return OK


def main():
    ap = argparse.ArgumentParser(
        description="本地跨盘备份（可验证）：git bundle + 快照归档 tar + MEMORY.md"
    )
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help=f"目标目录（默认 {DEFAULT_DEST}）")
    ap.add_argument("--no-archive", action="store_true", help="跳过 _trash/snapshots-archive")
    ap.add_argument("--no-memory", action="store_true", help="跳过 MEMORY.md 备份")
    ap.add_argument("--list", action="store_true", help="只列现有备份，不做新的")
    ap.add_argument(
        "--retention",
        action="store_true",
        help="按保留策略出「保留/可清理」报告（只读，不删任何文件）",
    )
    ap.add_argument("--self-test", action="store_true", help="只验证「验证逻辑」本身")
    ap.add_argument(
        "--memory-only",
        action="store_true",
        help="只备 MEMORY.md（跳过 bundle/快照归档，供手动快速跨盘）",
    )
    args = ap.parse_args()

    # 浅克隆绝不能当备份源：Git 允许在 shallow 仓库里 `bundle create --all`，
    # 但产出的 bundle **不可还原**（缺历史边界对象），而"能还原"正是本工具的全部价值。
    # 2026-10-23 CI 实测：actions/checkout 默认 --depth 1，本工具自检 4 项挂第 1 项。
    # 与其产一个看起来成功、实际还原不了的包，不如前置拒绝并说清怎么修。
    # memory-only 不产 bundle，不需要 git 历史完整，故豁免浅克隆检查。
    if not (args.list or args.retention or args.memory_only):
        rc, out = _run(["git", "rev-parse", "--is-shallow-repository"])
        if rc == 0 and out.strip() == "true":
            print(
                "[ERR] 当前是浅克隆（shallow）—— bundle 无法还原，拒绝生成。\n"
                "      修：git fetch --unshallow（或改用完整 clone）后重跑",
                file=sys.stderr,
            )
            return ERR

    if args.self_test:
        return self_test()

    dest = Path(args.dest).expanduser()
    if args.list:
        return do_list(dest)
    if args.retention:
        return do_retention(dest)

    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    failed = 0

    # ① git 历史（memory-only 跳过：不需要 git，也不该在浅克隆上被拒）
    if not args.memory_only:
        src = git_facts(ROOT)
        if not src:
            print(
                "[ERR] 取不到源仓库事实（HEAD/提交数/文件数），拒绝生成无法验证的备份",
                file=sys.stderr,
            )
            return ERR
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

    # ③ MEMORY.md 跨盘备份（本地内置盘，绝不入库/上云）
    if not args.no_memory:
        sources = memory_sources()
        ok3, msg3, mtar = build_memory_tar(dest, stamp, sources)
        if mtar is None:
            print(f"  ⚠️ MEMORY：{msg3}")
        else:
            print(f"  {'✅' if ok3 else '🔴'} {mtar.name}  {human(mtar.stat().st_size)}  {msg3}")
            failed += 0 if ok3 else 1

    print()
    if failed:
        print(f"[FAIL] {failed} 份产物没通过还原验证 —— 别把它当备份", file=sys.stderr)
        return BAD
    print(f"[PASS] {stamp} 备份完成且逐份验证通过 → {dest}")
    print(
        "       提醒：本工具不会删旧备份。要清理先跑 --retention（按策略分类 + 给出复核/执行命令）。"
    )
    return OK


if __name__ == "__main__":
    sys.exit(main())
