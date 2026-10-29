#!/bin/sh
# tools/coverage_gate.sh —— 覆盖率 ratchet 门槛（CI 步骤用，非 A–H 门禁项）
#
# 为什么用 ratchet 而不是固定阈值：基线只许上不许下（容差 -2 防波动），
# 删测试或加未测代码都会红；要抬高基线就改 coverage-baseline.txt（一次一行，仅数字）。
#
# 用法：
#   tools/coverage_gate.sh 82          # 直接给 TOTAL（CI 里由 awk 从 coverage 输出提取）
#   tools/coverage_gate.sh --self-test # 5 组正负样本（基线读真实文件，样本随基线自动跟随）
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BASE_FILE="$ROOT/tools/coverage-baseline.txt"

# gate_check PCT BASE —— 打印结论，返回 0（过）/ 1（低于容差）/ 2（输入非法）
gate_check() {
    PCT="$1"; BASE="$2"
    if [ -z "$PCT" ]; then
        echo "[FAIL] 未提供 TOTAL（coverage_run 输出坏了也算失败，不许静默跳过）" >&2
        return 2
    fi
    case "$PCT" in
        ''|*[!0-9]*) echo "[FAIL] TOTAL 非整数：'$PCT'" >&2; return 2 ;;
    esac
    TOL=$((BASE - 2))
    echo "覆盖率 TOTAL=${PCT}% ｜ 基线 ${BASE}%（容差 -2 → 下限 ${TOL}%）"
    if [ "$PCT" -lt "$TOL" ]; then
        echo "[FAIL] 覆盖率 ${PCT}% 低于下限 ${TOL}% —— 删了测试或加了未测代码，先补测再合" >&2
        return 1
    fi
    echo "[PASS] 覆盖率门槛通过（${PCT}% ≥ ${TOL}%）"
    return 0
}

if [ "${1:-}" = "--self-test" ]; then
    # 5 组正负样本。★ 基线取真实文件：基线抬高后样本自动跟随，不会自己过期（R3 教训）。
    # set -e 下 gate_check 的非零返回必须落在条件上下文里，用 `|| rc=$?` 接住。
    BASE=$(cat "$BASE_FILE")
    B2=$((BASE-2)); B3=$((BASE-3))
    n_ok=0; n_bad=0
    expect() {  # expect 期望rc 实际rc 标签
        if [ "$1" = "$2" ]; then
            n_ok=$((n_ok+1)); echo "  [PASS] $3 (rc=$2)"
        else
            n_bad=$((n_bad+1)); echo "  [FAIL] $3 期望 rc=$1 实为 rc=$2"
        fi
    }
    rc=0; gate_check "$BASE" "$BASE" >/dev/null 2>&1 || rc=$?
    expect 0 "$rc" "正样本：TOTAL=基线"
    rc=0; gate_check "$B2" "$BASE" >/dev/null 2>&1 || rc=$?
    expect 0 "$rc" "正边界：TOTAL=基线-2"
    rc=0; gate_check "$B3" "$BASE" >/dev/null 2>&1 || rc=$?
    expect 1 "$rc" "负样本：低于容差（rc=1）"
    rc=0; gate_check "" "$BASE" >/dev/null 2>&1 || rc=$?
    expect 2 "$rc" "负样本：TOTAL 缺失（不许静默跳过）"
    rc=0; gate_check "abc" "$BASE" >/dev/null 2>&1 || rc=$?
    expect 2 "$rc" "负样本：TOTAL 非整数"
    echo
    if [ "$n_bad" -gt 0 ]; then
        echo "[FAIL] 门槛自检未通过：$n_bad 项失败"
        exit 2
    fi
    echo "[PASS] 门槛自检通过：$n_ok/$((n_ok+n_bad)) 项"
    exit 0
fi

BASE=$(cat "$BASE_FILE")
if gate_check "${1:-}" "$BASE"; then
    exit 0
else
    exit $?
fi
