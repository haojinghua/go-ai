#!/usr/bin/env python3
"""
功能1「SGF 棋谱导入」验收测试（API 级）
=======================================

用法:
    python3 tests/test_f1_import_sgf.py              # 默认 http://localhost:8080
    python3 tests/test_f1_import_sgf.py --base http://localhost:9090

覆盖范围（见 tests/TEST_PLAN.md F1 节）:
    1.  标准 19 路棋谱（字段完整性 + 坐标转换 pd→Q16 等）
    2.  9 路 / 13 路棋盘
    3.  AB/AW 摆子（含让子棋 HA）
    4.  变化图只取主线第一分支
    5.  pass 两种写法（B[] 与 B[tt]）
    6.  含转义字符 \\] 的注释
    7.  含中文注释 C[...] 的 UTF-8 棋谱
    8.  自有导出格式回环（模拟 index.html exportSGF 输出格式再导入）
    9.  多属性值 AB[aa][cc]
    10. 错误场景（空内容 400、纯文本、GM[2]、SZ[25]、无着法）
    11. 边界（纯 pass 棋谱、仅摆子死活题、截断括号、带 LB/C 的复杂棋谱）
    12. 全部错误用例打完后服务仍存活（/api/status 双引擎就绪）

约束: 纯标准库（urllib/json）；每步 PASS/FAIL；失败时退出码非 0。
SGF 用例全部留存于本文件，作为后续回归资产。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

results = []  # [(name, ok, detail)]


def http_post_json(base, path, payload, timeout=15):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def http_get(base, path, timeout=10):
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("[PASS] " + name if ok else "[FAIL] " + name + ("  -> " + detail if detail else ""))


def import_sgf(base, sgf_text):
    return http_post_json(base, "/api/import_sgf", {"sgf": sgf_text})


# ---------------------------------------------------------------
# SGF 样例资产
# ---------------------------------------------------------------

# S01: 标准 19 路对局（含完整元信息），手数已知 6 手
SGF_19 = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[7.5]PB[黑方棋手]PW[白方棋手]"
    "RE[B+2.5]DT[2026-09-28]EV[测试杯决赛]GN[test_game]PC[北京]CP[版权]US[记录员]"
    "BR[6d]WR[6d]HA[0]"
    ";B[pd];W[dp];B[pp];W[dd];B[qq];W[cc])"
)
# pd → Q16, dp → D16? 校验用期望值在断言里逐个写

# S02: 9 路棋谱（含 pass）
SGF_9 = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[9]KM[5.5]PB[A]PW[B]"
    ";B[cc];W[gg];B[ce];W[ec];B[];W[tt])"
)

# S03: 13 路棋谱
SGF_13 = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[13]KM[6.5]PB[X]PW[Y]"
    ";B[dd];W[kk];B[dj];W[jj])"
)

# S04: 让子棋（AB 多子摆子 + HA 元信息）
SGF_HANDICAP = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[0.5]HA[4]PB[上手]PW[下手]AB[dd][dp][pp][pd]"
    ";W[qq];B[qj];W[od])"
)

# S05: 变化图——主线只取第一分支
SGF_VARIATION = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[6.5]"
    ";B[dd](;W[dp](;B[pp];W[dd])(;B[qq]))(;W[cc];B[qq])(;W[jj]))"
)

# S06: 带转义字符注释（值内含 \] 与 \\）
SGF_ESCAPE = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[6.5]"
    "C[注释含转义 \\] 与反斜杠 \\\\ 还有 [假括号]"
    ";B[dd];W[dp];B[pp])"
)

# S07: 中文注释 UTF-8
SGF_CN_COMMENT = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[6.5]PB[柯洁]PW[申真谞]"
    "C[这是中文注释：黑棋占角，白棋挂角。]"
    ";B[dd]C[第一手，占空角];W[dp]C[白棋分投];B[pp])"
)

# S08: 模拟 index.html exportSGF() 的输出格式（KM 固定 6.5、每 10 手换行、pass 为 B[]）
# 9 路，11 手，第 11 手为黑 pass
SGF_ROUNDTRIP = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[9]KM[6.5]\n"
    "PB[Player]\nPW[KataGo]\nDT[2026-10-01]\nAP[GoAI-Local]\n"
    ";B[dc];W[cc];B[cf];W[gc];B[gg];W[ce];B[eg];W[ge];B[ec];W[ee]\n"
    ";B[])"
)

# S09: 多属性值摆子（AB[aa][cc] 写法）
SGF_MULTI_AB = "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[6.5]AB[aa][cc];B[dd];W[ee])"

# S10: 仅摆子的死活题（无着法）
SGF_PROBLEM_ONLY = "(;GM[1]FF[4]CA[UTF-8]SZ[19]AW[ab][bb][cb][ba][ca]AB[aa][bc]KM[0])"

# S11: 纯 pass 棋谱
SGF_ALL_PASS = "(;GM[1]FF[4]SZ[19]KM[6.5];B[];W[tt];B[])"

# S12: 复杂 SGF（LB 标号、TR 标记、嵌套变化图）
SGF_COMPLEX = (
    "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[6.5]"
    ";B[dd]LB[dd:A][dp:B]TR[pp]C[标记测试]"
    "(;W[dp]C[正解分支];B[pp])(;W[cc]C[失败分支];B[dp]))"
)

# E01: 纯文本非 SGF
BAD_PLAIN_TEXT = "这不是SGF，只是一段普通文本\nhello world"

# E02: GM 非 1（国际象棋）
BAD_GM2 = "(;GM[2]FF[4]SZ[19];B[e2];W[e4])"

# E03: 棋盘过大
BAD_SZ25 = "(;GM[1]FF[4]SZ[25]KM[6.5];B[dd];W[dp])"

# E04: 无着法无摆子
BAD_NO_MOVES = "(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[6.5]PB[x]PW[y]RE[B+R])"

# E05: 截断括号（未闭合的值和树）
BAD_TRUNCATED = "(;GM[1]FF[4]SZ[19]KM[6.5];B[dd];W[dp"


# ---------------------------------------------------------------
# 断言辅助
# ---------------------------------------------------------------

def assert_moves(name, got, expected):
    ok = got == expected
    check(name, ok, "got=%s expected=%s" % (got, expected))


def assert_full_fields(name, data):
    """校验成功响应的字段完整性"""
    need = ["status", "board_size", "komi", "meta", "setup_stones", "moves", "move_count"]
    missing = [k for k in need if k not in data]
    check(name, not missing, "missing=%s data=%s" % (missing, data))


# ---------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------

def run(base):
    # ---- T01 标准 19 路棋谱 ----
    st, d = import_sgf(base, SGF_19)
    check("T01a 19路返回200且status=ok", st == 200 and d.get("status") == "ok",
          "http=%s data=%s" % (st, d))
    assert_full_fields("T01b 字段完整性", d)
    check("T01c board_size=19 komi=7.5", d.get("board_size") == 19 and d.get("komi") == 7.5,
          "sz=%s km=%s" % (d.get("board_size"), d.get("komi")))
    assert_moves("T01d 主线着法与坐标(pd→Q16)", d.get("moves"),
                 [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"],
                  ["B", "R3"], ["W", "C17"]])
    check("T01e move_count=6", d.get("move_count") == 6, "got=%s" % d.get("move_count"))
    m = d.get("meta", {})
    check("T01f 元信息 PB/PW/RE/DT/EV/HA/BR/WR",
          m.get("PB") == "黑方棋手" and m.get("PW") == "白方棋手"
          and m.get("RE") == "B+2.5" and m.get("DT") == "2026-09-28"
          and m.get("EV") == "测试杯决赛" and m.get("HA") == "0"
          and m.get("BR") == "6d" and m.get("WR") == "6d",
          "meta=%s" % m)
    check("T01g 无摆子时setup_stones为空", d.get("setup_stones") == [], "got=%s" % d.get("setup_stones"))

    # ---- T02 9 路 ----
    st, d = import_sgf(base, SGF_9)
    check("T02a 9路status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    check("T02b board_size=9 komi=5.5", d.get("board_size") == 9 and d.get("komi") == 5.5,
          "sz=%s km=%s" % (d.get("board_size"), d.get("komi")))
    assert_moves("T02c 9路坐标(cc→C7)与两种pass写法", d.get("moves"),
                 [["B", "C7"], ["W", "G3"], ["B", "C5"], ["W", "E7"],
                  ["B", "pass"], ["W", "pass"]])

    # ---- T03 13 路 ----
    st, d = import_sgf(base, SGF_13)
    check("T03a 13路status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    assert_moves("T03b 13路坐标(dd→D10, kk→L3, jj→K4)", d.get("moves"),
                 [["B", "D10"], ["W", "L3"], ["B", "D4"], ["W", "K4"]])

    # ---- T04 让子棋 AB 摆子 ----
    st, d = import_sgf(base, SGF_HANDICAP)
    check("T04a 让子棋status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    assert_moves("T04b AB四子摆子坐标", d.get("setup_stones"),
                 [["B", "D16"], ["B", "D4"], ["B", "Q4"], ["B", "Q16"]])
    check("T04c HA=4 KM=0.5", d.get("meta", {}).get("HA") == "4" and d.get("komi") == 0.5,
          "meta=%s km=%s" % (d.get("meta"), d.get("komi")))
    assert_moves("T04d 让子后白先着法", d.get("moves"),
                 [["W", "R3"], ["B", "R10"], ["W", "P16"]])
    check("T04e move_count=3", d.get("move_count") == 3, "got=%s" % d.get("move_count"))

    # ---- T05 变化图只取第一分支 ----
    st, d = import_sgf(base, SGF_VARIATION)
    check("T05a 变化图棋谱status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    # 主线: B[dd] W[dp] B[pp] W[dd]（第二层第一个分支）
    assert_moves("T05b 只取主线第一分支", d.get("moves"),
                 [["B", "D16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"]])

    # ---- T06 已并入 T02c（B[] 与 W[tt] 两种 pass 写法）----
    st, d = import_sgf(base, SGF_ALL_PASS)
    assert_moves("T06 纯pass棋谱(B[]与B[tt]均识别)", d.get("moves"),
                 [["B", "pass"], ["W", "pass"], ["B", "pass"]])
    check("T06b 纯pass棋谱move_count=3", d.get("move_count") == 3, "got=%s" % d.get("move_count"))

    # ---- T07 转义字符注释 ----
    st, d = import_sgf(base, SGF_ESCAPE)
    check("T07a 含\\]注释status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    assert_moves("T07b 转义注释不影响着法解析", d.get("moves"),
                 [["B", "D16"], ["W", "D4"], ["B", "Q4"]])

    # ---- T08 中文注释 UTF-8 ----
    st, d = import_sgf(base, SGF_CN_COMMENT)
    check("T08a 中文注释棋谱status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    check("T08b 中文元信息PB/PW不乱码",
          d.get("meta", {}).get("PB") == "柯洁" and d.get("meta", {}).get("PW") == "申真谞",
          "meta=%s" % d.get("meta"))
    assert_moves("T08c 节点内中文注释不影响着法", d.get("moves"),
                 [["B", "D16"], ["W", "D4"], ["B", "Q4"]])

    # ---- T09 自有导出格式回环 ----
    st, d = import_sgf(base, SGF_ROUNDTRIP)
    check("T09a 回环棋谱status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    check("T09b 回环SZ/KM", d.get("board_size") == 9 and d.get("komi") == 6.5,
          "sz=%s km=%s" % (d.get("board_size"), d.get("komi")))
    assert_moves("T09c 回环11手(含末手黑pass B[])", d.get("moves"),
                 [["B", "D7"], ["W", "C7"], ["B", "C4"], ["W", "G7"], ["B", "G3"],
                  ["W", "C5"], ["B", "E3"], ["W", "G5"], ["B", "E7"], ["W", "E5"],
                  ["B", "pass"]])
    check("T09d 回环move_count=11", d.get("move_count") == 11, "got=%s" % d.get("move_count"))

    # ---- T10 多属性值 AB[aa][cc] ----
    st, d = import_sgf(base, SGF_MULTI_AB)
    check("T10a 多值AB status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    assert_moves("T10b AB[aa][cc]两枚摆子", d.get("setup_stones"),
                 [["B", "A19"], ["B", "C17"]])
    assert_moves("T10c 摆子棋谱着法正常", d.get("moves"),
                 [["B", "D16"], ["W", "E15"]])

    # ---- T11 仅摆子死活题 ----
    st, d = import_sgf(base, SGF_PROBLEM_ONLY)
    check("T11a 仅摆子死活题status=ok", st == 200 and d.get("status") == "ok", "data=%s" % d)
    check("T11b 死活题move_count=0且setup=7",
          d.get("move_count") == 0 and len(d.get("setup_stones", [])) == 7,
          "mc=%s setup=%s" % (d.get("move_count"), d.get("setup_stones")))
    check("T11c 死活题黑白摆子颜色正确",
          sorted(x[0] for x in d.get("setup_stones", [])) == ["B", "B", "W", "W", "W", "W", "W"],
          "setup=%s" % d.get("setup_stones"))

    # ---- T12 复杂 SGF（LB/TR/嵌套变化图）----
    st, d = import_sgf(base, SGF_COMPLEX)
    check("T12a 复杂SGF不崩且返回JSON结构", st == 200 and isinstance(d, dict) and "status" in d,
          "http=%s data=%s" % (st, d))
    if d.get("status") == "ok":
        assert_moves("T12b 复杂SGF只取正解主线", d.get("moves"),
                     [["B", "D16"], ["W", "D4"], ["B", "Q4"]])
    else:
        check("T12b 复杂SGF只取正解主线", False, "status=%s" % d.get("status"))

    # ---- 错误场景 ----
    st, d = http_post_json(base, "/api/import_sgf", {})
    check("E01a 空请求体返回400", st == 400, "http=%s data=%s" % (st, d))
    st, d = http_post_json(base, "/api/import_sgf", {"sgf": "   "})
    check("E01b 空白sgf返回400", st == 400, "http=%s data=%s" % (st, d))

    st, d = import_sgf(base, BAD_PLAIN_TEXT)
    check("E02 纯文本返回结构化error且不500",
          st == 200 and d.get("status") == "error" and "SGF" in d.get("message", ""),
          "http=%s data=%s" % (st, d))

    st, d = import_sgf(base, BAD_GM2)
    check("E03 GM[2]返回围棋错误提示",
          st == 200 and d.get("status") == "error" and "围棋" in d.get("message", ""),
          "http=%s data=%s" % (st, d))

    st, d = import_sgf(base, BAD_SZ25)
    check("E04 SZ[25]返回棋盘大小错误",
          st == 200 and d.get("status") == "error" and "棋盘大小" in d.get("message", ""),
          "http=%s data=%s" % (st, d))

    st, d = import_sgf(base, BAD_NO_MOVES)
    check("E05 无着法返回无着法错误",
          st == 200 and d.get("status") == "error" and "着法" in d.get("message", ""),
          "http=%s data=%s" % (st, d))

    st, d = import_sgf(base, BAD_TRUNCATED)
    check("E06 截断括号不500不崩(返回JSON)",
          st == 200 and isinstance(d, dict) and "status" in d,
          "http=%s data=%s" % (st, d))

    # ---- T20 错误轰炸后服务仍存活 ----
    st, d = http_get(base, "/api/status")
    check("T20 错误用例后 /api/status 双引擎仍就绪",
          st == 200 and d.get("engine_running") is True and d.get("analysis_ready") is True,
          "http=%s data=%s" % (st, d))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    args = parser.parse_args()

    print("=" * 60)
    print("F1 SGF 棋谱导入 验收测试  base=%s" % args.base)
    print("=" * 60)

    # 前置: 服务存活
    try:
        st, d = http_get(args.base, "/api/status")
        if not (d.get("engine_running") and d.get("analysis_ready")):
            print("[FAIL] 前置检查: 双引擎未就绪 %s" % d)
            sys.exit(2)
    except Exception as e:
        print("[FAIL] 前置检查: 服务不可达 %s" % e)
        sys.exit(2)
    print("[PASS] 前置检查: 服务存活且双引擎就绪")

    run(args.base)

    print("-" * 60)
    total = len(results)
    failed = [r for r in results if not r[1]]
    print("汇总: %d/%d PASS" % (total - len(failed), total))
    if failed:
        print("失败用例:")
        for name, _, detail in failed:
            print("  - %s: %s" % (name, detail))
        sys.exit(1)


if __name__ == "__main__":
    main()
