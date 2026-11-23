#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_manifest.py —— 生成迁移包 MANIFEST（全量清单 + sha256）

用途：① 校验包完整性（跨机拷贝后逐项核对）
      ② 为「证据链」提供锚点（硬裁判的 evidence 里也用 sha256）
      ③ 检测共享输入目录是否被意外写入（对比两次 MANIFEST 即可）

依赖：仅 Python 标准库。跨平台。

用法：
    python3 gen_manifest.py --root . --out MANIFEST.json
    python3 gen_manifest.py --root . --check MANIFEST.json   # 校验模式
"""

import argparse
import fnmatch
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ_CN = timezone(timedelta(hours=8))

# 不纳入清单的项（临时/校验产物自身）
SKIP_NAMES = {".DS_Store", "Thumbs.db", "__pycache__", ".precheck_write_probe"}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_newline(path):
    """判断换行风格。跨平台 sha256 一致性的前提是换行统一。"""
    try:
        data = path.read_bytes()
    except OSError:
        return "unknown"
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and not lf:
        return "CRLF"
    if lf and not crlf:
        return "LF"
    if crlf and lf:
        return "MIXED"
    return "NONE"


def build(root: Path, path_prefixes=None, include_globs=None):
    """扫描 root，返回 [{path, size, sha256, newline}]。

    path_prefixes / include_globs 用于只纳入部分子目录。
    ★ 关键：过滤发生在 rel = p.relative_to(root) **之后**，
      因此 rel 始终相对 root —— 两次 MANIFEST（全包 / 子集）的 path 可互比。
      若把 --root 直接指向子目录，rel 就会变成相对该子目录，两次清单无法比对。
    """
    entries = []
    prefixes = tuple(path_prefixes) if path_prefixes else None
    globs = list(include_globs) if include_globs else None

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
        for fn in sorted(filenames):
            if fn in SKIP_NAMES:
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if rel == "MANIFEST.json":
                continue
            if prefixes is not None and not rel.startswith(prefixes):
                continue
            if globs is not None and not any(fnmatch.fnmatch(rel, g) for g in globs):
                continue
            try:
                entries.append({
                    "path": rel,
                    "size": p.stat().st_size,
                    "sha256": sha256_of(p),
                    "newline": detect_newline(p),
                })
            except OSError as e:
                entries.append({"path": rel, "error": str(e)})
    entries.sort(key=lambda e: e["path"])
    return entries


def load_domain_config(path):
    """读取写入域配置。占位符（{n} / {seat}）取第一个 '{' 之前的部分作为真实前缀。

    例：'runs/round{n}/{seat}/' -> 'runs/round'
    否则占位符会让 startswith 恒不匹配，allow 域永远报 0 个文件。
    """
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))

    def norm(lst):
        out = []
        for p in lst:
            real = p.split("{")[0]
            if real and real not in out:
                out.append(real)
        return out

    return norm(cfg.get("allow", [])), norm(cfg.get("deny", []))


def write_domain_baseline(root, cfg_path, out_path):
    """轮次【前】：对 deny 前缀建基线快照。"""
    _, deny = load_domain_config(cfg_path)
    files = build(root, path_prefixes=deny)
    snap = {
        "generated_at": datetime.now(TZ_CN).replace(microsecond=0).isoformat(),
        "kind": "write_domain_baseline",
        "root": ".",
        "deny_prefixes": deny,
        "note": "轮次前的共享目录快照。轮次后用 --write-domain <cfg> --check <本文件> 比对。",
        "files": files,
    }
    Path(out_path).write_bytes((json.dumps(snap, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[OK] 写入域基线 -> {out_path}  ({len(files)} 条, 覆盖 {len(deny)} 个禁止前缀)")
    return 0


def write_domain_check(root, cfg_path, baseline_path, out_path):
    """轮次【后】：比对 deny 前缀，输出 write-domain-check.json。

    ★ 语义边界：只判「结构变化」（新增/删除/变更），**不判语义**。
      「越权写入」vs「合法再生成」由 PM 在 docs/decisions/ 留痕裁定 ——
      符合 AD-01「只驳结构不驳结论」的定位。
    """
    allow, deny = load_domain_config(cfg_path)
    old = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    old_map = {e["path"]: e.get("sha256") for e in old.get("files", [])}
    new = build(root, path_prefixes=deny)
    new_map = {e["path"]: e.get("sha256") for e in new}

    hits = []
    for p in sorted(set(new_map) - set(old_map)):
        hits.append({"path": p, "status": "added", "sha256": new_map[p]})
    for p in sorted(set(old_map) - set(new_map)):
        hits.append({"path": p, "status": "removed", "sha256": None})
    for p in sorted(k for k in set(old_map) & set(new_map) if old_map[k] != new_map[k]):
        hits.append({"path": p, "status": "changed", "sha256": new_map[p]})

    # 同时报告 allow 域的变化，供对照（不参与 HIT 判定）
    allow_files = []
    if allow:
        allow_files = [{"path": e["path"], "sha256": e.get("sha256")} for e in build(root, path_prefixes=tuple(allow))]

    report = {
        "generated_at": datetime.now(TZ_CN).replace(microsecond=0).isoformat(),
        "kind": "write_domain_check",
        "deny_prefixes": deny,
        "allow_prefixes": allow,
        "changed_under_deny": hits,
        "changed_under_allow": allow_files,
        "verdict": "HIT" if hits else "CLEAN",
        "note": (
            "verdict=HIT 表示共享/禁止目录在轮次期间发生变化（结构事实）。"
            "是否为授权再生成，须由 PM 在 docs/decisions/ 留痕裁定；本脚本不判语义。"
        ),
    }
    if out_path:
        Path(out_path).write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    print(f"[第8查] 写入域检查    verdict={report['verdict']}")
    print(f"  禁止前缀 {len(deny)} 个 | 命中 {len(hits)} 条")
    for h in hits:
        print(f"    [{h['status']:7s}] {h['path']}")
    print(f"  allow 域文件 {len(allow_files)} 个（不参与判定）")
    if out_path:
        print(f"  -> {out_path}")
    return 0 if not hits else 1


def main():
    ap = argparse.ArgumentParser(description="生成/校验清单 · 写入域检查")
    ap.add_argument("--root", default=".", help="包根目录")
    ap.add_argument("--out", default="MANIFEST.json", help="清单/报告输出路径")
    ap.add_argument("--check", default=None, help="校验模式：给已有 MANIFEST 或基线路径")
    ap.add_argument("--path-prefix", action="append", default=None,
                    help="只纳入该路径前缀（可重复）。★用 --root 指向子目录会破坏可比性")
    ap.add_argument("--include", action="append", default=None,
                    help="只纳入匹配该 glob 的路径（可重复）")
    ap.add_argument("--write-domain", default=None, help="写入域配置 JSON（启用第 8 查模式）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（校验模式）")
    args = ap.parse_args()

    root = Path(args.root).resolve()

    # ---- 第 8 查模式 ----
    if args.write_domain:
        if args.check:
            return write_domain_check(root, args.write_domain, args.check, args.out)
        return write_domain_baseline(root, args.write_domain, args.out)

    if args.check:
        old = json.loads(Path(args.check).read_text(encoding="utf-8"))
        old_map = {e["path"]: e.get("sha256") for e in old["files"]}
        new = build(root, args.path_prefix, args.include)
        new_map = {e["path"]: e.get("sha256") for e in new}
        added = sorted(set(new_map) - set(old_map))
        removed = sorted(set(old_map) - set(new_map))
        changed = sorted(k for k in set(old_map) & set(new_map) if old_map[k] != new_map[k])
        if args.json:
            print(json.dumps({
                "root": str(root), "added": added, "removed": removed, "changed": changed,
                "clean": not (added or removed or changed),
            }, ensure_ascii=False, indent=2))
            return 0 if not (added or removed or changed) else 1
        print(f"校验 {root}")
        print(f"  新增 {len(added)} | 删除 {len(removed)} | 变更 {len(changed)}")
        for label, items in (("新增", added), ("删除", removed), ("变更", changed)):
            for it in items:
                print(f"    [{label}] {it}")
        if not (added or removed or changed):
            print("  ✅ 完全一致")
            return 0
        return 1

    files = build(root, args.path_prefix, args.include)
    mixed = [e["path"] for e in files if e.get("newline") == "MIXED"]
    manifest = {
        "generated_at": datetime.now(TZ_CN).replace(microsecond=0).isoformat(),
        "root": "migration-package/",
        "note": "sha256 用于跨机完整性校验。newline=MIXED 的文本文件会导致证据链跨平台断裂，须修正。",
        "stats": {
            "file_count": len(files),
            "total_bytes": sum(e.get("size", 0) for e in files),
            "mixed_newline_files": mixed,
        },
        "files": files,
    }
    out = Path(args.out)
    out.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"[OK] {out} 写入 {len(files)} 条")
    print(f"     总字节 {manifest['stats']['total_bytes']}")
    if mixed:
        print(f"     ⚠️ 换行混用（须修正）：{mixed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
