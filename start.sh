#!/bin/bash
# 围棋 AI 对弈启动脚本
cd "$(dirname "$0")"
echo "正在启动围棋 AI 对弈..."
echo "浏览器打开: http://localhost:8080"
echo ""
open http://localhost:8080 2>/dev/null &
python3 app.py
