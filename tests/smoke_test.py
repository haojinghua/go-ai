#!/usr/bin/env python3
"""
围棋 AI 程序冒烟测试（API 级回归）
====================================

用法:
    python3 tests/smoke_test.py              # 默认 http://localhost:8080
    python3 tests/smoke_test.py --base http://localhost:9090

覆盖范围（见 tests/TEST_PLAN.md 第 1.3 节）:
    1. /api/status        双引擎就绪
    2. /api/new_game      9 路新对局
    3. /api/play          黑下 D4 后 AI 应答（校验 9 路 GTP 坐标合法性）
    4. /api/undo          撤两手后引擎仍可用（/api/ai_move 能应答）
    5. /api/analyze       9 路两手指定局面 maxVisits=100（超时 30s）
    6. /api/recognize     grid_debug.png（19 路）
    7. 首页 HTML 可达

约束: 纯标准库（urllib/json/base64）；每步 PASS/FAIL；失败时退出码非 0。
注意: 不测试 /api/screenshot（需要 GUI 交互式截屏，人工测试）。
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
GRID_DEBUG_PNG = os.path.join(PROJECT_ROOT, "grid_debug.png")

# 9 路棋盘合法 GTP 坐标
COLS_9 = "ABCDEFGHIJ"
GTP_9 = {c + str(n) for c in COLS_9 for n in range(1, 10)}

results = []  # [(name, ok, detail)]


def http_get(base, path, timeout=10):
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def http_post_json(base, path, payload, timeout=10):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    tag = "PASS" if ok else "FAIL"
    print("[{}] {}{}".format(tag, name, (" -- " + detail) if detail else ""))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------

def test_status(base):
    status, data = http_get(base, "/api/status")
    obj = json.loads(data.decode("utf-8"))
    check(status == 200, "HTTP %s" % status)
    check(obj.get("engine_running") is True, "engine_running=%r" % obj.get("engine_running"))
    check(obj.get("analysis_ready") is True, "analysis_ready=%r" % obj.get("analysis_ready"))
    record("api/status 双引擎就绪", True, "engine+analysis ready")


def test_index(base):
    status, data = http_get(base, "/", timeout=10)
    check(status == 200, "HTTP %s" % status)
    check(b"board-canvas" in data, "首页 HTML 缺少 board-canvas")
    record("首页 HTML 可达", True, "含 board-canvas")


def test_new_game(base):
    _, obj = http_post_json(base, "/api/new_game",
                            {"board_size": 9, "komi": 6.5, "max_visits": 100})
    check(obj.get("status") == "ok", "status=%r" % obj.get("status"))
    check(obj.get("board_size") == 9, "board_size=%r" % obj.get("board_size"))
    check(obj.get("komi") == 6.5, "komi=%r" % obj.get("komi"))
    record("api/new_game 9路", True, "回显 board_size=9 komi=6.5")


def test_play_and_ai_reply(base):
    last_err = None
    obj = None
    # 引擎锁可能被用户正在进行的对弈占用，最长等 60s，失败重试一次
    for attempt in range(2):
        try:
            _, obj = http_post_json(base, "/api/play", {"color": "B", "move": "D4"}, timeout=60)
            last_err = None
            break
        except Exception as e:
            last_err = e
            time.sleep(2)
    if last_err is not None:
        raise last_err
    check(obj.get("status") == "ok", "status=%r msg=%r" % (obj.get("status"), obj.get("message")))
    ai_move = obj.get("ai_move")
    check(bool(ai_move), "ai_move 为空")
    check(obj.get("ai_color") == "W", "ai_color=%r" % obj.get("ai_color"))
    if ai_move.lower() not in ("pass", "resign"):
        check(ai_move in GTP_9, "AI 落子 %r 超出 9 路棋盘范围" % ai_move)
    record("api/play D4 + AI 应答", True, "ai_move=%s" % ai_move)


def test_undo_then_ai_move(base):
    _, obj = http_post_json(base, "/api/undo", {})
    check(obj.get("status") == "ok", "undo status=%r" % obj.get("status"))
    _, obj2 = http_post_json(base, "/api/ai_move", {"color": "B"}, timeout=60)
    check(obj2.get("status") == "ok", "ai_move status=%r" % obj2.get("status"))
    mv = obj2.get("ai_move")
    check(bool(mv), "ai_move 为空")
    if mv.lower() not in ("pass", "resign"):
        check(mv in GTP_9, "AI 落子 %r 超出 9 路范围" % mv)
    record("api/undo + 引擎仍可用", True, "undo ok, ai_move=%s" % mv)


def test_analyze(base):
    payload = {
        "stones": [{"color": "B", "pos": "D4"}, {"color": "W", "pos": "Q16"}],
        # 9 路棋盘上没有 Q16，改用 19 路来测（保证局面合法）
    }
    # 注意：上面 stones 用了 Q16，因此实际用 19 路棋盘做本用例
    payload["board_size"] = 19
    payload["next_player"] = "B"
    payload["komi"] = 6.5
    payload["max_visits"] = 100
    payload["move_history"] = [["B", "D4"], ["W", "Q16"]]
    t0 = time.time()
    _, obj = http_post_json(base, "/api/analyze", payload, timeout=30)
    dt = time.time() - t0
    check(obj.get("status") == "ok", "status=%r error=%r" % (obj.get("status"), obj.get("error")))
    recs = obj.get("recommendations")
    check(isinstance(recs, list) and len(recs) >= 1, "recommendations 为空")
    for r in recs:
        check("move" in r and "winrate" in r and "scoreLead" in r and "visits" in r,
              "推荐字段缺失: %r" % r)
        check(0 <= r["winrate"] <= 100, "winrate 越界: %r" % r["winrate"])
    wrs = [r["winrate"] for r in recs]
    check(wrs == sorted(wrs, reverse=True), "recommendations 未按胜率降序: %r" % wrs)
    record("api/analyze maxVisits=100", True,
           "%d 条推荐, 最佳 %s %.1f%%, 耗时 %.1fs" % (len(recs), recs[0]["move"], recs[0]["winrate"], dt))


def test_recognize(base):
    check(os.path.isfile(GRID_DEBUG_PNG), "缺少测试图 %s" % GRID_DEBUG_PNG)
    with open(GRID_DEBUG_PNG, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    _, obj = http_post_json(base, "/api/recognize",
                            {"image": b64, "board_size": 19}, timeout=60)
    check(obj.get("status") == "ok", "status=%r error=%r" % (obj.get("status"), obj.get("error")))
    stones = obj.get("stones", [])
    sc = obj.get("stone_count", {})
    nb = sum(1 for s in stones if s["color"] == "B")
    nw = sum(1 for s in stones if s["color"] == "W")
    check(nb == sc.get("black"), "黑子计数不一致: stones=%d stone_count=%r" % (nb, sc))
    check(nw == sc.get("white"), "白子计数不一致: stones=%d stone_count=%r" % (nw, sc))
    check(nb + nw > 0, "识别到 0 颗棋子（grid_debug.png 应有棋子）")
    board = obj.get("board", [])
    check(len(board) == 19 and all(len(r) == 19 for r in board), "board 矩阵非 19x19")
    for s in stones:
        check(0 <= s["row"] < 19 and 0 <= s["col"] < 19, "越界坐标 %r" % s)
        check(board[s["row"]][s["col"]] == (1 if s["color"] == "B" else 2),
              "stones 与 board 不一致: %r" % s)
    record("api/recognize grid_debug.png", True,
           "黑 %d 白 %d" % (nb, nw))


# ---------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="围棋 AI 冒烟测试")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--skip-analyze", action="store_true",
                        help="跳过慢速的 /api/analyze 用例")
    parser.add_argument("--skip-recognize", action="store_true",
                        help="跳过 /api/recognize 用例")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print("=" * 62)
    print("围棋 AI 冒烟测试  ->  %s" % base)
    print("=" * 62)

    cases = [
        ("status", lambda: test_status(base)),
        ("index", lambda: test_index(base)),
        ("new_game", lambda: test_new_game(base)),
        ("play", lambda: test_play_and_ai_reply(base)),
        ("undo", lambda: test_undo_then_ai_move(base)),
    ]
    if not args.skip_analyze:
        cases.append(("analyze", lambda: test_analyze(base)))
    if not args.skip_recognize:
        cases.append(("recognize", lambda: test_recognize(base)))

    for name, fn in cases:
        try:
            fn()
        except Exception as e:
            record(name, False, "%s: %s" % (type(e).__name__, e))

    # 汇总
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print("-" * 62)
    print("汇总: %d/%d PASS, %d FAIL" % (passed, total, failed))
    for name, ok, detail in results:
        if not ok:
            print("  FAIL -> %s : %s" % (name, detail))
    print("-" * 62)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
