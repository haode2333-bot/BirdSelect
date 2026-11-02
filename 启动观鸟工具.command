#!/bin/bash
# 启动观鸟工具 (macOS)
# 双击本文件即可启动；首次若提示"无法打开"，右键 → 打开，或终端里执行:
#   chmod +x 启动观鸟工具.command
cd "$(dirname "$0")"

# 优先用随包便携 Python，否则用系统 python3（brew 安装）
if [ -x "./python/bin/python3" ]; then
    PY="./python/bin/python3"
elif [ -x "./python/bin/python" ]; then
    PY="./python/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

# 检查 exiftool（macOS 用 brew install exiftool）
if ! command -v exiftool >/dev/null 2>&1 && [ ! -x "./exiftool/exiftool" ]; then
    echo "⚠️ 未找到 exiftool，请先安装：brew install exiftool" >&2
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "$PY" bird_desktop.py
