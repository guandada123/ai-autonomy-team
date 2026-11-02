#!/usr/bin/env python3
"""handover_facts.py —— 交接摘要易变数字的**单一真源**（生成器）。

## 为什么需要它

2026-10-29 审计（P1）：docs/交接摘要-20261029.md 手写「kit 自测 64/64」，
25 分钟后 kit 涨到 75 —— 手写计数必然过期（R3 根因当天复发的活体样本）。
本脚本把交接摘要里的易变数字全部改为从单一真源**生成**：

| 数字 | 真源 | 说明 |
|---|---|---|
| 门禁项数 | `pre_release_check.CHECKS` 长度 | A–H |
| kit 自测项数 | `audit/kit/test_kit.CHECKS` 长度 | |
| 接手验收项数 | `verify_handover.py --count` | 脚本自报 |
| 全量回归项数 | `regress.py` 里 `s.expect/s.check` 的**去重项号** | 调用点≠项数（L-0034），去重才是项数 |
| 覆盖率基线 | `tools/coverage-baseline.txt` | ratchet 基线 |
| 判据自检项数 | `judges/verify.py --self-test` 实跑解析 | 约 1s |
| 决策/经验/协议 | 目录文件计数 | |

约定：
- 生成块用 `<!-- BEGIN:handover-facts -->` / `<!-- END:handover-facts -->` 包裹，
  门禁 A 项的 F 类校验会比对块与重新生成的输出（过期 → 红）。
- **HEAD 不进块**（每次提交都变，会造成永远追不平的假红）；时刻类数字也不进块。

用法：
    python3 scripts/handover_facts.py                 # 打印生成块
    python3 scripts/handover_facts.py --emit <doc.md> # 把块写回文档（就地更新）
    python3 scripts/handover_facts.py --check <doc.md># 校验文档里的块是否最新（exit 0/1）
    python3 scripts/handover_facts.py --json          # 机器可读
    python3 scripts/handover_facts.py --self-test     # 临时文档上自测 emit/check 往返
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BEGIN = (
    "<!-- BEGIN:handover-facts（由 scripts/handover_facts.py 生成，勿手改；门禁 F 类校验本块） -->"
)
END = "<!-- END:handover-facts -->"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _sh(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def collect() -> dict:
    """从各自的单一真源采集易变数字。任何一项失败都给固定占位（输出确定性优先）。"""
    facts = {}

    bp = _load_module(ROOT / "scripts/pre_release_check.py", "hf_prc")
    facts["gate_items"] = len(bp.CHECKS)

    tk = _load_module(ROOT / "audit/kit/test_kit.py", "hf_tk")
    facts["kit_items"] = len(tk.CHECKS)

    rc, out = _sh([PY, str(ROOT / "scripts/verify_handover.py"), "--count"])
    facts["handover_items"] = int(out.strip()) if rc == 0 and out.strip().isdigit() else -1

    src = (ROOT / "scripts/regress.py").read_text(encoding="utf-8")
    nums = sorted({int(n) for n in re.findall(r"s\.(?:expect|check)\(\s*(\d+)\s*,", src)})
    facts["regress_items"] = len(nums) if nums else -1
    facts["regress_continuous"] = bool(nums) and nums == list(range(1, nums[-1] + 1))

    try:
        facts["coverage_baseline"] = int(
            (ROOT / "tools/coverage-baseline.txt").read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError):
        facts["coverage_baseline"] = -1

    rc, out = _sh([PY, str(ROOT / "migration-package/judges/verify.py"), "--self-test"])
    m = re.search(r"自检通过：(\d+)/(\d+)", out)
    facts["criteria_items"] = int(m.group(1)) if (rc == 0 and m) else -1

    facts["decisions"] = len(list((ROOT / "docs/decisions").glob("D-*.md")))
    facts["learnings"] = len(list((ROOT / ".learnings").glob("L-*.md")))
    facts["protocols"] = len(list((ROOT / "docs/protocol").glob("*.md")))
    return facts


def render_inner(facts: dict) -> str:
    """渲染块内文（不含标记行）。emit/check 都以它为准，避免首尾换行归属不清。"""
    cont = "" if facts["regress_continuous"] else " ⚠️项号不连续，请核对"
    lines = [
        "- HEAD：以 `git log -1` 为准（不写死，提交即变）",
        f"- 门禁项数：{facts['gate_items']}（A–H）",
        f"- 全量回归项数：{facts['regress_items']}（1..{facts['regress_items']} 连续{cont}）",
        f"- kit 自测项数：{facts['kit_items']}",
        f"- 接手验收项数：{facts['handover_items']}",
        f"- 判据自检项数：{facts['criteria_items']}",
        f"- 覆盖率基线：{facts['coverage_baseline']}（CI ratchet，下限 = 基线-2）",
        f"- 资产：决策 {facts['decisions']} ｜ 经验 {facts['learnings']} ｜ 协议 {facts['protocols']}",
    ]
    return "\n".join(lines) + "\n"


def render_block(facts: dict) -> str:
    """完整块（含标记行），供 CLI 直接打印。"""
    return BEGIN + "\n" + render_inner(facts) + END + "\n"


def emit(doc: Path) -> int:
    text = doc.read_text(encoding="utf-8")
    inner = render_inner(collect())
    pat = re.compile(re.escape(BEGIN) + r"\n.*?" + re.escape(END) + r"\n", re.S)
    full = BEGIN + "\n" + inner + END + "\n"
    if pat.search(text):
        text = pat.sub(lambda _: full, text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n" + full
    doc.write_text(text, encoding="utf-8")
    print(f"[OK] 生成块已写入 {doc.name}")
    return 0


def check(doc: Path) -> int:
    text = doc.read_text(encoding="utf-8")
    m = re.search(re.escape(BEGIN) + r"\n(.*?)" + re.escape(END) + r"\n", text, re.S)
    if not m:
        print(f"[FAIL] {doc.name} 没有 handover-facts 生成块（应先 --emit）", file=sys.stderr)
        return 1
    want = render_inner(collect())
    if m.group(1) != want:
        print(
            f"[FAIL] {doc.name} 生成块已过期 → python3 scripts/handover_facts.py --emit {doc}",
            file=sys.stderr,
        )
        return 1
    print(f"[PASS] {doc.name} 生成块与真源一致")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "doc.md"
            doc.write_text("# 标题\n\n正文\n", encoding="utf-8")
            assert check(doc) == 1, "无块时应 FAIL"
            emit(doc)
            assert check(doc) == 0, "emit 后应 PASS"
            text = doc.read_text(encoding="utf-8")
            doc.write_text(text.replace("门禁项数：8", "门禁项数：9"), encoding="utf-8")
            assert check(doc) == 1, "手改块后应 FAIL"
            emit(doc)
            assert check(doc) == 0, "重新 emit 后应恢复"
        facts = collect()
        assert facts["gate_items"] >= 8 and facts["kit_items"] >= 64, facts
        assert facts["regress_items"] >= 50 and facts["regress_continuous"], facts
        print("[PASS] handover_facts 自检通过（往返 + 过期检出 + 真源合理性）")
        return 0

    if "--check" in args:
        return check(Path(args[args.index("--check") + 1]))
    if "--emit" in args:
        return emit(Path(args[args.index("--emit") + 1]))
    if "--json" in args:
        print(json.dumps(collect(), ensure_ascii=False, indent=2))
        return 0
    print(render_block(collect()), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
