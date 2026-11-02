#!/usr/bin/env python3
"""模型留痕：记录当前会话/运行时使用的模型，模型变化时自动追加一条。

为什么要有这个：2026-10-31 那次"跨模型盲验"卡在一个无法回溯的点上——用户换了模型，
但机器上没有任何"切换前是什么模型"的记录，导致报告只能写"待确认"。
结论标签的强度不该由记忆决定，应该由日志决定。故本工具补这条历史。

用法：
    python3 scripts/record_model.py            # 检查并记录（模型有变化才追加）
    python3 scripts/record_model.py --show     # 只查看历史，不写入
    python3 scripts/record_model.py --history "A>B>A" --note "用户口述"
                                               # 回填机器查不到的过去（标为口述、未机器验证）
    python3 scripts/record_model.py --note "用户切到 X；子代理探测见 RESULT-xmodel §四点六"

退出码：0=正常（无论是否新增）；1=读不到模型标识（视为取证失败，不静默放过）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "audit" / "baseline" / "model-log.jsonl"

# 只读取"描述身份"的变量；不碰任何 *_PROXY / *_KEY / *_TOKEN（凭据隔离）。
MODEL_ENV_KEYS = ("CODEBUDDY_CURRENT_MODEL_ID", "WORKBUDDY_VLM_MODEL", "ANTHROPIC_MODEL")


def current_model() -> dict:
    env = {k: os.environ.get(k, "") for k in MODEL_ENV_KEYS}
    return {
        "model_id": env.get("CODEBUDDY_CURRENT_MODEL_ID", ""),
        "vlm_model": env.get("WORKBUDDY_VLM_MODEL", ""),
        "anthropic_model": env.get("ANTHROPIC_MODEL", ""),
    }


def git_rev() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 坏行跳过，不因一行脏数据丢掉整段历史
    return rows


def show(rows: list[dict]) -> None:
    if not rows:
        print("（日志为空，尚未记录过任何模型）")
        return
    print(f"共 {len(rows)} 条记录：")
    for r in rows:
        if r.get("type") == "history-testimony":
            seq = " → ".join(r.get("history") or [])
            print(f"  {r.get('ts', '?'):<20} [口述历史·未机器验证] {seq}")
            if r.get("note"):
                print(f"      备注：{r['note']}")
            continue
        mark = "← 变化" if r.get("changed") else "（基线）"
        print(
            f"  {r.get('ts', '?'):<20} {r.get('model_id') or '(空)':<24} "
            f"git={r.get('git_rev', '?'):<8} [机器验证] {mark}"
        )
        if r.get("note"):
            print(f"      备注：{r['note']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="模型留痕：模型变化时追加一条历史记录")
    ap.add_argument("--show", action="store_true", help="只查看历史，不写入")
    ap.add_argument("--note", default="", help="给本条记录加备注（如切换原因）")
    ap.add_argument(
        "--history",
        default="",
        help='回填口述模型历史，格式 "A>B>A"（标为未机器验证，用于机器查不到的过去）',
    )
    ap.add_argument(
        "--log", default=str(LOG), help="日志路径（测试用；默认 audit/baseline/model-log.jsonl）"
    )
    args = ap.parse_args()
    log_path = Path(args.log)

    rows = read_log(log_path)
    if args.show:
        show(rows)
        return 0

    # 口述历史：机器上查不到的过去（谁在什么时候换了什么模型），只能由用户提供。
    # 这类记录必须与机器探测分开标注——口述是线索，不是证据（见 .learnings/L-0043）。
    if args.history:
        seq = [s.strip() for s in args.history.replace("->", ">").split(">") if s.strip()]
        if len(seq) < 2:
            print(
                '🔴 --history 至少需要两段，格式如 "deepseek-v4.1-flash>GLM-5.3-flash"',
                file=sys.stderr,
            )
            return 1
        entry = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "type": "history-testimony",
            "evidence": "user-testimony",
            "machine_verified": False,
            "history": seq,
            "from_model": seq[0],
            "to_model": seq[-1],
            "note": args.note,
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"✅ 已记录口述历史：{' → '.join(seq)}")
        print("   注意：本条为用户口述，未机器验证；精确时间未知，故不写 ts 到历史内部。")
        return 0

    cur = current_model()
    if not cur["model_id"]:
        print(
            "🔴 取证失败：读不到 CODEBUDDY_CURRENT_MODEL_ID——"
            "无法确认运行时模型。请勿在报告中写具体模型名。",
            file=sys.stderr,
        )
        return 1

    # 去重只跟"机器探测"记录比：口述历史条目没有 model_id，
    # 若把它当成 last，会把同一个模型重复记成"变化"。
    probes = [r for r in rows if r.get("type") != "history-testimony"]
    last = probes[-1] if probes else None
    if last and last.get("model_id") == cur["model_id"]:
        print(f"未变化：{cur['model_id']}（上次记录 {last.get('ts', '?')}）")
        return 0

    entry = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "model_id": cur["model_id"],
        "vlm_model": cur["vlm_model"],
        "anthropic_model": cur["anthropic_model"],
        "git_rev": git_rev(),
        "changed": last is not None,
        "prev_model_id": last.get("model_id") if last else None,
        "note": args.note,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if last:
        print(f"✅ 已记录模型变化：{last.get('model_id')} → {cur['model_id']}")
    else:
        print(f"✅ 已建立基线：{cur['model_id']}")
    print(f"   日志：{log_path}（{len(rows) + 1} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
