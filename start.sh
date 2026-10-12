#!/bin/bash
# 围棋 AI 对弈启动脚本
cd "$(dirname "$0")"
PYTHON="/Users/jinghuahao/.catpaw/desk_default_workspace/.go-ai-runtime/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "错误: 图像识别运行环境未准备好: $PYTHON"
    exit 1
fi

echo "正在启动围棋 AI 对弈..."
echo "浏览器打开: http://localhost:8080"
echo ""
open http://localhost:8080 2>/dev/null &
"$PYTHON" server.py
