#!/bin/sh
# 安装/卸载「v1.1 真值每日重算」定时任务（v3 Q02 用户裁 A）
#
#   sh scripts/install_launchd.sh           安装（重复执行=更新）
#   sh scripts/install_launchd.sh --uninstall  卸载
#
# 为什么不把 plist 直接入库：launchd 要求绝对路径，而绝对路径写进版本控制就绑死了
# 机器与目录。这里改成模板 + 安装时注入，仓库换位置只要重跑本脚本。
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
LABEL=com.guan.aiteam.recompute
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_N=$(id -u)

if [ "${1:-}" = "--uninstall" ]; then
    launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
    rm -f "$DST"
    echo "已卸载 ${LABEL}（$DST 已删）"
    exit 0
fi

sed "s|__REPO__|$REPO|g" "$REPO/scripts/launchd/$LABEL.plist.tmpl" > "$DST"
chmod 644 "$DST"

# 已加载则先卸再装，避免 bootstrap 报 already bootstrapped
launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$DST"
launchctl enable "gui/$UID_N/$LABEL"

echo "已安装：$DST"
echo "仓库路径：$REPO"
launchctl print "gui/$UID_N/$LABEL" 2>/dev/null | grep -E "state|program|runs" | head -5 || true
