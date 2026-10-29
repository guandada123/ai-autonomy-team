#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_package.py —— 单一真实源 → 迁移包 的同步与一致性校验

问题背景（结构缺陷 X3）：
    `scripts/judges/verify.py` 与 `migration-package/judges/verify.py` 曾**逐字节相同**，
    各自独立存在。两份都要手工同步 → 必然漂移。

本脚本确立：**`scripts/` 与 `docs/protocol/`、`templates/` 是唯一真实源**；
`migration-package/` 下的同类文件是**生成物**，不手工编辑。

依赖：仅 Python 标准库。跨平台。

用法：
    python3 build_package.py --check              # 逐字节比对源↔包（drift → exit 1）
    python3 build_package.py --apply              # 同步（UTF-8 / LF 落盘）
    python3 build_package.py --apply --manifest   # 同步后重建 MANIFEST
    python3 build_package.py --list               # 只打印映射表
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import datetime
import sys
from pathlib import Path

# ---------------------------------------------------------------- 映射表
# ★ 硬编码白名单，**不递归通配** —— 避免误覆盖包独有文件
#
# ★★ 源/生成物的边界（容易被误解，特此写明）：
#   源（唯一真实）：scripts/**、docs/protocol/**、templates/**
#   生成物        ：migration-package/{judges,protocol,prompts}/** 与包根的 3 个脚本
#   包内独有源     ：migration-package/{README.md, precheck.py, gen_manifest.py,
#                    MANIFEST.json, config/**, fixtures/**, runs/**, docs/**}
#                    —— 这些**不在**同步管辖内，改动直接落在包内，不必也不该有源侧副本。
#
#   ★ 注意一个反直觉的事实：`config/params.json` 与 `fixtures/spec/*.json` 虽然
#     是「唯一参数源 / 唯一判据源」，但它们的物理位置就在包内。
#     不要因为「唯一源应该在 scripts/ 那边」而把它们搬出来 —— 那会触发
#     MANIFEST 重建与多处路径引用改写，收益小于代价（见 D-0003 同条记录）。

FIXED_MAPPING = [
    ("scripts/judges/verify.py", "migration-package/judges/verify.py"),
    ("scripts/judges/seat_check.py", "migration-package/judges/seat_check.py"),
    ("scripts/judges/run.sh", "migration-package/judges/run.sh"),
    ("scripts/judges/run.cmd", "migration-package/judges/run.cmd"),
    ("scripts/judges/README.md", "migration-package/judges/README.md"),
    ("scripts/make_fixtures.py", "migration-package/make_fixtures.py"),
    ("scripts/inspect_agent_routing.py", "migration-package/inspect_agent_routing.py"),
    ("scripts/new_round.py", "migration-package/new_round.py"),
    ("scripts/fsutil.py", "migration-package/fsutil.py"),
    # ★ 2026-10-24 补（D-0054）：包里一直带着 audit/probes/premise/*.json（探针定义）
    #   却没带 premise_probe.py（探针本体）——目标机上 L3 无从执行，闸门在干净包里必红。
    ("scripts/premise_probe.py", "migration-package/premise_probe.py"),
    ("scripts/veto_sweep.py", "migration-package/veto_sweep.py"),
    ("scripts/metrics.py", "migration-package/metrics.py"),
]
# 注：`scripts/regress.py` **刻意不入包** —— 它依赖 `fixtures/frozen/` 下的冻结基线
#     （2026-10-23 从 `_trash/backup-pre-P5/` 迁来）与源机的 `docs/`，是「源机专用验收套件」。
# 注：`scripts/check_docs_consistency.py` 同样**不入包** —— 它校验的是团队根
#     `docs/*.md` 与包内 `MANIFEST.json` 的一致性，属「源机专用文档验收」。
#     包内没有 `docs/`（团队根那份），入包后无从校验。

# 目录级映射：源目录下所有 *.md → 目标目录（文件名保持）
DIR_MAPPING = [
    ("docs/protocol", "migration-package/protocol"),
    ("templates", "migration-package/prompts"),
]


def build_mapping(root: Path):
    """展开为 [(src_rel, dst_rel)]，全部用 POSIX 风格相对路径。"""
    pairs = list(FIXED_MAPPING)
    for src_dir, dst_dir in DIR_MAPPING:
        d = root / src_dir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            pairs.append((f"{src_dir}/{f.name}", f"{dst_dir}/{f.name}"))
    # 去重保序
    seen = set()
    out = []
    for s, t in pairs:
        if (s, t) in seen:
            continue
        seen.add((s, t))
        out.append((s, t))
    return out


def sha256_of(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


def do_check(root: Path):
    pairs = build_mapping(root)
    ok, missing_src, missing_dst, drift = [], [], [], []
    for s, t in pairs:
        sp, tp = root / s, root / t
        if not sp.is_file():
            missing_src.append(s)
            continue
        if not tp.is_file():
            missing_dst.append(t)
            continue
        if sha256_of(sp) == sha256_of(tp):
            ok.append(s)
        else:
            drift.append((s, t, sha256_of(sp), sha256_of(tp)))

    print(f"单一真实源一致性校验   {root}")
    print(f"  映射 {len(pairs)} 条 | 一致 {len(ok)} | 目标缺失 {len(missing_dst)} "
          f"| 源缺失 {len(missing_src)} | 内容漂移 {len(drift)}")
    for x in missing_src:
        print(f"    [源缺失]   {x}")
    for x in missing_dst:
        print(f"    [目标缺失] {x}")
    for s, t, a, b in drift:
        print(f"    [漂移]     {s}")
        print(f"               源 {a[:16]}  vs  包 {b[:16]}  -> {t}")

    if missing_src or missing_dst or drift:
        print("\n  ❌ 不一致。请执行：python3 build_package.py --apply")
        print("     （若漂移来自「直接改了包内文件」，请把那处改动挪回源文件）")
        return 1
    print("\n  ✅ 全部一致 —— 包内文件均为源文件的忠实副本")
    return 0


def do_apply(root: Path):
    pairs = build_mapping(root)
    written, unchanged = 0, 0
    changed = []
    for s, t in pairs:
        sp, tp = root / s, root / t
        if not sp.is_file():
            print(f"  [跳过·源缺失] {s}")
            continue
        tp.parent.mkdir(parents=True, exist_ok=True)
        if tp.is_file() and sha256_of(sp) == sha256_of(tp):
            unchanged += 1
            continue
        # 逐字节复制（不做文本换行转换）—— 保留源的 LF
        shutil.copyfile(sp, tp)
        written += 1
        changed.append(s)
        print(f"  [同步] {s}  ->  {t}")
    # ★ D-0018（2026-10-20，round11 AD-01 第 8 查实测缺口）：装配写入必须留痕。
    #   包内 mtime 刷新与席位越权写入在元数据上不可区分——AD 无法判定
    #   "161 个 fixtures 文件是谁写的"。装配日志把「发起方=编排方装配脚本」
    #   落盘，写入域检查与结构审计据此区分合法装配与越权。
    log = root / "assembly-log.jsonl"
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actor": "build_package.py(--apply)",
        "written": written,
        "unchanged": unchanged,
        "changed_files": changed,
    }
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\n[OK] 同步完成：写入 {written} 个，未变 {unchanged} 个（装配日志 {log.name}）")
    return 0


def do_manifest(root: Path):
    gen = root / "migration-package" / "gen_manifest.py"
    if not gen.is_file():
        print(f"[ERR] 找不到 {gen}", file=sys.stderr)
        return 2
    r = subprocess.run(
        [sys.executable, str(gen), "--root", ".", "--out", "MANIFEST.json"],
        cwd=str(root / "migration-package"),
        capture_output=True, text=True, encoding="utf-8",
    )
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description="单一真实源 → 迁移包 同步与校验")
    ap.add_argument("--root", default=".", help="工作区根目录")
    ap.add_argument("--check", action="store_true", help="逐字节比对（drift → exit 1）")
    ap.add_argument("--apply", action="store_true", help="同步源 → 包")
    ap.add_argument("--manifest", action="store_true", help="同步后重建 MANIFEST")
    ap.add_argument("--list", action="store_true", help="只打印映射表")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    pairs = build_mapping(root)

    if args.list:
        print(f"映射表（{len(pairs)} 条）—— 源 = 唯一真实源，目标 = 生成物")
        for s, t in pairs:
            print(f"  {s:44s} -> {t}")
        return 0

    rc = 0
    if args.check:
        rc = do_check(root)
    if args.apply:
        rc = max(rc, do_apply(root))
    if args.manifest:
        rc = max(rc, do_manifest(root))
    if not (args.check or args.apply or args.manifest):
        ap.print_help()
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
