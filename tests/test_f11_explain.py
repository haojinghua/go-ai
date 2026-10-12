#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F11 验收测试（API 部分）：LLM 自然语言讲解 /api/explain
纯标准库；服务需运行在 http://localhost:8080；visits=400、单请求超时 120s。

覆盖：
  T0 前置：双引擎就绪；确认当前环境无 LLM 配置（mode 应为 template）
  T1 讲解生成（move_history 路径，5 手局，轮白）：
     status=ok / mode=template / explanation>100 字 / 五段标记齐全
     （形势总览/推荐一手/其他选择/区域分析/教练建议）/ 耗时尾注
     best_move 合法 GTP 坐标且与 /api/analyze 同局面首推一致
     winrate 数值合理 / elapsed>0
  T2 stones 路径：摆子局（6 子，轮黑）→ 正常生成五段式
  T3 视角：next_player=W → "轮白棋落子"+"白棋胜率"；next_player=B → "轮黑棋落子"
     （TEST_PLAN 要求：不能张冠李戴说成另一方）
  T4 分支文案：komi=-30 黑大优 → "形势大优"+"稳步推进"、无落后文案；
     komi=+30 黑大落后 → "形势落后"+"挑起战斗"、无优势文案
  T5 内容一致性（对全部成功响应动态复算）：
     ① "胜率约 {winrate}%" 出现在正文；② "领先 {score_lead:+.1f} 目" 出现；
     ③ "AI 首选 {best_move}" 出现；④ 教练建议分支与 score_lead 阈值映射一致
     （lead>=3→稳步推进 / lead<=-3→挑起战斗 / 其余→细微）
  T6 错误分支：空棋盘 400（stones/move_history 均空）；非法坐标 Z99 → 500
     结构化错误；max_visits=99999 钳制为 400 仍正常；引擎错误后仍就绪
     （/api/status ok + 后续正常讲解成功）
"""
import json
import re
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8080"
TIMEOUT = 120
COLS19 = "ABCDEFGHJKLMNOPQRST"
GTP_RE = re.compile(r"^([A-HJ-Z])([1-9][0-9]*)$")
SECTION_MARKS = ["【形势总览】", "【推荐一手】", "【其他选择】", "【区域分析】", "【教练建议】"]

passed = 0
failed = 0
fails = []
ok_responses = []   # 全部 200 响应，供 T5 一致性复算


def post(path, obj, timeout=TIMEOUT):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def post_err(path, obj, timeout=30):
    """返回 (code, body)；code=-1 表示连接被断开/无响应"""
    req = urllib.request.Request(
        BASE + path, data=json.dumps(obj).encode("utf-8"),
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


def explain(stones=None, move_history=None, next_player="B", komi=6.5,
            board_size=19, max_visits=400):
    """POST /api/explain；返回 (ok, resp)"""
    body = {"next_player": next_player, "board_size": board_size,
            "komi": komi, "max_visits": max_visits}
    if stones is not None:
        body["stones"] = stones
    if move_history is not None:
        body["move_history"] = move_history
    r = post("/api/explain", body)
    if r.get("status") == "ok":
        ok_responses.append(r)
        return True, r
    return False, r


# ================================================================
print("=" * 64)
print("F11 验收测试：/api/explain LLM 自然语言讲解")
print("=" * 64)

# ---- T0 前置 ---------------------------------------------------
st = get("/api/status")
check("T0 双引擎就绪", st.get("engine_running") and st.get("analysis_ready"), str(st))

# ---- T1 讲解生成（move_history 路径，5 手局轮白）----------------
MH5 = [["B", "D4"], ["W", "Q16"], ["B", "D16"], ["W", "C4"], ["B", "Q3"]]
ok1, r1 = explain(move_history=MH5, next_player="W")
check("T1 move_history 路径 status=ok", ok1, str(r1)[:200])
if ok1:
    e = r1["explanation"]
    check("T1 mode=template（无 LLM 配置降级）", r1.get("mode") == "template", str(r1.get("mode")))
    check("T1 explanation 非空 >100 字", isinstance(e, str) and len(e) > 100, "len=%s" % len(e or ""))
    miss = [m for m in SECTION_MARKS if m not in e]
    check("T1 五段式标记齐全", not miss, "缺: %s" % miss)
    check("T1 耗时尾注", "分析耗时" in e, "")
    bm = r1.get("best_move", "")
    check("T1 best_move 合法 GTP 坐标", bool(GTP_RE.match(bm)) or bm == "pass", bm)
    check("T1 winrate 数值合理", isinstance(r1.get("winrate"), (int, float)) and 0 <= r1["winrate"] <= 100, str(r1.get("winrate")))
    check("T1 elapsed>0", isinstance(r1.get("elapsed"), (int, float)) and r1["elapsed"] > 0, str(r1.get("elapsed")))
    # 与 /api/analyze 同局面首推一致
    ana = post("/api/analyze", {
        "stones": [], "next_player": "W", "board_size": 19, "komi": 6.5,
        "max_visits": 400, "move_history": MH5,
    })
    ana_best = (ana.get("recommendations") or [{}])[0].get("move")
    check("T1 best_move 与 /api/analyze 首推一致", bm == ana_best, "explain=%s analyze=%s" % (bm, ana_best))

# ---- T2 stones 路径 ---------------------------------------------
ST6 = [
    {"color": "B", "pos": "D4"}, {"color": "W", "pos": "Q16"},
    {"color": "B", "pos": "Q4"}, {"color": "W", "pos": "D16"},
    {"color": "B", "pos": "K10"}, {"color": "W", "pos": "C3"},
]
ok2, r2 = explain(stones=ST6, next_player="B")
check("T2 stones 路径 status=ok", ok2, str(r2)[:200])
if ok2:
    e2 = r2["explanation"]
    miss2 = [m for m in SECTION_MARKS if m not in e2]
    check("T2 stones 路径五段式齐全", not miss2, "缺: %s" % miss2)
    check("T2 stones 路径 mode=template", r2.get("mode") == "template", str(r2.get("mode")))

# ---- T3 视角 ----------------------------------------------------
if ok1:
    e1 = r1["explanation"]
    check("T3 W 视角：轮白棋落子", "轮白棋落子" in e1, "")
    check("T3 W 视角：白棋胜率表述", "白棋胜率约" in e1, "")
    check("T3 W 视角：未张冠李戴为黑棋行棋", "轮黑棋落子" not in e1, "")
if ok2:
    check("T3 B 视角：轮黑棋落子", "轮黑棋落子" in r2["explanation"], "")
    check("T3 B 视角：黑棋胜率表述", "黑棋胜率约" in r2["explanation"], "")

# ---- T4 分支文案 ------------------------------------------------
okA, rA = explain(stones=[{"color": "B", "pos": "K10"}], next_player="B", komi=-30)
check("T4 大优局 status=ok", okA, str(rA)[:200])
if okA:
    eA = rA["explanation"]
    check("T4 大优：形势大优文案", "形势大优" in eA, "")
    check("T4 大优：教练建议稳步推进", "稳步推进" in eA, "")
    check("T4 大优：无落后挑战文案", "挑起战斗" not in eA, "")
okL, rL = explain(stones=[{"color": "B", "pos": "K10"}], next_player="B", komi=30)
check("T4 落后局 status=ok", okL, str(rL)[:200])
if okL:
    eL = rL["explanation"]
    check("T4 落后：形势落后文案", "形势落后" in eL, "")
    check("T4 落后：教练建议挑起战斗", "挑起战斗" in eL, "")
    check("T4 落后：无优势稳步文案", "稳步推进" not in eL, "")

# ---- T5 内容一致性（动态复算全部成功响应）----------------------
for i, r in enumerate(ok_responses):
    e = r["explanation"]
    wr, sl, bm = r.get("winrate"), r.get("score_lead"), r.get("best_move")
    tag = "T5 resp#%d" % (i + 1)
    check(tag + " 正文胜率与字段一致", "胜率约 %s%%" % wr in e, "字段 winrate=%s" % wr)
    check(tag + " 正文目差与字段一致", "领先 %+.1f 目" % sl in e, "字段 score_lead=%s" % sl)
    check(tag + " 正文首推与字段一致", "AI 首选 %s" % bm in e, "字段 best_move=%s" % bm)
    # 教练建议分支 ↔ score_lead 阈值映射
    if sl >= 3:
        check(tag + " 建议分支映射 lead>=3→稳步推进", "稳步推进" in e and "挑起战斗" not in e, "lead=%s" % sl)
    elif sl <= -3:
        check(tag + " 建议分支映射 lead<=-3→挑起战斗", "挑起战斗" in e and "稳步推进" not in e, "lead=%s" % sl)
    else:
        check(tag + " 建议分支映射 |lead|<3→细微", "细微" in e, "lead=%s" % sl)

# ---- T6 错误分支 ------------------------------------------------
code, body = post_err("/api/explain", {})
check("T6 空棋盘（无参数）400", code == 400, "code=%s body=%s" % (code, body))
check("T6 空棋盘错误消息", isinstance(body, dict) and "棋盘为空" in str(body.get("message", "")), str(body))
code, body = post_err("/api/explain", {"stones": [], "move_history": [], "next_player": "B"})
check("T6 空列表等价空盘 400", code == 400, "code=%s body=%s" % (code, body))
code, body = post_err("/api/explain", {"stones": [{"color": "B", "pos": "Z99"}], "next_player": "B"})
check("T6 非法坐标 500 结构化错误", code == 500 and isinstance(body, dict) and body.get("status") == "error",
      "code=%s body=%s" % (code, body))
st2 = get("/api/status")
check("T6 引擎错误后仍就绪", st2.get("engine_running") and st2.get("analysis_ready"), str(st2))
okV, rV = explain(stones=[{"color": "B", "pos": "D4"}, {"color": "W", "pos": "Q16"}],
                  next_player="B", max_visits=99999)
check("T6 引擎错误后正常讲解仍成功", okV, str(rV)[:200])
check("T6 max_visits=99999 钳制后正常完成", okV and rV.get("elapsed", 0) > 0, str(rV)[:200])

# ---- 汇总 ------------------------------------------------------
print("=" * 64)
print("汇总: PASS %d / FAIL %d" % (passed, failed))
if fails:
    print("失败项: %s" % "; ".join(fails))
print("=" * 64)
sys.exit(1 if failed else 0)
