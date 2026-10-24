#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# sync_score.sh —— 把判分器单一真源推送到各仓库（见 score.SINGLE_SOURCE.md）
#
# 用法：
#   bash audit/kit/sync_score.sh                      # 同步默认目标（Claw/StockInsight）
#   bash audit/kit/sync_score.sh /path/to/QTS         # 追加显式目标
#
# 不变量：副本只允许头部注释与真源不同；正文须逐字节一致。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC="$SCRIPT_DIR/score.py"

# 自检：真源必须自包含（仅标准库，禁止本地兄弟模块依赖）
nonstd="$(grep -oE "^[[:space:]]*(import|from) [a-zA-Z_][a-zA-Z0-9_]*" "$SRC" \
          | grep -vE "(import|from) (os|sys|json|re|pathlib|collections|argparse|math|datetime|typing|functools|itertools|__future__)" \
          || true)"
if [ -n "$nonstd" ]; then
  echo "[sync] ❌ 真源含非标准库依赖，拒绝同步（避免副本带隐藏依赖）：" >&2
  echo "$nonstd" >&2
  exit 1
fi

COMMIT="$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
TS="$(date +%Y-%m-%dT%H:%M:%S)"

# 默认目标：Claw/StockInsight/QTS 在 WorkBuddy/ 下，ai-autonomy-team 在 WorkBuddy/<日期>/ 下多嵌一层
# QTS = QuantTradingSystem（2026-10-25 定案：git remote = QuantTradingSystem.git）
DEFAULT_TARGETS=(
  "$REPO_ROOT/../../Claw"
  "$REPO_ROOT/../../StockInsight"
  "$REPO_ROOT/../../QuantTradingSystem"
)
TARGETS=("${DEFAULT_TARGETS[@]}" "$@")

for t in "${TARGETS[@]}"; do
  # 展开相对路径
  t="$(cd "$REPO_ROOT" && cd "$(dirname "$t")" 2>/dev/null && pwd)/$(basename "$t")" || {
    echo "[sync] ⚠ 跳过不存在的目标：$t"; continue; }
  [ -d "$t" ] || { echo "[sync] ⚠ 跳过不存在的目标：$t"; continue; }

  DEST_DIR="$t/tools/audit_kit"
  mkdir -p "$DEST_DIR"
  DEST="$DEST_DIR/score.py"

  # 写头部注释 + 真源正文
  {
    echo "# SYNCED COPY — single source: ai-autonomy-team/audit/kit/score.py"
    echo "# SYNCED FROM @ $COMMIT  ($TS) — 禁止在此副本直接改；改真源后重跑 sync_score.sh"
    echo "# ============================================================================="
    cat "$SRC"
  } > "$DEST"

  echo "[sync] ✅ $t/tools/audit_kit/score.py  (from @ $COMMIT)"
done

echo "[sync] 完成。请在各目标仓库自行 git add/commit；副本仅在头部注释上与真源不同。"
