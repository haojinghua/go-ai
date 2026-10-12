#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F10 验收测试（API 部分）：死活提示 /api/group_life
纯标准库；服务需运行在 http://localhost:8080；visits=400、单请求超时 120s。

覆盖：
  T0  前置：双引擎就绪
  T1  死活判定专项
      ① 黑单子被白四面围（0 气）→ dead / liberties=0 / survival<-0.5
      ② 黑 6 子角阵（Q16-R17 2x3）→ alive / survival>0.5 / 本地气数=10
      ③ 两眼活棋（13 子环形，真眼 R16/R18）→ alive / survival>0.5 / 双眼在气点中
      ④ 生死攸关（黑子被半围 2 气，可逃）→ critical（探针实测 survival≈+0.41）
  T2  群体/气数
      三连子 D4/D5/D6 → group 恰为三子、气=8；点群体任一子（D4/D6）结果一致；
      点对方棋子（死子局面中的白子 P16）→ 评估白方群体，survival=-life_score
  T3  路径
      stones 路径（T1/T2 已覆盖）；move_history 合法序列 → 群体正确；
      move_history 含提子重演 → 被提子点报"无棋子"、提子点成为白方气点
  T4  急救点：半围黑子 vital_points 非空（≤3 个）、坐标合法、dist≤2、含 pv
  T5  陈旧数据专项：死局面→角阵→死局面交替查询，每次结果反映当次局面
  T6  错误分支：非法坐标 / 越界 / 无子 / 空棋盘 / 缺 target / 数字 target /
      字母 U-Z（COLS 越界，已知触发后端未捕获异常，连接被断）→ 之后引擎仍就绪
  T7  全量一致性：verdict↔survival 阈值映射、颜色↔符号映射、
      target∈group、liberties==len(liberty_points)、group_size==len(group)

已知行为（开发交付说明 + 探针确认）：非法坐标/越界/无子/缺 target 返回 500
（结构化 JSON），空棋盘返回 400；U-Z 列字母触发 COLS.index ValueError 未捕获，
连接被服务端直接断开（客户端 RemoteDisconnected），服务线程存活。
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8080"
TIMEOUT = 120
COLS19 = "ABCDEFGHJKLMNOPQRST"
GTP_RE_CHARS = set(COLS19)

passed = 0
failed = 0
fails = []
ok_responses = []   # 收集所有 200 响应用于 T7 一致性检查


def post(path, obj, timeout=TIMEOUT):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def post_err(path, obj, timeout=30):
    """返回 (code, body)；code=-1 表示连接被断开/无响应"""
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
    except Exception as e:
        return -1, repr(e)


def get(path, timeout=15):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.load(r)


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS  %s" % name)
    else:
        failed += 1
        fails.append(name)
        print("FAIL  %s   %s" % (name, detail))


def gl(stones, target, next_player="B", move_history=None, max_visits=400):
    """group_life 查询；返回 (ok, resp)"""
    body = {"stones": stones, "next_player": next_player, "target": target,
            "board_size": 19, "komi": 6.5, "max_visits": max_visits}
    if move_history is not None:
        body["move_history"] = move_history
    r = post("/api/group_life", body)
    if r.get("status") == "ok":
        ok_responses.append(r)
    return r


def valid_gtp(p):
    if not isinstance(p, str) or len(p) < 2:
        return False
    if p[0] not in GTP_RE_CHARS:
        return False
    try:
        n = int(p[1:])
    except ValueError:
        return False
    return 1 <= n <= 19


def stones_of(pts, color):
    return [{"color": color, "pos": p} for p in pts]


# ---------- T0 前置 ----------
print("== T0 前置 ==")
st = get("/api/status")
check("T0 双引擎就绪",
      st.get("engine_running") is True and st.get("analysis_ready") is True, str(st))

# ---------- T1 死活判定专项 ----------
print("== T1 死活判定专项 ==")

# ① 黑单子被白四面围（0 气）
DEAD_STONES = stones_of(["Q16"], "B") + stones_of(["P16", "R16", "Q15", "Q17"], "W")
r = gl(DEAD_STONES, "Q16")
check("T1① 被围死单子 status=ok", r.get("status") == "ok", str(r)[:200])
if r.get("status") == "ok":
    check("T1① 判定 dead", r["verdict"] == "dead",
          "verdict=%s" % r["verdict"])
    check("T1① 气数=0", r["liberties"] == 0 and r["liberty_points"] == [],
          "liberties=%s" % r["liberties"])
    check("T1① survival<-0.5", r["survival"] < -0.5, "survival=%s" % r["survival"])
    check("T1① 黑棋 survival=life_score", abs(r["survival"] - r["life_score"]) < 1e-9,
          "survival=%s life_score=%s" % (r["survival"], r["life_score"]))
    check("T1① 群体=[Q16] 颜色=B",
          r["group"] == ["Q16"] and r["color"] == "B" and r["group_size"] == 1,
          "group=%s color=%s" % (r["group"], r["color"]))

# ② 黑 6 子角阵（Q16,Q17 行 × Q,R,S 列）
CORNER = ["Q16", "R16", "S16", "Q17", "R17", "S17"]
r = gl(stones_of(CORNER, "B"), "Q16", next_player="W")
check("T1② 6子角阵 status=ok", r.get("status") == "ok", str(r)[:200])
if r.get("status") == "ok":
    check("T1② 判定 alive", r["verdict"] == "alive", "verdict=%s" % r["verdict"])
    check("T1② survival>0.5", r["survival"] > 0.5, "survival=%s" % r["survival"])
    check("T1② 群体=6子且集合正确",
          r["group_size"] == 6 and set(r["group"]) == set(CORNER),
          "group=%s" % r["group"])
    check("T1② 本地气数=10（洪水填充确定性）",
          r["liberties"] == 10 and r["liberties"] == len(r["liberty_points"]),
          "liberties=%s" % r["liberties"])

# ③ 两眼活棋：13 子环形，真眼 R16 / R18
TWO_EYES = ["Q15", "R15", "S15", "Q16", "S16", "Q17", "R17", "S17",
            "Q18", "S18", "Q19", "R19", "S19"]
r = gl(stones_of(TWO_EYES, "B"), "Q16", next_player="W")
check("T1③ 两眼活棋 status=ok", r.get("status") == "ok", str(r)[:200])
if r.get("status") == "ok":
    check("T1③ 判定 alive", r["verdict"] == "alive", "verdict=%s" % r["verdict"])
    check("T1③ survival>0.5", r["survival"] > 0.5, "survival=%s" % r["survival"])
    check("T1③ 群体=13子", r["group_size"] == 13 and set(r["group"]) == set(TWO_EYES),
          "group_size=%s" % r["group_size"])
    check("T1③ 两个真眼 R16/R18 都在气点中",
          "R16" in r["liberty_points"] and "R18" in r["liberty_points"],
          "libs=%s" % r["liberty_points"])

# ④ 生死攸关：黑子被半围（2 气，可逃），探针实测 survival≈+0.41 → critical
CRIT_STONES = stones_of(["Q16"], "B") + stones_of(["Q15", "R16"], "W")
r = gl(CRIT_STONES, "Q16", next_player="B")
check("T1④ 半围黑子 status=ok", r.get("status") == "ok", str(r)[:200])
if r.get("status") == "ok":
    check("T1④ 判定 critical", r["verdict"] == "critical",
          "verdict=%s survival=%s" % (r["verdict"], r["survival"]))
    check("T1④ survival 落在 critical 区间 [-0.6,0.6)",
          -0.6 <= r["survival"] < 0.6, "survival=%s" % r["survival"])
    check("T1④ 气数=2（Q17/P16）",
          r["liberties"] == 2 and set(r["liberty_points"]) == {"Q17", "P16"},
          "libs=%s" % r["liberty_points"])

# ---------- T2 群体/气数 ----------
print("== T2 群体/气数 ==")
TRI = stones_of(["D4", "D5", "D6"], "B")
r_mid = gl(TRI, "D5")
check("T2 三连子点 D5 status=ok", r_mid.get("status") == "ok", str(r_mid)[:200])
if r_mid.get("status") == "ok":
    check("T2 群体=[D4,D5,D6]",
          set(r_mid["group"]) == {"D4", "D5", "D6"} and r_mid["group_size"] == 3,
          "group=%s" % r_mid["group"])
    check("T2 气数=8", r_mid["liberties"] == 8 and len(r_mid["liberty_points"]) == 8,
          "liberties=%s" % r_mid["liberties"])

r_a = gl(TRI, "D4")
r_b = gl(TRI, "D6")
ok_a = r_a.get("status") == "ok" and r_b.get("status") == "ok"
check("T2 点群体任一子（D4/D6）均成功", ok_a,
      "%s | %s" % (str(r_a)[:120], str(r_b)[:120]))
if ok_a:
    check("T2 D4/D6 结果与 D5 群体一致",
          set(r_a["group"]) == set(r_b["group"]) == set(r_mid["group"])
          and r_a["liberties"] == r_b["liberties"] == r_mid["liberties"] == 8,
          "groups=%s/%s libs=%s/%s" % (r_a["group"], r_b["group"],
                                      r_a["liberties"], r_b["liberties"]))

# 点对方棋子 → 评估白方群体
r_w = gl(DEAD_STONES, "P16", next_player="B")
check("T2 点对方白子 status=ok", r_w.get("status") == "ok", str(r_w)[:200])
if r_w.get("status") == "ok":
    check("T2 白子 color=W group=[P16]", r_w["color"] == "W" and r_w["group"] == ["P16"],
          "color=%s group=%s" % (r_w["color"], r_w["group"]))
    check("T2 白方 survival=-life_score（取反映射）",
          abs(r_w["survival"] + r_w["life_score"]) < 1e-9,
          "survival=%s life_score=%s" % (r_w["survival"], r_w["life_score"]))
    check("T2 围死黑子的白子判定 alive（survival>0.5）",
          r_w["verdict"] == "alive" and r_w["survival"] > 0.5,
          "verdict=%s survival=%s" % (r_w["verdict"], r_w["survival"]))
    check("T2 白子气数=3", r_w["liberties"] == 3, "liberties=%s" % r_w["liberties"])

# ---------- T3 路径：stones 与 move_history ----------
print("== T3 路径 ==")
mh_legal = [["B", "D4"], ["W", "Q16"], ["B", "D5"], ["W", "Q4"],
            ["B", "D6"], ["W", "Q17"]]
r_mh = gl([], "D5", next_player="B", move_history=mh_legal)
check("T3 move_history 路径 status=ok", r_mh.get("status") == "ok", str(r_mh)[:200])
if r_mh.get("status") == "ok":
    check("T3 move_history 群体=三连子 8 气",
          set(r_mh["group"]) == {"D4", "D5", "D6"} and r_mh["liberties"] == 8,
          "group=%s libs=%s" % (r_mh["group"], r_mh["liberties"]))

# 含提子的重演：黑 E5 被白四面包围提掉
mh_capture = [["B", "E5"], ["W", "D5"], ["B", "pass"], ["W", "F5"],
              ["B", "pass"], ["W", "E6"], ["B", "pass"], ["W", "E4"]]
code, body = post_err("/api/group_life",
                      {"stones": [], "move_history": mh_capture, "next_player": "B",
                       "target": "E5", "board_size": 19, "max_visits": 400})
check("T3 提子重演后被提点 E5 报错（不再有子）",
      code in (400, 500) and body and "无棋子" in str(body.get("message", "")),
      "code=%s body=%s" % (code, body))
r_cap = gl([], "D5", next_player="B", move_history=mh_capture)
check("T3 提子重演后白 D5 status=ok", r_cap.get("status") == "ok", str(r_cap)[:200])
if r_cap.get("status") == "ok":
    check("T3 白 D5 群体=[D5] 且提子点 E5 已成其气点（提子重演正确）",
          r_cap["group"] == ["D5"] and r_cap["color"] == "W"
          and "E5" in r_cap["liberty_points"] and r_cap["liberties"] == 4,
          "libs=%s" % r_cap["liberty_points"])

# ---------- T4 急救点 ----------
print("== T4 急救点 ==")
r_v = gl(CRIT_STONES, "Q16", next_player="B")
check("T4 半围黑子 vital_points 非空",
      r_v.get("status") == "ok" and isinstance(r_v.get("vital_points"), list)
      and len(r_v["vital_points"]) > 0,
      "vital=%s" % r_v.get("vital_points"))
if r_v.get("status") == "ok" and r_v.get("vital_points"):
    vp = r_v["vital_points"]
    check("T4 急救点≤3 个且全部为合法棋盘坐标",
          len(vp) <= 3 and all(valid_gtp(v["move"]) for v in vp),
          "vp=%s" % [v["move"] for v in vp])
    check("T4 急救点距群体≤2 且带 pv（≤6 手）",
          all(isinstance(v.get("dist"), int) and 0 <= v["dist"] <= 2
              and isinstance(v.get("pv"), list) and len(v["pv"]) <= 6 for v in vp),
          "vp=%s" % vp)
    check("T4 recommended 存在且合法", valid_gtp(r_v.get("recommended") or ""),
          "recommended=%s" % r_v.get("recommended"))

# ---------- T5 陈旧数据专项 ----------
print("== T5 陈旧数据专项 ==")
r_d1 = gl(DEAD_STONES, "Q16")
r_c1 = gl(stones_of(CORNER, "B"), "Q16", next_player="W")
r_d2 = gl(DEAD_STONES, "Q16")
ok3 = all(x.get("status") == "ok" for x in (r_d1, r_c1, r_d2))
check("T5 三次交替查询全部成功", ok3,
      "%s|%s|%s" % (str(r_d1)[:80], str(r_c1)[:80], str(r_d2)[:80]))
if ok3:
    check("T5 第1次（死局面）→ dead",
          r_d1["verdict"] == "dead" and r_d1["survival"] < -0.5,
          "v=%s s=%s" % (r_d1["verdict"], r_d1["survival"]))
    check("T5 第2次（角阵，同目标点 Q16）→ alive（未复用第1次 ownership）",
          r_c1["verdict"] == "alive" and r_c1["survival"] > 0.5
          and r_c1["group_size"] == 6,
          "v=%s s=%s size=%s" % (r_c1["verdict"], r_c1["survival"], r_c1["group_size"]))
    check("T5 第3次（回到死局面）→ dead（未复用第2次 ownership）",
          r_d2["verdict"] == "dead" and r_d2["survival"] < -0.5
          and r_d2["group_size"] == 1,
          "v=%s s=%s size=%s" % (r_d2["verdict"], r_d2["survival"], r_d2["group_size"]))

# ---------- T6 错误分支 ----------
print("== T6 错误分支 ==")
ERRS = [
    ("非法坐标 xyz", {"stones": DEAD_STONES, "target": "xyz", "next_player": "B"}),
    ("越界 T20", {"stones": DEAD_STONES, "target": "T20", "next_player": "B"}),
    ("无子位置 D10", {"stones": DEAD_STONES, "target": "D10", "next_player": "B"}),
    ("空棋盘", {"stones": [], "target": "D4", "next_player": "B"}),
    ("缺 target", {"stones": DEAD_STONES, "next_player": "B"}),
    ("target 非字符串", {"stones": DEAD_STONES, "target": 123, "next_player": "B"}),
]
for name, body in ERRS:
    code, resp = post_err("/api/group_life", body)
    check("T6 %s → 4xx/5xx 结构化 JSON" % name,
          code in (400, 404, 500) and resp and resp.get("status") == "error",
          "code=%s body=%s" % (code, resp))
check("T6 空棋盘特判为 400",
      post_err("/api/group_life", {"stones": [], "target": "D4", "next_player": "B"})[0] == 400,
      "实际 code=%s" % post_err("/api/group_life", {"stones": [], "target": "D4"})[0])

# U-Z 列字母：COLS.index 未捕获 → 连接被断（已知缺陷，回归其不致服务崩溃）
code, resp = post_err("/api/group_life",
                      {"stones": DEAD_STONES, "target": "Z99", "next_player": "B"})
print("  [info] U-Z 越界字母: code=%s resp=%s" % (code, resp))
check("T6 U-Z 越界字母不返回 200（当前实现为连接断开/5xx）",
      code != 200, "code=%s" % code)

st2 = get("/api/status")
check("T6 错误风暴后双引擎仍就绪",
      st2.get("engine_running") is True and st2.get("analysis_ready") is True, str(st2))
r_recover = gl(stones_of(CORNER, "B"), "Q16", next_player="W")
check("T6 错误后 group_life 仍可用",
      r_recover.get("status") == "ok" and r_recover["verdict"] == "alive",
      str(r_recover)[:150])

# ---------- T7 全量一致性 ----------
print("== T7 全量一致性（%d 个成功响应）==" % len(ok_responses))
bad = []
for i, r in enumerate(ok_responses):
    s, v = r["survival"], r["verdict"]
    exp_v = "alive" if s >= 0.6 else ("critical" if s >= -0.6 else "dead")
    if v != exp_v:
        bad.append("#%d verdict 映射: %s vs %s" % (i, v, exp_v))
    sign = 1 if r["color"] == "B" else -1
    if abs(s - round(sign * r["life_score"], 3)) > 0.001:
        bad.append("#%d 颜色取反: survival=%s life=%s color=%s" % (i, s, r["life_score"], r["color"]))
    if r["target"] not in r["group"]:
        bad.append("#%d target 不在群体中" % i)
    if r["group_size"] != len(r["group"]) or r["liberties"] != len(r["liberty_points"]):
        bad.append("#%d 计数不一致" % i)
    if not all(valid_gtp(g) for g in r["group"]):
        bad.append("#%d 群体坐标非法" % i)
check("T7 全部响应字段一致（verdict/符号/计数/坐标）", not bad, "; ".join(bad[:5]))

# ---------- 汇总 ----------
print("=" * 70)
print("F10 API 测试汇总: PASS %d / FAIL %d  (共 %d)" % (passed, failed, passed + failed))
if fails:
    print("失败项:")
    for f in fails:
        print("  - " + f)
print("=" * 70)
sys.exit(1 if failed else 0)
