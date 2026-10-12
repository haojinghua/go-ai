#!/usr/bin/env python3
"""
围棋 AI 对弈 Web 应用 —— 兼容入口。

实际实现已按业务线拆分：
  server.py   HTTP 服务入口（路由表 + 启动流程）
  play.py     对弈业务线（GTP 引擎管家 + 对弈 API）
  analysis.py 分析业务线（Analysis 引擎管家 + 识别 + 讲解 + 分析 API）
  common/     公共工具（坐标 / KataGo 子进程基座 / SGF 解析）

旧的 `python app.py` 启动方式继续可用；推荐使用 ./start.sh。
"""
from server import main

if __name__ == "__main__":
    main()
