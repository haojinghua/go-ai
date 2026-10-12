#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F5 验收测试（API 部分）：全局胜率曲线 /api/analyze_game
纯标准库；服务需运行在 http://localhost:8080
按开发说明约束：max_visits=50、小对局（≤10 手）、单请求超时 120s。
"""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8080"
TIMEOUT = 120

passed = 0
failed = 0
fails = []
perf = []  # (name, moves, elapsed)


def post(path, obj, timeout=TIMEOUT):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def post_err(path, obj, timeout=30):
    """返回 (status_code, body_json_or_None)"""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS  %s" % name)
    else:
        failed += 1
        fails.append(name)
        print("FAIL  %s  %s" % (name, detail))


def analyze_game(move_history, board_size=19, komi=6.5, max_visits=50):
    return post("/api/analyze_game", {
        "move_history": move_history,
        "board_size": board_size,
        "komi": komi,
        "max_visits": max_visits,
    })


def validate_points(name, data, n_expected, lim=None):
    """通用数据点校验，返回 points"""
    ok = data.get("status") == "ok"
    check(name + " /status=ok", ok, str(data)[:200])
    if not ok:
        return None
    pts = data.get("points", [])
    check(name + " /点数=%d" % n_expected, len(pts) == n_expected,
          "实际 %d" % len(pts))
    turns = [p.get("turn") for p in pts]
    check(name + " /turn从1连续递增",
          turns == list(range(1, len(pts) + 1)), str(turns))
    wr_ok = all(isinstance(p.get("winrate"), (int, float)) and 0 <= p["winrate"] <= 100 for p in pts)
    check(name + " /winrate∈[0,100]", wr_ok, str([p.get("winrate") for p in pts]))
    # scoreLead 数值合理：19路早期 |s|<40，9路 |s|<25（一边倒对局单独放宽到 45）
    if lim is None:
        lim = 40 if name.find("19") >= 0 else 25
    sl = [p.get("scoreLead") for p in pts]
    sl_ok = all(isinstance(s, (int, float)) and abs(s) <= lim for s in sl)
    check(name + " /scoreLead合理(|s|≤%d)" % lim, sl_ok, str(sl))
    # elapsed 为服务端 round(dt,1)；重复相同局面时 KataGo eval cache 命中可到 0.0
    check(name + " /elapsed字段存在且≥0", isinstance(data.get("elapsed"), (int, float)) and data["elapsed"] >= 0,
          str(data.get("elapsed")))
    perf.append((name, len(pts), data.get("elapsed", 0)))
    return pts


# ============ 1. 正常 19 路 8 手 ============
print("--- 1. 正常 19 路 8 手 (visits=50) ---")
g19 = [["B", "Q16"], ["W", "D4"], ["B", "D16"], ["W", "Q4"],
       ["B", "C3"], ["W", "E5"], ["B", "R16"], ["W", "K10"]]
t0 = time.time()
d = analyze_game(g19, board_size=19, max_visits=50)
pts19 = validate_points("19路8手", d, 8)

# ============ 2. 含 pass 序列 9 路 ============
print("--- 2. 含 pass 序列 9 路 (visits=50) ---")
gp = [["B", "E5"], ["W", "pass"], ["B", "C3"]]
d = analyze_game(gp, board_size=9, max_visits=50)
validate_points("9路pass3手", d, 3)

# ============ 3. 9 路普通 6 手（供一致性测试） ============
print("--- 3. 9 路 6 手 (visits=50) ---")
g9 = [["B", "E5"], ["W", "C3"], ["B", "D5"], ["W", "C5"],
      ["B", "F5"], ["W", "D3"]]
d1 = analyze_game(g9, board_size=9, max_visits=50)
pts9a = validate_points("9路6手#1", d1, 6)

print("--- 4. 相同局面两次调用一致性 (visits=50 有随机性, 差异<10%) ---")
d2 = analyze_game(g9, board_size=9, max_visits=50)
pts9b = validate_points("9路6手#2", d2, 6)
if pts9a and pts9b:
    diffs = [abs(a["winrate"] - b["winrate"]) for a, b in zip(pts9a, pts9b)]
    check("一致性/逐手winrate差异<10%", max(diffs) < 10, str(diffs))

# ============ 5. 黑棋视角验证（一边倒对局） ============
# 9路：黑连下3子占中腹，白全 pass。黑棋视角下终局 winrate 应显著>50 且 scoreLead>0
print("--- 5. 黑棋视角验证：黑3子 vs 白全pass (9路, visits=50) ---")
gb = [["B", "E5"], ["W", "pass"], ["B", "D5"], ["W", "pass"], ["B", "F5"], ["W", "pass"]]
db = analyze_game(gb, board_size=9, max_visits=50)
ptsb = validate_points("黑优9路6手", db, 6, lim=45)
if ptsb:
    last = ptsb[-1]
    check("黑棋视角/终局黑胜率>60%", last["winrate"] > 60, "实际 %s" % last["winrate"])
    check("黑棋视角/终局scoreLead>0(黑领先)", last["scoreLead"] > 0, "实际 %s" % last["scoreLead"])
    # 若实现误用"当前行棋方视角"，奇数手后 currentPlayer=白，winrate 会 zigzag 到白方
    wrs = [p["winrate"] for p in ptsb]
    check("黑棋视角/无视角交替zigzag(偶数手与奇数手都倾向黑)", wrs[-1] > 50 and wrs[-3] > 50,
          str(wrs))

# ============ 6. 错误场景 ============
print("--- 6. 错误场景 ---")
code, body = post_err("/api/analyze_game", {"move_history": [], "board_size": 19})
check("空move_history/400", code == 400, "code=%s body=%s" % (code, body))
code, body = post_err("/api/analyze_game", {"move_history": "B D4 W Q16", "board_size": 19})
check("非数组(字符串)/400", code == 400, "code=%s body=%s" % (code, body))
code, body = post_err("/api/analyze_game", {"move_history": {"B": "Q16"}, "board_size": 19})
check("非数组(对象)/400", code == 400, "code=%s body=%s" % (code, body))
code, body = post_err("/api/analyze_game", {
    "move_history": [["B", "Q4"], ["W", "C3"]], "board_size": 9})
check("非法坐标9路Q4/500+引擎错误信息", code == 500 and body and "location" in (body.get("message") or ""),
      "code=%s body=%s" % (code, body))

# 1 手正常返回 1 点（API 层不要求 ≥2 手，那是前端约束）
print("--- 7. 1 手对局（API 应 ok 返回 1 点） ---")
d = analyze_game([["B", "Q16"]], board_size=19, max_visits=50)
validate_points("19路1手", d, 1)

# ============ 8. max_visits 边界 ============
print("--- 8. max_visits 边界 (3手小对局) ---")
gv = [["B", "E5"], ["W", "C3"], ["B", "D5"]]
d = analyze_game(gv, board_size=9, max_visits=5)
validate_points("max_visits=5(夹到默认100)", d, 3)
d = analyze_game(gv, board_size=9, max_visits=5000)
validate_points("max_visits=5000(夹到默认100)", d, 3)

# ============ 9. 错误后引擎仍就绪 ============
print("--- 9. 错误场景后 /api/status 双引擎仍就绪 ---")
with urllib.request.urlopen(BASE + "/api/status", timeout=10) as r:
    st = json.load(r)
check("status/engine_running", st.get("engine_running") is True, str(st))
check("status/analysis_ready", st.get("analysis_ready") is True, str(st))

# ============ 汇总 ============
print()
print("=" * 60)
print("API 测试: %d 通过 / %d 失败" % (passed, failed))
if perf:
    print("性能记录（每手平均耗时）:")
    for name, n, el in perf:
        if n and el:
            print("  %-28s %2d 手  %6.1fs  → %.2fs/手" % (name, n, el, el / n))
if fails:
    print("失败项: %s" % "; ".join(fails))
    sys.exit(1)
print("ALL PASS")
