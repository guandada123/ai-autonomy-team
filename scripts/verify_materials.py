#!/usr/bin/env python3
"""复核外部素材：① 每件哈希与清单一致；② 素材依旧未被启用。

为什么要这个：`MATERIALS-SOURCES.md` 里写了"只导入不启用"，但文档声明不等于事实——
别人（或未来的我）可能加了工作流文件、改了 CODEOWNERS 名字、装了钩子。
本脚本把三条声明变成可执行检查，防止"说好了不启用结果悄悄启用了"。

用法：
    python3 scripts/verify_materials.py            # 复核，违规 exit 1
    python3 scripts/verify_materials.py --quiet    # 只输出结论
退出码：0=一致且未启用；1=哈希不符 / 被启用 / 清单缺失。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "templates" / "MATERIALS-MANIFEST.json"
# 只拦"素材被启用"：工作流目录出现与导入素材同名、或内容哈希相同的文件。
# 不用"白名单只许 selftest.yml"——那会误伤用户后来自己加的正常工作流。
MATERIAL_WORKFLOW_SUFFIX = (".yml", ".yaml")
ACTIVE_CODEOWNERS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def main() -> int:
    ap = argparse.ArgumentParser(description="复核外部素材：哈希一致 + 未被启用")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"🔴 清单不存在：{MANIFEST}", file=sys.stderr)
        return 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = man.get("items", [])
    problems: list[str] = []
    warns: list[str] = []

    # ① 逐件哈希
    for it in items:
        p = ROOT / it["target_path"]
        if not p.exists():
            problems.append(f"缺失：{it['target_path']}")
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h != it["sha256"]:
            problems.append(f"哈希不符：{it['target_path']}（期望 {it['sha256'][:8]}，实际 {h[:8]}）")

    # ② 未被启用：工作流目录不得出现"素材同名"或"素材同内容"的文件
    material_names = {Path(it["target_path"]).name for it in items
                      if it["target_path"].endswith(MATERIAL_WORKFLOW_SUFFIX)}
    material_hashes = {it["sha256"] for it in items
                       if it["target_path"].endswith(MATERIAL_WORKFLOW_SUFFIX)}
    wf_dir = ROOT / ".github" / "workflows"
    activated = []
    if wf_dir.exists():
        for f in sorted(wf_dir.glob("*")):
            if f.name in material_names:
                activated.append(f"{f.name}（与素材同名）")
            elif hashlib.sha256(f.read_bytes()).hexdigest() in material_hashes:
                activated.append(f"{f.name}（与素材同内容）")
    if activated:
        problems.append(f"工作流目录出现素材文件（说明已被启用）：{activated}")

    # ③ 未被启用：不得存在活跃 CODEOWNERS
    live = [p for p in ACTIVE_CODEOWNERS if (ROOT / p).exists()]
    if live:
        problems.append(f"存在活跃 CODEOWNERS（占位团队会坏 PR 路由）：{live}")

    # ④ 本地钩子只是提示，不算失败（CI 环境本来就没有；本地装了是用户自己的选择）
    if (ROOT / ".git" / "hooks" / "pre-commit").exists():
        warns.append("本地已安装 pre-commit 钩子（若是有意启用，请在 MATERIALS-SOURCES.md §四 记一笔）")

    if not args.quiet:
        print(f"素材 {len(items)} 件；工作流 {sorted(p.name for p in wf_dir.glob('*')) if wf_dir.exists() else '—'}")
        for w in warns:
            print(f"  ⚠️  {w}")

    if problems:
        for p in problems:
            print(f"  🔴 {p}")
        print(f"🔴 {len(problems)} 项不合规")
        return 1
    print(f"✅ 素材 {len(items)} 件哈希一致，且均未被启用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
