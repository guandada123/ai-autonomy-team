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

# "装上了"不等于"跑得起来"：外置卷 + 未授予完全磁盘访问权限时，任务会以 ALERT 退出。
# 本脚本不做 kickstart（那会立刻真跑一次），但必须把这个前提说清楚。
echo
echo "⚠️  装好 ≠ 能跑。请先验证一次："
echo "    launchctl kickstart -k gui/\$(id -u)/$LABEL && sleep 3 && tail -3 /tmp/aiteam-recompute.err"
echo "    若出现 ALERT 或 Operation not permitted → 说明解释器缺「完全磁盘访问权限」，"
echo "    去 系统设置 › 隐私与安全性 › 完全磁盘访问权限 里加上 /usr/bin/python3 后再试。"
