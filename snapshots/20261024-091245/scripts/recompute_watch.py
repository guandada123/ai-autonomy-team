#!/usr/bin/env python3
"""每日重算 v1.1 真值，漂移即告警（v3 Q02 用户裁 A）。

## 为什么要有它

v1.1 的 26 道事实题靠锚点定真值，`make recompute` 能重算但要手动跑。
手动跑的触发点只有「准备出分时」——那时已经晚了：8 天内实测漂过 8/27。

## 设计上的两个坎

1. **仓库在外置卷上**：仓库路径是 `/Volumes/ZHITAI/...`。卷没挂载时，脚本自己都
   读不到，launchd 会静默失败。所以第一件事是检查根目录在不在，不在就**大声报错**，
   而不是安静退出 —— 静默失败正是本项目最忌讳的失败模式。
2. **告警通道**：仓库里不存 webhook。设了环境变量 `FEISHU_WEBHOOK` 才发消息，
   没设就只写日志 + 非零退出码，由 launchd 记进 stderr。

## 用法

    python3 scripts/recompute_watch.py            # 跑一次，漂移则 exit 1
    python3 scripts/recompute_watch.py --quiet    # 只写日志

装定时任务（需手动，agent 不代替你 load）：

    cp scripts/launchd/com.guan.aiteam.recompute.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.guan.aiteam.recompute.plist
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "outputs" / "recompute-watch.log"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def alert(msg: str) -> None:
    """有 webhook 就发飞书，没有就只落日志。不把凭据写进仓库。"""
    hook = os.environ.get("FEISHU_WEBHOOK")
    if not hook:
        log("（未设 FEISHU_WEBHOOK，仅本地日志）")
        return
    body = json.dumps({"msg_type": "text",
                       "content": {"text": f"[ai-autonomy] {msg}"}}).encode()
    req = urllib.request.Request(hook, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        log("已发飞书告警")
    except Exception as e:                      # 网络失败不能吞掉
        log(f"⚠️ 告警发送失败：{e}")


def main() -> int:
    if not (ROOT / "Makefile").is_file():
        # 外置卷没挂载时最可能走到这里：必须大声说，不能静默
        msg = f"仓库根目录不可用：{ROOT}（外置卷未挂载？）"
        log(msg)
        alert(msg)
        return 2

    rc = subprocess.run([sys.executable, str(ROOT / "audit/baseline/v1.1/recompute_truth.py")],
                        cwd=ROOT, capture_output=True, text=True)
    tail = (rc.stdout or rc.stderr).strip().splitlines()
    summary = tail[-1] if tail else "无输出"
    if rc.returncode != 0:
        log(f"❌ 真值漂移或重算失败（EXIT={rc.returncode}）：{summary}")
        alert(f"v1.1 真值重算未通过：{summary}")
        return 1
    log(f"✅ 重算通过：{summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
