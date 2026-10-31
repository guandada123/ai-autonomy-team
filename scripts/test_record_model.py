#!/usr/bin/env python3
"""record_model.py 的验收用例。

判据刻意用"反证"方式写：每条都必须能因为实现被改坏而变红（见 .learnings/L-0042）。
例如把"模型相同就不追加"改成"总是追加"，用例 2 立刻红。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "record_model.py"
MODEL_ENV = "CODEBUDDY_CURRENT_MODEL_ID"


def run(args: list[str], model: str | None, log: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop(MODEL_ENV, None)
    if model is not None:
        env[MODEL_ENV] = model
    return subprocess.run([sys.executable, str(SCRIPT), "--log", str(log), *args],
                          capture_output=True, text=True, env=env, timeout=30)


def rows(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    failures = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name}  {detail}")
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "model-log.jsonl"

        print("用例 1：空日志 → 建立基线")
        r = run([], "model-a", log)
        rs = rows(log)
        check("退出码 0", r.returncode == 0, r.stderr)
        check("写入 1 条", len(rs) == 1, str(len(rs)))
        check("changed=False（基线不算变化）", rs and rs[0]["changed"] is False)
        check("model_id 正确", rs and rs[0]["model_id"] == "model-a")
        check("输出含『建立基线』", "建立基线" in r.stdout, r.stdout)

        print("用例 2：同模型再跑 → 不追加")
        r = run([], "model-a", log)
        check("退出码 0", r.returncode == 0)
        check("仍只有 1 条（去重生效）", len(rows(log)) == 1, str(len(rows(log))))
        check("输出含『未变化』", "未变化" in r.stdout, r.stdout)

        print("用例 3：换模型 → 追加并记 prev")
        r = run(["--note", "用户切换"], "model-b", log)
        rs = rows(log)
        check("变成 2 条", len(rs) == 2, str(len(rs)))
        check("changed=True", rs[-1]["changed"] is True)
        check("prev_model_id=model-a", rs[-1]["prev_model_id"] == "model-a", str(rs[-1].get("prev_model_id")))
        check("note 落盘", rs[-1]["note"] == "用户切换")
        check("输出含箭头", "model-a → model-b" in r.stdout, r.stdout)

        print("用例 4：读不到模型 → 退出码 1，且不污染日志")
        before = len(rows(log))
        r = run([], None, log)          # 环境里没有 MODEL_ENV
        check("退出码 1", r.returncode == 1, str(r.returncode))
        check("日志未变", len(rows(log)) == before, f"{before} -> {len(rows(log))}")
        check("stderr 有提示", "取证失败" in r.stderr, r.stderr)

        print("用例 5：坏行不拖垮历史")
        with log.open("a", encoding="utf-8") as f:
            f.write("{这不是合法 json\n")
        r = run(["--show"], None, log)
        check("--show 退出码 0", r.returncode == 0)
        check("仍显示 2 条有效记录", "共 2 条记录" in r.stdout, r.stdout)

        print("用例 6：--show 不写入")
        before = log.read_text(encoding="utf-8")
        run(["--show"], "model-c", log)
        check("文件内容未变", log.read_text(encoding="utf-8") == before)

    print()
    if failures:
        print(f"🔴 {len(failures)} 项失败：{failures}")
        return 1
    print("✅ 全部通过（6 组、18 项断言）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
