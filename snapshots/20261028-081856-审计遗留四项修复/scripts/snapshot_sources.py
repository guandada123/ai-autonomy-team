#!/usr/bin/env python3
"""snapshot_sources.py —— 真源快照（备份 + 差异核验）。

## 为什么存在

本项目**没有 git**，备份是唯一的回滚路径。2026-10-20 会话审计发现：改了
`make_tasks_20.py` / `new_round.py` / `build_package.py` 却**没有任何备份**，
只能靠事后补。靠人记得备份不可靠，故做成命令。

## 快照对象（真源全集，取自 build_package.py 的定义）

    scripts/**          —— 所有脚本真源
    docs/protocol/**    —— 协议真源
    templates/**        —— 提示词模板真源
    docs/decisions/**   —— 决策链真源

## 用法

    python3 scripts/snapshot_sources.py                  # 打快照 → snapshots/<ts>/（并保留最近 3 个）
    python3 scripts/snapshot_sources.py --verify         # 对比最近快照，报告改动/新增/删除
    python3 scripts/snapshot_sources.py --list           # 列出已有快照（含归档数量提示）
    python3 scripts/snapshot_sources.py --label 改口径   # 给快照加标签
    python3 scripts/snapshot_sources.py --no-prune       # 打完不清理（快照会持续累积）
    python3 scripts/snapshot_sources.py --prune-keep 2   # 改保留数为 2
    python3 scripts/snapshot_sources.py --self-test      # 自检 prune 逻辑（用临时目录）

## 调用者痕迹

每个快照的 `_index.json` 带 `caller` 字段（pid / ppid / 父进程命令行）。
2026-10-25 曾出现来源不明的快照且无法归因（平台不落 automation 日志），
故加此痕迹：明日若再现来源不明的快照，可当场对出是谁。

## 保留策略

`snapshots/` 入库，所以不能无限增长：每打一次快照约 +1.0MB / 87 文件。
默认只保留最近 3 个，更早的**移动**（不删除）到 `_trash/snapshots-archive/`。
归档不入库、本机可查，也可 `cp -R` 取回。

退出码：0 成功 / 1 --verify 发现差异 / 2 环境错误
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 2026-10-23：从 `_trash/snapshots` 迁到 `snapshots`。
# 理由同 L-0009：H 项（pre_release_check）依赖「最近快照与真源一致」，
# 而 _trash 整体不入版本控制 —— 新 clone 的仓库 H 项必红，等于门禁无法
# 在干净环境里全过。快照是验收依赖，必须住在交付物里。
# 历史快照（77 个 / 69MB）留在 `_trash/snapshots-archive/`，不入库。
SNAP_ROOT = ROOT / "snapshots"
# 超出保留数的快照移到这里。**只移动，不删除** —— 归档目录在 _trash/ 下（不入库），
# 本机仍可查、可回滚。历史回滚的主责已交给 git（2026-10-23 建仓之后），
# 快照的职责变成「H 项的比对基准」（见 ARCHITECTURE 目录结构一节）。
ARCHIVE_ROOT = ROOT / "_trash" / "snapshots-archive"
SOURCES = ["scripts", "docs/protocol", "docs/decisions", "templates"]
SKIP_SUFFIX = {".pyc", ".bak"}
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_sources():
    """产出 (相对路径, 绝对路径)。"""
    for rel in SOURCES:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix in SKIP_SUFFIX:
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p.relative_to(ROOT).as_posix(), p


def safe_label(label):
    """label 会拼进目录名，必须做路径安全处理。

    2026-10-21 实测：`--label "A+B/C"` 中的 `/` 被当成路径分隔符，
    快照被创建到 `snapshots/<ts>-A+B/C/` 的嵌套目录里，
    导致 latest() 取到父目录、_index.json 读不到、H 项误报。
    """
    if not label:
        return ""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._+-]", "_", label)   # 保留中文/字母/数字/._+-
    return cleaned.replace("/", "_").replace("\\", "_").strip("._") or ""


def caller_info() -> dict:
    """记录谁打了这个快照（pid / ppid / 父进程命令行）。

    2026-10-25 实测： snapshots/ 里出现过一次来源不明的快照（08:24 生成、
    后被 prune 归档），而两个清晨自动化的 prompt 都声明「不修改任何文件」。
    因无调用者痕迹、且 WorkBuddy 侧不落 automation 执行日志，无法归因，
    只能登记待观察。快照生成时把来源写进 _index.json，让「下一个 08:24」
    有据可查。拿不到父进程信息时不阻断打快照（fail-safe：痕迹是附加价值，
    不是快照的前提）。
    """
    info = {"pid": os.getpid(), "ppid": os.getppid(), "parent_cmd": None}
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(info["ppid"])],
                             capture_output=True, text=True, timeout=5)
        cmd = (out.stdout or "").strip()
        info["parent_cmd"] = cmd[:200] if cmd else None
    except Exception:
        pass
    return info


def snapshot(label=""):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = safe_label(label)
    name = f"{ts}{('-' + safe) if safe else ''}"
    dst = SNAP_ROOT / name
    dst.mkdir(parents=True, exist_ok=False)
    index = {}
    for rel, p in iter_sources():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        index[rel] = {"sha256": sha256_of(p), "bytes": p.stat().st_size}
    (dst / "_index.json").write_text(
        json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"),
                    "label": label, "file_count": len(index), "files": index,
                    "caller": caller_info()},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] 快照 {name}：{len(index)} 个文件 → {dst.relative_to(ROOT)}")
    return dst


def catalog(out=None, quiet=False, snap_root=None, archive_root=None):
    """把**全部**快照（入库的 + 归档的 + 已不在盘上的历史记录）写成一份入库清单。

    ## 为什么需要（2026-10-28，D-0078）

    `prune()` 把超期快照移到 `_trash/snapshots-archive/`（不入库）——字节留在本机，
    但**git 里从此没有任何记录**：clone 一份新仓库，只看得到 3 个快照，看不出
    历史上打过多少份、都叫什么、为什么消失。2026-10-28 审计把这条列为
    「数据在盘上但失去 git 追溯」。

    修法不是把字节搬回 git（74MB 且逐份近乎全同，会把仓库压垮），
    而是**入库存清单**：名字 / 标签 / 时间 / 文件数 / 当前所在位置。

    ## 累计而非快照式

    清单会**合并**上一次的内容：某个快照若已从两个目录里都消失（人工清理过），
    条目仍保留并标 `location="missing"` + 记下 `last_seen_location`。
    若每次都只写"现在盘上有什么"，这份文件就只是 `ls` 的缓存，追溯价值为零。

    ## 刻意不写 `generated_at`

    每跑一次快照就改一行时间戳，等于每次提交都带一条无信息 diff（快照 churn 的
    另一半）。"这份清单什么时候产生的"由 git 自己回答（commit 时间），不需要第
    二个真源。

    返回 (写入路径, 统计 dict)。
    """
    out = Path(out) if out else (SNAP_ROOT / "CATALOG.json")
    sr = snap_root if snap_root is not None else SNAP_ROOT
    ar = archive_root if archive_root is not None else ARCHIVE_ROOT
    prev = {}
    if out.is_file():
        try:
            for r in json.loads(out.read_text(encoding="utf-8")).get("snapshots") or []:
                if r.get("name"):
                    prev[r["name"]] = r
        except (OSError, json.JSONDecodeError):
            prev = {}

    rows, seen = [], set()
    for loc, root, in_git in (("snapshots", sr, True),
                              ("_trash/snapshots-archive", ar, False)):
        if not root.is_dir():
            continue
        for d in sorted([x for x in root.iterdir() if x.is_dir()]):
            meta = {}
            idx = d / "_index.json"
            if idx.is_file():
                try:
                    meta = json.loads(idx.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta = {}
            rows.append({"name": d.name, "location": loc, "in_git": in_git,
                         "label": meta.get("label") or "",
                         "created_at": meta.get("created_at") or "",
                         "file_count": meta.get("file_count")})
            seen.add(d.name)

    missing = [prev[n] for n in sorted(prev) if n not in seen]
    for r in missing:
        rows.append({"name": r["name"], "location": "missing", "in_git": False,
                     "label": r.get("label") or "", "created_at": r.get("created_at") or "",
                     "file_count": r.get("file_count"),
                     "last_seen_location": r.get("location"),
                     "last_seen_label": r.get("label") or "",
                     "last_seen_created_at": r.get("created_at") or ""})

    rows.sort(key=lambda r: r["name"])
    stat = {"in_git": sum(1 for r in rows if r["in_git"]),
            "archived": sum(1 for r in rows if r["location"].startswith("_trash")),
            "missing": len(missing), "total": len(rows)}
    doc = {
        "_note": ("快照目录清单（入库）。`snapshots/` 下的快照字节在 git 里；"
                  "`_trash/snapshots-archive/` 下的只在本机盘上并被 ~/Backups 的 tar 覆盖。"
                  "本文件回答「有过哪些快照、现在在哪」，不替代字节。"
                  "location=missing 表示该快照已从两个目录消失（保留历史记录，不删条目）。"),
        "_generated_by": "scripts/snapshot_sources.py --catalog",
        "count": stat,
        "snapshots": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not quiet:
        print(f"[OK] 清单 {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}："
              f"入库 {stat['in_git']} / 归档 {stat['archived']} / 缺失 {stat['missing']}"
              f"（共 {stat['total']}）")
    return out, stat


def prune(keep=3, snap_root=None, archive_root=None, quiet=False):
    """只保留最近 keep 个快照在 snapshots/，其余移到归档目录。返回 (moved, kept)。

    ★ **不删除任何文件**，只移动。归档在 _trash/ 下（不入库），本机仍可查。

    keep 指的是**入库保留数**。默认 3：H 项只用最近 1 个，留 3 个是为了
    「最新那个坏了还能退一个」；再多就是纯占体积 —— 每快照约 1.0MB，
    而快照入 git 后每次提交都要带上，不管就等于每天涨几 MB。

    keep < 1 直接拒绝：那会把 H 项唯一的比对基准也移走，门禁必然红。
    """
    sr = snap_root if snap_root is not None else SNAP_ROOT
    ar = archive_root if archive_root is not None else ARCHIVE_ROOT

    if keep < 1:
        print(
            f"[ERR] keep={keep} 不合法：必须 ≥ 1。移走全部快照会让 H 项失去比对基准。",
            file=sys.stderr,
        )
        return None, 0
    if not sr.is_dir():
        return [], 0

    snaps = sorted([d for d in sr.iterdir() if d.is_dir()])
    if len(snaps) <= keep:
        return [], len(snaps)

    ar.mkdir(parents=True, exist_ok=True)
    moved, skipped = [], []
    for d in snaps[:-keep]:
        dst = ar / d.name
        if dst.exists():
            # 同一秒打过两次同名快照时会出现。不覆盖归档里的旧的那份 ——
            # 覆盖等于静默丢一个历史状态，而"保留历史"正是归档的唯一目的。
            skipped.append(d.name)
            continue
        shutil.move(str(d), str(dst))
        moved.append(d.name)

    if not quiet:
        if moved:
            print(f"[PRUNE] 移出 {len(moved)} 个旧快照 → {ar.relative_to(ROOT)}/")
            for n in moved:
                print(f"        · {n}")
        if skipped:
            print(f"[PRUNE] ⚠️ {len(skipped)} 个同名快照已在归档中，未覆盖（需人工确认）")
            for n in skipped:
                print(f"        · {n}")
        print(f"        入库保留 {keep} 个（原有 {len(snaps)} 个）")
    return moved, len(snaps) - len(moved)


def latest():
    if not SNAP_ROOT.is_dir():
        return None
    dirs = sorted([d for d in SNAP_ROOT.iterdir() if d.is_dir() and (d / "_index.json").is_file()])
    return dirs[-1] if dirs else None


def verify():
    snap = latest()
    if snap is None:
        print("[ERR] 无可用快照，先跑一次不带 --verify 的 snapshot_sources.py", file=sys.stderr)
        return 2
    old = json.loads((snap / "_index.json").read_text(encoding="utf-8"))["files"]
    cur = {rel: {"sha256": sha256_of(p), "bytes": p.stat().st_size} for rel, p in iter_sources()}

    changed = [k for k in old.keys() & cur.keys() if old[k]["sha256"] != cur[k]["sha256"]]
    added = sorted(cur.keys() - old.keys())
    removed = sorted(old.keys() - cur.keys())

    print(f"[verify] 对比最近快照 {snap.name}（{len(old)} 文件）")
    if not (changed or added or removed):
        print("[PASS] 真源与快照完全一致")
        return 0
    if changed:
        print(f"  修改 {len(changed)}：" + ", ".join(sorted(changed)[:8]))
    if added:
        print(f"  新增 {len(added)}：" + ", ".join(added[:8]))
    if removed:
        print(f"  删除 {len(removed)}：" + ", ".join(removed[:8]))
    print("\n[FAIL] 真源已变动 —— 若为有意改动，请先打新快照再继续；若为意外，可从上快照回滚：")
    print(f"  cp -R {snap.relative_to(ROOT)}/scripts/. scripts/")
    return 1


def listing():
    if not SNAP_ROOT.is_dir():
        print("无快照")
        return 0
    for d in sorted([x for x in SNAP_ROOT.iterdir() if x.is_dir()]):
        idx = d / "_index.json"
        if idx.is_file():
            meta = json.loads(idx.read_text(encoding="utf-8"))
            print(f"  {d.name:<32} {meta['file_count']:>3} 文件  {meta.get('label') or ''}")
        else:
            print(f"  {d.name:<32} （无索引）")

    # ★ 归档提示（2026-10-23 审计 M5）：不加这段的话，想找历史状态的人只会看到
    #   入库的那几个，以为更早的全没了 —— 而实际上它们都在 _trash 下好好待着。
    if ARCHIVE_ROOT.is_dir():
        arch = sorted([x for x in ARCHIVE_ROOT.iterdir() if x.is_dir()])
        if arch:
            print()
            print(f"  另有 {len(arch)} 个历史快照已归档在 {ARCHIVE_ROOT.relative_to(ROOT)}/（不入库）")
            print(f"  最近归档: {arch[-1].name}")
            print("  取回其一: cp -R <上述路径>/<快照名> snapshots/")
    return 0


def mirror(dest, all_snaps=False):
    """把快照复制到外部位置（缓解「快照目录自身丢失即无回滚来源」的单点依赖）。

    why：快照与真源同盘。磁盘故障或误删该目录时，
    回滚来源与待恢复对象一起消失。本命令提供显式外拷出口，供拷到外部盘/另一台机。

    dest 是**目标根目录**（不是具体快照目录），会在其下创建 `<快照名>/`。
    """
    if not dest:
        print("[ERR] --mirror 需要目标目录", file=sys.stderr)
        return 2
    dst_root = Path(dest).expanduser()
    try:
        dst_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[ERR] 无法创建目标目录 {dst_root}: {e}", file=sys.stderr)
        return 2

    snaps = sorted([d for d in SNAP_ROOT.iterdir() if d.is_dir() and (d / "_index.json").is_file()]) \
        if SNAP_ROOT.is_dir() else []
    if not snaps:
        print("[ERR] 无可用快照", file=sys.stderr)
        return 2
    targets = snaps if all_snaps else snaplast(snaps)

    n_ok = 0
    for src in targets:
        dst = dst_root / src.name
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            n_ok += 1
            print(f"[OK] 已镜像 {src.name} → {dst}")
        except OSError as e:
            print(f"[ERR] 镜像 {src.name} 失败: {e}", file=sys.stderr)
    print(f"\n共镜像 {n_ok}/{len(targets)} 个快照到 {dst_root}")
    print("提示：目标应位于**不同物理介质**（外置盘/另一台机）才有意义；同盘只是多一个副本。")
    return 0 if n_ok == len(targets) else 1


def snaplast(snaps):
    """取最后一个（最新的）快照目录。"""
    return snaps[-1:]



def self_test():
    """自检 prune 逻辑。全程用临时目录，**不碰真实 snapshots**。"""
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

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        sr, ar = td / "snaps", td / "archive"
        sr.mkdir()

        # ① keep < 1 必须拒绝（否则会把 H 项的基准也移走）
        with contextlib.redirect_stderr(io.StringIO()):
            moved, _ = prune(0, sr, ar, quiet=True)
        expect("keep=0 被拒绝（返回 None）", moved is None)
        expect("keep=0 时未创建归档目录", not ar.exists())

        # ② 空目录
        moved, kept = prune(3, sr, ar, quiet=True)
        expect("空目录 → 无移动、kept=0", (moved, kept) == ([], 0))

        # ③ 不足 keep 时不动
        for name in ["20260101-000001-a", "20260101-000002-b"]:
            (sr / name).mkdir()
        moved, kept = prune(3, sr, ar, quiet=True)
        expect("2 个快照 / keep=3 → 不动", (moved, kept) == ([], 2))
        expect("两个都还在 snapshots/", len(list(sr.iterdir())) == 2)

        # ④ 超出时移走最旧的，且是移动不是删除
        for name in ["20260101-000003-c", "20260101-000004-d", "20260101-000005-e"]:
            (sr / name).mkdir()
        moved, kept = prune(3, sr, ar, quiet=True)
        expect("5 个快照 / keep=3 → 移 2 个", len(moved) == 2 and kept == 3, f"moved={moved}")
        expect("移走的是最旧两个", moved == ["20260101-000001-a", "20260101-000002-b"])
        expect("snapshots/ 剩 3 个", len(list(sr.iterdir())) == 3)
        expect(
            "归档里能找回（移动而非删除）",
            (ar / "20260101-000001-a").is_dir() and (ar / "20260101-000002-b").is_dir(),
        )

        # ⑤ 同名冲突：不覆盖归档里已有的那份
        (sr / "20260101-000000-z").mkdir()
        (ar / "20260101-000000-z").mkdir()
        (ar / "20260101-000000-z" / "marker.txt").write_text("旧的那份", encoding="utf-8")
        moved, _ = prune(3, sr, ar, quiet=True)
        expect("同名冲突 → 不移动", "20260101-000000-z" not in moved, f"moved={moved}")
        expect(
            "归档里的旧内容未被覆盖",
            (ar / "20260101-000000-z" / "marker.txt").read_text(encoding="utf-8") == "旧的那份",
        )

        # ⑥ 清单（D-0078）：必须同时覆盖两个位置，且**保留已消失快照的记录**
        #    判据看三件事：入库/归档/缺失计数各自对得上、鬼条目被标 missing、
        #    排序确定。若把合并逻辑删掉（只写"现在盘上有什么"），鬼条目会消失 → 红。
        (sr / "20260101-000003-c" / "_index.json").write_text(
            json.dumps({"label": "标签C", "created_at": "2026-01-01T00:00:03",
                        "file_count": 7}), encoding="utf-8")
        cat_out = td / "CATALOG.json"
        cat_out.write_text(json.dumps({"snapshots": [
            {"name": "20200101-000000-ghost", "location": "_trash/snapshots-archive",
             "label": "已消失的旧快照", "created_at": "2020-01-01T00:00:00",
             "file_count": 5}]}, ensure_ascii=False), encoding="utf-8")
        _, stat = catalog(cat_out, quiet=True, snap_root=sr, archive_root=ar)
        doc = json.loads(cat_out.read_text(encoding="utf-8"))
        rows = {r["name"]: r for r in doc["snapshots"]}
        n_sr = len([d for d in sr.iterdir() if d.is_dir()])
        n_ar = len([d for d in ar.iterdir() if d.is_dir()])
        expect("清单：入库计数 = snapshots/ 目录数", stat["in_git"] == n_sr, f"{stat['in_git']} vs {n_sr}")
        expect("清单：归档计数 = 归档目录数", stat["archived"] == n_ar, f"{stat['archived']} vs {n_ar}")
        expect("清单：已消失快照仍留记录且标 missing",
               rows.get("20200101-000000-ghost", {}).get("location") == "missing"
               and rows["20200101-000000-ghost"].get("last_seen_label") == "已消失的旧快照")
        expect("清单：index 元信息被带上",
               rows.get("20260101-000003-c", {}).get("label") == "标签C"
               and rows["20260101-000003-c"].get("file_count") == 7)
        expect("清单：按名字排序（确定性）",
               [r["name"] for r in doc["snapshots"]] == sorted(r["name"] for r in doc["snapshots"]))

    print()
    if fails:
        print(f"[FAIL] 自检未通过：{len(fails)}/{n_ok + len(fails)} 项失败")
        for f in fails:
            print(f"  · {f}")
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{n_ok} 项")
    return 0


def main():
    ap = argparse.ArgumentParser(description="真源快照 / 差异核验")
    ap.add_argument("--verify", action="store_true", help="对比最近快照")
    ap.add_argument("--list", action="store_true", help="列出已有快照")
    ap.add_argument("--label", default="", help="给快照加标签（如：改口径）")
    ap.add_argument("--mirror", default="", metavar="DIR",
                    help="把快照复制到外部目录（缓解单点依赖）")
    ap.add_argument("--mirror-all", action="store_true",
                    help="配合 --mirror：镜像全部快照而非仅最新")
    ap.add_argument("--prune-keep", type=int, default=3, metavar="N",
                    help="打完快照后只保留最近 N 个入库，其余移到归档（默认 3）")
    ap.add_argument("--no-prune", action="store_true",
                    help="打完不清理，保留全部（快照会持续累积，谨慎使用）")
    ap.add_argument("--self-test", action="store_true",
                    help="自检 prune 逻辑（用临时目录，不碰真实快照）")
    ap.add_argument("--catalog", action="store_true",
                    help="重建入库的快照清单 snapshots/CATALOG.json（只读快照目录）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not (ROOT / "scripts").is_dir():
        print(f"[ERR] 目录缺失: {ROOT / 'scripts'}", file=sys.stderr)
        return 2
    if args.list:
        return listing()
    if args.mirror:
        return mirror(args.mirror, args.mirror_all)
    if args.verify:
        return verify()
    if args.catalog:
        catalog()
        return 0
    snapshot(args.label)
    if not args.no_prune:
        moved, _ = prune(args.prune_keep)
        if moved is None:          # keep 非法
            return 2
    # 打完/移完立即刷新清单：清单若只在人工记得跑 --catalog 时更新，
    # 它就会在上述 churn 之后立刻变成过期事实（正是本次要修的那个病）。
    catalog()
    return 0


if __name__ == "__main__":
    sys.exit(main())
