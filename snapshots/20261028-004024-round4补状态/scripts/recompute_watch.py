#!/usr/bin/env python3
"""每日重算 v1.2 真值，漂移即告警（v3 Q02 用户裁 A）。

## 2026-10-27（D-0074）：题库切到 v1.2

v1.2 = v1.1 的 26 题回补 Q17/Q25/Q26（各写死唯一口径）、移出 Q31（盘上无源）后的 25 题，
重算覆盖从 16 项升到 18 项（新增 `doc_line_num`：验文件 sha256 后按行取数）。
v1.1 的目录保持原样，只是**不再是每日自检的对象**；两版分数不得混算（`D-0057`）。

## 为什么要有它

v1.2 的 25 道事实题靠锚点定真值，`make recompute` 能重算但要手动跑。
手动跑的触发点只有「准备出分时」——那时已经晚了：8 天内实测漂过 8/27。

## 设计上的两个坎

1. **仓库在外置卷上**（形如 `/Volumes/<卷名>/...`）。卷没挂载时，脚本自己都
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
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    # 外置卷卸载时 ROOT 不存在，写 ROOT/outputs 会抛异常 —— 那不是「静默失败」，
    # 但会变成一段没人看的 traceback。退到 /tmp 保证「卷没挂」这件事被记下来。
    for target in (LOG, Path("/tmp/aiteam-recompute.log")):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            break
        except OSError:
            continue
    print(line)


def alert(msg: str) -> bool:
    """有 webhook 就发飞书，没有就只落日志。**返回告警是否真的送达**。

    ★ 2026-10-27：原实现无返回值，调用方无从区分「发了告警」与「告警送达」。
    实测代价：08-20 的 T03 漂移属 P1，但当时未配 FEISHU_WEBHOOK，
    只落了一行「（未设 FEISHU_WEBHOOK，仅本地日志）」，**7 小时后才被人工发现**。
    现在把「未送达」显式说出来，让日志本身就能回答「这件事有没有人知道」。
    """
    hook = os.environ.get("FEISHU_WEBHOOK")
    if not hook:
        log("⚠️ 告警未送达：未配置 FEISHU_WEBHOOK —— 本条只写进本地日志，不会有人收到")
        return False
    body = json.dumps({"msg_type": "text",
                       "content": {"text": f"[ai-autonomy] {msg}"}}).encode()
    req = urllib.request.Request(hook, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        log("已发飞书告警")
        return True
    except Exception as e:                      # 网络失败不能吞掉
        log(f"⚠️ 告警发送失败：{e}")
        return False


def main() -> int:
    if not (ROOT / "Makefile").is_file():
        # 外置卷没挂载时最可能走到这里：必须大声说，不能静默
        msg = f"仓库根目录不可用：{ROOT}（外置卷未挂载？）"
        log(msg)
        # 与其他 alert 调用点保持一致：返回值也要看，否则「发了」与「送达」又混为一谈
        if not alert(msg):
            log("🔴 本条同样无人收到 —— 需人工确认外置卷挂载状态")
        return 2

    rc = subprocess.run([sys.executable, str(ROOT / "audit/baseline/v1.2/recompute_truth.py")],
                        cwd=ROOT, capture_output=True, text=True)
    # 汇总行是中间的 [PASS]/[FAIL] 那行，不是末行——末行是「不可重算」明细清单。
    # 首版取 tail[-1]，结果把「T06 ← docs/...」当成结论写进日志（实测踩到）。
    lines = ((rc.stdout or "") + "\n" + (rc.stderr or "")).strip().splitlines()
    summary = next((ln.strip() for ln in reversed(lines)
                    if ln.strip().startswith(("[PASS]", "[FAIL]"))),
                   lines[-1].strip() if lines else "无输出")
    if rc.returncode != 0:
        log(f"❌ 真值漂移或重算失败（EXIT={rc.returncode}）：{summary}")
        delivered = alert(f"v1.2 真值重算未通过：{summary}")
        if not delivered:
            # 这一行是给「事后翻日志的人」看的：不必再猜告警是否发出去过。
            log("🔴 本次失败**无人收到通知** —— 需人工介入，或配置 FEISHU_WEBHOOK")
        return 1
    log(f"✅ 重算通过：{summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
