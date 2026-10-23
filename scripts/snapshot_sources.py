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

    python3 scripts/snapshot_sources.py                  # 打快照 → snapshots/<ts>/
    python3 scripts/snapshot_sources.py --verify         # 对比最近快照，报告改动/新增/删除
    python3 scripts/snapshot_sources.py --list           # 列出已有快照
    python3 scripts/snapshot_sources.py --label 改口径   # 给快照加标签

退出码：0 成功 / 1 --verify 发现差异 / 2 环境错误
"""
import argparse
import hashlib
import json
import re
import shutil
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
                    "label": label, "file_count": len(index), "files": index},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] 快照 {name}：{len(index)} 个文件 → {dst.relative_to(ROOT)}")
    return dst


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



def main():
    ap = argparse.ArgumentParser(description="真源快照 / 差异核验")
    ap.add_argument("--verify", action="store_true", help="对比最近快照")
    ap.add_argument("--list", action="store_true", help="列出已有快照")
    ap.add_argument("--label", default="", help="给快照加标签（如：改口径）")
    ap.add_argument("--mirror", default="", metavar="DIR",
                    help="把快照复制到外部目录（缓解单点依赖）")
    ap.add_argument("--mirror-all", action="store_true",
                    help="配合 --mirror：镜像全部快照而非仅最新")
    args = ap.parse_args()

    if not (ROOT / "scripts").is_dir():
        print(f"[ERR] 目录缺失: {ROOT / 'scripts'}", file=sys.stderr)
        return 2
    if args.list:
        return listing()
    if args.mirror:
        return mirror(args.mirror, args.mirror_all)
    if args.verify:
        return verify()
    snapshot(args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
