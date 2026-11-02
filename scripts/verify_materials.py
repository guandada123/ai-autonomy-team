#!/usr/bin/env python3
"""复核外部素材：① 每件哈希与清单一致；② 素材若已启用，内容必须是对的。

为什么要这个：`MATERIALS-SOURCES.md` 里写的"只导入不启用"是 2026-11-01 的决定，
2026-11-02 用户改主意要求启用 pre-commit / CODEOWNERS / gitleaks。
但**文档声明不等于事实**——启用之后同样可能出事：有人改了 .gitleaks.toml
却没更新清单、CODEOWNERS 还留着模板占位团队、或者悄悄又塞进一个工作流。
本脚本把这些声明变成可执行检查。

2026-11-02 口径变更（重要，不是放宽而是换靶子）：
    原 ③「不得存在活跃 CODEOWNERS」→ 改为「活跃 CODEOWNERS 不得含占位账号」。
    理由：会坏事的不是"文件存在"，而是"内容还是 @team/leads 这种本仓库不存在的
    owner"——GitHub 认不出它，PR 路由直接失效。查内容比查存在更贴近真实风险。
    原 ①「哈希必须等于导入快照」→ 改为按 activated 分档：未启用查快照哈希，
    已启用查 activated_sha256（防"启用后被人背着改"）。

用法：
    python3 scripts/verify_materials.py            # 复核，违规 exit 1
    python3 scripts/verify_materials.py --quiet    # 只输出结论
退出码：0=一致；1=哈希不符 / 含占位 owner / 清单缺失。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "templates" / "MATERIALS-MANIFEST.json"
# 只拦"素材被启用"：工作流目录出现与导入素材同名、或内容哈希相同的文件。
# 不用"白名单只许 selftest.yml"——那会误伤用户后来自己加的正常工作流。
MATERIAL_WORKFLOW_SUFFIX = (".yml", ".yaml")
ACTIVE_CODEOWNERS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
# CODEOWNERS 里的"占位 owner"：模板带来的示例团队/账号。
# 这些在本仓库不存在，GitHub 会当作"无人负责"→ PR 路由失效（不报错，只是静默不指派）。
PLACEHOLDER_OWNER_RE = re.compile(
    r"@(?:team/[\w-]+|alice\b|bob\b|carol\b|example\b|your-[\w-]+)", re.I
)


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
    #   2026-11-02 口径变更：素材可以"被启用"，启用后内容必然与导入快照不同。
    #   故分两档：未启用 → 查 sha256（保证还是原封不动的快照）；
    #            已启用 → 查 activated_sha256（保证启用后的内容没被人再偷偷改）。
    #   为什么不一刀切删掉判据：保留哈希是为了回答"启用后有没有人背着改配置"，
    #   这跟"改动是否合理"是两件事——前者要机器盯，后者要 review。
    for it in items:
        p = ROOT / it["target_path"]
        if not p.exists():
            if it.get("activated"):
                # 已启用项的目标文件可能已改名（如 CODEOWNERS.sample → CODEOWNERS）
                alt = it.get("activated_path")
                if alt and (ROOT / alt).exists():
                    p = ROOT / alt
                else:
                    problems.append(f"缺失：{it['target_path']}（已启用项）")
                    continue
            else:
                problems.append(f"缺失：{it['target_path']}")
                continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if it.get("activated"):
            expect = it.get("activated_sha256")
            if not expect:
                problems.append(f"{it['target_path']} 标为已启用但缺 activated_sha256")
            elif h != expect:
                problems.append(
                    f"启用后内容被改动：{it['target_path']}（期望 {expect[:8]}，实际 {h[:8]}）"
                    " —— 若是有意调整，请同步更新清单里的 activated_sha256"
                )
        elif h != it["sha256"]:
            problems.append(
                f"哈希不符：{it['target_path']}（期望 {it['sha256'][:8]}，实际 {h[:8]}）"
            )

    # ② 未被启用：工作流目录不得出现"素材同名"或"素材同内容"的文件
    material_names = {
        Path(it["target_path"]).name
        for it in items
        if it["target_path"].endswith(MATERIAL_WORKFLOW_SUFFIX)
    }
    material_hashes = {
        it["sha256"] for it in items if it["target_path"].endswith(MATERIAL_WORKFLOW_SUFFIX)
    }
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

    # ③ CODEOWNERS：2026-11-02 口径变更 —— 从"不得启用"改为"启用后不得含占位"。
    #   原判据的前提是"素材一律只导入不启用"，但用户 2026-11-02 明确要求启用。
    #   真正会坏事的不是"文件存在"，而是**内容还是模板占位**：@team/leads、@alice、@bob
    #   这些团队/账号在本仓库不存在，GitHub 会把它当成"无人负责"，PR 路由直接失效
    #   （开分支保护时表现为"等一个永远不会来的 approve"）。
    #   所以判据改成"查内容"而不是"查存在"——这比原判据更接近真实风险。
    live = [p for p in ACTIVE_CODEOWNERS if (ROOT / p).exists()]
    live_info: list[str] = []
    for lv in live:
        try:
            txt = (ROOT / lv).read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{lv} 读不出来：{exc}")
            continue
        # 只查"生效行"：GitHub 把 # 开头的整行当注释忽略，判据也必须一致。
        # 反例（2026-11-02 实测）：本文件注释里写着"模板原来是 @team/leads / @alice / @bob"，
        # 用来解释为什么不用占位——若判据不分注释，会把"说明自己没用占位"误判成"用了占位"，
        # 逼人删掉解释性注释（那才是真正的信息损失）。
        # 注意 CODEOWNERS 不支持行内注释，故只按行首判断，不切 # 之后。
        effective = [
            ln for ln in txt.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
        ]
        eff_txt = "\n".join(effective)
        hits = sorted({m.group(0) for m in PLACEHOLDER_OWNER_RE.finditer(eff_txt)})
        if hits:
            problems.append(
                f"{lv} 含占位团队/示例账号 {hits} → PR 路由会失效（GitHub 认不出这些 owner）"
            )
        else:
            owners = sorted({m.group(0) for m in re.finditer(r"@[\w./-]+", eff_txt)})
            live_info.append(f"{lv}（生效 owner: {', '.join(owners) or '无'}）")

    # ④ 本地钩子只是提示，不算失败（CI 环境本来就没有；本地装了是用户自己的选择）
    if (ROOT / ".git" / "hooks" / "pre-commit").exists():
        warns.append(
            "本地已安装 pre-commit 钩子（若是有意启用，请在 MATERIALS-SOURCES.md §四 记一笔）"
        )

    if not args.quiet:
        n_act = sum(1 for it in items if it.get("activated"))
        print(
            f"素材 {len(items)} 件（已启用 {n_act}）；"
            f"工作流 {sorted(p.name for p in wf_dir.glob('*')) if wf_dir.exists() else '—'}"
        )
        for lv in live_info:
            print(f"  ✅ {lv}")
        for w in warns:
            print(f"  ⚠️  {w}")

    if problems:
        for p in problems:
            print(f"  🔴 {p}")
        print(f"🔴 {len(problems)} 项不合规")
        return 1
    n_act = sum(1 for it in items if it.get("activated"))
    print(f"✅ 素材 {len(items)} 件（已启用 {n_act}）哈希与清单一致，启用的内容合规")
    return 0


if __name__ == "__main__":
    sys.exit(main())
