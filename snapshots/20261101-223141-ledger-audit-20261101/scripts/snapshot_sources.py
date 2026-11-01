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
    python3 scripts/snapshot_sources.py --verify         # 三向比对：索引 ↔ 副本字节 ↔ 真源
    python3 scripts/snapshot_sources.py --restore latest # 回滚 dry-run（校验副本完好，不写盘）
    python3 scripts/snapshot_sources.py --restore latest --apply   # 确认后才写回真源
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

退出码：0 成功 / 1 --verify 发现差异、--restore 因副本不完好被拒 / 2 环境错误

## --verify 与 --restore（2026-10-28，F3）

旧版只比「索引 vs 真源」，从不读副本字节：副本被删或被改时，它照样 PASS ——
门禁 H 是假绿，且照它给的 `cp -R` 回滚会把坏字节盖到真源上。现改为三向比对，
副本状态（缺失/被改/不可读）单独成类报告；回滚一律走 `--restore`（先校验后写、
默认 dry-run、写后 readback），不再给出裸 `cp -R`。
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
# ★ 2026-10-29（H 覆盖盲区修复）：真源不止 scripts/。
#   Makefile / 根级四文档 / audit/（判据与冻结基线）/ .learnings / .github/workflows
#   同样是「改了不重跑就没意义」的真源。原范围漏掉它们 = 改这些文件永远不需要打快照，
#   H 项对它们零约束（F6 实测：一次改了 12 个文件，只有 5 个被 H 约束）。
#   outputs/ 是机器产出、runs/ 是业务数据，均刻意不入快照（真源 = 可执行代码 + 判据 + 决策 + 文档）。
SOURCES = ["scripts", "docs/protocol", "docs/decisions", "templates",
           "audit", ".learnings", ".github/workflows", "tools"]
# 根级单文件（SOURCES 只支持目录，单文件走这里）
ROOT_FILES = ["Makefile", "README.md", "ARCHITECTURE.md", "DEPENDENCIES.md", "M4-接续指南.md"]
SKIP_SUFFIX = {".pyc", ".bak"}
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_sources(root=None):
    """产出 (相对路径, 绝对路径)。

    root 可注入：自检要在临时目录里造一个迷你仓库，不能用真仓库做污染实验
    （自校验套件不得写它正在校验的资产）。
    """
    base_root = Path(root) if root is not None else ROOT
    for rel in SOURCES:
        base = base_root / rel
        if base.is_file():                      # 2026-10-29：支持根级单文件（Makefile 等）
            if base.suffix not in SKIP_SUFFIX:
                yield rel, base
            continue
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix in SKIP_SUFFIX:
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p.relative_to(base_root).as_posix(), p
    for rel in ROOT_FILES:                      # 根级四文档 + Makefile：不在任何目录里
        base = base_root / rel
        if base.is_file() and base.suffix not in SKIP_SUFFIX:
            yield rel, base


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


def snapshot(label="", snap_root=None, root=None):
    base_root = Path(root) if root is not None else ROOT
    sr = Path(snap_root) if snap_root is not None else SNAP_ROOT
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = safe_label(label)
    name = f"{ts}{('-' + safe) if safe else ''}"
    dst = sr / name
    dst.mkdir(parents=True, exist_ok=False)
    index = {}
    for rel, p in iter_sources(base_root):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        index[rel] = {"sha256": sha256_of(p), "bytes": p.stat().st_size}
    (dst / "_index.json").write_text(
        json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"),
                    "label": label, "file_count": len(index), "files": index,
                    "caller": caller_info()},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] 快照 {name}：{len(index)} 个文件 → "
          f"{dst.relative_to(base_root) if dst.is_relative_to(base_root) else dst}")
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
        except (OSError, json.JSONDecodeError) as e:
            # ★ 不静默（2026-10-28 代码审查 P2-2，探针实证）：读坏既有清单时
            #   missing 历史会丢，本次写入只含当前盘上的快照 —— 「累计式」静默降级
            #   成快照式，正是本文件要防的病。也不硬失败：快照流程不该被旁挂清单
            #   卡死；原文若已入库，可从 git 历史取回。
            try:
                disp = out.relative_to(ROOT)
            except ValueError:
                disp = out
            print(f"[WARN] 既有清单不可读（{e}）——本次写入只含当前盘上的快照，"
                  f"已记录的 missing 历史将丢失（若已入库，原文可从 git 历史取回：{disp}）",
                  file=sys.stderr)
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
    # ★ fresh clone 边缘（审查 P3-7）：snapshots/ 不存在且毫无记录时，mkdir 会凭空
    #   创建空目录，把门禁 H 的报错从「无快照目录」变成「verify 失败」，掩盖真实状态。
    if not rows and not prev and not out.parent.exists():
        print(f"[WARN] 无任何快照记录且 {out.parent} 不存在 —— 不创建目录、不写清单"
              f"（先打一次快照再 --catalog）", file=sys.stderr)
        return out, {"in_git": 0, "archived": 0, "missing": 0, "total": 0}
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


def three_way(snap_dir, root=None):
    """三向比对：索引记录 ↔ 快照副本的实际字节 ↔ 真源的实际字节。

    ## 为什么必须有（2026-10-28，F3）

    旧版 `verify()` 只比「索引 vs 真源」，**从不读副本一个字节**。实测后果：
    删掉副本、或把副本内容改掉，`--verify` 照样 PASS ——
    绿的只是「索引与真源一致」，而**回滚来源已经坏了却无人知晓**。
    门禁 H 因此是假绿：它守的回滚路径在它绿着的时候就已经不可用，
    防线反向变成攻击面（真出事时照着它给的命令回滚，会把坏字节盖到真源上）。

    ## 三类差异（互不重叠，分别报告）

    | 类 | 含义 | 谁坏了 |
    |---|---|---|
    | A `source_*` | 真源相对索引有变（改/增/删） | 真源动了（正常迭代，打新快照即可） |
    | B `copy_missing` | 副本文件不在盘上 | 回滚来源缺件 |
    | C `copy_corrupt` | 副本在，但哈希 ≠ 索引 | 回滚来源被污染 |

    `source_vs_copy` 是诊断项（真源与副本直接对撞），用于识别「三方各一值」。
    """
    base_root = Path(root) if root is not None else ROOT
    snap_dir = Path(snap_dir)
    idx = json.loads((snap_dir / "_index.json").read_text(encoding="utf-8"))["files"]
    cur = {rel: {"sha256": sha256_of(p), "bytes": p.stat().st_size}
           for rel, p in iter_sources(base_root)}

    source_changed = sorted(r for r in idx.keys() & cur.keys()
                            if idx[r]["sha256"] != cur[r]["sha256"])
    source_added = sorted(cur.keys() - idx.keys())
    source_removed = sorted(idx.keys() - cur.keys())

    copy_missing, copy_corrupt, copy_unreadable, src_vs_copy = [], [], [], []
    for rel in sorted(idx):
        cp = snap_dir / rel
        if not cp.is_file():
            copy_missing.append(rel)
            continue
        try:
            csha = sha256_of(cp)
        except OSError as e:
            copy_unreadable.append((rel, type(e).__name__))
            continue
        if csha != idx[rel]["sha256"]:
            copy_corrupt.append(rel)
        if rel in cur and cur[rel]["sha256"] != csha:
            src_vs_copy.append(rel)

    return {"snapshot": snap_dir.name, "index_count": len(idx), "source_count": len(cur),
            "source_changed": source_changed, "source_added": source_added,
            "source_removed": source_removed,
            "copy_missing": copy_missing, "copy_corrupt": copy_corrupt,
            "copy_unreadable": copy_unreadable, "source_vs_copy": src_vs_copy,
            "clean": not (source_changed or source_added or source_removed
                          or copy_missing or copy_corrupt or copy_unreadable)}


def _resolve_snap(target, snap_root=None):
    sr = Path(snap_root) if snap_root is not None else SNAP_ROOT
    if not sr.is_dir():
        return None
    dirs = sorted([d for d in sr.iterdir()
                   if d.is_dir() and (d / "_index.json").is_file()])
    if not dirs:
        return None
    if target in ("", "latest", "last"):
        return dirs[-1]
    for d in dirs:
        if d.name == target:
            return d
    return None


def verify(snap_root=None, root=None, quiet=False):
    snap = _resolve_snap("latest", snap_root)
    if snap is None:
        print("[ERR] 无可用快照，先跑一次不带 --verify 的 snapshot_sources.py", file=sys.stderr)
        return 2
    base_root = Path(root) if root is not None else ROOT
    tw = three_way(snap, base_root)

    def _line(tag, items, why):
        print(f"  {tag} {len(items)}：" + ", ".join(items[:8]) + (" …" if len(items) > 8 else ""))
        print(f"      → {why}")

    if not quiet:
        print(f"[verify] 三向比对 {snap.name}（索引 {tw['index_count']} 文件 / "
              f"真源 {tw['source_count']} 文件）")
    if tw["clean"]:
        if not quiet:
            print("[PASS] 索引 ↔ 副本 ↔ 真源 三方一致")
        return 0
    if not quiet:
        if tw["source_changed"] or tw["source_added"] or tw["source_removed"]:
            _line("A 真源改动", tw["source_changed"] + tw["source_added"] + tw["source_removed"],
                  "真源相对快照变了（正常迭代则打新快照）")
        if tw["copy_missing"]:
            _line("B 副本缺失", tw["copy_missing"],
                  "回滚来源缺件 —— 这份快照已不能完整回滚")
        if tw["copy_corrupt"]:
            _line("C 副本被改", tw["copy_corrupt"],
                  "副本字节与索引不符 —— 回滚来源被污染")
        if tw["copy_unreadable"]:
            _line("C 副本不可读", [f"{r}({e})" for r, e in tw["copy_unreadable"]],
                  "副本存在但读不出来 —— 回滚来源不可用")
        if tw["source_vs_copy"] and not (tw["copy_corrupt"] or tw["copy_missing"]):
            _line("D 真源与副本互异", tw["source_vs_copy"], "两者都在，但字节不同")

    if not quiet:
        print("\n[FAIL] 若为有意改动，请先打新快照再继续；若要回滚，用带校验的 restore：")
        print("  python3 scripts/snapshot_sources.py --restore latest            # 先看 dry-run")
        print("  python3 scripts/snapshot_sources.py --restore latest --apply    # 确认后才写回")
    return 1


def restore(target="latest", root=None, snap_root=None, apply=False, quiet=False):
    """把快照副本写回真源。**默认 dry-run**，必须显式 --apply 才落盘。

    为什么不再建议裸 `cp -R <快照>/scripts/. scripts/`：`cp` 不校验副本是否完好。
    副本若已污染或缺件，裸 cp 会把坏字节盖到真源上 ——
    回滚这个动作本身变成事故。故四道闸：
      ① 写回前逐文件校验副本 sha == 索引记录；
      ② 任一文件缺失/不符即**整体拒绝**（部分回滚会造出半新半旧的真源）；
      ③ 默认 dry-run，`--apply` 才写；
      ④ 写后 readback：真源 sha 必须等于索引（写了 ≠ 写对）。
    """
    sr = Path(snap_root) if snap_root is not None else SNAP_ROOT
    snap = _resolve_snap(target, sr)
    if snap is None:
        print(f"[ERR] 找不到可用快照：{target}", file=sys.stderr)
        return 2
    base_root = Path(root) if root is not None else ROOT
    tw = three_way(snap, base_root)

    problems = [(r, "副本缺失") for r in tw["copy_missing"]]
    problems += [(r, "副本哈希与索引不符") for r in tw["copy_corrupt"]]
    problems += [(r, f"副本不可读({e})") for r, e in tw["copy_unreadable"]]
    if problems:
        if not quiet:
            print(f"[REFUSE] 快照 {snap.name} 的副本不完整，拒绝回滚（{len(problems)} 处）：",
                  file=sys.stderr)
            for rel, why in problems[:10]:
                print(f"  · {rel} —— {why}", file=sys.stderr)
            if len(problems) > 10:
                print(f"  · …另有 {len(problems) - 10} 处", file=sys.stderr)
            print("  回滚来源本身已不可信；真源未被改动。", file=sys.stderr)
        return 1

    idx = json.loads((snap / "_index.json").read_text(encoding="utf-8"))["files"]
    n_diff = (len(tw["source_changed"]) + len(tw["source_added"]) + len(tw["source_removed"]))
    if not apply:
        if not quiet:
            print(f"[DRY-RUN] 快照 {snap.name}：{len(idx)} 个文件副本校验通过，可以回滚")
            print(f"  真源当前与快照相差 {n_diff} 个文件；确认后加 --apply 才写回")
        return 0

    n = 0
    for rel in sorted(idx):
        dst = base_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snap / rel, dst)
        n += 1

    after = three_way(snap, base_root)
    bad = after["source_changed"] + after["source_removed"]
    if bad:
        print(f"[ERR] 写回后仍有 {len(bad)} 个文件与快照不一致：{bad[:5]}", file=sys.stderr)
        return 1
    if not quiet:
        print(f"[OK] 已从 {snap.name} 回滚 {n} 个文件，写后校验通过")
        if after["source_added"]:
            print(f"  另有 {len(after['source_added'])} 个快照外的新文件保持不动（restore 不删文件）")
    return 0


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

        # ⑦ 清单文件损坏：必须告警而非静默丢历史（2026-10-28 审查 P2-2，
        #    探针实证：损坏后 ghost 条目消失且无提示）。判据：stderr 有 WARN
        #    且本次仍产出合法清单（不崩、含当前盘上快照）。
        bad_out = td / "CATALOG-bad.json"
        bad_out.write_text("not-json{{{", encoding="utf-8")
        _err = io.StringIO()
        with contextlib.redirect_stderr(_err):
            _, stat_bad = catalog(bad_out, quiet=True, snap_root=sr, archive_root=ar)
        expect("清单：文件损坏时告警而非静默（WARN 可见且仍产出合法清单）",
               "WARN" in _err.getvalue()
               and json.loads(bad_out.read_text(encoding="utf-8"))["count"]["total"] >= 1)

        # ⑧ 三向比对与 restore（2026-10-28，F3）
        #    真仓库不做污染实验，这里造迷你仓库：两份真源 + 一份快照。
        def _mk(tag):
            r = td / f"repo-{tag}"
            (r / "scripts").mkdir(parents=True)
            (r / "scripts" / "a.py").write_text("A\n", encoding="utf-8")
            (r / "scripts" / "b.py").write_text("B\n", encoding="utf-8")
            # 2026-10-29（H 覆盖盲区）：迷你仓库同步造根文件与 audit/，
            # 否则「新 SOURCES 生效」只能靠人眼确认。
            (r / "audit").mkdir()
            (r / "audit" / "x.md").write_text("X\n", encoding="utf-8")
            (r / "Makefile").write_text("all:\n\techo ok\n", encoding="utf-8")
            sr = td / f"snaps-{tag}"
            sr.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                snap = snapshot("t", snap_root=sr, root=r)
            return r, sr, snap

        def _quiet(fn, *a, **k):
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                return fn(*a, **k)

        # 正样本：三方一致
        r, sr, snap = _mk("ok")
        expect("三向：干净快照 → 三方一致", three_way(snap, r)["clean"] is True)
        expect("三向：干净快照 → verify rc=0（正样本）",
               _quiet(verify, snap_root=sr, root=r) == 0)

        # ⑧b 覆盖面（2026-10-29，H 覆盖盲区修复）：SOURCES 不再只是 scripts/
        idx = json.loads((snap / "_index.json").read_text(encoding="utf-8"))["files"]
        expect("覆盖面：根文件 Makefile 与 audit/ 进入快照索引（H 对它们有约束力）",
               "Makefile" in idx and "audit/x.md" in idx,
               f"索引 {len(idx)} 项")
        expect("防回退：SOURCES 必须含 audit/.learnings/.github/workflows，ROOT_FILES 5 个",
               {"audit", ".learnings", ".github/workflows"} <= set(SOURCES)
               and set(ROOT_FILES) == {"Makefile", "README.md", "ARCHITECTURE.md",
                                       "DEPENDENCIES.md", "M4-接续指南.md"})
        # 负样本：新增范围的真源被改，必须命中 A 类 —— 旧范围下这里会假绿
        (r / "Makefile").write_text("all:\n\techo changed\n", encoding="utf-8")
        (r / "audit" / "x.md").write_text("X2\n", encoding="utf-8")
        tw = three_way(snap, r)
        expect("覆盖面负样本：根文件/audit 被改 → A 类命中（旧范围在此假绿）",
               sorted(tw["source_changed"]) == ["Makefile", "audit/x.md"],
               str(tw["source_changed"]))

        # 负样本 A：真源被改
        r, sr, snap = _mk("src")
        (r / "scripts" / "a.py").write_text("A-modified\n", encoding="utf-8")
        tw = three_way(snap, r)
        expect("三向：真源被改 → A 类命中", tw["source_changed"] == ["scripts/a.py"],
               str(tw["source_changed"]))
        expect("三向：真源被改时副本仍完好（B/C 不得误报）",
               tw["copy_missing"] == [] and tw["copy_corrupt"] == [])

        # 负样本 B：副本被删 —— 旧版 verify 在此假绿（只读索引，不读副本）
        r, sr, snap = _mk("del")
        # 用「移出」而非 unlink 造副本缺失：scripts 下禁止删除调用
        # （回归第 20 项：删除的唯一去向是改名到 _trash/）。移出同样让副本不在盘上，
        # 且可找回 —— 造场景不该顺手破坏项目纪律。
        shutil.move(str(snap / "scripts" / "a.py"), str(td / "gone-del-a.py"))
        tw = three_way(snap, r)
        expect("三向：副本被删 → B 类命中", tw["copy_missing"] == ["scripts/a.py"],
               str(tw["copy_missing"]))
        expect("三向：副本被删 → verify rc=1（旧实现在此 PASS，即假绿）",
               _quiet(verify, snap_root=sr, root=r) == 1)

        # 负样本 C：副本被污染
        r, sr, snap = _mk("cor")
        (snap / "scripts" / "a.py").write_text("TAMPERED\n", encoding="utf-8")
        tw = three_way(snap, r)
        expect("三向：副本被污染 → C 类命中", tw["copy_corrupt"] == ["scripts/a.py"])
        expect("三向：副本被污染但真源没动 → A 类不得命中", tw["source_changed"] == [])

        # 负样本 D：三方全改
        r, sr, snap = _mk("all")
        (r / "scripts" / "a.py").write_text("NEW\n", encoding="utf-8")
        shutil.move(str(snap / "scripts" / "a.py"), str(td / "gone-all-a.py"))
        (snap / "scripts" / "b.py").write_text("TAMPER\n", encoding="utf-8")
        tw = three_way(snap, r)
        expect("三向：三方全改 → A/B/C 三类齐发",
               tw["source_changed"] == ["scripts/a.py"]
               and tw["copy_missing"] == ["scripts/a.py"]
               and tw["copy_corrupt"] == ["scripts/b.py"], str(tw))

        # restore 负样本：副本被污染 → 拒绝，且绝不能把坏字节写进真源
        r, sr, snap = _mk("r-tamper")
        (r / "scripts" / "a.py").write_text("LIVE\n", encoding="utf-8")
        (snap / "scripts" / "a.py").write_text("TAMPER\n", encoding="utf-8")
        expect("restore：副本被污染 → 拒绝（rc=1）",
               _quiet(restore, "latest", root=r, snap_root=sr, apply=True) == 1)
        expect("restore：拒绝时真源未被坏副本写坏",
               (r / "scripts" / "a.py").read_text(encoding="utf-8") == "LIVE\n")

        # restore 负样本：副本缺件 → 整体拒绝（防半新半旧）
        r, sr, snap = _mk("r-missing")
        shutil.move(str(snap / "scripts" / "b.py"), str(td / "gone-rmiss-b.py"))
        (r / "scripts" / "a.py").write_text("LIVE2\n", encoding="utf-8")
        expect("restore：副本缺件 → 整体拒绝（rc=1）",
               _quiet(restore, "latest", root=r, snap_root=sr, apply=True) == 1)
        expect("restore：缺件拒绝时真源同样未被改动",
               (r / "scripts" / "a.py").read_text(encoding="utf-8") == "LIVE2\n")

        # restore 负样本：默认 dry-run 不写盘
        r, sr, snap = _mk("r-dry")
        (r / "scripts" / "a.py").write_text("CHANGED\n", encoding="utf-8")
        expect("restore：默认 dry-run → rc=0 但真源不动",
               _quiet(restore, "latest", root=r, snap_root=sr) == 0
               and (r / "scripts" / "a.py").read_text(encoding="utf-8") == "CHANGED\n")

        # restore 正样本：干净副本 + --apply → 写回且 readback 通过
        expect("restore：干净副本 + --apply → rc=0（正样本）",
               _quiet(restore, "latest", root=r, snap_root=sr, apply=True) == 0)
        expect("restore：写后真源 == 快照（readback 校验）",
               (r / "scripts" / "a.py").read_text(encoding="utf-8") == "A\n")
        expect("restore：回滚后再 verify → rc=0",
               _quiet(verify, snap_root=sr, root=r) == 0)

        # restore 负样本：目标不存在 / 无快照 → rc=2（环境错误，与"拒绝"区分）
        expect("restore：目标快照不存在 → rc=2",
               _quiet(restore, "no-such-snapshot", root=r, snap_root=sr, apply=True) == 2)
        expect("verify：无快照目录 → rc=2",
               _quiet(verify, snap_root=td / "no-such-dir", root=r) == 2)

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
    ap.add_argument("--verify", action="store_true",
                    help="三向比对（索引 ↔ 副本字节 ↔ 真源）")
    ap.add_argument("--restore", nargs="?", const="latest", default=None, metavar="NAME",
                    help="把快照副本写回真源（默认 latest；先 dry-run，加 --apply 才写）")
    ap.add_argument("--apply", action="store_true",
                    help="配合 --restore：真正写回（默认只 dry-run）")
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
    if args.apply and args.restore is None:
        print("[ERR] --apply 只在 --restore 时有意义", file=sys.stderr)
        return 2
    if args.restore is not None:
        return restore(args.restore, apply=args.apply)
    if args.list:
        return listing()
    if args.mirror:
        return mirror(args.mirror, args.mirror_all)
    if args.verify:
        return verify()
    if args.catalog:
        try:
            catalog()
        except Exception as e:      # 清单是本命令的目的，失败要说明白，不留裸 traceback
            print(f"[ERR] 清单生成失败：{e}", file=sys.stderr)
            return 1
        return 0
    snapshot(args.label)
    if not args.no_prune:
        moved, _ = prune(args.prune_keep)
        if moved is None:          # keep 非法
            return 2
    # 打完/移完立即刷新清单：清单若只在人工记得跑 --catalog 时更新，
    # 它就会在上述 churn 之后立刻变成过期事实（正是本次要修的那个病）。
    # ★ 快照本体已成功，清单失败不能当成"快照失败"重试（会再产一个快照，churn），
    #   但也必须非 0 退出让人看见 —— 明确说清"快照已成功、清单过期"。
    try:
        catalog()
    except Exception as e:
        print(f"[ERR] 快照已生成，但清单刷新失败：{e} —— 清单此刻已过期，请重跑 --catalog",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
