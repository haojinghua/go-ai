#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F9 验收测试（逻辑单测部分）：PV 变化图（togglePinnedPV / renderPvText /
drawPVOverlay / gtpToBoard / clearPinnedPV 覆盖完整性）+ 棋盘试下点击交互
纯标准库 Python + 系统 node（用 node 直接执行从 index.html 提取的真实页面 JS，
而非等价重写，保证测的就是线上生效的代码）。

覆盖：
  A  坐标命中 gtpToBoard（Q16/D4/K10、pass/resign→null、越界→null）
  C  togglePinnedPV 状态机三态（由状态栏推荐条目点击触发）：未固定→固定→再点取消；
     点其他条目切换；点非推荐点不影响；固定时清除悬停
  D  renderPvText 文字序列：黑白交替、首手=轮走方、>12 手截断「…（共 X 手）」
  E  pv 空分支：固定后提示「该候选点无后续变化数据」
  F  drawPVOverlay 绘制逻辑：半透明 0.55、编号 1..N、黑白交替、pass 跳过、
     最多绘制上限
  G  clearPinnedPV 覆盖完整性（静态审查，7 处调用 + runAnalysis 直接置 -1）：
     切 Tab / 重跑分析 / 前翻 / 后翻 / F5 胜率跳转 /
     落子 / 导入棋谱（经 initAnalyzeBoardData）/ 切换 next-player
  H  点击交互静态审查：棋盘点击直接试下落子（推荐点不再拦截），
     变化图固定入口在状态栏推荐条目，悬停预览与 mouseleave 语义不变
"""
import json
import re
import subprocess
import sys
import urllib.request

BASE = "http://localhost:8080"
INDEX_LOCAL = "/Users/jinghuahao/Desktop/小H/go-ai/index.html"
HARNESS = "/Users/jinghuahao/Desktop/小H/go-ai/tests/f9_harness.js"

passed = 0
failed = 0
fails = []


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS  " + name + (("  | " + detail) if detail else ""))
    else:
        failed += 1
        fails.append(name)
        print("FAIL  " + name + (("  | " + detail) if detail else ""))


def extract_function(src, name):
    """按 'function NAME(' 定位并用花括号配平提取函数源码。"""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        return None
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    return None


def fetch_index():
    with urllib.request.urlopen(BASE + "/", timeout=15) as r:
        return r.read().decode("utf-8")


def build_harness(page_src):
    funcs = ["gtpToBoard", "togglePinnedPV", "renderAnalyzeStatusHtml",
             "renderPvText", "clearPinnedPV", "drawPVOverlay"]
    parts = []
    for f in funcs:
        code = extract_function(page_src, f)
        if not code:
            raise RuntimeError("function not found in index.html: " + f)
        parts.append("// ===== extracted: " + f + " =====\n" + code + "\n")
    extracted = "\n".join(parts)

    prelude = r"""
'use strict';
var fs = require('fs');
// ---- 页面全局桩 ----
var COLS = 'ABCDEFGHJKLMNOPQRST';
var boardSize = 19;
var RANK_COLORS = ['#1B5E20','#2E7D32','#4CAF50','#81C784','#A5D6A7'];
var PV_MAX_SHOW = 12;
var margin = 30, cellSize = 30;
var analyzeResults = [];
var analyzeNextColor = 'B';
var hoveredPVIndex = -1;
var pinnedPVIndex = -1;
var currentTab = 'analyze';
var statusHtml = '';
var nextPlayerElValue = 'B';
var document = {
  getElementById: function(id) {
    if (id === 'analyze-status') return { get innerHTML(){return statusHtml;}, set innerHTML(v){statusHtml = v;} };
    if (id === 'next-player') return { get value(){return nextPlayerElValue;}, set value(v){nextPlayerElValue = v;} };
    return {};
  }
};
var drawBoardCalls = 0;
function drawBoard(){ drawBoardCalls++; }

function makeCtx(){
  var log = [];
  var ctx = {
    globalAlpha: 1, fillStyle: '', strokeStyle: '', lineWidth: 0,
    textAlign: '', textBaseline: '', font: '',
    beginPath: function(){ log.push(['beginPath']); },
    arc: function(x,y,r){ log.push(['arc', x, y, r]); },
    fill: function(){ log.push(['fill', ctx.fillStyle, ctx.globalAlpha]); },
    stroke: function(){ log.push(['stroke', ctx.strokeStyle, ctx.globalAlpha]); },
    fillText: function(t,x,y){ log.push(['fillText', String(t), x, y]); }
  };
  ctx._log = log;
  return ctx;
}

function resetFixture(results, nextColor, tab){
  analyzeResults = results; analyzeNextColor = nextColor || 'B';
  currentTab = tab || 'analyze';
  hoveredPVIndex = -1; pinnedPVIndex = -1;
  statusHtml = ''; drawBoardCalls = 0;
  globalThis.ctx = makeCtx(); // 每次重置时换新 ctx 桩（块C等未显式建桩的绘制有默认可归集的桩）
}
function stdResults(){
  return [
    {move:'Q16', winrate:51.2, scoreLead:0.3, visits:400, pv:['Q16','D4','Q3','C17','R14']},
    {move:'D4',  winrate:49.8, scoreLead:-0.1, visits:380, pv:['D4','Q16','C3','R6','K10']},
    {move:'K10', winrate:48.9, scoreLead:-0.5, visits:300, pv:[]}
  ];
}
function pos(g){ return gtpToBoard(g); }

var results = [];
function T(name, cond, detail){
  results.push({name: name, pass: !!cond, detail: detail === undefined ? '' : String(detail)});
}
"""

    tests = r"""
// ===== A 坐标命中 =====
(function(){
  T('A1 gtpToBoard Q16 == [3,15]', JSON.stringify(gtpToBoard('Q16'))==='[3,15]', gtpToBoard('Q16'));
  T('A2 gtpToBoard D4 == [15,3]', JSON.stringify(gtpToBoard('D4'))==='[15,3]', gtpToBoard('D4'));
  T('A3 gtpToBoard K10 == [9,9]', JSON.stringify(gtpToBoard('K10'))==='[9,9]', gtpToBoard('K10'));
  T('A4 gtpToBoard pass == null', gtpToBoard('pass')===null);
  T('A5 gtpToBoard resign == null', gtpToBoard('resign')===null);
  T('A6 gtpToBoard A20 越界 == null', gtpToBoard('A20')===null);
  T('A7 gtpToBoard Z5 非法列 == null', gtpToBoard('Z5')===null);
})();

// ===== C togglePinnedPV 状态机三态 =====
(function(){
  // 态1: 未固定 -> 固定
  resetFixture(stdResults(), 'B');
  togglePinnedPV(3, 15);
  T('C1 点击推荐点固定 idx=0', pinnedPVIndex===0, 'pinned='+pinnedPVIndex);
  T('C2 固定时状态栏为变化序列', statusHtml.indexOf('「Q16」后续变化')>=0 && statusHtml.indexOf('点击推荐条目收起')>=0);
  T('C3 序列首手为黑(轮走方)', statusHtml.indexOf('黑:Q16')>=0);
  // 态2: 再点同一点 -> 取消
  togglePinnedPV(3, 15);
  T('C4 再点同一推荐点取消 pinned=-1', pinnedPVIndex===-1);
  T('C5 取消后恢复推荐列表', statusHtml.indexOf('Top 3 推荐走法')>=0 && statusHtml.indexOf('点击推荐条目可展开/收起变化图')>=0);
  // 切换: 固定 A -> 点推荐点 B -> 切换为 B
  togglePinnedPV(3, 15);
  togglePinnedPV(15, 3);
  T('C6 点其他推荐点切换 idx=1', pinnedPVIndex===1, 'pinned='+pinnedPVIndex);
  T('C7 切换后序列为新推荐点', statusHtml.indexOf('「D4」后续变化')>=0);
  // 点非推荐点坐标: togglePinnedPV 直接返回, 状态不变
  togglePinnedPV(0, 0);
  T('C8 点非推荐点状态不变', pinnedPVIndex===1, 'pinned='+pinnedPVIndex);
  // 固定时清除悬停
  hoveredPVIndex = 2;
  togglePinnedPV(9, 9); // K10 是第3个推荐点(pv空)
  T('C9 切换到第3推荐点', pinnedPVIndex===2);
  T('C10 固定后悬停索引清除', hoveredPVIndex===-1);
  togglePinnedPV(9, 9);
  T('C11 取消固定后可再次固定', (togglePinnedPV(3,15), pinnedPVIndex===0));
})();

// ===== D renderPvText 文字序列 =====
(function(){
  var longPv = [];
  for (var i=0;i<15;i++) longPv.push('K'+(10+i)); // 15 手
  var longResults = [{move:'Q16', winrate:50, scoreLead:0, visits:100, pv:longPv}];
  resetFixture(longResults, 'B');
  togglePinnedPV(3, 15);
  T('D1 黑先交替序列', statusHtml.indexOf('黑:K10')>=0 && statusHtml.indexOf('白:K11')>=0 && statusHtml.indexOf('黑:K12')>=0);
  T('D2 前12手标注', statusHtml.indexOf('前 12 手')>=0);
  T('D3 >12手截断显示总数', statusHtml.indexOf('…（共 15 手）')>=0);
  T('D4 第13手起不展示', statusHtml.indexOf('K22')<0 && statusHtml.indexOf('K23')<0 && statusHtml.indexOf('K24')<0);
  resetFixture(longResults, 'W');
  togglePinnedPV(3, 15);
  T('D5 白先首手=白', statusHtml.indexOf('白:K10')>=0 && statusHtml.indexOf('黑:K11')>=0);
  resetFixture([{move:'Q16', winrate:50, scoreLead:0, visits:100, pv:['Q16','D4']}], 'B');
  togglePinnedPV(3, 15);
  T('D6 短序列无截断标注', statusHtml.indexOf('共 ')<0 && statusHtml.indexOf('前 2 手')>=0);
})();

// ===== E pv 空分支 =====
(function(){
  resetFixture(stdResults(), 'B');
  togglePinnedPV(9, 9); // K10 pv=[]
  T('E1 pv空固定仍生效', pinnedPVIndex===2);
  T('E2 pv空提示无后续变化数据', statusHtml.indexOf('该候选点无后续变化数据')>=0, statusHtml.slice(0,120));
  // drawPVOverlay 空 pv 不画任何子
  var c0 = makeCtx(); globalThis.ctx = c0;
  drawPVOverlay({pvMoves: [], nextPlayer: 'B'});
  T('E3 空pv绘制0个元素', c0._log.filter(function(e){return e[0]==='arc';}).length===0);
})();

// ===== F drawPVOverlay 绘制逻辑（真实提取函数 + ctx 桩） =====
(function(){
  var c = makeCtx();
  globalThis.ctx = c;
  drawPVOverlay({pvMoves: ['Q16','D4','Q3'], nextPlayer: 'B'});
  var arcs = c._log.filter(function(e){return e[0]==='arc';});
  var fills = c._log.filter(function(e){return e[0]==='fill';});
  var labels = c._log.filter(function(e){return e[0]==='fillText';});
  T('F1 3手绘制3子', arcs.length===3, 'arcs='+arcs.length);
  T('F2 半透明0.55', fills.every(function(f){return Math.abs(f[2]-0.55)<1e-9;}));
  T('F3 黑白交替(黑先)', fills[0][1]==='#222' && fills[1][1]==='#eee' && fills[2][1]==='#222',
    fills.map(function(f){return f[1];}).join(','));
  T('F4 手数编号1..3', labels[0][1]==='1' && labels[1][1]==='2' && labels[2][1]==='3');
  T('F5 白先首子为白', (function(){ var c2=makeCtx(); globalThis.ctx=c2;
      drawPVOverlay({pvMoves:['D4'], nextPlayer:'W'});
      var f2=c2._log.filter(function(e){return e[0]==='fill';});
      return f2.length===1 && f2[0][1]==='#eee'; })());
  T('F6 pass跳过不绘制', (function(){ var c3=makeCtx(); globalThis.ctx=c3;
      drawPVOverlay({pvMoves:['Q16','pass','D4'], nextPlayer:'B'});
      var a3=c3._log.filter(function(e){return e[0]==='arc';});
      var l3=c3._log.filter(function(e){return e[0]==='fillText';});
      return a3.length===2 && l3[0][1]==='1' && l3[1][1]==='3'; })(),
    'pass 后编号顺延(实现行为)');
  T('F7 超上限截断(实现=PV_MAX_SHOW=12)', (function(){ var c4=makeCtx(); globalThis.ctx=c4;
      var pv15=[]; for(var i=0;i<15;i++) pv15.push('K'+(1+i)); // 15 个全部合法坐标
      drawPVOverlay({pvMoves:pv15, nextPlayer:'B'});
      return c4._log.filter(function(e){return e[0]==='arc';}).length===12; })());
})();

console.log(JSON.stringify(results));
"""
    return prelude + "\n" + extracted + "\n" + tests


def run_node_harness():
    with open(INDEX_LOCAL, "r", encoding="utf-8") as f:
        page_src = f.read()
    # 校验服务端实际生效的页面同样含 F9 代码（磁盘实时读取）
    served = fetch_index()
    for key in ("togglePinnedPV", "pinnedPVIndex", "drawPVOverlay", "renderPvText"):
        check("T0." + key + " 已在服务端页面生效", key in served and key in page_src)
    harness = build_harness(page_src)
    with open(HARNESS, "w", encoding="utf-8") as f:
        f.write(harness)
    proc = subprocess.run(["node", HARNESS], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        print("node stderr:\n" + proc.stderr)
        raise RuntimeError("node harness failed")
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip().startswith("[")]
    return json.loads(lines[-1])


def static_clear_coverage(page_src):
    """G 组：clearPinnedPV 覆盖完整性（7 处调用 + runAnalysis 直接置 -1）。"""
    lines = page_src.splitlines()
    call_sites = [i + 1 for i, l in enumerate(lines)
                  if "clearPinnedPV()" in l and "function clearPinnedPV" not in l]
    check("G0 clearPinnedPV 清理调用共 7 处", len(call_sites) == 7,
          "lines=" + str(call_sites))

    def near(marker, desc, window=15, before=15):
        idx = None
        for i, l in enumerate(lines):
            if marker in l:
                idx = i
                break
        if idx is None:
            check("G-定位失败 " + desc, False, marker)
            return
        lo, hi = max(0, idx - before), min(len(lines), idx + window)
        hit = any("clearPinnedPV()" in lines[j] or "pinnedPVIndex = -1" in lines[j]
                  for j in range(lo, hi))
        check("G " + desc, hit)

    near("document.getElementById('pane-'+tab).classList.add('active');", "切 Tab 清理", window=8)
    near("重新分析时清除固定展开", "runAnalysis 重跑清理", window=2)
    near("已跳转到第 '+pt.turn+' 手", "F5 胜率曲线跳转清理", before=20, window=3)

    for fn, desc in [("analyzeMoveBack", "浏览手数-后翻清理"),
                     ("analyzeMoveForward", "浏览手数-前翻清理"),
                     ("handleAnalyzeClick", "落子清理"),
                     ("onNextPlayerChange", "切换 next-player 清理"),
                     ("initAnalyzeBoardData", "导入棋谱/识别流共用清理(initAnalyzeBoardData)")]:
        body = extract_function(page_src, fn)
        check("G " + desc, body is not None and ("clearPinnedPV()" in body or "pinnedPVIndex = -1" in body))

    # 导入棋谱经由 initAnalyzeBoardData 清理
    ais = extract_function(page_src, "applyImportedSgf")
    check("G applyImportedSgf(SGF导入) 调用 initAnalyzeBoardData", ais is not None and "initAnalyzeBoardData()" in ais)


def static_click_priority(page_src):
    """H 组：点击交互静态审查（试下优先 + 状态栏条目固定变化图）。"""
    m = re.search(r"canvas\.addEventListener\('click'", page_src)
    check("H0 存在 canvas click 监听", m is not None)
    if not m:
        return
    seg = page_src[m.start():m.start() + 1200]
    i_place = seg.find("handleAnalyzeClick(row, col)")
    check("H1 分析页棋盘点击直接走试下落子(handleAnalyzeClick)", i_place > 0)
    end = seg.find("});", i_place) if i_place > 0 else -1
    handler = seg[:end] if end > 0 else seg
    check("H2 棋盘点击路径无推荐点拦截(无 isPvHit/togglePinnedPV 分支)",
          "isPvHit" not in handler and "togglePinnedPV" not in handler)
    # 状态栏推荐条目接线 togglePinnedPV（变化图固定新入口）
    i_status = page_src.find("function renderAnalyzeStatusHtml")
    seg_s = page_src[i_status:i_status + 2200] if i_status > 0 else ""
    check("H3 推荐条目可点击固定变化图(onclick=togglePinnedPV)",
          'onclick="togglePinnedPV(' in seg_s)
    # mousemove: 固定时 return 不切换悬停
    m2 = re.search(r"canvas\.addEventListener\('mousemove'", page_src)
    seg2 = page_src[m2.start():m2.start() + 2500] if m2 else ""
    check("H4 mousemove 固定分支存在且提前 return",
          "pinnedPVIndex >= 0" in seg2 and seg2.find("return", seg2.find("pinnedPVIndex >= 0")) > 0)
    # mouseleave 只清 hover 不清 pin
    m3 = re.search(r"canvas\.addEventListener\('mouseleave'", page_src)
    seg3 = page_src[m3.start():m3.start() + 400] if m3 else ""
    check("H5 mouseleave 只清悬停不清固定", "clearPinnedPV" not in seg3 and "pinnedPVIndex" not in seg3)


def main():
    print("=" * 64)
    print("F9 逻辑单测：PV 点击展开（node 执行 index.html 真实提取代码）")
    print("=" * 64)
    with open(INDEX_LOCAL, "r", encoding="utf-8") as f:
        page_src = f.read()
    results = run_node_harness()
    for r in results:
        check(r["name"], r["pass"], r["detail"])
    static_clear_coverage(page_src)
    static_click_priority(page_src)

    print("-" * 64)
    print("总计: PASS=%d FAIL=%d" % (passed, failed))
    if fails:
        print("失败项: " + ", ".join(fails))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
