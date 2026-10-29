#!/bin/sh
# tools/coverage_run.sh —— 在独立 venv 里跑全量自检并统计覆盖率（开发期工具）
#
# 为什么需要独立环境：本仓产品代码零第三方依赖（D-0032），coverage 只用于开发期度量，
# 绝不能进主依赖。找不到装好 coverage 的解释器就报错，不静默降级。
#
# 用法：
#   tools/coverage_run.sh                 # 默认找 /tmp/cov-venv 或 .venv-cov
#   COV_PY=/path/to/python tools/coverage_run.sh
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -z "$COV_PY" ]; then
    for c in "$ROOT/.venv-cov/bin/python" "/tmp/cov-venv/bin/python"; do
        if [ -x "$c" ]; then COV_PY=$c; break; fi
    done
fi
if [ -z "$COV_PY" ]; then
    echo "[ERR] 找不到装了 coverage 的解释器。建一个：python3 -m venv /tmp/cov-venv && /tmp/cov-venv/bin/pip install coverage" >&2
    exit 2
fi
if ! "$COV_PY" -c "import coverage" 2>/dev/null; then
    echo "[ERR] $COV_PY 里没有 coverage 模块" >&2
    exit 2
fi

DATA_DIR=/tmp/aiteam-coverage
mkdir -p "$DATA_DIR"
if ls "$DATA_DIR"/.coverage* >/dev/null 2>&1; then
    # 旧数据改名而不是删除（项目规则：删除的唯一去向是 _trash/）
    mv "$DATA_DIR" "$DATA_DIR.$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$DATA_DIR"
fi

cd "$ROOT"
export COVERAGE_PROCESS_START="$ROOT/tools/.coveragerc"
# data_file 的落点由 COVERAGE_FILE 决定（rc 里的 data_file 对 combine 不生效，实测）
export COVERAGE_FILE="$DATA_DIR/.coverage"
export PYTHONPATH="$ROOT/tools${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$(dirname "$COV_PY"):$PATH"

echo "── 跑全量自检（覆盖率旁路已开）──"
make selftest

echo
echo "── 合并子进程数据 ──"
# ★ 2026-10-29：combine/report 必须显式带 --rcfile（rc 在 tools/ 下、不在 cwd，
#   不带的话 [report]/[paths] 两段从未生效 —— omit 恰好在采集期生效才没露馅）。
"$COV_PY" -m coverage combine --rcfile="$ROOT/tools/.coveragerc" "$DATA_DIR"
BASE=$(basename "$ROOT")
# ★ 2026-10-29：scripts/metrics.py 与 scripts/fsutil.py 是包内同构副本的镜像，
#   [paths] 按文件映射无效（见 .coveragerc 注释），报告期直接省略，
#   以包内副本（真正被执行的份）为准，避免 38% 的假低分行。
"$COV_PY" -m coverage report --rcfile="$ROOT/tools/.coveragerc" \
    --include="*/$BASE/scripts/*,*/$BASE/migration-package/*" \
    --omit="*/scripts/metrics.py,*/scripts/fsutil.py"
