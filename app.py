"""
围棋 AI 对弈 Web 应用
使用 KataGo 引擎 + Python 内置 HTTP 服务器（无需第三方依赖）
"""
import subprocess
import threading
import json
import os
import sys
import time
import base64
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

try:
    from board_recognition import recognize_board
    HAS_RECOGNITION = True
except ImportError:
    HAS_RECOGNITION = False
    print("警告: board_recognition 模块不可用，图像识别功能将禁用")

# KataGo GTP 引擎管理（用于对弈）
class KataGoEngine:
    def __init__(self, katago_path, model_path, config_path=None):
        self.katago_path = katago_path
        self.model_path = model_path
        self.config_path = config_path
        self.process = None
        self.lock = threading.Lock()
        
    def start(self):
        cmd = [self.katago_path, "gtp"]
        if self.model_path:
            cmd.extend(["-model", self.model_path])
        if self.config_path:
            cmd.extend(["-config", self.config_path])
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()
        print("KataGo GTP 引擎已启动")
        
    def _drain_stderr(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
            except:
                break
        
    def send_command(self, command):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                print("引擎已退出，尝试自动重启...")
                self._restart()
                if not self.process or self.process.poll() is not None:
                    return None
            try:
                self.process.stdin.write(command + "\n")
                self.process.stdin.flush()
                
                response_lines = []
                while True:
                    line = self.process.stdout.readline()
                    if line == "":
                        break
                    stripped = line.rstrip('\n')
                    if stripped == "" and response_lines:
                        break
                    if stripped != "":
                        response_lines.append(stripped)
                
                return "\n".join(response_lines)
            except Exception as e:
                print(f"引擎通信错误: {e}")
                return None
    
    def _restart(self):
        """重启 GTP 引擎"""
        try:
            if self.process:
                self.process.terminate()
                self.process = None
            self.start()
            print("GTP 引擎自动重启成功")
        except Exception as e:
            print(f"GTP 引擎重启失败: {e}")
    
    def set_max_visits(self, visits):
        """设置 AI 最大搜索次数来控制强度"""
        self.send_command(f"kata-set-param maxVisits {visits}")
    
    def genmove(self, color):
        resp = self.send_command(f"genmove {color}")
        if resp and resp.startswith("= "):
            return resp[2:].strip()
        return None
    
    def play(self, color, move):
        resp = self.send_command(f"play {color} {move}")
        return resp is not None and resp.startswith("=")
    
    def clear_board(self):
        self.send_command("clear_board")
        
    def set_boardsize(self, size):
        self.send_command(f"boardsize {size}")
        
    def set_komi(self, komi):
        self.send_command(f"komi {komi}")
    
    def undo(self):
        resp = self.send_command("undo")
        return resp is not None and resp.startswith("=")
    
    def stop(self):
        if self.process:
            try:
                self.send_command("quit")
            except:
                pass
            self.process.terminate()
            self.process = None


# KataGo Analysis 引擎（用于局面分析）
class KataGoAnalysisEngine:
    def __init__(self, katago_path, model_path, config_path):
        self.katago_path = katago_path
        self.model_path = model_path
        self.config_path = config_path
        self.process = None
        self.lock = threading.Lock()
        self.ready = False
    
    def start(self):
        cmd = [
            self.katago_path, "analysis",
            "-model", self.model_path,
            "-config", self.config_path
        ]
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()
        # 等待引擎就绪
        time.sleep(3)
        self.ready = True
        print("KataGo Analysis 引擎已启动")
    
    def _drain_stderr(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
            except:
                break
    
    def _restart(self):
        """重启 Analysis 引擎"""
        try:
            if self.process:
                self.process.terminate()
                self.process = None
            self.ready = False
            self.start()
            print("Analysis 引擎自动重启成功")
        except Exception as e:
            print(f"Analysis 引擎重启失败: {e}")
    
    def analyze(self, stones, next_player, board_size=19, komi=6.5, max_visits=5000, max_time=30.0):
        """
        分析局面，返回 Top N 推荐走法。
        
        stones: [{"color": "B"/"W", "pos": "D4"}, ...] 棋盘上所有棋子
        next_player: "B" 或 "W"，轮到谁走
        board_size: 棋盘大小
        komi: 贴目
        max_visits: 最大搜索次数（控制 AI 强度）
        max_time: 最大思考时间（秒）
        """
        with self.lock:
            if not self.process or self.process.poll() is not None:
                print("Analysis 引擎已退出，尝试自动重启...")
                self._restart()
                if not self.process or self.process.poll() is not None:
                    return {"error": "Analysis engine not running"}
            
            # 构造 moves 序列：按交替顺序排列棋子
            # KataGo 的 moves 序列决定了评估的上下文（谁先手、手数等）
            black_stones = [s["pos"] for s in stones if s["color"] == "B"]
            white_stones = [s["pos"] for s in stones if s["color"] == "W"]
            
            # 按 B,W,B,W... 交替排列
            moves = []
            bi, wi = 0, 0
            while bi < len(black_stones) or wi < len(white_stones):
                if bi < len(black_stones):
                    moves.append(["B", black_stones[bi]])
                    bi += 1
                if wi < len(white_stones):
                    moves.append(["W", white_stones[wi]])
                    wi += 1
            
            # 确定当前轮到谁走：
            # moves 序列结束后，下一手由序列长度的奇偶决定（偶数=轮黑，奇数=轮白）
            # 如果 next_player 与自然顺序不同，插入一个 pass
            natural_next = "B" if len(moves) % 2 == 0 else "W"
            if natural_next != next_player:
                moves.append([natural_next, "pass"])
            
            query = {
                "id": f"analyze_{int(time.time()*1000)}",
                "moves": moves,
                "rules": "chinese",
                "komi": komi,
                "boardXSize": board_size,
                "boardYSize": board_size,
                "analyzeTurns": [len(moves)],
                "maxVisits": max_visits,
                "overrideSettings": {
                    "maxTime": max_time,
                    "ignorePreRootHistory": True
                }
            }
            
            try:
                self.process.stdin.write(json.dumps(query) + "\n")
                self.process.stdin.flush()
                
                line = self.process.stdout.readline()
                if not line:
                    return {"error": "No response from engine"}
                
                resp = json.loads(line)
                
                if "error" in resp:
                    return {"error": resp["error"]}
                
                move_infos = resp.get("moveInfos", [])
                results = []
                for mi in move_infos[:5]:
                    # reportAnalysisWinratesAs = BLACK，所以 winrate/scoreLead 始终是黑棋视角
                    # 当 next_player 为白棋时，需要翻转为白棋视角
                    raw_winrate = mi["winrate"]
                    raw_score = mi.get("scoreLead", 0)
                    if next_player == "W":
                        display_winrate = 1.0 - raw_winrate
                        display_score = -raw_score
                    else:
                        display_winrate = raw_winrate
                        display_score = raw_score
                    results.append({
                        "move": mi["move"],
                        "winrate": round(display_winrate * 100, 1),
                        "scoreLead": round(display_score, 1),
                        "visits": mi["visits"],
                        "pv": mi.get("pv", [])
                    })
                # 按胜率从高到低排序
                results.sort(key=lambda x: x["winrate"], reverse=True)
                
                return {
                    "status": "ok",
                    "recommendations": results,
                    "turnNumber": resp.get("turnNumber")
                }
            except Exception as e:
                return {"error": str(e)}
    
    def stop(self):
        if self.process:
            try:
                self.process.terminate()
            except:
                pass
            self.process = None


# 全局引擎实例
engine = None
analysis_engine = None

def find_katago():
    candidates = [
        "/opt/homebrew/bin/katago",
        "/usr/local/bin/katago",
        os.path.join(os.path.dirname(__file__), "katago-src", "cpp", "build", "katago"),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    result = subprocess.run(["which", "katago"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None

def find_model():
    go_ai_dir = os.path.dirname(os.path.abspath(__file__))
    for f in os.listdir(go_ai_dir):
        if f.endswith(".bin.gz"):
            return os.path.join(go_ai_dir, f)
    return None

def find_config():
    go_ai_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = os.path.join(go_ai_dir, "gtp.cfg")
    if os.path.exists(cfg):
        return cfg
    return None

def find_analysis_config():
    go_ai_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = os.path.join(go_ai_dir, "analysis.cfg")
    if os.path.exists(cfg):
        return cfg
    return None


# HTTP 请求处理
class GoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.directory = os.path.dirname(os.path.abspath(__file__))
        super().__init__(*args, directory=self.directory, **kwargs)
    
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.serve_html()
        elif parsed.path == "/api/status":
            gtp_ok = engine is not None and engine.process is not None and engine.process.poll() is None
            ana_ok = analysis_engine is not None and analysis_engine.ready and analysis_engine.process is not None and analysis_engine.process.poll() is None
            self.send_json({
                "engine_running": gtp_ok,
                "analysis_ready": ana_ok
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
        
        if parsed.path == "/api/new_game":
            self.handle_new_game(data)
        elif parsed.path == "/api/play":
            self.handle_play(data)
        elif parsed.path == "/api/ai_move":
            self.handle_ai_move(data)
        elif parsed.path == "/api/undo":
            self.handle_undo(data)
        elif parsed.path == "/api/pass":
            self.handle_pass(data)
        elif parsed.path == "/api/resign":
            self.send_json({"status": "ok", "message": "你认输了，AI 获胜"})
        elif parsed.path == "/api/analyze":
            self.handle_analyze(data)
        elif parsed.path == "/api/recognize":
            self.handle_recognize(data)
        elif parsed.path == "/api/screenshot":
            self.handle_screenshot(data)
        else:
            self.send_error(404)
    
    def handle_new_game(self, data):
        global engine
        board_size = data.get("board_size", 19)
        komi = data.get("komi", 6.5)
        max_visits = data.get("max_visits", 1600)
        
        if engine:
            engine.set_boardsize(board_size)
            engine.clear_board()
            engine.set_komi(komi)
            engine.set_max_visits(max_visits)
        
        self.send_json({"status": "ok", "board_size": board_size, "komi": komi})
    
    def handle_play(self, data):
        global engine
        color = data.get("color", "B")
        move = data.get("move")
        
        if not engine:
            self.send_json({"status": "error", "message": "引擎未启动"}, 500)
            return
        
        success = engine.play(color, move)
        if not success:
            self.send_json({"status": "error", "message": "非法落子"}, 400)
            return
        
        ai_color = "W" if color == "B" else "B"
        ai_move = engine.genmove(ai_color)
        
        if ai_move:
            self.send_json({"status": "ok", "ai_move": ai_move, "ai_color": ai_color})
        else:
            self.send_json({"status": "error", "message": "AI 无法生成落子"}, 500)
    
    def handle_ai_move(self, data):
        global engine
        color = data.get("color", "B")
        
        if not engine:
            self.send_json({"status": "error", "message": "引擎未启动"}, 500)
            return
        
        ai_move = engine.genmove(color)
        if ai_move:
            self.send_json({"status": "ok", "ai_move": ai_move, "ai_color": color})
        else:
            self.send_json({"status": "error", "message": "AI 无法生成落子"}, 500)
    
    def handle_undo(self, data):
        global engine
        if not engine:
            self.send_json({"status": "error", "message": "引擎未启动"}, 500)
            return
        
        engine.undo()
        engine.undo()
        self.send_json({"status": "ok"})
    
    def handle_pass(self, data):
        global engine
        color = data.get("color", "B")
        
        if not engine:
            self.send_json({"status": "error", "message": "引擎未启动"}, 500)
            return
        
        engine.play(color, "pass")
        ai_color = "W" if color == "B" else "B"
        ai_move = engine.genmove(ai_color)
        
        self.send_json({"status": "ok", "ai_move": ai_move, "ai_color": ai_color})
    
    def handle_recognize(self, data):
        """处理棋盘截图识别请求"""
        if not HAS_RECOGNITION:
            self.send_json({"status": "error", "message": "图像识别模块不可用，请安装 opencv-python-headless 和 numpy"}, 500)
            return
        
        image_data = data.get("image")
        board_size = data.get("board_size", 19)
        
        if not image_data:
            self.send_json({"status": "error", "message": "未提供图片数据"}, 400)
            return
        
        result = recognize_board(image_data, board_size)
        self.send_json(result)
    
    def handle_screenshot(self, data):
        """一键截屏 + 识别 + 分析"""
        import tempfile
        
        if not HAS_RECOGNITION:
            self.send_json({"status": "error", "message": "图像识别模块不可用"}, 500)
            return
        
        board_size = data.get("board_size", 19)
        max_visits = data.get("max_visits", 5000)
        
        # 使用 macOS screencapture 进行交互式区域截屏
        tmp_path = os.path.join(tempfile.gettempdir(), "go_screenshot.png")
        try:
            result = subprocess.run(
                ["screencapture", "-i", tmp_path],
                timeout=30
            )
            if result.returncode != 0 or not os.path.exists(tmp_path):
                self.send_json({"status": "cancelled", "message": "截屏已取消"})
                return
        except subprocess.TimeoutExpired:
            self.send_json({"status": "error", "message": "截屏超时"})
            return
        except Exception as e:
            self.send_json({"status": "error", "message": f"截屏失败: {e}"})
            return
        
        # 读取截图并转 base64
        try:
            with open(tmp_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")
            os.remove(tmp_path)
        except Exception as e:
            self.send_json({"status": "error", "message": f"读取截图失败: {e}"})
            return
        
        # 识别棋盘
        recognize_result = recognize_board(image_b64, board_size)
        if recognize_result.get("status") != "ok":
            self.send_json(recognize_result)
            return
        
        # 自动进行 AI 分析
        stones = recognize_result.get("stones", [])
        if not stones:
            self.send_json({"status": "ok", "recognized": True, "stones": [], "stone_count": recognize_result.get("stone_count", {}), "message": "棋盘为空，无需分析"})
            return
        
        # 推断下一手颜色
        black_count = sum(1 for s in stones if s["color"] == "B")
        white_count = sum(1 for s in stones if s["color"] == "W")
        next_player = "B" if black_count <= white_count else "W"
        
        # 调用分析引擎
        if analysis_engine and analysis_engine.ready:
            analysis_result = analysis_engine.analyze(
                stones=[{"color": s["color"], "pos": s["pos"]} for s in stones if "pos" in s],
                next_player=next_player,
                board_size=board_size,
                max_visits=max_visits,
                max_time=30.0
            )
            # 合并结果
            self.send_json({
                "status": "ok",
                "stones": stones,
                "stone_count": recognize_result.get("stone_count", {}),
                "next_player": next_player,
                "recommendations": analysis_result.get("recommendations", []),
                "image": image_b64
            })
        else:
            # 引擎不可用时只返回识别结果
            self.send_json({
                "status": "ok",
                "stones": stones,
                "stone_count": recognize_result.get("stone_count", {}),
                "next_player": next_player,
                "recommendations": [],
                "message": "分析引擎未就绪，仅返回识别结果",
                "image": image_b64
            })
    
    def handle_analyze(self, data):
        """处理局面分析请求"""
        global analysis_engine
        
        if not analysis_engine or not analysis_engine.ready:
            self.send_json({"status": "error", "message": "分析引擎未就绪"}, 500)
            return
        
        stones = data.get("stones", [])
        next_player = data.get("next_player", "B")
        board_size = data.get("board_size", 19)
        komi = data.get("komi", 6.5)
        max_visits = data.get("max_visits", 5000)
        
        if not stones:
            self.send_json({"status": "error", "message": "棋盘为空"}, 400)
            return
        
        result = analysis_engine.analyze(
            stones=stones,
            next_player=next_player,
            board_size=board_size,
            komi=komi,
            max_visits=max_visits,
            max_time=30.0
        )
        
        self.send_json(result)
    
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


if __name__ == "__main__":
    go_ai_dir = os.path.dirname(os.path.abspath(__file__))
    
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
    
    # 启动 GTP 引擎（对弈用）
    print("  正在启动 GTP 引擎...")
    engine = KataGoEngine(katago_path, model_path, config_path)
    engine.start()
    
    test = engine.send_command("name")
    if test and "KataGo" in test:
        print("  GTP 引擎启动成功! ✓")
    else:
        print(f"  GTP 引擎响应: {test}")
    
    # 启动 Analysis 引擎（局面分析用）
    if analysis_config_path:
        print("  正在启动 Analysis 引擎...")
        analysis_engine = KataGoAnalysisEngine(katago_path, model_path, analysis_config_path)
        analysis_engine.start()
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
    
    server = HTTPServer(('0.0.0.0', port), GoHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        engine.stop()
        if analysis_engine:
            analysis_engine.stop()
        server.shutdown()
