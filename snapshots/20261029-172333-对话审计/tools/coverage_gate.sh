#!/bin/sh
# tools/coverage_gate.sh —— 覆盖率 ratchet 门槛（CI 步骤用，非 A–H 门禁项）
#
# 为什么用 ratchet 而不是固定阈值：基线只许上不许下（容差 -2 防波动），
# 删测试或加未测代码都会红；要抬高基线就改 coverage-baseline.txt（一次一行，仅数字）。
#
# 用法1：tools/coverage_gate.sh 79     # 直接给 TOTAL，可单测比较逻辑
# 用法2：在 CI 里先跑 coverage_run.sh 落盘，再用 awk 取 TOTAL 传入
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BASE=$(cat "$ROOT/tools/coverage-baseline.txt")
PCT="$1"
if [ -z "$PCT" ]; then
    echo "[FAIL] 未提供 TOTAL（coverage_run 输出坏了也算失败，不许静默跳过）" >&2
    exit 2
fi
case "$PCT" in
    ''|*[!0-9]*) echo "[FAIL] TOTAL 非整数：'$PCT'" >&2; exit 2 ;;
esac
TOL=$((BASE - 2))
echo "覆盖率 TOTAL=${PCT}% ｜ 基线 ${BASE}%（容差 -2 → 下限 ${TOL}%）"
if [ "$PCT" -lt "$TOL" ]; then
    echo "[FAIL] 覆盖率 ${PCT}% 低于下限 ${TOL}% —— 删了测试或加了未测代码，先补测再合" >&2
    exit 1
fi
echo "[PASS] 覆盖率门槛通过（${PCT}% ≥ ${TOL}%）"
