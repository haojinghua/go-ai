"""
局面分析业务线：Analysis 引擎管家 + 截图识别 + AI 讲解 + 分析相关 API 实现。

职责边界（与对弈业务线无数据耦合）：
- 只服务分析场景：analyze / analyze_game / group_life / explain / recognize / import_sgf
- 接收前端发来的局面快照（stones / move_history），完全不读取对弈模块的状态
"""
import json
import os
import sys
import threading
import time
import urllib.request

from common import coords as _coords
from common.katago_base import KatagoProcess
from common.sgf import parse_sgf

try:
    from board_recognition import recognize_board
    HAS_RECOGNITION = True
except ImportError:
    # 兼容修复：直接用系统 python3 启动（如 ~/.catdesk/runtimes 的 python3）时缺 opencv/numpy，
    # 自动注入 go-ai 专用运行时的 site-packages（同版本 CPython 3.12，二进制兼容），再重试导入。
    _GOAI_RUNTIME_SITE = os.path.expanduser(
        "~/.catpaw/desk_default_workspace/.go-ai-runtime/lib/python3.12/site-packages"
    )
    if os.path.isdir(_GOAI_RUNTIME_SITE):
        sys.path.insert(0, _GOAI_RUNTIME_SITE)
    try:
        from board_recognition import recognize_board
        HAS_RECOGNITION = True
        print("图像识别依赖已从专用运行时加载:", _GOAI_RUNTIME_SITE)
    except ImportError:
        HAS_RECOGNITION = False
        print("警告: board_recognition 模块不可用，图像识别功能将禁用")
        print("提示: 请用 ./start.sh 启动，或安装 opencv-python-headless 和 numpy")


class KataGoAnalysisEngine(KatagoProcess):
    """KataGo Analysis 引擎（用于局面分析）"""

    label = "Analysis 引擎"

    def __init__(self, katago_path, model_path, config_path):
        super().__init__(katago_path, model_path, config_path)
        self.ready = False
        self._gen = 0                # 请求序号（过期淘汰用）
        self._gen_lock = threading.Lock()

    def next_ticket(self):
        """领取分析请求序号：新请求到达后，之前排队中的同引擎请求即视为过期"""
        with self._gen_lock:
            self._gen += 1
            return self._gen

    def start(self):
        cmd = [
            self.katago_path, "analysis",
            "-model", self.model_path,
            "-config", self.config_path
        ]
        self._spawn(cmd)
        # 等待引擎就绪（监听 stderr 中的就绪信号，最多等 10 秒）
        for _ in range(100):
            if self.ready:
                break
            time.sleep(0.1)
        if not self.ready:
            print("警告: Analysis 引擎就绪信号未检测到，继续尝试...")
            self.ready = True
        print("KataGo Analysis 引擎已启动")

    def _drain_stderr(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
                # KataGo analysis 启动后会输出包含 "Started" 或 "ready" 的行
                if not self.ready and ('Started' in line or 'ready' in line.lower() or 'Begin' in line):
                    self.ready = True
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

    def analyze(self, stones, next_player, board_size=19, komi=6.5, max_visits=5000, max_time=None, move_history=None, include_ownership=False, ticket=None):
        """
        分析局面，返回 Top N 推荐走法。

        stones: [{"color": "B"/"W", "pos": "D4"}, ...] 棋盘上所有棋子
        next_player: "B" 或 "W"，轮到谁走
        board_size: 棋盘大小
        komi: 贴目
        max_visits: 最大搜索次数（控制 AI 强度）
        max_time: 最大思考时间（秒）
        move_history: [["B","Q16"],["W","D4"],...] 按实际下棋顺序排列的棋步。
                      如果提供，使用原始下棋顺序作为 moves 序列（最准确）。
                      如果不提供，使用 initialStones 直接设置棋盘状态（适用于截图分析）。
        include_ownership: True 时额外返回 ownership（每个交叉点的黑方归属概率 -1..1）
        """
        # 动态计算 max_time：visits 越多给越多时间，但不超过 60 秒
        if max_time is None:
            max_time = min(max_visits / 200.0 + 5, 60)

        with self.lock:
            if not self.process or self.process.poll() is not None:
                print("Analysis 引擎已退出，尝试自动重启...")
                self._restart()
                if not self.process or self.process.poll() is not None:
                    return {"error": "Analysis engine not running"}

            if ticket is not None and ticket != self._gen:
                # 排队期间有更新的分析请求到达，本请求已过期，直接跳过（不占用引擎算力）
                return {"status": "superseded"}

            if move_history:
                # 有完整的下棋历史：直接用原始序列，KataGo 能正确理解局面上下文
                moves = [list(m) for m in move_history]
                # 确保下一手颜色正确：如果自然轮到的颜色与 next_player 不同，插入 pass
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
                    "includeOwnership": bool(include_ownership),
                    "overrideSettings": {
                        "maxTime": max_time,
                        "ignorePreRootHistory": True
                    }
                }
            else:
                # 无下棋历史（截图分析场景）：用 initialStones 直接设置棋盘状态
                # 避免虚构 B,W 交替的 moves 序列，那会在有提子时产生错误分析
                initial_stones = [[s["color"], s["pos"]] for s in stones if s.get("pos")]

                query = {
                    "id": f"analyze_{int(time.time()*1000)}",
                    "initialStones": initial_stones,
                    "moves": [],
                    "initialPlayer": next_player,
                    "rules": "chinese",
                    "komi": komi,
                    "boardXSize": board_size,
                    "boardYSize": board_size,
                    "maxVisits": max_visits,
                    "includeOwnership": bool(include_ownership),
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

                out = {
                    "status": "ok",
                    "recommendations": results,
                    "turnNumber": resp.get("turnNumber")
                }
                # ownership: 长度 board_size^2 的数组，值域 [-1,1]，正=黑方归属。
                # KataGo 行主序：前 board_size 个点是第一行（黑视角顶部）。
                own = resp.get("ownership")
                if include_ownership and isinstance(own, list) and len(own) == board_size * board_size:
                    out["ownership"] = [round(v, 3) for v in own]
                return out
            except Exception as e:
                return {"error": str(e)}

    def stop(self):
        if self.process:
            try:
                self.process.terminate()
            except:
                pass
            self.process = None

    def analyze_full_game(self, move_history, board_size=19, komi=6.5, max_visits=100, max_time_per_turn=None):
        """
        整局批量分析：用 analyzeTurns 一次请求分析每一手过后的局面，
        返回逐手胜率/目差曲线（黑棋视角），供前端绘制胜率曲线图。

        move_history: [["B","Q16"],["W","D4"],...] 完整着法序列（含 pass）
        max_visits: 每手访问数（整局分析建议 100-200，平衡速度与精度）
        max_time_per_turn: 每手最大分析时间（秒），默认 3
        返回: {"status":"ok", "points":[{"turn":1,"winrate":52.3,"scoreLead":1.2},...], "turn_count":n}
        """
        if not move_history:
            return {"error": "没有着法可分析"}
        if max_time_per_turn is None:
            max_time_per_turn = 3.0

        with self.lock:
            if not self.process or self.process.poll() is not None:
                print("Analysis 引擎已退出，尝试自动重启...")
                self._restart()
                if not self.process or self.process.poll() is not None:
                    return {"error": "Analysis engine not running"}

            moves = [list(m) for m in move_history]
            if len(moves) > 600:
                return {"error": "着法数超过 600，暂不支持整局分析"}
            turns = list(range(1, len(moves) + 1))

            query = {
                "id": f"gameanalyze_{int(time.time()*1000)}",
                "moves": moves,
                "rules": "chinese",
                "komi": komi,
                "boardXSize": board_size,
                "boardYSize": board_size,
                "analyzeTurns": turns,
                "maxVisits": max_visits,
                "includeOwnership": False,
                "overrideSettings": {
                    "maxTime": max_time_per_turn,
                    "ignorePreRootHistory": True
                }
            }

            try:
                self.process.stdin.write(json.dumps(query) + "\n")
                self.process.stdin.flush()

                points = {}
                final_turn = turns[-1]
                # KataGo 对 analyzeTurns 中每个 turn 输出一行 JSON，按序到达，
                # 最后一行 turnNumber == 最大 turn。安全上限：多读几行防重复。
                deadline = time.time() + max_time_per_turn * len(turns) + 30
                while len(points) < len(turns) and time.time() < deadline:
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    try:
                        resp = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in resp:
                        return {"error": resp["error"]}
                    tn = resp.get("turnNumber")
                    ri = resp.get("rootInfo") or {}
                    if tn is not None and ("winrate" in ri):
                        points[tn] = {
                            "turn": tn,
                            "winrate": round(ri["winrate"] * 100, 1),
                            "scoreLead": round(ri.get("scoreLead", 0), 1),
                        }
                    if tn == final_turn and tn in points:
                        break

                if not points:
                    return {"error": "引擎未返回任何分析结果"}
                ordered = [points[t] for t in sorted(points.keys())]
                # turn t = 下完第 t 手后的局面
                return {"status": "ok", "points": ordered, "turn_count": len(ordered)}
            except Exception as e:
                return {"error": str(e)}

    def analyze_group_life(self, stones, next_player, target, board_size=19, komi=6.5, max_visits=1000, max_time=None, move_history=None):
        """
        死活提示：评估用户点击的棋子所在群体（连通分量）的存活状态。

        原理：
        1. 本地棋盘重建（stones 或 move_history 重演）→ 洪水填充找同色连通群体
        2. KataGo 分析当前局面（includeOwnership）→ 取群体所在交叉点 ownership 均值
           （正=黑方归属，负=白方归属）→ 归属概率与目标颜色同号为活、异号为死
        3. 补救要点：moveInfos 推荐着法中，找到距离群体最近的着法（曼哈顿距离≤2）作为急救点；
           并提供群体的气数（自由交叉点数）辅助判断

        返回 {
            "status":"ok", "target": 点击点, "color": 群体颜色,
            "group": [群内所有坐标], "liberties": 气数,
            "life_score": 归属均值（黑视角 -1..1），
            "survival": 活/危/死 判定（对目标颜色归一化到 0..1）,
            "verdict": alive/critical/dead,
            "vital_points": [急救着法候选（含说明）],
            "recommended": 引擎全局最佳着法
        }
        """
        if not target or not isinstance(target, str):
            return {"error": "未提供目标棋子坐标"}
        target = target.strip().upper()
        if not _coords.GTP_COORD_RE.match(target):
            return {"error": "目标坐标不合法: %s" % target}
        if next_player not in ("B", "W"):
            next_player = "B"
        if max_time is None:
            max_time = min(max_visits / 200.0 + 5, 60)

        # GTP 坐标 -> (row, col)（I 跳过）
        def gtp_to_rc(pos):
            return _coords.gtp_to_rc(pos, board_size)

        # 1. 重建棋盘
        board = [[0] * board_size for _ in range(board_size)]
        if move_history and isinstance(move_history, list):
            captured = set()
            for mv in move_history:
                color, pos = mv[0], mv[1]
                if pos == "pass":
                    continue
                rc = gtp_to_rc(str(pos).upper())
                if rc is None:
                    continue
                board[rc[0]][rc[1]] = 1 if color == "B" else 2
                # 提子：去除无气的异色邻接群
                for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
                    nr, nc = rc[0]+dr, rc[1]+dc
                    if 0 <= nr < board_size and 0 <= nc < board_size and board[nr][nc] not in (0, board[rc[0]][rc[1]]):
                        opp, seen = board[nr][nc], set()
                        stack, grp, libs = [(nr, nc)], [], set()
                        while stack:
                            r, c = stack.pop()
                            if (r, c) in seen:
                                continue
                            seen.add((r, c))
                            if board[r][c] == opp:
                                grp.append((r, c))
                                for dr2, dc2 in ((1,0),(-1,0),(0,1),(0,-1)):
                                    rr, cc = r+dr2, c+dc2
                                    if 0 <= rr < board_size and 0 <= cc < board_size:
                                        if board[rr][cc] == 0:
                                            libs.add((rr, cc))
                                        elif board[rr][cc] == opp and (rr, cc) not in seen:
                                            stack.append((rr, cc))
                        if not libs:
                            for r, c in grp:
                                board[r][c] = 0
        else:
            for s in (stones or []):
                if not isinstance(s, dict):
                    continue
                rc = gtp_to_rc(str(s.get("pos", "")).upper())
                if rc and s.get("color") in ("B", "W"):
                    board[rc[0]][rc[1]] = 1 if s["color"] == "B" else 2

        tgt_rc = gtp_to_rc(target)
        if tgt_rc is None:
            return {"error": "目标坐标越界: %s" % target}
        tgt_color = board[tgt_rc[0]][tgt_rc[1]]
        if tgt_color == 0:
            return {"error": "目标位置无棋子: %s" % target}

        # 2. 洪水填充找连通群体 + 气
        seen = set()
        stack = [tgt_rc]
        group = []
        liberties = set()
        opp = 3 - tgt_color
        while stack:
            r, c = stack.pop()
            if (r, c) in seen:
                continue
            seen.add((r, c))
            if board[r][c] == tgt_color:
                group.append((r, c))
                for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
                    rr, cc = r+dr, c+dc
                    if 0 <= rr < board_size and 0 <= cc < board_size:
                        if board[rr][cc] == 0:
                            liberties.add((rr, cc))
                        elif board[rr][cc] == tgt_color and (rr, cc) not in seen:
                            stack.append((rr, cc))
        group_set = set(group)

        # 3. KataGo 分析（带 ownership）
        with self.lock:
            if not self.process or self.process.poll() is not None:
                self._restart()
                if not self.process or self.process.poll() is not None:
                    return {"error": "Analysis engine not running"}

            if move_history and isinstance(move_history, list):
                moves = [list(m) for m in move_history]
                natural_next = "B" if len(moves) % 2 == 0 else "W"
                if natural_next != next_player:
                    moves.append([natural_next, "pass"])
                query = {
                    "id": f"life_{int(time.time()*1000)}",
                    "moves": moves,
                    "rules": "chinese", "komi": komi,
                    "boardXSize": board_size, "boardYSize": board_size,
                    "analyzeTurns": [len(moves)],
                    "maxVisits": max_visits,
                    "includeOwnership": True,
                    "overrideSettings": {"maxTime": max_time, "ignorePreRootHistory": True}
                }
            else:
                initial_stones = [[s["color"], s["pos"]] for s in (stones or []) if isinstance(s, dict) and s.get("pos")]
                query = {
                    "id": f"life_{int(time.time()*1000)}",
                    "initialStones": initial_stones,
                    "moves": [],
                    "initialPlayer": next_player,
                    "rules": "chinese", "komi": komi,
                    "boardXSize": board_size, "boardYSize": board_size,
                    "analyzeTurns": [0],
                    "maxVisits": max_visits,
                    "includeOwnership": True,
                    "overrideSettings": {"maxTime": max_time, "ignorePreRootHistory": True}
                }

            try:
                self.process.stdin.write(json.dumps(query) + "\n")
                self.process.stdin.flush()
                # 读循环：跳过 warning 记录（含 field/warning 字段），拿到真实响应（含 turnNumber）
                resp = None
                deadline = time.time() + max_time * 2 + 30
                while time.time() < deadline:
                    line = self.process.stdout.readline()
                    if not line:
                        return {"error": "No response from engine"}
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in r:
                        return {"error": r["error"]}
                    if "field" in r and "warning" in r and "turnNumber" not in r:
                        continue  # 字段警告记录，跳过
                    resp = r
                    break
                if resp is None:
                    return {"error": "No response from engine"}
            except Exception as e:
                return {"error": str(e)}

        own = resp.get("ownership")
        if not isinstance(own, list) or len(own) != board_size * board_size:
            return {"error": "引擎未返回形势归属数据"}

        # 4. 群体归属均值（黑视角）
        life_sum = sum(own[r * board_size + c] for (r, c) in group)
        life_score = life_sum / len(group)   # 黑视角，正=黑地
        # 目标颜色视角：黑→原值，白→取反
        survival = life_score if tgt_color == 1 else -life_score
        if survival >= 0.6:
            verdict = "alive"
        elif survival >= -0.6:
            verdict = "critical"
        else:
            verdict = "dead"

        # 5. 急救要点：推荐着法中距离群体≤2的点，按与群体的空间关系排序
        move_infos = resp.get("moveInfos", [])
        ordered = sorted(move_infos, key=lambda m: m.get("order", 999))
        vital, seen_v = [], set()
        for mi in ordered:
            mv = mi.get("move", "")
            if not mv or mv.upper() in ("PASS", "RESIGN") or mv.upper() in seen_v:
                continue
            mrc = gtp_to_rc(mv.upper())
            if mrc is None:
                continue
            # 与群体的最近距离
            dist = min(abs(mrc[0]-gr) + abs(mrc[1]-gc) for (gr, gc) in group)
            if dist <= 2:
                seen_v.add(mv.upper())
                vital.append({
                    "move": mv.upper(),
                    "dist": dist,
                    # reportAnalysisWinratesAs=BLACK：winrate 恒为黑视角，行棋方为白时取 1-wr（与 analyze() 一致）
                    "winrate": round((mi["winrate"] if next_player == "B" else 1.0 - mi["winrate"]) * 100, 1) if "winrate" in mi else None,
                    "pv": mi.get("pv", [])[:6],
                })
            if len(vital) >= 3:
                break
        # 气尽提子风险提示
        n_libs = len(liberties)

        def rc_to_gtp(rc):
            return _coords.rc_to_gtp(rc, board_size)

        return {
            "status": "ok",
            "target": target,
            "color": "B" if tgt_color == 1 else "W",
            "group": [rc_to_gtp(g) for g in sorted(group)],
            "group_size": len(group),
            "liberties": n_libs,
            "liberty_points": [rc_to_gtp(l) for l in sorted(liberties)],
            "life_score": round(life_score, 3),
            "survival": round(survival, 3),
            "verdict": verdict,
            "vital_points": vital,
            "recommended": ordered[0].get("move", "").upper() if ordered else None,
        }


# ==============================================================
# LLM 自然语言讲解（功能11）：双模式 —— 配置了 OpenAI 兼容 API 时调用真 LLM，
# 否则用规则式讲解生成器（基于 KataGo 分析数据组装围棋解说，开箱即用）
# 环境变量：GOAI_LLM_BASE_URL / GOAI_LLM_API_KEY / GOAI_LLM_MODEL
# ==============================================================

def _llm_config():
    base = os.environ.get("GOAI_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("GOAI_LLM_API_KEY", "")
    model = os.environ.get("GOAI_LLM_MODEL", "")
    if base and key and model:
        return {"base": base, "key": key, "model": model}
    return None

def _call_llm(prompt, system, timeout=60):
    """调用 OpenAI 兼容 chat completions 接口，返回文本或抛异常"""
    cfg = _llm_config()
    if not cfg:
        raise RuntimeError("LLM 未配置")
    body = json.dumps({
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 800,
    }).encode()
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        body,
        {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["key"]},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"]

def _pos_region(row, col, size):
    """坐标 → 区域名（如 右上角/上边/中腹）"""
    v = "上" if row < size / 3 else ("下" if row >= size * 2 / 3 else "中")
    h = "右" if col >= size * 2 / 3 else ("左" if col < size / 3 else "中")
    if v == "中" and h == "中":
        return "中腹"
    if v == "中":
        return h + "边"
    if h == "中":
        return v + "边"
    return h + v + "角"

def _coord_cn(pos):
    """GTP 坐标转中文口语（Q16 → Q16（右上））"""
    return pos

def generate_position_explanation(recs, next_player, board_size, komi, ownership=None, move_count=0, elapsed=0.0, llm_prompt=None):
    """
    规则式讲解生成器（无 LLM 时的回退）：基于 KataGo 分析数据组装围棋解说。
    返回讲解文本；若 llm_prompt 提供且 LLM 可用则由调用方先尝试 LLM。
    """
    if not recs:
        return "当前局面没有可用的分析数据。"
    side = "黑棋" if next_player == "B" else "白棋"
    opp = "白棋" if next_player == "B" else "黑棋"
    best = recs[0]
    parts = []

    # 1. 阶段与形势总述
    if move_count <= 12:
        phase = "开局阶段"
    elif move_count <= 100:
        phase = "中盘阶段"
    elif move_count <= 200:
        phase = "中后盘"
    else:
        phase = "官子阶段"

    lead = best.get("scoreLead", 0)  # 已是 next_player 视角（analyze 返回行棋方视角）
    if lead >= 10:
        sit = f"目前{side}形势大优，目差领先约 {lead:.0f} 目"
    elif lead >= 3:
        sit = f"目前{side}稍占上风，目差领先约 {lead:.1f} 目"
    elif lead <= -10:
        sit = f"目前{side}形势落后，目差约 {lead:.0f} 目，需要奋力追赶"
    elif lead <= -3:
        sit = f"目前{side}略处下风，目差约 {lead:.1f} 目"
    else:
        sit = "目前局势相当细微，胜负敏感"
    wr = best.get("winrate", 50)
    parts.append(f"【形势总览】现在处于{phase}，轮{side}落子。{sit}，AI 估算{side}胜率约 {wr}%。")

    # 2. 最佳推荐讲解
    m = best.get("move", "")
    pv = best.get("pv", [])
    parts.append(f"【推荐一手】AI 首选 {m}，落子后{side}胜率可达 {wr}%、领先 {lead:+.1f} 目。"
                 + (f"预计后续双方大致会走：{' → '.join(pv[:6])}。" if pv else ""))

    # 3. 备选对比
    if len(recs) >= 2:
        second = recs[1]
        swr = second.get("winrate", 0)
        gap = wr - swr
        if gap < 2:
            alt = f"备选 {second.get('move','')}（胜率 {swr}%）与首选几乎等价，两者都可行"
        elif gap < 8:
            alt = f"备选 {second.get('move','')}（胜率 {swr}%）也不错，与首选差距不大，可按棋风选择"
        else:
            alt = f"备选 {second.get('move','')}（胜率 {swr}%）明显不如首选，建议优先考虑 {m}"
        parts.append(f"【其他选择】{alt}。")

    # 4. 形势图区域解读（ownership 可用时）
    if isinstance(ownership, list) and len(ownership) == board_size * board_size:
        # 9 区域均分（3x3）
        regions = {}
        names = {}
        for r in range(board_size):
            for c in range(board_size):
                vi = "上" if r < board_size/3 else ("下" if r >= board_size*2/3 else "中")
                hi = "右" if c >= board_size*2/3 else ("左" if c < board_size/3 else "中")
                key = (vi, hi)
                regions.setdefault(key, []).append(ownership[r*board_size+c])
        label = {("上","左"): "左上角", ("上","右"): "右上角", ("下","左"): "左下角", ("下","右"): "右下角",
                 ("上","中"): "上边", ("中","左"): "左边", ("中","右"): "右边", ("下","中"): "下边", ("中","中"): "中腹"}
        strong_b, strong_w = [], []  # 注意不能共享列表
        for key, vals in regions.items():
            avg = sum(vals) / len(vals)
            names[label.get(key, str(key))] = avg
            if avg > 0.5:
                strong_b.append((label.get(key, str(key)), avg))
            elif avg < -0.5:
                strong_w.append((label.get(key, str(key)), -avg))
        region_txt = ""
        if strong_b and not strong_w:
            region_txt = "从形势图看，黑方大范围掌控局面：" + "、".join(k for k, _ in sorted(strong_b, key=lambda x: -x[1])) + "均为黑方势力范围。"
        elif strong_w and not strong_b:
            region_txt = "从形势图看，白方大范围掌控局面：" + "、".join(k for k, _ in sorted(strong_w, key=lambda x: -x[1])) + "均为白方势力范围。"
        elif strong_b or strong_w:
            region_txt = ("从形势图看双方分区对峙：黑方主导" + "、".join(k for k, _ in strong_b) + "；白方主导" + "、".join(k for k, _ in strong_w) + "。") if (strong_b and strong_w) else ""
        else:
            region_txt = "从形势图看，各方势力犬牙交错，局面尚不分明，行棋价值很大。"
        parts.append("【区域分析】" + region_txt)

    # 5. 总结建议
    if lead >= 3:
        parts.append("【教练建议】保持简明，优势棋不宜过分用强，稳步推进即可扩大战果。")
    elif lead <= -3:
        parts.append("【教练建议】落后时需主动挑起战斗、寻找翻盘机会，平稳收官难以追回目差。")
    else:
        parts.append("【教练建议】细微局面下，先手价值与厚薄的判断是关键，避免随手棋。")

    if elapsed:
        parts.append(f"（分析耗时 {elapsed:.1f} 秒）")
    return "\n\n".join(parts)

def build_llm_prompt(recs, next_player, board_size, komi, ownership, move_count, stones_desc, move_history):
    """把局面组装成给 LLM 的提示词"""
    mh = " ".join(f"{'黑' if c=='B' else '白'}{p}" for c, p in (move_history or [])[:60]) or "无"
    rec_lines = "\n".join(
        f"  {i+1}. {r.get('move')} 胜率{r.get('winrate')}% 目差{r.get('scoreLead',0):+.1f} 后续:{'->'.join(r.get('pv',[])[:6])}"
        for i, r in enumerate(recs[:5]))
    own_desc = "有" if isinstance(ownership, list) else "无"
    return f"你是围棋教练。当前对局信息：\n棋盘 {board_size} 路，贴目 {komi}，已下 {move_count} 手。\n着法序列：{mh}\n棋子分布：{stones_desc}\n形势数据（KataGo 分析）：\n{rec_lines}\nownership 数据：{own_desc}\n轮{'黑' if next_player=='B' else '白'}方行棋。请用中文以围棋教练口吻讲解当前局面：形势判断、首选着法的意图、备选方案对比、下一步建议。控制在 250 字内，不用 Markdown 标记。"


# ==============================================================
# 模块级引擎实例与 API 实现
# 每个 api_* 函数接收已解析的请求 JSON，返回 (响应dict, HTTP状态码)
# ==============================================================

analysis_engine = None


def start_engine(katago_path, model_path, config_path):
    """启动 Analysis 引擎（局面分析用），返回引擎实例"""
    global analysis_engine
    analysis_engine = KataGoAnalysisEngine(katago_path, model_path, config_path)
    analysis_engine.start()
    return analysis_engine


def stop_engine():
    if analysis_engine:
        analysis_engine.stop()


def analysis_ready():
    return (analysis_engine is not None and analysis_engine.ready
            and analysis_engine.process is not None and analysis_engine.process.poll() is None)


def _engine_unavailable():
    return {"status": "error", "message": "分析引擎未启动"}, 500


def _select_query_path(data):
    """
    路径选择（下沉自前端）：
    - 基础棋盘无顺序摆子（setup_stones 为空）且有完整着法历史 → move_history 路径（保留 ko/提子上下文，更准确）
    - 有摆子（截图识别 / SGF AB/AW）或无历史 → initialStones 路径
    返回 (stones, mh)：mh 为 None 表示走 initialStones 路径。
    """
    stones = data.get("stones", [])
    if not isinstance(stones, list):
        stones = []
    setup_stones = data.get("setup_stones") or []
    if not isinstance(setup_stones, list):
        setup_stones = []
    move_history = data.get("move_history")
    mh = move_history if (isinstance(move_history, list) and move_history and not setup_stones) else None
    return stones, mh


def api_analyze(data):
    """处理局面分析请求"""
    if not analysis_ready():
        return {"status": "error", "message": "分析引擎未就绪"}, 500

    stones, mh = _select_query_path(data)
    next_player = data.get("next_player", "B")
    board_size = data.get("board_size", 19)
    komi = data.get("komi", 6.5)
    max_visits = data.get("max_visits", 5000)
    include_ownership = bool(data.get("include_ownership", False))

    if not stones and not mh:
        return {"status": "error", "message": "棋盘为空"}, 400

    # 领取序号：排队期间若来了更新的分析请求，本请求会被引擎跳过（过期淘汰）
    ticket = analysis_engine.next_ticket()
    result = analysis_engine.analyze(
        stones=stones,
        next_player=next_player,
        board_size=board_size,
        komi=komi,
        max_visits=max_visits,
        move_history=mh,
        include_ownership=include_ownership,
        ticket=ticket,
    )

    return result, 200


def api_recognize(data):
    """处理棋盘截图识别请求"""
    if not HAS_RECOGNITION:
        return {"status": "error", "message": "图像识别模块不可用，请安装 opencv-python-headless 和 numpy"}, 500

    image_data = data.get("image")
    board_size = data.get("board_size", 19)

    if not image_data:
        return {"status": "error", "message": "未提供图片数据"}, 400

    result = recognize_board(image_data, board_size)
    return result, 200


def api_analyze_game(data):
    """整局批量分析：返回逐手胜率/目差曲线数据"""
    if not analysis_ready():
        return _engine_unavailable()

    move_history = data.get("move_history", [])
    board_size = data.get("board_size", 19)
    komi = data.get("komi", 6.5)
    max_visits = data.get("max_visits", 100)

    if not isinstance(move_history, list) or not move_history:
        return {"status": "error", "message": "没有着法历史，无法做整局分析（请先导入棋谱或下一局棋）"}, 400
    if not isinstance(board_size, int) or not (2 <= board_size <= 19):
        board_size = 19
    try:
        komi = float(komi)
        max_visits = int(max_visits)
    except (TypeError, ValueError):
        komi, max_visits = 6.5, 100
    if not (10 <= max_visits <= 2000):
        max_visits = 100

    print(f"[analyze_game] 开始整局分析: {len(move_history)} 手, visits={max_visits}")
    t0 = time.time()
    result = analysis_engine.analyze_full_game(
        move_history, board_size=board_size, komi=komi, max_visits=max_visits
    )
    dt = time.time() - t0
    print(f"[analyze_game] 完成: {dt:.1f} 秒")
    if "error" in result:
        return {"status": "error", "message": result["error"]}, 500
    result["elapsed"] = round(dt, 2)
    return result, 200


def api_explain(data):
    """LLM/规则式自然语言讲解：分析当前局面后生成中文解说"""
    if not analysis_ready():
        return _engine_unavailable()

    next_player = data.get("next_player", "B")
    stones, mh = _select_query_path(data)
    board_size = data.get("board_size", 19)
    komi = data.get("komi", 6.5)
    max_visits = data.get("max_visits", 400)

    if not isinstance(board_size, int) or not (2 <= board_size <= 19):
        board_size = 19
    try:
        komi = float(komi)
        max_visits = int(max_visits)
    except (TypeError, ValueError):
        komi, max_visits = 6.5, 400
    if not (10 <= max_visits <= 5000):
        max_visits = 400
    if not mh and not stones:
        return {"status": "error", "message": "棋盘为空，请先摆子或导入棋谱"}, 400

    print(f"[explain] 讲解生成: {'B' if next_player=='B' else 'W'} 行棋, visits={max_visits}")
    t0 = time.time()
    # 1. 分析（带 ownership）
    ana = analysis_engine.analyze(
        stones=stones, next_player=next_player, board_size=board_size,
        komi=komi, max_visits=max_visits, move_history=mh,
        include_ownership=True,
    )
    if ana.get("status") != "ok" or not ana.get("recommendations"):
        return {"status": "error", "message": ana.get("error") or ana.get("message") or "分析失败"}, 500
    recs = ana["recommendations"]
    own = ana.get("ownership")
    move_count = len(mh) if mh else len(stones)
    elapsed = time.time() - t0

    # 2. 组装棋子分布描述（用于 LLM prompt）
    stones_desc = "无摆子" if not stones else f"{len(stones)} 子（黑{sum(1 for s in stones if s.get('color')=='B')}白{sum(1 for s in stones if s.get('color')=='W')}）"

    # 3. 双模式讲解：LLM 优先，失败/未配置回退规则式
    mode = "template"
    text = None
    if _llm_config():
        try:
            prompt = build_llm_prompt(recs, next_player, board_size, komi, own, move_count, stones_desc, mh)
            text = _call_llm(prompt, "你是一位专业的围棋教练，善于用通俗生动的中文讲解围棋局面。")
            mode = "llm"
        except Exception as e:
            print(f"[explain] LLM 调用失败，回退规则式: {e}")
            text = None
    if text is None:
        text = generate_position_explanation(
            recs, next_player, board_size, komi, ownership=own,
            move_count=move_count, elapsed=elapsed,
        )

    print(f"[explain] 完成: mode={mode}, {time.time()-t0:.1f}s")
    return {
        "status": "ok",
        "mode": mode,
        "explanation": text,
        "winrate": recs[0].get("winrate"),
        "score_lead": recs[0].get("scoreLead"),
        "best_move": recs[0].get("move"),
        "elapsed": round(time.time() - t0, 1),
    }, 200


def api_group_life(data):
    """死活提示：评估点击棋子所在群体的存活状态与急救点"""
    if not analysis_ready():
        return _engine_unavailable()

    target = data.get("target", "")
    next_player = data.get("next_player", "B")
    stones, mh = _select_query_path(data)
    board_size = data.get("board_size", 19)
    komi = data.get("komi", 6.5)
    max_visits = data.get("max_visits", 1000)

    if not isinstance(board_size, int) or not (2 <= board_size <= 19):
        board_size = 19
    try:
        komi = float(komi)
        max_visits = int(max_visits)
    except (TypeError, ValueError):
        komi, max_visits = 6.5, 1000
    if not (10 <= max_visits <= 5000):
        max_visits = 1000
    if not mh and not stones:
        return {"status": "error", "message": "棋盘为空，请先摆子或导入棋谱"}, 400

    print(f"[group_life] 死活评估: {next_player} 点击 {target}")
    t0 = time.time()
    result = analysis_engine.analyze_group_life(
        stones, next_player, target,
        board_size=board_size, komi=komi, max_visits=max_visits,
        move_history=mh,
    )
    dt = time.time() - t0
    print(f"[group_life] 完成: {dt:.1f} 秒")
    if "error" in result:
        # 用户输入类错误（坐标/无子）返 400，引擎类错误返 500
        code = 400 if any(k in result["error"] for k in ("目标", "不合法", "越界", "无棋子")) else 500
        return {"status": "error", "message": result["error"]}, code
    result["elapsed"] = round(dt, 2)
    return result, 200


def api_import_sgf(data):
    """处理 SGF 棋谱导入请求"""
    sgf_text = data.get("sgf", "")
    if not sgf_text or not sgf_text.strip():
        return {"status": "error", "message": "未提供 SGF 内容"}, 400
    result = parse_sgf(sgf_text)
    return result, 200
