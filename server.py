"""
HTTP 服务入口：路由表 + 静态文件托管 + 启动流程。

本文件不含任何业务逻辑：
- 对弈线 → play.py（/api/new_game, /api/play, /api/ai_move, /api/undo, /api/pass, /api/load_game）
- 分析线 → analysis.py（/api/analyze, /api/analyze_game, /api/group_life, /api/explain,
                       /api/recognize, /api/import_sgf）
- 两条线各自独立管理自己的引擎子进程，仅共享本文件提供的 HTTP 服务与静态文件。
"""
import json
import os
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import analysis
import play
from common.katago_base import find_katago, find_model, find_config, find_analysis_config

# POST 路由表：path -> (data) -> (payload, http_code)
POST_ROUTES = {
    # —— 对弈业务线 ——
    "/api/new_game":  play.api_new_game,
    "/api/play":      play.api_play,
    "/api/ai_move":   play.api_ai_move,
    "/api/undo":      play.api_undo,
    "/api/pass":      play.api_pass,
    "/api/resign":    lambda data: ({"status": "ok", "message": "你认输了，AI 获胜"}, 200),
    "/api/load_game": play.api_load_game,
    # —— 分析业务线 ——
    "/api/analyze":     analysis.api_analyze,
    "/api/analyze_game": analysis.api_analyze_game,
    "/api/group_life":  analysis.api_group_life,
    "/api/explain":     analysis.api_explain,
    "/api/recognize":   analysis.api_recognize,
    "/api/import_sgf":  analysis.api_import_sgf,
}


class GoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.directory = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=self.directory, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.serve_html()
        elif parsed.path == "/api/status":
            self.send_json({
                "engine_running": play.engine_alive(),
                "analysis_ready": analysis.analysis_ready()
            })
        else:
            super().do_GET()

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len) if content_len > 0 else b'{}'
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}

        parsed = urlparse(self.path)
        handler = POST_ROUTES.get(parsed.path)
        if handler is None:
            self.send_error(404)
            return
        try:
            payload, code = handler(data)
        except Exception as e:
            payload, code = {"status": "error", "message": "服务器内部错误: %s" % e}, 500
        self.send_json(payload, code)

    def send_json(self, data, code=200):
        response = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def serve_html(self):
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
        with open(html_path, 'rb') as f:
            content = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        if '/api/' in str(args[0]):
            print(f"  {args[0]}")


def main():
    katago_path = find_katago()
    model_path = find_model()
    config_path = find_config()
    analysis_config_path = find_analysis_config()

    if not katago_path:
        print("=" * 50)
        print("错误: 找不到 KataGo 可执行文件!")
        print("请运行: brew install katago")
        print("=" * 50)
        sys.exit(1)

    if not model_path:
        print("=" * 50)
        print("错误: 找不到模型文件 (.bin.gz)!")
        print("请确保模型文件位于 go-ai 目录下")
        print("=" * 50)
        sys.exit(1)

    print("=" * 50)
    print("  围棋 AI 对弈 & 分析系统")
    print("=" * 50)
    print(f"  KataGo:       {katago_path}")
    print(f"  模型:         {os.path.basename(model_path)}")
    print(f"  GTP 配置:     {config_path}")
    print(f"  Analysis 配置: {analysis_config_path}")
    print("-" * 50)

    # 启动 GTP 引擎（对弈线）
    print("  正在启动 GTP 引擎...")
    play_engine = play.start_engine(katago_path, model_path, config_path)

    test = play_engine.send_command("name")
    if test and "KataGo" in test:
        print("  GTP 引擎启动成功! ✓")
    else:
        print(f"  GTP 引擎响应: {test}")

    # 启动 Analysis 引擎（分析线）
    if analysis_config_path:
        print("  正在启动 Analysis 引擎...")
        analysis.start_engine(katago_path, model_path, analysis_config_path)
        print("  Analysis 引擎启动成功! ✓")
    else:
        print("  警告: 未找到 analysis.cfg，局面分析功能不可用")

    port = 8080
    print("-" * 50)
    print(f"  Web 界面已启动:")
    print(f"  → http://localhost:{port}")
    print("-" * 50)
    print("  按 Ctrl+C 退出")
    print("=" * 50)

    server = ThreadingHTTPServer(('0.0.0.0', port), GoHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        play.stop_engine()
        analysis.stop_engine()
        server.shutdown()


if __name__ == "__main__":
    main()
