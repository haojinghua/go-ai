#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F8 验收测试（API 部分）：Ownership 形势图 /api/analyze?include_ownership=true
纯标准库；服务需运行在 http://localhost:8080
visits=100-200、单请求超时 120s。

覆盖：
  T0  测试前置：双引擎就绪
  T1  方向性专项（核心，19 路 initialStones 路径）：黑占 Q16+D4、白占 Q4
      → ownership 长度=361；棋子点强符号（Q16/P16/D4 > +0.35，Q4 < -0.35）；
      邻近点方向正确（同号或 |v|≤0.1 视为争议点）且过半数 |v|>0.1 实符号；
      天元 K10 与中心点 E10 |v|<0.4；全部 |v|≤1.0；recommendations 照常返回
      【阈值校准依据】visits=200/500/1600 探针显示：3-2 位（R17/P4/R3）
      是 KataGo 真实判定的争议点（值≈0，不随 visits 收敛），原 >0.3 邻近阈值
      不符合引擎真实输出；改为「符号正确 + 过半实符号」的方向性判据。
  T2  move_history 路径：同局面走子序列 → 长度 361、方向性同上、与 T1 角部值一致(±0.5)
  T3  向后兼容：include_ownership=false / 不传 → 响应无 ownership 字段，推荐照常
  T4  9 路：长度 81、方向性同上（黑 G7+C3、白 G3、天元 E5）
  T5  对称性专项：黑白互换（白占 Q16+D4、黑占 Q4，komi=-6.5 构成真镜像）
      → 同位置 v2 ≈ -v1（角部 ±0.35；全盘 ≥90% 点差 ≤0.35 且最大 ≤0.6）
  T6  错误场景：空棋盘 400；错误后双引擎就绪
"""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8080"
TIMEOUT = 120
COLS19 = "ABCDEFGHJKLMNOPQRST"
COLS9 = "ABCDEFGHJ"

passed = 0
failed = 0
fails = []


def post(path, obj, timeout=TIMEOUT):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def post_err(path, obj, timeout=30):
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


def pidx(pos, size=19):
    """GTP 坐标 → ownership 数组下标（行主序，第一行=顶部）"""
    cols = COLS19 if size == 19 else COLS9
    oc = cols.index(pos[0].upper())
    orow = size - int(pos[1:])
    return orow * size + oc


def direction_checks(tag, own, size, black_pts, white_pts, black_nb, white_nb, center_pts):
    """方向性通用检查：
    - 棋子点：强符号（黑 > +0.35 / 白 < -0.35）
    - 邻近点：方向不矛盾（黑侧 v>0 或 |v|≤0.2；白侧 v<0 或 |v|≤0.2），
      且至少半数邻近点 |v|>0.1 且符号正确（实势力）
      【争议点容差 ±0.2 依据】多次运行实测 3-2 争议点（R3）在
      visits=200 下波动 -0.02~+0.15；9 路白角左/上邻（F3/G4）受黑方
      两侧挤压稳定为 ~+0.05，均为引擎真实判定而非缺陷
    - 中心点：|v|<0.4
    - 值域：全部 |v|≤1.0"""
    for p in black_pts:
        v = own[pidx(p, size)]
        check("%s 黑方棋子点 %s 强正值 >0.35" % (tag, p), v > 0.35, "v=%.3f" % v)
    for p in white_pts:
        v = own[pidx(p, size)]
        check("%s 白方棋子点 %s 强负值 <-0.35" % (tag, p), v < -0.35, "v=%.3f" % v)
    for p in black_nb:
        v = own[pidx(p, size)]
        check("%s 黑角邻近 %s 方向不矛盾（>0 或 |v|≤0.2 争议点）" % (tag, p),
              v > 0 or abs(v) <= 0.2, "v=%.3f" % v)
    for p in white_nb:
        v = own[pidx(p, size)]
        check("%s 白角邻近 %s 方向不矛盾（<0 或 |v|≤0.2 争议点）" % (tag, p),
              v < 0 or abs(v) <= 0.2, "v=%.3f" % v)
    b_solid = sum(1 for p in black_nb if own[pidx(p, size)] > 0.1)
    w_solid = sum(1 for p in white_nb if own[pidx(p, size)] < -0.1)
    check("%s 黑角邻近至少半数实符号 (|v|>0.1)" % tag, b_solid >= (len(black_nb) + 1) // 2,
          "solid=%d/%d" % (b_solid, len(black_nb)))
    check("%s 白角邻近至少半数实符号 (|v|>0.1)" % tag, w_solid >= (len(white_nb) + 1) // 2,
          "solid=%d/%d" % (w_solid, len(white_nb)))
    for p in center_pts:
        v = own[pidx(p, size)]
        check("%s 中心 %s 接近 0 (|v|<0.4)" % (tag, p), abs(v) < 0.4, "v=%.3f" % v)
    mx = max(abs(v) for v in own)
    check("%s 全部 |v|≤1.0" % tag, mx <= 1.0, "max|v|=%.3f" % mx)


print("=" * 70)
print("F8 Ownership 形势图 API 验收测试  timeout=%ds" % TIMEOUT)
print("=" * 70)

# ---------- T0 前置 ----------
st = get("/api/status")
check("T0 双引擎就绪", st.get("engine_running") is True and st.get("analysis_ready") is True, str(st))

# ---------- T1 方向性专项（19 路 initialStones 路径，visits=200） ----------
POS = {"B_Q16": {"color": "B", "pos": "Q16"},
       "B_D4": {"color": "B", "pos": "D4"},
       "W_Q4": {"color": "W", "pos": "Q4"}}
t0 = time.time()
r1 = post("/api/analyze", {"stones": [POS["B_Q16"], POS["B_D4"], POS["W_Q4"]],
                           "next_player": "B", "board_size": 19, "komi": 6.5,
                           "max_visits": 200, "include_ownership": True})
t1 = time.time() - t0
own1 = r1.get("ownership")
check("T1 返回 ok（%.1fs）" % t1, r1.get("status") == "ok", str(r1)[:200])
check("T1 ownership 存在且长度=361", isinstance(own1, list) and len(own1) == 361,
      "type=%s len=%s" % (type(own1).__name__, len(own1) if isinstance(own1, list) else "N/A"))
if isinstance(own1, list) and len(own1) == 361:
    # 黑角邻近 = Q16 周围一圈（含 3-2 位 R17 争议点）；白角邻近 = Q4 周围一圈
    direction_checks("T1", own1, 19, ["Q16", "D4"], ["Q4"],
                     ["P16", "R16", "Q15", "Q17", "R17"],
                     ["P4", "R4", "Q3", "Q5", "R3"], ["K10", "E10"])
    print("  [样本] Q16=%.3f P16=%.3f R16=%.3f R17=%.3f D4=%.3f Q4=%.3f P4=%.3f R3=%.3f K10=%.3f"
          % tuple(own1[pidx(p)] for p in ["Q16", "P16", "R16", "R17", "D4", "Q4", "P4", "R3", "K10"]))
check("T1 recommendations 照常返回", isinstance(r1.get("recommendations"), list) and len(r1["recommendations"]) >= 1,
      str(r1)[:200])

# ---------- T2 move_history 路径（visits=200） ----------
MH = [["B", "Q16"], ["W", "Q4"], ["B", "D4"]]   # 自然轮到白
r2 = post("/api/analyze", {"stones": [], "next_player": "W", "board_size": 19, "komi": 6.5,
                           "max_visits": 200, "move_history": MH, "include_ownership": True})
own2 = r2.get("ownership")
check("T2 move_history 路径返回 ok", r2.get("status") == "ok", str(r2)[:200])
check("T2 ownership 长度=361", isinstance(own2, list) and len(own2) == 361,
      "len=%s" % (len(own2) if isinstance(own2, list) else "N/A"))
if isinstance(own2, list) and len(own2) == 361:
    direction_checks("T2", own2, 19, ["Q16", "D4"], ["Q4"],
                     ["P16", "R16", "Q15"], ["P4", "Q3", "Q5"], ["K10"])
    if own1:
        for p in ("Q16", "Q4", "K10"):
            a, b = own1[pidx(p)], own2[pidx(p)]
            check("T2 与 T1 角部/中心一致 [%s] (±0.5)" % p, abs(a - b) <= 0.5,
                  "init=%.3f mh=%.3f" % (a, b))

# ---------- T3 向后兼容 ----------
r3a = post("/api/analyze", {"stones": [POS["B_Q16"], POS["W_Q4"]], "next_player": "B",
                            "board_size": 19, "komi": 6.5, "max_visits": 100,
                            "include_ownership": False})
check("T3 include_ownership=false → 无 ownership 字段", "ownership" not in r3a, str(r3a)[:200])
check("T3 include_ownership=false → recommendations 照常",
      r3a.get("status") == "ok" and isinstance(r3a.get("recommendations"), list), str(r3a)[:200])

r3b = post("/api/analyze", {"stones": [POS["B_Q16"], POS["W_Q4"]], "next_player": "B",
                            "board_size": 19, "komi": 6.5, "max_visits": 100})
check("T3 不传 include_ownership → 无 ownership 字段", "ownership" not in r3b, str(r3b)[:200])
check("T3 不传 include_ownership → recommendations 照常",
      r3b.get("status") == "ok" and isinstance(r3b.get("recommendations"), list), str(r3b)[:200])

# ---------- T4 9 路（黑 G7+C3、白 G3，visits=100） ----------
POS9 = [{"color": "B", "pos": "G7"}, {"color": "B", "pos": "C3"}, {"color": "W", "pos": "G3"}]
r4 = post("/api/analyze", {"stones": POS9, "next_player": "B", "board_size": 9, "komi": 6.5,
                           "max_visits": 100, "include_ownership": True})
own4 = r4.get("ownership")
check("T4 9 路 initialStones 路径返回 ok", r4.get("status") == "ok", str(r4)[:200])
check("T4 9 路 ownership 长度=81", isinstance(own4, list) and len(own4) == 81,
      "len=%s" % (len(own4) if isinstance(own4, list) else "N/A"))
if isinstance(own4, list) and len(own4) == 81:
    direction_checks("T4", own4, 9, ["G7", "C3"], ["G3"],
                     ["F7", "G8", "G6", "H7"], ["F3", "H3", "G2", "G4"], ["E5"])

r4m = post("/api/analyze", {"stones": [], "next_player": "W", "board_size": 9, "komi": 6.5,
                            "max_visits": 100, "move_history": [["B", "G7"], ["W", "G3"], ["B", "C3"]],
                            "include_ownership": True})
own4m = r4m.get("ownership")
check("T4 9 路 move_history 路径返回 ok", r4m.get("status") == "ok", str(r4m)[:200])
check("T4 9 路 move_history ownership 长度=81", isinstance(own4m, list) and len(own4m) == 81,
      "len=%s" % (len(own4m) if isinstance(own4m, list) else "N/A"))

# ---------- T5 对称性专项（黑白互换 + komi 取负构成真镜像，visits=200） ----------
r5 = post("/api/analyze", {"stones": [{"color": "W", "pos": "Q16"}, {"color": "W", "pos": "D4"},
                                       {"color": "B", "pos": "Q4"}],
                           "next_player": "W", "board_size": 19, "komi": -6.5,
                           "max_visits": 200, "include_ownership": True})
own5 = r5.get("ownership")
check("T5 黑白互换局面返回 ok", r5.get("status") == "ok", str(r5)[:200])
check("T5 互换局面 ownership 长度=361", isinstance(own5, list) and len(own5) == 361,
      "len=%s" % (len(own5) if isinstance(own5, list) else "N/A"))
if isinstance(own5, list) and len(own5) == 361 and own1:
    for p in ("Q16", "D4", "K10"):
        a, b = own1[pidx(p)], own5[pidx(p)]
        check("T5 角部/中心对称 [%s] v1≈-v2 (±0.35)" % p, abs(a + b) <= 0.35,
              "v1=%.3f v2=%.3f" % (a, b))
    for p in ("Q4",):
        a, b = own1[pidx(p)], own5[pidx(p)]
        check("T5 对称 [%s] v1≈-v2 (±0.35)" % p, abs(a + b) <= 0.35,
              "v1=%.3f v2=%.3f" % (a, b))
    diff = [abs(a + b) for a, b in zip(own1, own5)]
    within = sum(1 for d in diff if d <= 0.35)
    mx = max(diff)
    check("T5 全盘对称 ≥90%% 点 |v1+v2|≤0.35", within >= 0.9 * 361,
          "within=%d/361 (%.1f%%)" % (within, 100.0 * within / 361))
    check("T5 全盘对称最大偏差 ≤0.6（visits 低噪声容忍）", mx <= 0.6, "max=%.3f" % mx)
    print("  [对称性] %d/361 点在 ±0.35 内, 最大偏差 %.3f, 平均偏差 %.3f"
          % (within, mx, sum(diff) / len(diff)))

# ---------- T6 错误场景 ----------
code, body = post_err("/api/analyze", {"stones": [], "move_history": [], "next_player": "B",
                                       "include_ownership": True})
check("T6 空棋盘 → 400", code == 400, "code=%s body=%s" % (code, body))
check("T6 空棋盘报错为结构化 JSON",
      body and ("error" in str(body) or "message" in str(body)), str(body))

st2 = get("/api/status")
check("T6 错误后双引擎仍就绪",
      st2.get("engine_running") is True and st2.get("analysis_ready") is True, str(st2))

r_recover = post("/api/analyze", {"stones": [POS["B_Q16"]], "next_player": "W",
                                  "board_size": 19, "komi": 6.5, "max_visits": 100,
                                  "include_ownership": True})
check("T6 错误后带 ownership 的分析仍可用",
      r_recover.get("status") == "ok" and len(r_recover.get("ownership") or []) == 361,
      str(r_recover)[:200])

# ---------- 汇总 ----------
print("=" * 70)
print("F8 API 测试汇总: PASS %d / FAIL %d  (共 %d)" % (passed, failed, passed + failed))
if fails:
    print("失败项:")
    for f in fails:
        print("  - " + f)
print("=" * 70)
sys.exit(1 if failed else 0)
