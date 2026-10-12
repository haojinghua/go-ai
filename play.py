"""
对弈业务线：GTP 引擎管家 + 对弈相关 API 实现。

职责边界（与分析业务线无数据耦合）：
- 只服务人机对弈场景：new_game / play / ai_move / undo / pass / load_game
- 棋局状态只存在于本模块（GTP 引擎内部），分析线不读写
"""
import threading

from common.katago_base import KatagoProcess


class KataGoEngine(KatagoProcess):
    """KataGo GTP 引擎管理（用于对弈）"""

    label = "GTP 引擎"

    def start(self):
        cmd = [self.katago_path, "gtp"]
        if self.model_path:
            cmd.extend(["-model", self.model_path])
        if self.config_path:
            cmd.extend(["-config", self.config_path])

        self._spawn(cmd)
        print("KataGo GTP 引擎已启动")

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


# ==============================================================
# 模块级引擎实例与 API 实现
# 每个 api_* 函数接收已解析的请求 JSON，返回 (响应dict, HTTP状态码)
# ==============================================================

class PlaySession:
    """对局状态（对弈模块内部的唯一真相源）：与 GTP 引擎保持同步的着法历史。
    undo 不再盲撤两手，而是截断本历史后从空盘重放，两份状态永不分叉。"""

    def __init__(self):
        self.moves = []        # [[color, move], ...] move 为 GTP 坐标或 "pass"
        self.board_size = 19
        self.komi = 6.5

    def reset(self, board_size=None, komi=None):
        self.moves = []
        if board_size:
            self.board_size = board_size
        if komi is not None:
            self.komi = komi

    def record(self, color, move):
        self.moves.append([color, move])

    def truncate(self, keep):
        self.moves = self.moves[:max(0, keep)]


engine = None
session = PlaySession()


def start_engine(katago_path, model_path, config_path):
    """启动 GTP 引擎（对弈用），返回引擎实例"""
    global engine
    engine = KataGoEngine(katago_path, model_path, config_path)
    engine.start()
    return engine


def stop_engine():
    if engine:
        engine.stop()


def engine_alive():
    return engine is not None and engine.process is not None and engine.process.poll() is None


def api_new_game(data):
    board_size = data.get("board_size", 19)
    komi = data.get("komi", 6.5)
    max_visits = data.get("max_visits", 1600)

    session.reset(board_size, komi)
    if engine:
        engine.set_boardsize(board_size)
        engine.clear_board()
        engine.set_komi(komi)
        engine.set_max_visits(max_visits)

    return {"status": "ok", "board_size": board_size, "komi": komi}, 200


def api_play(data):
    color = data.get("color", "B")
    move = data.get("move")

    if not engine:
        return {"status": "error", "message": "引擎未启动"}, 500

    success = engine.play(color, move)
    if not success:
        return {"status": "error", "message": "非法落子"}, 400
    session.record(color, move)

    ai_color = "W" if color == "B" else "B"
    ai_move = engine.genmove(ai_color)

    if ai_move:
        if ai_move.strip().lower() != "resign":
            session.record(ai_color, ai_move)  # pass 也记录，重放时需要
        return {"status": "ok", "ai_move": ai_move, "ai_color": ai_color}, 200
    return {"status": "error", "message": "AI 无法生成落子"}, 500


def api_ai_move(data):
    color = data.get("color", "B")

    if not engine:
        return {"status": "error", "message": "引擎未启动"}, 500

    ai_move = engine.genmove(color)
    if ai_move:
        if ai_move.strip().lower() != "resign":
            session.record(color, ai_move)  # pass 也记录，重放时需要
        return {"status": "ok", "ai_move": ai_move, "ai_color": color}, 200
    return {"status": "error", "message": "AI 无法生成落子"}, 500


def api_undo(data):
    if not engine:
        return {"status": "error", "message": "引擎未启动"}, 500

    if "keep" in data:
        # 新契约：前端告知要保留的着法数，后端截断自己的历史后从空盘重放，
        # 不再盲撤两手（盲撤在 AI pass/认输后会与前端状态分叉）
        keep = data.get("keep")
        if not isinstance(keep, int) or keep < 0:
            keep = 0
        if keep > len(session.moves):
            keep = len(session.moves)
        session.truncate(keep)
        engine.clear_board()
        for color, move in session.moves:
            if not engine.play(color, move):
                # 重放失败（正常不会发生）：清空会话避免两份状态继续分叉
                session.reset()
                engine.clear_board()
                return {"status": "error", "message": "悔棋重放失败，对局已重置"}, 500
        return {"status": "ok", "moves_kept": keep}, 200

    # 兼容旧客户端：无 keep 参数时保持原有行为
    engine.undo()
    engine.undo()
    return {"status": "ok"}, 200


def api_pass(data):
    color = data.get("color", "B")

    if not engine:
        return {"status": "error", "message": "引擎未启动"}, 500

    engine.play(color, "pass")
    session.record(color, "pass")
    ai_color = "W" if color == "B" else "B"
    ai_move = engine.genmove(ai_color)
    if ai_move and ai_move.strip().lower() != "resign":
        session.record(ai_color, ai_move)

    return {"status": "ok", "ai_move": ai_move, "ai_color": ai_color}, 200


def api_load_game(data):
    """载入保存的对局：重放全部着法到 GTP 引擎（不触发 AI 应答）"""
    if not engine:
        return {"status": "error", "message": "引擎未启动"}, 500

    board_size = data.get("board_size", 19)
    komi = data.get("komi", 6.5)
    max_visits = data.get("max_visits", 1600)
    moves = data.get("moves", [])

    if not isinstance(moves, list):
        return {"status": "error", "message": "moves 必须是数组"}, 400
    if len(moves) > 1000:
        return {"status": "error", "message": "着法数超过上限 1000"}, 400

    engine.set_boardsize(board_size)
    engine.clear_board()
    engine.set_komi(komi)
    engine.set_max_visits(max_visits)
    session.reset(board_size, komi)

    replayed = 0
    for m in moves:
        if not isinstance(m, list) or len(m) != 2:
            return {"status": "error", "message": "着法格式错误: %r" % (m,), "replayed": replayed}, 400
        color, move = m[0], m[1]
        if color not in ("B", "W") or not isinstance(move, str) or not move.strip():
            return {"status": "error", "message": "着法格式错误: %r" % (m,), "replayed": replayed}, 400
        if not engine.play(color, move):
            session.reset()  # 棋盘已重置，会话同步清空
            return {"status": "error", "message": "第 %d 手重放失败（引擎拒绝 %s %s），棋盘已重置，请检查棋谱" % (replayed + 1, color, move), "replayed": replayed}, 400
        session.record(color, move)
        replayed += 1

    return {"status": "ok", "replayed": replayed, "board_size": board_size, "komi": komi}, 200
