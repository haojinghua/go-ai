#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F5 性能验收（TEST_PLAN.md F5 后端验收点）：
50 手棋 maxVisits=100 应在 60s 内完成（单次 analyzeTurns 查询）。
独立脚本，避免拖慢主套件。服务需运行在 http://localhost:8080。
"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8080"

# 构造 50 手合法互异对局（星位+三线交错，全部空点、无提子，必合法）
coords = []
for c in ["Q16", "D4", "D16", "Q4", "K10", "C3", "R16", "D17",
          "Q17", "R4", "C6", "K3", "K17", "C16", "R6", "G7",
          "C13", "R13", "G13", "D9", "Q9", "K13", "M17", "E3",
          "F5", "K5", "O5", "B11", "S11", "B7", "S7", "N11",
          "E17", "F14", "L14", "S15", "B15", "G9", "O17", "N4"]:
    coords.append(c)
while len(coords) < 50:
    for extra in ["H3", "P3", "H17", "P17", "J10", "L10", "B3", "S3", "B17", "S17"]:
        coords.append(extra)
        if len(coords) >= 50:
            break
coords = coords[:50]
assert len(set(coords)) == 50, "坐标重复: %d/%d" % (len(set(coords)), len(coords))
moves = [["B" if i % 2 == 0 else "W", c] for i, c in enumerate(coords)]

payload = {"move_history": moves, "board_size": 19, "komi": 6.5, "max_visits": 100}
req = urllib.request.Request(
    BASE + "/api/analyze_game",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
t0 = time.time()
with urllib.request.urlopen(req, timeout=120) as r:
    data = json.load(r)
wall = time.time() - t0

print("status      : %s" % data.get("status"))
print("turn_count  : %s" % data.get("turn_count"))
print("服务端elapsed: %s s" % data.get("elapsed"))
print("客户端墙钟  : %.1f s" % wall)
if data.get("points"):
    per = data["elapsed"] / len(data["points"])
    print("每手平均    : %.2f s/手 (maxVisits=100)" % per)
    print("首手/末手WR : %s%% / %s%%" % (data["points"][0]["winrate"], data["points"][-1]["winrate"]))

ok = data.get("status") == "ok" and data.get("turn_count") == 50
print()
if ok and data["elapsed"] <= 60:
    print("PASS: 50手 maxVisits=100 于 %.1fs (<60s) 完成，单次 analyzeTurns 查询" % data["elapsed"])
    sys.exit(0)
else:
    print("FAIL: status=%s turn_count=%s elapsed=%s" % (data.get("status"), data.get("turn_count"), data.get("elapsed")))
    sys.exit(1)
