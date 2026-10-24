#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# sync_score.sh —— 把判分器单一真源推送到各仓库（见 score.SINGLE_SOURCE.md）
#
# 用法：
#   bash audit/kit/sync_score.sh                      # 同步默认目标（Claw/StockInsight/QTS）
#   bash audit/kit/sync_score.sh /path/to/other-repo  # 追加显式目标
#
# 不变量：副本只允许头部注释与真源不同；正文须逐字节一致。
#
# 2026-10-25 修：目标全部不存在时**必须失败退出**。旧版只 warn，退出码仍 0，
# 于是"一个都没同步"与"全部同步成功"在 CI 里长得一样（见 L-0027）。

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

SYNCED=0
SKIPPED=0

for t in "${TARGETS[@]}"; do
  # 展开相对路径
  t="$(cd "$REPO_ROOT" && cd "$(dirname "$t")" 2>/dev/null && pwd)/$(basename "$t")" || {
    echo "[sync] ⚠ 跳过不存在的目标：$t"; SKIPPED=$((SKIPPED+1)); continue; }
  if [ ! -d "$t" ]; then
    echo "[sync] ⚠ 跳过不存在的目标：$t"; SKIPPED=$((SKIPPED+1)); continue
  fi

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
  SYNCED=$((SYNCED+1))
done

# 一个都没同步成 → 必须失败，不能让"静默 no-op"冒充成功（L-0027）
if [ "$SYNCED" -eq 0 ]; then
  echo "[sync] ❌ 成功同步 0 个目标（跳过 $SKIPPED 个）——视为失败。" >&2
  echo "       常见原因：默认路径基准不对（本仓库嵌在 WorkBuddy/<日期>/ 下，目标在 WorkBuddy/ 下）。" >&2
  exit 1
fi

echo "[sync] 完成：成功 $SYNCED 个，跳过 $SKIPPED 个。请在各目标仓库自行 git add/commit；副本仅在头部注释上与真源不同。"
