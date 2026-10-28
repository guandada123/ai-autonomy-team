#!/usr/bin/env sh
# run.sh —— 硬裁判 JD-01 的 POSIX 薄封装
# 适用：macOS / Linux / Windows Git Bash
# 职责单一：找到解释器 → 转发参数。不做任何业务逻辑。

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        PY="$c"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "[ERR] 未找到 python3 / python。" >&2
    echo "      macOS 可执行: xcode-select --install" >&2
    echo "      或从 python.org 安装 Python 3.9+" >&2
    exit 2
fi

# 版本下限检查（硬裁判只用标准库，但语法需 3.9+）
VER=$("$PY" -c 'import sys;print("%d%02d"%sys.version_info[:2])' 2>/dev/null || echo "000")
if [ "$VER" -lt 309 ] 2>/dev/null; then
    echo "[ERR] $PY 版本过低（需 Python 3.9+，检测到 $VER）" >&2
    exit 2
fi

exec "$PY" "$DIR/verify.py" "$@"
